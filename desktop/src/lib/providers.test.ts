import { describe, expect, it } from "vitest";

import { PROVIDER_PRESETS, presetForBaseUrl } from "./providers";

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

  it("only the local preset may omit a key, and every remote preset uses HTTPS", () => {
    for (const preset of PROVIDER_PRESETS) {
      expect(preset.requiresKey).toBe(preset.id !== "local");
      if (preset.id !== "local") expect(preset.baseUrl.startsWith("https://")).toBe(true);
    }
  });
});
