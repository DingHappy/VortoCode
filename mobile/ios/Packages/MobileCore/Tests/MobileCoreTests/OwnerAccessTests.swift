import XCTest
@testable import MobileCore

final class OwnerAccessTests: XCTestCase {
    func wire() -> [String: Any] {
        ["schema_version": 1, "generated_at": "2026-11-03T10:00:00Z", "permissions": [
            "profile": "owner_device", "stored_level": "full", "effective_level": "reads", "ceiling": "reads",
            "scopes": ["once"], "taint_revokes_preauthorization": true,
            "categories": [["operation_kind": "own_data", "risk": "normal", "required_key": "device", "preauthorized": true]]],
         "history": ["status": "partial", "bounded": true, "entries": [["id": "h1", "ts": "today", "decision": "denied", "tainted": true,
             "record_type": "card", "origin": "app", "operation_kind": "deliver", "approval_outcome": "expired", "decision_source": "timeout", "approval_scope": "once"]]]]
    }
    func decode(_ raw: [String: Any]) throws -> OwnerAccessSnapshot {
        try MobileAPI.decoder.decode(OwnerAccessSnapshot.self, from: JSONSerialization.data(withJSONObject: raw))
    }
    func testPolicyAndMetadataLabelsAreReadOnlyAndConservative() throws {
        let value = try decode(wire()); try value.validate()
        XCTAssertEqual(value.permissions.effectiveLevel, "reads")
        XCTAssertEqual(value.history.entries[0].label, "对外发送")
        XCTAssertEqual(value.history.entries[0].outcomeLabel, "已过期，未放行")
        XCTAssertEqual(value.history.entries[0].sourceLabel, "超时")
        XCTAssertEqual(value.history.entries[0].scopeLabel, "仅本次")
        var raw = wire(); var history = raw["history"] as! [String: Any]
        history["entries"] = [["id": "legacy", "ts": "old", "decision": "allowed", "tainted": false, "record_type": "decision", "origin": "runtime"]]
        raw["history"] = history
        let legacy = try decode(raw).history.entries[0]
        XCTAssertEqual(legacy.label, "类别待核对"); XCTAssertEqual(legacy.sourceLabel, "来源待核对")
        XCTAssertEqual(legacy.outcomeLabel, "已允许")
    }
    func testFutureGrantOrVersionDoesNotBecomeAnAcceptedPolicy() throws {
        var raw = wire(); raw["schema_version"] = 2
        XCTAssertThrowsError(try decode(raw).validate())
        for update in [["scopes": ["always"] as Any], ["effective_level": "full" as Any], ["taint_revokes_preauthorization": false as Any]] {
            var raw = wire(); var permissions = raw["permissions"] as! [String: Any]
            permissions.merge(update) { _, value in value }; raw["permissions"] = permissions
            XCTAssertThrowsError(try decode(raw).validate())
        }
    }
    func testLostTurnHasNoActionableCardsEvenWithStaleCardPayload() throws {
        let raw: [String: Any] = ["rid": "app-lost", "request_id": "", "state": "waiting", "outcome": "unknown", "reply": "", "error": "进度无法核对",
            "cards": [["id": "old", "text": "old", "risk": "normal", "status": "pending", "digest": "d"]]]
        let turn = try MobileAPI.decoder.decode(ChatTurn.self, from: JSONSerialization.data(withJSONObject: raw))
        XCTAssertTrue(turn.isFinished); XCTAssertTrue(turn.pendingCards.isEmpty)
    }

    func testDomainDecisionAndExecutionStateDoNotCollapseIntoOneResult() throws {
        var raw = wire()
        raw["history"] = ["status": "partial", "bounded": true, "sources": ["codex": "current", "dispatches": "unavailable"],
            "entries": [["id": "domain", "ts": "today", "decision": "allowed", "tainted": false,
                "record_type": "codex_action", "domain_kind": "message", "origin": "im", "approval_outcome": "approved",
                "decision_source": "user", "approval_scope": "once", "source_status": "unknown"],
                ["id": "auto", "ts": "today", "decision": "allowed", "tainted": false, "record_type": "dispatch", "origin": "runtime",
                 "approval_outcome": "approved", "decision_source": "automatic_review", "source_status": "done"]]]
        let value = try decode(raw); try value.validate()
        XCTAssertEqual(value.history.unavailableLabels, ["任务单"])
        let codex = value.history.entries[0]
        XCTAssertEqual(codex.label, "Codex 消息"); XCTAssertEqual(codex.outcomeLabel, "已允许")
        XCTAssertEqual(codex.originLabel, "IM"); XCTAssertEqual(codex.sourceStatusLabel, "发送结果无法核对")
        let automatic = value.history.entries[1]
        XCTAssertEqual(automatic.sourceLabel, "后台审核")
        XCTAssertEqual(automatic.sourceStatusLabel, "运行端报告结束，验收仍需核对")
    }

    func testLegacyUnknownDecisionIsNeitherApprovedNorDenied() throws {
        var raw = wire()
        raw["history"] = ["status": "current", "bounded": true, "entries": [["id": "old", "ts": "", "decision": "unknown", "tainted": false,
            "record_type": "codex_action", "origin": "unknown", "source_status": "sent", "decision_metadata_missing": true]]]
        let row = try decode(raw).history.entries[0]
        XCTAssertEqual(row.outcomeLabel, "结果待核对"); XCTAssertEqual(row.originLabel, "入口待核对")
        XCTAssertEqual(row.sourceStatusLabel, "Codex 已受理，执行结果仍需核对")
        XCTAssertEqual(row.decisionMetadataMissing, true)
    }
}
