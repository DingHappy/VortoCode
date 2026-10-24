import { expect, it } from "vitest";
import wire from "../../../tests/fixtures/execution_observation_v1.json";
import { readExecutionObservation } from "./executionObservation";

it("reads the same v1 wire fixture as the real Python observer", () => {
  const observation = readExecutionObservation(wire)!;
  expect(observation.state).toBe("waiting_user");
  expect(observation.ref).toEqual(wire.ref);
  expect(observation.progress).toEqual(wire.progress);
  expect(observation.observed_at).toBe(100);
  expect(observation).not.toHaveProperty("result");
  expect(observation).not.toHaveProperty("features");
});

it("accepts additive fields and new vendors but preserves unknown states", () => {
  const observation = readExecutionObservation({ ...wire, optional_future_field: 42,
    ref: { ...wire.ref, backend_id: "vendor.future" }, state: "vendor_future_state", native_state: "" })!;
  expect(observation.ref.backend_id).toBe("vendor.future");
  expect(observation.state).toBe("unknown"); expect(observation.native_state).toBe("vendor_future_state");
});

it("keeps a known unknown state distinct from a newly introduced native state", () => {
  expect(readExecutionObservation({ ...wire, state: "unknown", native_state: "" })?.native_state).toBe("");
});

it.each([null, [], { ...wire, schema_version: 2 }, { ...wire, schema_version: true },
  { ...wire, ref: { ...wire.ref, instance_id: "" } }, { ...wire, ref: { ...wire.ref, session_id: false } },
  { ...wire, ref: { ...wire.ref, scope: "forever" } }, { ...wire, observed_at: Infinity },
  { ...wire, observed_at: -1 }, { ...wire, observed_at: true }, { ...wire, state: null },
  { ...wire, native_state: false }, { ...wire, progress: null },
  { ...wire, progress: { completed_steps: 3, total_steps: 2 } },
  { ...wire, progress: { completed_steps: true } },
  { ...wire, progress: { total_steps: Number.MAX_SAFE_INTEGER + 1 } },
])("unusable observations never become an idle or successful state (%#)", value => {
  expect(readExecutionObservation(value)).toBeNull();
});
