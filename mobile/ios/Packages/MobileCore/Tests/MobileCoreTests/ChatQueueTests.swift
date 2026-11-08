import XCTest
@testable import MobileCore

final class ChatQueueTests: XCTestCase {
    private func turn(_ state: String, outcome: String? = nil) -> ChatTurn {
        ChatTurn(rid: "app-0000000000000001", requestId: "q", state: state, reply: "", error: "", cards: [], outcome: outcome)
    }

    func testOnlyACleanFinishSendsTheNextMessage() {
        var queue = ChatQueue()
        XCTAssertTrue(queue.enqueue("  第一条 ", requestId: "r1"))
        XCTAssertTrue(queue.enqueue("第二条", requestId: "r2"))
        XCTAssertEqual(queue.next(after: turn("running"), needsReview: false), .none)
        XCTAssertEqual(queue.next(after: turn("waiting"), needsReview: false), .none)
        XCTAssertEqual(queue.next(after: turn("done"), needsReview: false), .send(.init(id: "r1", text: "第一条")))
        queue.sent("r1")
        XCTAssertEqual(queue.next(after: nil, needsReview: false), .send(.init(id: "r2", text: "第二条")))
    }

    func testUncleanEndsHoldUntilTheOwnerReleases() {
        for ended in [turn("failed"), turn("done", outcome: "unknown"), turn("busy")] {
            var queue = ChatQueue()
            queue.enqueue("排着的", requestId: "r1")
            guard case .hold = queue.next(after: ended, needsReview: false) else { return XCTFail("\(ended.state) should hold") }
            XCTAssertNotNil(queue.held)
            XCTAssertEqual(queue.next(after: turn("done"), needsReview: false), .none)   // 停住后不会自己再发
            XCTAssertEqual(queue.release()?.id, "r1")
            XCTAssertNil(queue.held)
        }
        var reviewing = ChatQueue()
        reviewing.enqueue("排着的", requestId: "r1")
        guard case .hold = reviewing.next(after: nil, needsReview: true) else { return XCTFail("review should hold") }
    }

    func testLimitsAndRemoval() {
        var queue = ChatQueue()
        XCTAssertFalse(queue.enqueue("   ", requestId: "blank"))
        for index in 0..<ChatQueue.limit { XCTAssertTrue(queue.enqueue("第\(index)条", requestId: "r\(index)")) }
        XCTAssertTrue(queue.isFull)
        XCTAssertFalse(queue.enqueue("多出来的", requestId: "extra"))
        queue.remove("r4")
        XCTAssertFalse(queue.enqueue("重复 id", requestId: "r0"))   // 同一个请求 id 不排两次
        XCTAssertTrue(queue.enqueue("补上", requestId: "r4"))
        queue.hold("停住")
        for index in 0..<ChatQueue.limit { queue.remove("r\(index)") }
        XCTAssertTrue(queue.items.isEmpty)
        XCTAssertNil(queue.held)                                  // 清空后不再显示停住的原因
        XCTAssertEqual(queue.next(after: nil, needsReview: false), .none)
    }

    func testTurnDecodesStreamingFieldsAndStaysCompatible() throws {
        let raw: [String: Any] = ["rid": "app-0000000000000001", "request_id": "q", "state": "running", "reply": "",
                                  "error": "", "cards": [], "partial": "正在整理",
                                  "steps": [["id": "t1", "summary": "写入 notes/周报.md", "status": "running"]]]
        let live = try MobileAPI.decoder.decode(ChatTurn.self, from: JSONSerialization.data(withJSONObject: raw))
        XCTAssertEqual(live.partial, "正在整理")
        XCTAssertEqual(live.steps?.first?.isRunning, true)
        XCTAssertEqual(live.steps?.first?.statusLabel, "在忙")
        var old = raw; old.removeValue(forKey: "partial"); old.removeValue(forKey: "steps")
        let legacy = try MobileAPI.decoder.decode(ChatTurn.self, from: JSONSerialization.data(withJSONObject: old))
        XCTAssertNil(legacy.partial); XCTAssertNil(legacy.steps)
        XCTAssertEqual(ChatStep(id: "x", summary: "s", status: "weird").statusLabel, "无法确认")
    }
}
