import XCTest
@testable import MobileCore

final class ApprovalCardTests: XCTestCase {
    let scopeFields: [String: Any] = ["operation_kind": "own_data", "tainted": false, "scope_options": ["once", "turn"],
        "scope_keys": ["once": "device", "turn": "approval"], "scope_limits": ["max_actions": 10, "ttl_seconds": 600]]
    func card(_ fields: [String: Any]) throws -> ChatCard {
        var raw: [String: Any] = ["id": "c", "text": "只是看看", "risk": "normal", "status": "pending", "digest": "d"]
        raw.merge(fields) { _, value in value }
        return try MobileAPI.decoder.decode(ChatCard.self, from: JSONSerialization.data(withJSONObject: raw))
    }

    func testServerKeyRequirementCannotBeDowngradedByRiskOrText() throws {
        let sensitive = try card(["operation_kind": "deliver", "required_key": "approval", "tainted": true, "approval_scope": "once"])
        XCTAssertTrue(sensitive.needsFaceID)
        XCTAssertEqual(sensitive.categoryLabel, "对外发送")
        XCTAssertEqual(sensitive.tainted, true)
        XCTAssertEqual(sensitive.approvalScope, "once")
        XCTAssertTrue(try card(["risk": "high", "required_key": "device"]).needsFaceID)
        XCTAssertTrue(try card(["required_key": "future_key"]).needsFaceID)
        XCTAssertTrue(try card(["risk": "future_risk"]).needsFaceID)
    }

    func testLegacyAndOrdinaryCardContracts() throws {
        let legacy = try card([:])
        XCTAssertFalse(legacy.needsFaceID)
        XCTAssertEqual(legacy.categoryLabel, "类别待核对")
        let ordinary = try card(["operation_kind": "own_data", "required_key": "device"])
        XCTAssertFalse(ordinary.needsFaceID)
        XCTAssertEqual(ordinary.categoryLabel, "你的事项")
        // 不认识的类别和 Desktop 一样显示「未分类操作」（design/copy.json）
        XCTAssertEqual(try card(["operation_kind": "future_kind"]).categoryLabel, "未分类操作")
    }

    func testBoundedChoiceRequiresAllServerFieldsAndNeverCoversSensitiveKinds() throws {
        XCTAssertFalse(try card([:]).canAllowTurn)
        XCTAssertTrue(try card(scopeFields).canAllowTurn)
        for patch: [String: Any] in [["tainted": true], ["risk": "high"], ["operation_kind": "deliver"], ["operation_kind": "read"],
            ["scope_options": ["once"]], ["scope_keys": ["turn": "device"]], ["scope_limits": ["max_actions": 11, "ttl_seconds": 600]],
            ["scope_limits": ["max_actions": 10, "ttl_seconds": 601]], ["scope_limits": ["max_actions": 0, "ttl_seconds": 600]]] {
            var fields = scopeFields; fields.merge(patch) { _, value in value }
            XCTAssertFalse(try card(fields).canAllowTurn)
        }
    }

    func testRangeChoiceSelectsApprovalKeyAndIsCoveredBySignedBody() async throws {
        let api = MobileAPI(entry: try AssistantEntry("https://example.invalid"))
        let credential = NativeCredential(schemaVersion: 1, token: "vcn_" + String(repeating: "a", count: 43),
            instanceId: String(repeating: "b", count: 32), name: "test", expiresAt: Int(Date().timeIntervalSince1970) + 600)
        let device = String(repeating: "c", count: 32)
        for scope in ["once", "turn"] {
            let signer = CaptureAndStopSigner()
            do {
                _ = try await api.chatDecide(credential, signer: signer, deviceId: device, rid: "r", card: card(scopeFields), approve: true, scope: scope)
                XCTFail("test signer must stop before any network request")
            } catch CaptureAndStopSigner.Stopped.beforeNetwork {} catch { XCTFail("unexpected: \(error)") }
            let (message, approval) = await signer.captured()
            XCTAssertEqual(approval, scope == "turn")
            var body: [String: Any] = ["rid": "r", "card_id": "c", "approve": true, "digest": "d", "key_kind": scope == "turn" ? "approval" : "device"]
            if scope == "turn" { body["scope"] = scope }
            let parts = String(decoding: message, as: UTF8.self).components(separatedBy: "\n")
            XCTAssertEqual(parts.count, 7)
            guard parts.count == 7 else { continue }
            let expected = RequestSigning.message(method: "POST", path: "/api/mobile/native/chat/card",
                body: try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]), timestamp: parts[4], nonce: parts[5], deviceId: device)
            XCTAssertEqual(message, expected)
        }
    }
}

private actor CaptureAndStopSigner: RequestSigner {
    enum Stopped: Error { case beforeNetwork }
    var message = Data(); var approval = false
    func sign(_ message: Data, with key: SigningKey) async throws -> Data {
        self.message = message
        if case .approval = key { approval = true }
        throw Stopped.beforeNetwork
    }
    func captured() -> (Data, Bool) { (message, approval) }
}
