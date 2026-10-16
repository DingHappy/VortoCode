"""Local calibration envelope; tokenizer estimates are not provider hard-budget proof."""
from __future__ import annotations

import json
import hashlib
import math
import time

from src.llm.client import ProviderContentFiltered


class CalibrationStopped(RuntimeError):
    pass


def failure_record(error):
    """Safe outcome metadata; preserve provider blockers through exception wrapping."""
    cause, seen = error, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ProviderContentFiltered):
            return {"status": "blocked", "error": type(error).__name__,
                    "error_category": "provider_content_filter", "finish_reason": "content_filter"}
        cause = cause.__cause__ or cause.__context__
    return {"status": "failed", "error": type(error).__name__}


def coverage_summary(results, expected):
    counts = {status: sum(row.get('status') == status for row in results)
              for status in ('completed', 'blocked', 'failed')}
    return {"planned_trials": expected, "attempted_trials": len(results), **counts,
            "unattempted_trials": max(0, expected - len(results)),
            "full_coverage": len(results) == expected and counts['completed'] == expected}


class Meter:
    def __init__(self, encoding, *, tokens=1_000_000, requests=160, checkpoint=lambda: None):
        self.encoding, self.limit, self.request_limit = encoding, tokens, requests
        self.entries, self.committed, self.reported, self.reason = [], 0, 0, ""
        self.checkpoint = checkpoint
        self.current_trial = None

    def begin_trial(self, benchmark: str, question_id: str, repeat: int = 1):
        if benchmark not in {"ToolSandbox", "LongMemEval-S"} or not isinstance(question_id, str) or not question_id or type(repeat) is not int or repeat < 1:
            raise ValueError("invalid trial identity")
        self.current_trial = {"benchmark": benchmark, "id": question_id, "repeat": repeat}

    def _cost(self, entry, usage):
        from src.llm.observations import configured_tariff
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        exact = (all(type(v) is int and v >= 0 for v in (prompt, completion))
                 and prompt + completion == usage.get('total_tokens'))
        target = entry['target']
        price = configured_tariff(target['provider'], target['model'])
        cost = ((prompt * price['input_per_million'] + completion * price['output_per_million']) / 1_000_000) if exact and price else None
        entry.update(usage_evidence='exact' if exact else 'total_only', tariff=price,
                     cost_estimate=cost if cost is None or math.isfinite(cost) else None,
                     currency=price['currency'] if price else None)

    def snapshot(self):
        return {"limit": self.limit, "request_limit": self.request_limit, "requests": len(self.entries),
                "reported_tokens": self.reported, "committed_tokens": self.committed,
                "usage_complete": all(entry.get('settled') for entry in self.entries), "stopped_reason": self.reason,
                "reservation": "cl100k proxy x1.5 plus output cap; not a provider-enforced token upper bound"}

    async def chat(self, client, role, messages, **kwargs):
        if self.reason:
            raise CalibrationStopped(self.reason)
        cap = min(int(kwargs.get('max_tokens') or 2048), 2048)
        body = json.dumps({"messages": messages, "tools": kwargs.get('tools')}, ensure_ascii=False)
        estimate = math.ceil(len(self.encoding.encode(body, disallowed_special=())) * 1.5) + cap + 128
        if len(self.entries) >= self.request_limit or self.committed + estimate > self.limit:
            self.reason = "local request/token reservation limit reached"
            self.checkpoint()
            raise CalibrationStopped(self.reason)
        from src.llm.providers import request_target_view
        target = request_target_view(client)
        target['model'] = kwargs.get('model') or client.config.model
        entry = {"role": role, "reserved": estimate, "settled": False, "model": target['model'],
                 "target": target, "trial": dict(self.current_trial) if self.current_trial else None,
                 "input_sha256": hashlib.sha256(body.encode()).hexdigest(),
                 "parameters": {"temperature": kwargs.get('temperature'), "max_tokens": cap}}
        self.entries.append(entry); self.committed += estimate; self.checkpoint()
        started = time.monotonic()
        try:
            response = await client.chat(messages, **{**kwargs, 'max_tokens': cap})
            usage = response.get('usage') or {}
            entry.update(response_model=response.get('model'), observed_usage=usage)
            if type(usage.get('total_tokens')) is not int or usage['total_tokens'] < 0:
                self.reason = 'provider usage missing; reservation retained'
                raise CalibrationStopped('provider usage missing; reservation retained')
            total = usage['total_tokens']
            entry.update(settled=True, usage=usage, response_model=response.get('model'))
            self._cost(entry, usage)
            self.reported += total
            self.committed += total - estimate
            if self.committed > self.limit:
                self.reason = 'reported usage exceeded local estimate; further calls stopped'
            self.checkpoint()
            return response
        except ProviderContentFiltered as error:
            usage = error.usage
            entry.update(error=type(error).__name__, error_category="provider_content_filter",
                         finish_reason=error.finish_reason, response_model=error.response_model,
                         observed_usage=usage)
            if type(usage.get('total_tokens')) is int:
                total = usage['total_tokens']
                entry.update(settled=True, usage=usage)
                self._cost(entry, usage)
                self.reported += total
                self.committed += total - estimate
            self.reason = ('provider content_filter; calibration stopped' if entry['settled'] else
                           'provider content_filter; usage missing; reservation retained')
            self.checkpoint()
            raise CalibrationStopped(self.reason) from error
        except BaseException as error:
            entry['error'] = type(error).__name__
            self.reason = self.reason or 'request interrupted/failed; further calls stopped'
            self.checkpoint()
            raise
        finally:
            entry['elapsed_ms'] = int((time.monotonic() - started) * 1000)
            self.checkpoint()


class MeteredClient:
    def __init__(self, client, meter, role):
        self.client, self.meter, self.role = client, meter, role

    @property
    def config(self):
        return self.client.config

    async def chat(self, messages, **kwargs):
        return await self.meter.chat(self.client, self.role, messages, **kwargs)
