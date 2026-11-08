import XCTest
@testable import MobileCore

final class AssistantShellStateTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ raw: [String: Any]) throws -> T {
        try MobileAPI.decoder.decode(T.self, from: JSONSerialization.data(withJSONObject: raw))
    }

    func testStatusLinePutsProblemsAndWaitingFirst() {
        XCTAssertEqual(AssistantStatusLine.make(contactProblem: true, canWrite: true, turnRunning: true, waiting: 2, activeGoals: 3).tone, .problem)
        let waiting = AssistantStatusLine.make(contactProblem: false, canWrite: true, turnRunning: true, waiting: 2, activeGoals: 3)
        XCTAssertEqual(waiting, .init(text: "2 件等你处理", tone: .waiting))
        XCTAssertEqual(AssistantStatusLine.make(contactProblem: false, canWrite: true, turnRunning: false, waiting: 1,
                                                waitingUncertain: true, activeGoals: nil).text, "1+ 件等你处理")
        XCTAssertEqual(AssistantStatusLine.make(contactProblem: false, canWrite: true, turnRunning: true, waiting: 0, activeGoals: 3).tone, .running)
    }

    func testStatusLineNeverSaysIdleWhenSourcesAreMissing() {
        XCTAssertEqual(AssistantStatusLine.make(contactProblem: false, canWrite: true, turnRunning: false, waiting: 0, activeGoals: nil).text,
                       "目标还没读到")
        XCTAssertEqual(AssistantStatusLine.make(contactProblem: false, canWrite: true, turnRunning: false, waiting: 0,
                                                waitingUncertain: true, activeGoals: 0).text, "部分来源还没读到")
        XCTAssertEqual(AssistantStatusLine.make(contactProblem: false, canWrite: false, turnRunning: false, waiting: 0, activeGoals: 2).text,
                       "手机还不能发消息，只能查看")
        XCTAssertEqual(AssistantStatusLine.make(contactProblem: false, canWrite: true, turnRunning: false, waiting: 0, activeGoals: 2).text,
                       "在跟进 2 个目标")
    }

    func testWaitingItemsKeepChatCardsFirst() throws {
        let card = try decode(ChatCard.self, ["id": "c1", "text": "改一下周报\n第二行", "risk": "normal", "status": "pending",
                                              "digest": "d", "operation_kind": "own_data", "tainted": true])
        let decision = try decode(PendingDecision.self, ["id": "d1", "kind": "interrupt", "thread": "VortoCode", "text": "",
                                                         "tainted": false, "action_digest": "x"])
        let proposal = try decode(DispatchProposal.self, ["id": "p1", "title": "修登录", "goal": "g", "status": "proposed",
                                                          "tainted": false, "criteria": [], "digest": "y"])
        let items = WaitingItem.collect(cards: [card], decisions: [decision], dispatches: [proposal])
        XCTAssertEqual(items.map(\.id), ["card:c1", "decision:d1", "dispatch:p1"])
        XCTAssertEqual(items[0].title, "需要你确认 · 你的事项")
        XCTAssertEqual(items[0].detail, "改一下周报")
        XCTAssertTrue(items[0].tainted)
        XCTAssertEqual(items[1].detail, "叫停正在跑的这一轮")
        XCTAssertEqual(items[2].title, "任务单 · 项目")
    }

    func testSegmentsMatchDesktopPanelAndInitials() {
        XCTAssertEqual(AssistantSegment.allCases.map(\.title), ["动态", "目标", "审核记录", "定时", "身份"])
        XCTAssertEqual(assistantInitial("  vorto"), "V")
        XCTAssertEqual(assistantInitial("小助"), "小")
        XCTAssertEqual(assistantInitial(nil), "V")
    }

    func testSharedApprovalCopyFallbacks() {
        XCTAssertEqual(ApprovalLabels.category(nil), "类别待核对")
        XCTAssertEqual(ApprovalLabels.category("future"), "未分类操作")
        XCTAssertEqual(ApprovalLabels.risk("high"), "加强确认")
        XCTAssertEqual(ApprovalLabels.risk("future"), "级别待核对")
        XCTAssertEqual(ApprovalLabels.scope("existing_trust"), "既有授权范围")
        XCTAssertEqual(ApprovalLabels.source(nil), "来源待核对")
    }
}

final class TeamModelsTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ raw: [String: Any]) throws -> T {
        try MobileAPI.decoder.decode(T.self, from: JSONSerialization.data(withJSONObject: raw))
    }

    func testTeamsNeedingTheOwnerJoinTheWaitingBand() throws {
        let list = try decode(TeamList.self, ["teams": [
            ["id": "t1", "objective": "比较三家云", "status": "review", "mode": "auto", "waiting_reason": "", "project": NSNull(),
             "tainted": false, "updated_at": "2026-11-07T10:00:00", "members": [["name": "研究员", "role": "web-researcher"]]],
            ["id": "t2", "objective": "写周报", "status": "waiting", "mode": "auto", "waiting_reason": "预算多少？\n第二行",
             "project": "VortoCode", "tainted": false, "updated_at": NSNull()],
            ["id": "t3", "objective": "查机票", "status": "waiting", "mode": "auto", "waiting_reason": "请打开这个链接",
             "project": NSNull(), "tainted": true, "updated_at": NSNull()],
            ["id": "t4", "objective": "整理资料", "status": "active", "mode": "auto", "waiting_reason": "", "project": NSNull(),
             "tainted": false, "updated_at": NSNull()]]])
        XCTAssertEqual(list.teams.map(\.statusLabel), ["交付了，等你验收", "需要你回复", "需要你回复", "在忙"])
        let items = WaitingItem.collect(cards: [], decisions: [], dispatches: [], teams: list.teams)
        XCTAssertEqual(items.map(\.id), ["team:t1", "team:t2", "team:t3"])
        XCTAssertEqual(items[1].detail, "预算多少？")
        XCTAssertEqual(items[2].detail, "查机票")          // 读过外部内容的小组不把它的问题原文放进提醒
        XCTAssertTrue(items[2].tainted)
    }

    func testTeamChatAndSideChatDecode() throws {
        let chat = try decode(TeamChat.self, [
            "team": ["id": "t1", "objective": "比较三家云", "status": "review", "mode": "auto", "waiting_reason": "",
                     "used": ["rounds": 3], "project": NSNull(), "tainted": true, "acceptance": ["给推荐"], "updated_at": "x"],
            "members": [["name": "研究员", "role": "web-researcher", "title": "网页研究", "engine": "vortocode",
                         "state": "working", "rounds": 2, "delivered": 1, "failures": 0]],
            "messages": [["speaker": "owner", "kind": "owner_reply", "text": "500 以内", "tainted": false],
                         ["speaker": "member", "kind": "result", "member": "研究员", "text": "A 最便宜", "tainted": true],
                         ["speaker": "coordinator", "kind": "submitted", "text": "推荐 A", "tainted": true,
                          "evidence": [["criterion": "0", "text": "给推荐", "evidence": "价格表"]]]]])
        XCTAssertTrue(chat.members[0].working)
        XCTAssertEqual(chat.messages.map(\.speakerLabel), ["你", "研究员", "协调人"])
        XCTAssertEqual(chat.messages[2].evidence?.first?.evidence, "价格表")
        let side = try decode(SideChatTranscript.self, ["sid": "s1", "title": "查机票",
                                                        "items": [["role": "user", "text": "下周去上海", "at": NSNull()]]])
        XCTAssertTrue(side.items[0].isMine)
    }
}
