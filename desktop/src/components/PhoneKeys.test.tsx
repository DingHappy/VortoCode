import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PhoneKeys, type PhoneKeyClient } from "./PhoneKeys";

const client: PhoneKeyClient = {
  listPhoneKeys: async () => ({ pending: [], trusted: [] }),
  decidePhoneKey: async () => undefined,
  removeTrustedPhone: async () => undefined,
};

it("没连上、或还没读到记录时不显示这一段", () => {
  expect(renderToStaticMarkup(<PhoneKeys client={null} connected />)).toBe("");
  expect(renderToStaticMarkup(<PhoneKeys client={client} connected={false} />)).toBe("");
  expect(renderToStaticMarkup(<PhoneKeys client={client} connected />)).toBe("");
});
