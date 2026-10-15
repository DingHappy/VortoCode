"""Acceptance reports must be bounded, private, and explicit about incomplete coverage."""
import json
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError

import pytest

from src.gateway.runtime_acceptance import Target, fetch_json, monitor, probe, read_report


class Clock:
    value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def healthy(url, token, timeout):
    assert timeout <= 3
    if url.endswith('/quick'):
        return {'status': 'healthy', 'private': 'synthetic-private-text'}
    if url.endswith('/sessions'):
        return {'sessions': [{'status': 'running', 'queue_count': 2, 'title': 'synthetic-private-text'}]}
    if url.endswith('/tasks'):
        return {'tasks': [{'status': 'done', 'goal': 'synthetic-private-text'}]}
    return {'bridge': True, 'connected': True, 'busy': False, 'undelivered': 0,
            'last_error': 'synthetic-private-text'}


@pytest.mark.parametrize('url', [
    'http://example.com', 'http://127.0.0.1@other.invalid', 'http://user:secret@localhost',
    'http://localhost/?token=secret', 'http://localhost/private', 'file:///tmp/example',
])
def test_targets_reject_nonlocal_or_credential_bearing_urls(url):
    with pytest.raises(ValueError):
        Target('assistant', url).validate()


def test_report_contains_aggregates_and_no_credentials_or_payload(tmp_path):
    clock, output = Clock(), tmp_path / 'report.jsonl'
    result = monitor([Target('assistant', 'http://localhost:8081', True), Target('code', 'http://localhost:8082')],
                     output, duration=3, interval=1, token='synthetic-bearer', require_im=True,
                     fetch=healthy, clock=clock.now, sleep=clock.sleep)
    assert result['completed'] and result['coverage_complete']
    assert result['samples'] == 3 and result['model_requests'] == 0
    raw = output.read_text()
    for private in ('synthetic-bearer', 'synthetic-private-text', 'localhost', '8081'):
        assert private not in raw
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    records = [json.loads(line) for line in raw.splitlines()]
    assert records[1]['targets']['assistant']['queued_inputs'] == 2
    assert records[1]['targets']['code']['tasks'] == {'done': 1}
    assert read_report(output)['state'] == 'passed'
    assert read_report(output)['full_task_loop_verified'] is False


def test_auth_failure_is_unknown_and_does_not_log_raw_error_or_token(tmp_path):
    clock, output = Clock(), tmp_path / 'report.jsonl'

    def unauthorized(url, token, timeout):
        raise HTTPError(url, 401, 'synthetic-bearer', {}, None)

    result = monitor([Target('assistant', 'http://localhost', True)], output, duration=1, interval=1,
                     token='synthetic-bearer', fetch=unauthorized, clock=clock.now, sleep=clock.sleep)
    assert result['unhealthy_samples'] == 1
    assert read_report(output)['state'] == 'failed'
    raw = output.read_text()
    assert 'http_401' in raw and 'synthetic-bearer' not in raw and 'localhost' not in raw


def test_missing_end_record_and_truncated_record_never_pass(tmp_path):
    clock, output = Clock(), tmp_path / 'report.jsonl'
    monitor([Target('code', 'http://localhost')], output, duration=1, interval=1,
            fetch=healthy, clock=clock.now, sleep=clock.sleep)
    lines = output.read_text().splitlines()
    output.write_text('\n'.join(lines[:-1]) + '\n')
    assert read_report(output)['state'] == 'incomplete'
    with output.open('a') as handle:
        handle.write('{"type":')
    assert read_report(output)['state'] == 'incomplete'


def test_coverage_gap_is_not_approved_as_a_complete_run(tmp_path):
    clock, output = Clock(), tmp_path / 'report.jsonl'

    def slow_fetch(*args):
        clock.value += 5
        return healthy(*args)

    result = monitor([Target('code', 'http://localhost')], output, duration=10, interval=1,
                     fetch=slow_fetch, clock=clock.now, sleep=clock.sleep)
    assert result['completed'] and not result['coverage_complete']
    assert read_report(output)['state'] == 'incomplete'


def test_interrupt_leaves_a_durable_incomplete_summary(tmp_path):
    clock, output = Clock(), tmp_path / 'report.jsonl'

    def interrupt(seconds):
        raise KeyboardInterrupt()

    result = monitor([Target('code', 'http://localhost')], output, duration=10, interval=1,
                     fetch=healthy, clock=clock.now, sleep=interrupt)
    assert not result['completed']
    assert json.loads(output.read_text().splitlines()[-1])['type'] == 'summary'
    assert read_report(output)['state'] == 'incomplete'


def test_existing_report_cannot_be_overwritten(tmp_path):
    output = tmp_path / 'report.jsonl'
    output.write_text('retained')
    with pytest.raises(FileExistsError):
        monitor([Target('code', 'http://localhost')], output, duration=1, interval=1)
    assert output.read_text() == 'retained'


def test_required_im_connection_and_unknown_status_are_not_fabricated():
    def disconnected(url, token, timeout):
        if url.endswith('/status'):
            return {'bridge': True, 'connected': False}
        if url.endswith('/sessions'):
            return {'sessions': [{'status': 'synthetic-private-text', 'queue_count': 0}]}
        return healthy(url, token, timeout)

    result = probe(Target('assistant', 'http://localhost', True), fetch=disconnected, require_im=True)
    assert result['problems'] == ['sessions_unknown_status', 'im_disconnected']
    assert result['sessions'] == {'unknown': 1}
    assert 'synthetic-private-text' not in json.dumps(result)


def test_connected_bridge_with_pending_delivery_is_not_healthy():
    def pending(url, token, timeout):
        data = healthy(url, token, timeout)
        if url.endswith('/status'):
            data['undelivered'] = 1
        return data

    result = probe(Target('assistant', 'http://localhost', True), fetch=pending, require_im=True)
    assert result['problems'] == ['im_delivery_pending']


def test_redirects_never_forward_credentials(monkeypatch):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/unexpected')
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    try:
        with pytest.raises(HTTPError) as error:
            fetch_json(f'http://127.0.0.1:{server.server_port}/redirect', 'synthetic-bearer', 2)
        assert error.value.code == 302
        assert seen == ['/redirect']
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
