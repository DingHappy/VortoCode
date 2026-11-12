import XCTest
@testable import MobileCore

private func staffCard(_ patch: [String: Any] = [:]) throws -> StaffActionCard {
    var value: [String: Any] = ["id": "staff-action-test", "task_id": "task-test", "digest": "digest", "nonce": "nonce",
        "round": 1, "instance": "instance", "runtime": "runtime", "member_name": "事项专员", "role_title": "整理事项",
        "tool": "personal_goal_create", "kind": "own_data", "message": "整理当前事项", "args_text": "{\"text\":\"完整正文\"}",
        "tainted": false, "risk": "normal", "approval_level": "normal", "expires_at": 4_000_000_000.0,
        "status": "pending", "can_decide": true]
    value.merge(patch) { _, new in new }
    return try MobileAPI.decoder.decode(StaffActionCard.self, from: JSONSerialization.data(withJSONObject: value))
}

private func staffReceipt(_ patch: [String: Any] = [:]) throws -> StaffActionReceipt {
    var value: [String: Any] = ["action_id": "staff-action-test", "task_id": "task-test", "digest": "digest", "nonce": "nonce",
        "round": 1, "instance": "instance", "runtime": "runtime", "reason": "", "status": "approved", "replayed": false]
    value.merge(patch) { _, new in new }
    return try MobileAPI.decoder.decode(StaffActionReceipt.self, from: JSONSerialization.data(withJSONObject: value))
}

final class StaffApprovalTests: XCTestCase {
    func testUnknownHighExpiredOrInvalidCardsCannotStartMobileDecisions() throws {
        for patch: [String: Any] in [["kind": "write"], ["kind": "execute"], ["kind": "deliver"], ["kind": "future"],
            ["risk": "high"], ["approval_level": "desktop"], ["can_decide": false], ["nonce": ""],
            ["runtime": ""], ["status": "interrupted"], ["expires_at": 1]] {
            var state = StaffApprovalState()
            XCTAssertNil(state.begin(card: try staffCard(patch), approve: true))
            XCTAssertNil(state.begin(card: try staffCard(patch), approve: false, reason: "本次先不执行"))
        }
        XCTAssertTrue(try staffCard(["tainted": true]).canDecideOnPhone()) // Explicit choice still required.
    }

    func testAnUncertainPostNeverAutomaticallyReopensOrRetries() throws {
        var state = StaffApprovalState(); let card = try staffCard()
        let attempt = try XCTUnwrap(state.begin(card: card, approve: true))
        XCTAssertTrue(state.willSend(attempt)); XCTAssertFalse(state.willSend(attempt))
        state.failed(attempt)
        XCTAssertTrue(state.blocksDecisions); XCTAssertTrue(state.canCheck)
        XCTAssertNil(state.begin(card: card, approve: false, reason: "先不执行"))
        let checking = try XCTUnwrap(state.beginCheck())
        XCTAssertTrue(state.checked(card, for: checking))
        XCTAssertFalse(state.blocksDecisions) // An explicit fresh GET, still no POST.
        XCTAssertNotNil(state.begin(card: card, approve: false, reason: "先不执行"))
    }

    func testAConfirmedReceiptCannotBeReopenedByAStalePendingRead() throws {
        var state = StaffApprovalState(); let card = try staffCard()
        let attempt = try XCTUnwrap(state.begin(card: card, approve: true))
        XCTAssertTrue(state.willSend(attempt)); XCTAssertTrue(state.receive(try staffReceipt(), for: attempt))
        let checking = try XCTUnwrap(state.beginCheck())
        XCTAssertTrue(state.checked(card, for: checking)); XCTAssertTrue(state.blocksDecisions)
        let latest = try XCTUnwrap(state.beginCheck())
        XCTAssertTrue(state.checked(try staffCard(["status": "approved", "can_decide": false]), for: latest))
        XCTAssertFalse(state.blocksDecisions)
    }

    func testEveryBindingAndTheExactChoiceAreRequiredInTheReceipt() throws {
        for patch: [String: Any] in [["task_id": "other"], ["action_id": "other"], ["digest": "other"], ["nonce": "other"],
            ["round": 2], ["instance": "other"], ["runtime": "other"], ["status": "denied"], ["reason": "other"]] {
            var state = StaffApprovalState()
            let attempt = try XCTUnwrap(state.begin(card: try staffCard(), approve: true))
            XCTAssertTrue(state.willSend(attempt)); XCTAssertFalse(state.receive(try staffReceipt(patch), for: attempt))
            XCTAssertTrue(state.blocksDecisions); XCTAssertFalse(state.receiptConfirmed)
        }
    }

    func testAccountResetInvalidatesOldSignaturesAndMissingRejectReasonCannotStart() throws {
        var state = StaffApprovalState(); let card = try staffCard()
        XCTAssertNil(state.begin(card: card, approve: false))
        let old = try XCTUnwrap(state.begin(card: card, approve: true)); state.reset()
        let fresh = try XCTUnwrap(state.begin(card: card, approve: false, reason: "本次先不执行"))
        XCTAssertFalse(state.willSend(old)); XCTAssertFalse(state.failed(old)); XCTAssertTrue(state.isCurrent(fresh))
    }

    func testStaffWaitsAppearWithFullSourceFlagsAndDesktopOnlyCardsStayReadonly() throws {
        let ordinary = try staffCard(["tainted": true])
        let high = try staffCard(["id": "high", "kind": "execute", "risk": "high", "approval_level": "desktop", "can_decide": false])
        let items = WaitingItem.collect(cards: [], decisions: [], dispatches: [], staff: [ordinary, high])
        XCTAssertEqual(items.count, 2); XCTAssertEqual(items[0].kind, .staff); XCTAssertTrue(items[0].tainted)
        XCTAssertTrue(items[1].title.contains("电脑")); XCTAssertEqual(ordinary.argsText, "{\"text\":\"完整正文\"}")
    }
}
