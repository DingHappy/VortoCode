import { useRef } from "react";
import { Search, X } from "lucide-react";
import "./CollectionToolbar.css";

/** Local view controls. The owning page keeps its data, selection and write handlers. */
export function CollectionToolbar({ query, onQuery, searchLabel, filters, filter, onFilter, filterLabel, summary }: {
  query: string; onQuery: (value: string) => void; searchLabel: string;
  filters: Array<{ id: string; label: string; count: number }>;
  filter: string; onFilter: (id: string) => void; filterLabel: string; summary: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return <div className="vc-collection-toolbar">
    <div className="vc-collection-controls">
      <div className="vc-collection-search">
        <Search aria-hidden="true" />
        <input ref={input} type="search" aria-label={searchLabel} placeholder={searchLabel} value={query}
          onChange={event => onQuery(event.target.value)} />
        {query && <button type="button" aria-label="清除搜索" onClick={() => { onQuery(""); input.current?.focus(); }}><X aria-hidden="true" /></button>}
      </div>
      <div className="vc-collection-filters" role="group" aria-label={filterLabel}>
        {filters.map(item => <button key={item.id} type="button" aria-pressed={filter === item.id}
          onClick={() => onFilter(item.id)}>{item.label}<span>{item.count}</span></button>)}
      </div>
    </div>
    <p className="vc-collection-summary" role="status">{summary}</p>
  </div>;
}
