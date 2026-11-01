import { expect, it } from "vitest";
import { websiteConfirmation } from "./websiteConfirmation";

const valid = { version: 1, action: "login", origin: "https://example.com", account_label: "工作", device_mode: "desktop" };
it("accepts only an origin and public account metadata from the runtime", () => {
  expect(websiteConfirmation(valid)).toEqual(valid);
  expect(websiteConfirmation({ ...valid, origin: "https://example.com:8443" })?.origin).toBe("https://example.com:8443");
});
it.each([null, "browser_login", [], {}, { ...valid, version: 2 }, { ...valid, action: "execute" },
  { ...valid, origin: "https://example.com/callback?code=secret" }, { ...valid, origin: "https://u:p@example.com" },
  { ...valid, origin: "javascript:alert(1)" }, { ...valid, cookie: "secret" },
  { ...valid, action: ["login"] }, { ...valid, device_mode: ["desktop"] },
  { ...valid, device_mode: "native" }, { ...valid, account_label: "" }, { ...valid, account_label: "a\nb" }])(
  "falls back to the generic confirmation for invalid metadata %j", (value) => {
    expect(websiteConfirmation(value)).toBeUndefined();
  });
