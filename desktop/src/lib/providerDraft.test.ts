import { expect, it } from "vitest";
import { providerDraftState, type ProviderDraft } from "./providerDraft";
const provider = { id: "cloud", name: "Cloud", baseUrl: "https://cloud.example.test/v1", hasKey: true, models: ["m"] };
const draft: ProviderDraft = { ...provider, apiKey: "", editing: true };
it("retains a saved key only for the same provider and endpoint", () => {
  expect(providerDraftState(draft, [provider])).toMatchObject({ dirty: false, canKeepKey: true, canSave: false });
  expect(providerDraftState({ ...draft, name: "Renamed" }, [provider])).toMatchObject({ canKeepKey: true, canSave: true });
  expect(providerDraftState({ ...draft, baseUrl: "https://new.example.test/v1" }, [provider])).toMatchObject({ canKeepKey: false, canSave: false, missing: "接口地址已改变，请为新服务填写 API Key。" });
  expect(providerDraftState({ ...draft, baseUrl: "https://new.example.test/v1", apiKey: "fixture-only" }, [provider]).canSave).toBe(true);
});
it("does not assume an edited cloud provider has a key just because it exists", () => {
  expect(providerDraftState({ ...draft, name: "Renamed" }, [{ ...provider, hasKey: false }])).toMatchObject({ canSave: false, canKeepKey: false });
});
it("accepts local keyless services and native ID normalization", () => {
  expect(providerDraftState({ id: " LOCAL_2 ", name: "", baseUrl: "http://127.0.0.1:11500/v1", apiKey: "", editing: false }, []).canSave).toBe(true);
});
it("catches duplicate, invalid and reserved IDs before asking native confirmation", () => {
  expect(providerDraftState({ ...draft, editing: false }, [provider]).missing).toContain("已有同 ID");
  for (const id of ["bad-id", "1abc", "default", "anthropic", "cli", "a".repeat(25)]) {
    expect(providerDraftState({ ...draft, id, apiKey: "fixture-only", editing: false }, []).canSave).toBe(false);
  }
});
it("rejects insecure or credential-bearing endpoints without echoing entered credentials", () => {
  for (const baseUrl of ["http://cloud.example.test/v1", "ftp://localhost/v1", "https://user:fixture-password@cloud.example.test/v1", "https://cloud.example.test/v1?key=fixture-only", "https://cloud.example.test/v1#fragment", "not-url"]) {
    const state = providerDraftState({ ...draft, baseUrl, apiKey: "fixture-only" }, [provider]);
    expect(state.canSave).toBe(false);
    expect(state.missing).not.toContain("fixture-password");
    expect(state.missing).not.toContain("fixture-only");
  }
});
it("reports incomplete forms and validates the displayed service name", () => {
  expect(providerDraftState({ ...draft, id: "", editing: false }, []).missing).toContain("服务 ID");
  expect(providerDraftState({ ...draft, baseUrl: "" }, [provider]).missing).toContain("接口地址");
  for (const name of ["a".repeat(41), "bad\nname"]) expect(providerDraftState({ ...draft, name }, [provider]).canSave).toBe(false);
});
