import { describe, expect, it } from "vitest";

import { PROVIDER_PRESETS, isLocalModelBase, presetForBaseUrl, sameLlmBase } from "./providers";

describe("provider presets", () => {
  it("match known base URLs regardless of trailing slash or case", () => {
    expect(presetForBaseUrl("https://token.vortotech.com/v1/")?.id).toBe("vortocode");
    expect(presetForBaseUrl("HTTPS://api.deepseek.com/v1")?.id).toBe("deepseek");
  });

  it("treat any loopback address as the local preset and unknown hosts as custom", () => {
    expect(presetForBaseUrl("http://localhost:1234/v1")?.id).toBe("local");
    expect(presetForBaseUrl("https://gateway.example.com/v1")).toBeNull();
    expect(presetForBaseUrl("")).toBeNull();
  });

  it("only local presets may omit a key, and every remote preset uses HTTPS", () => {
    for (const preset of PROVIDER_PRESETS) {
      expect(preset.requiresKey).toBe(!isLocalModelBase(preset.baseUrl));
      if (preset.requiresKey) expect(preset.baseUrl.startsWith("https://")).toBe(true);
    }
  });
});

describe("sameLlmBase", () => {
  it("ignores surrounding space and trailing slashes but nothing else", () => {
    expect(sameLlmBase("https://a.example/v1", " https://a.example/v1/ ")).toBe(true);
    expect(sameLlmBase("https://a.example/v1", "https://b.example/v1")).toBe(false);
    expect(sameLlmBase("", "")).toBe(false);
  });
});

describe("local endpoint credentials", () => {
  it("rejects lookalike hosts, embedded credentials, metadata and invalid schemes", async () => {
    const {isLocalModelBase} = await import("./providers");
    for (const base of ["http://localhost.evil.test/v1", "http://127.0.0.1.evil.test/v1", "http://localhost@evil.test/v1", "http://user@localhost/v1", "http://localhost/v1?token=abc", "http://localhost/v1#x", "ftp://localhost/v1", "http://192.168.1.2:1234/v1", "http://localhost:bad/v1"]) {
      expect(isLocalModelBase(base)).toBe(false);
    }
    expect(isLocalModelBase(" http://localhost:1234/v1 ")).toBe(true);
    expect(isLocalModelBase("https://127.0.0.1:11434/v1")).toBe(true);
  });
});


it("oMLX has its own editable local preset without inferring server brand at other ports", () => {
  const preset = PROVIDER_PRESETS.find((item) => item.id === "omlx")!;
  expect(preset.baseUrl).toBe("http://127.0.0.1:8000/v1");
  expect(preset.model).toBe(""); expect(preset.requiresKey).toBe(false);
  expect(presetForBaseUrl(preset.baseUrl + "/")?.id).toBe("omlx");
  expect(presetForBaseUrl("http://127.0.0.1:8100/v1")?.id).toBe("local");
  expect(presetForBaseUrl("http://127.0.0.1:11500/v1")?.id).toBe("local");
});
