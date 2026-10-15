"""Shared lexical ranking; no model, embeddings, or persistent index."""
import re


def rank_memories(rows, query, limit=10):
    """Rank hits, preserving the caller's importance/recency order for ties."""
    q = (query or "").strip().lower()
    if not q:
        return rows[:limit]
    terms = [term for term in re.split(r"[\s,，。;；、/：:()（）\[\]{}]+", q) if term] or [q]
    for run in re.findall(r"[㐀-鿿]{2,}", q):
        terms.extend(run[index:index + 2] for index in range(len(run) - 1))
    terms = list(dict.fromkeys(terms))
    scored = []
    for row in rows:
        content = str(row.get("content", "")).lower()
        score = sum(1 for term in terms if term in content)
        if q in content:
            score += len(terms)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return [row for _, row in scored][:limit]
