/** QA-only transport for the original GatewayClient. No native plugin calls. */
export const fetch = (input: RequestInfo | URL, init?: RequestInit) => globalThis.fetch(input, init);
