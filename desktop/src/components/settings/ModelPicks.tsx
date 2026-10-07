// 设置 → 模型 → 输入框里的模型：输入框只列主模型 / 快速 / 强力档和这份短名单。
// 没调整过时短名单跟随服务端推荐（relay 可随时调整，不用发新版）；在这里增删、排序后改为用户自己的名单。
// 名单只影响输入框里列哪些模型，runtime 放行哪些模型仍由默认服务的模型清单决定。
import { ArrowDown, ArrowUp, Plus, RotateCcw, X } from "lucide-react";
import { useMemo, useState } from "react";

import { availableModels, recommendedModels } from "../../lib/modelChoice";
import type { DesktopLlmProfileStatus } from "../../types";

type Props = {
  profile: DesktopLlmProfileStatus | null;
  /** null = 跟随服务端推荐。 */
  picks: string[] | null;
  onChange: (picks: string[] | null) => void;
};

export function ModelPicks({ profile, picks, onChange }: Props) {
  const [query, setQuery] = useState("");
  const available = useMemo(() => availableModels(profile), [profile]);
  const byValue = useMemo(() => new Map(available.map((choice) => [choice.value, choice])), [available]);
  const recommended = useMemo(() => recommendedModels(profile), [profile]);
  // 名单里已不在服务上的模型不显示，也不会出现在输入框里；保存时一并清掉。
  const current = (picks ?? recommended).filter((model) => byValue.has(model));
  const keyword = query.trim().toLowerCase();
  const more = available.filter((choice) => !current.includes(choice.value) && (!keyword || choice.value.toLowerCase().includes(keyword)));
  const groups = [...new Set(more.map((choice) => choice.group ?? ""))];

  if (!profile?.configured || available.length === 0) return null;

  const move = (index: number, offset: number) => {
    const next = [...current];
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    onChange(next);
  };

  return (
    <section className="custom-providers model-picks" aria-label="输入框里的模型">
      <div className="custom-providers-head">
        <div>
          <strong>输入框里的模型</strong>
          <p>输入框只列主模型、快速 / 强力档和下面这几个；{picks ? "这是你调整过的名单。" : "当前跟随服务推荐。"}其余模型从「更多模型」里添加。</p>
        </div>
        {picks && (
          <div className="custom-providers-toolbar">
            <button onClick={() => onChange(null)} title="恢复为服务推荐的名单"><RotateCcw size={14} />恢复推荐</button>
          </div>
        )}
      </div>

      {current.length > 0 ? (
        <ol className="custom-provider-list model-picks-list">
          {current.map((model, index) => (
            <li key={model}>
              <div>
                <strong>{model}</strong>
                <span>{byValue.get(model)?.hint}</span>
              </div>
              <button aria-label={`上移 ${model}`} onClick={() => move(index, -1)} disabled={index === 0}><ArrowUp size={14} /></button>
              <button aria-label={`下移 ${model}`} onClick={() => move(index, 1)} disabled={index === current.length - 1}><ArrowDown size={14} /></button>
              <button aria-label={`从输入框移除 ${model}`} onClick={() => onChange(current.filter((item) => item !== model))}><X size={14} /></button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="custom-providers-note">输入框里只有已配置的模型。</p>
      )}

      <details className="model-picks-more">
        <summary>更多模型（{available.length - current.length}）</summary>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索模型名" aria-label="搜索模型" />
        {groups.map((group) => (
          <div key={group} className="model-picks-group">
            <span>{group}</span>
            <ul className="custom-provider-list">
              {more.filter((choice) => (choice.group ?? "") === group).map((choice) => (
                <li key={choice.value}>
                  <div>
                    <strong>{choice.label}</strong>
                    {choice.badges?.length ? <span>{choice.badges.join(" · ")}</span> : null}
                  </div>
                  <button aria-label={`添加 ${choice.value} 到输入框`} onClick={() => onChange([...current, choice.value])}><Plus size={14} />添加</button>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {more.length === 0 && <p className="custom-providers-note">{keyword ? "没有匹配的模型。" : "服务上的模型都已在输入框里。"}</p>}
      </details>
    </section>
  );
}
