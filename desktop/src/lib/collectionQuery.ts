/** Searches only metadata already loaded by the page, without reading document bodies or fetching. */
export function matchesCollectionQuery(query: string, values: readonly string[]) {
  const normalize = (value: string) => value.normalize("NFKC").toLowerCase();
  const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
  const text = values.map(normalize).join(" ");
  return terms.every(term => text.includes(term));
}
