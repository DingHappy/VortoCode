import { expect, it } from "vitest";
import { connectionAddress } from "./connectionAddress";
import { desktopContext } from "../lib/desktopContext";

const source = { registrationId: "resident", baseUrl: "https://assistant.example", sid: "main", generation: 2, epoch: 1 };

it("keeps the just-attached resident address when opening its main chat from an older render", () => {
  // Attachment has updated the ref and UI, but changeAppMode's awaiting callback still
  // holds the previous local address. Native remote transport resolves by registration.
  const nextAddress = connectionAddress(undefined, "resident", "http://127.0.0.1:8080", source);
  const context = desktopContext({ registrationId: "resident", assistantId: "resident", scope: "general", repoRoot: "",
    sid: "main", baseUrl: source.baseUrl, runtimeId: "", connection: "connected", profiles: [{ id: "resident", kind: "remote", name: "个人助手", repoRoot: "@assistant", baseUrl: source.baseUrl, lastOpenedAt: 0 }], localResidentId: null,
    source: { ...source, baseUrl: nextAddress } });
  expect(nextAddress).toBe(source.baseUrl);
  expect(context.ready).toBe(true);
  expect(context.space?.runtime.registrationId).toBe("resident");
});

it("preserves an explicit edited address and never borrows another registration's address", () => {
  expect(connectionAddress("https://edited.example", "resident", "old", source)).toBe("https://edited.example");
  expect(connectionAddress(undefined, "other", "https://other.example", source)).toBe("https://other.example");
  expect(connectionAddress(undefined, null, "http://127.0.0.1:9090", source)).toBe("http://127.0.0.1:9090");
  expect(connectionAddress(undefined, "resident", "https://assistant.example", null)).toBe("https://assistant.example");
});
