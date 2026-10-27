import XCTest
@testable import MobileCore

final class MobileReadStateTests: XCTestCase {
    private let first = String(repeating: "a", count: 32)
    private let second = String(repeating: "b", count: 32)
    private func snapshot(instance: String? = nil, unavailable: Set<String> = [], empty: Bool = false, revision: Int = 1) throws -> MobileSnapshot {
        let stamp = "2026-10-26T10:00:00Z"
        func source(_ name: String, _ data: [String: Any]) -> [String: Any] {
            unavailable.contains(name) ? ["status":"unavailable", "checked_at":NSNull(), "data":NSNull()] : ["status":"current", "checked_at":stamp, "data":data]
        }
        let goals: [[String: Any]] = empty ? [] : [["id":"personal-1", "objective":"读取原目标", "status":"active", "next_action":"核对", "blocker":"", "revision":revision]]
        let raw: [String: Any] = ["schema_version":1, "instance_id":instance ?? first, "sources":[
            "profile":source("profile", ["name":"助手"]),
            "main_chat":source("main_chat", ["sid":NSNull()]),
            "goals":source("goals", ["items":goals, "truncated":false])]]
        return try MobileAPI.decoder.decode(MobileSnapshot.self, from: JSONSerialization.data(withJSONObject: raw))
    }

    func testPartialFailureRetainsOnlyHistoricalDataWithOriginalTimestamp() throws {
        var state = MobileReadState()
        try state.accept(snapshot(), instance: first)
        let checked = state.goals.checkedDate
        try state.accept(snapshot(unavailable: ["goals"]), instance: first)
        XCTAssertEqual(state.goals.data?.items.first?.revision, 1)
        XCTAssertEqual(state.goals.checkedDate, checked)
        XCTAssertEqual(state.goals.freshness, .historical)
        XCTAssertEqual(state.profile.freshness, .current)
        XCTAssertFalse(state.allCurrent)
        try state.accept(snapshot(revision: 2), instance: first)
        XCTAssertEqual(state.goals.data?.items.first?.revision, 2)
        XCTAssertTrue(state.allCurrent)
    }

    func testOtherSourceFailuresCannotReportEverythingCurrent() throws {
        for missing in ["profile", "main_chat"] {
            var state = MobileReadState()
            try state.accept(snapshot(unavailable: [missing]), instance: first)
            XCTAssertFalse(state.allCurrent)
            XCTAssertEqual(state.goals.freshness, .current)
        }
    }

    func testUnavailableBeforeFirstReadIsNotAnEmptyGoalList() throws {
        var state = MobileReadState()
        try state.accept(snapshot(unavailable: ["goals"]), instance: first)
        XCTAssertNil(state.goals.data)
        XCTAssertNil(state.goals.checkedDate)
        XCTAssertEqual(state.goals.freshness, .unavailable)
    }

    func testVerifiedEmptyListReplacesRetainedGoals() throws {
        var state = MobileReadState()
        try state.accept(snapshot(), instance: first)
        state.markHistorical()
        try state.accept(snapshot(empty: true), instance: first)
        XCTAssertEqual(state.goals.data?.items.count, 0)
        XCTAssertEqual(state.goals.freshness, .current)
    }

    func testChangingAssistantCannotReusePreviousRecords() throws {
        var state = MobileReadState()
        try state.accept(snapshot(), instance: first)
        try state.accept(snapshot(instance: second, unavailable: ["goals"]), instance: second)
        XCTAssertNil(state.goals.data)
        XCTAssertNil(state.goals.checkedDate)
        XCTAssertEqual(state.goals.freshness, .unavailable)
        XCTAssertEqual(state.instanceId, second)
    }

    func testRejectedSnapshotDoesNotMutateRetainedState() throws {
        var state = MobileReadState()
        try state.accept(snapshot(), instance: first)
        XCTAssertThrowsError(try state.accept(snapshot(instance: second, revision: 2), instance: first))
        XCTAssertEqual(state.instanceId, first)
        XCTAssertEqual(state.goals.data?.items.first?.revision, 1)
        XCTAssertTrue(state.allCurrent)
    }

    func testBackgroundOrTransportFailureMarksAllRetainedSourcesHistorical() throws {
        var state = MobileReadState()
        try state.accept(snapshot(), instance: first)
        state.markHistorical()
        XCTAssertEqual(state.profile.freshness, .historical)
        XCTAssertEqual(state.mainChat.freshness, .historical)
        XCTAssertEqual(state.goals.freshness, .historical)
        XCTAssertFalse(state.allCurrent)
        try state.accept(snapshot(unavailable: ["main_chat"]), instance: first)
        XCTAssertEqual(state.profile.freshness, .current)
        XCTAssertEqual(state.mainChat.freshness, .historical)
        state = MobileReadState()
        XCTAssertNil(state.instanceId)
        XCTAssertNil(state.goals.data)
        XCTAssertNil(state.profile.data)
        XCTAssertNil(state.mainChat.data)
    }
}
