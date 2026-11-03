import XCTest
@testable import MobileCore

final class ApprovalCardTests: XCTestCase {
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
        XCTAssertEqual(try card(["operation_kind": "future_kind"]).categoryLabel, "类别待核对")
    }
}
