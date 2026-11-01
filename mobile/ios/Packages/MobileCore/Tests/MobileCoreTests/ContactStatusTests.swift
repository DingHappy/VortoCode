import XCTest
@testable import MobileCore

final class ContactStatusTests: XCTestCase {
    let now = Date(timeIntervalSince1970: 1_793_550_000)

    func testFreshContactIsConnected() {
        let status = ContactStatus.evaluate(lastContact: now.addingTimeInterval(-30), lastAttemptFailed: false,
                                            checking: false, now: now)
        XCTAssertEqual(status, .connected)
        XCTAssertFalse(status.isProblem)
    }

    func testFailedRefreshSaysWhenItLastWorked() {
        let status = ContactStatus.evaluate(lastContact: now.addingTimeInterval(-3 * 3600), lastAttemptFailed: true,
                                            checking: false, now: now)
        XCTAssertTrue(status.isProblem)
        XCTAssertTrue(status.text(now: now).hasPrefix("连不上家里的助手（上次连上：3 小时前）"))
    }

    func testNeverConnectedIsNotDressedUpAsATime() {
        let status = ContactStatus.evaluate(lastContact: nil, lastAttemptFailed: true, checking: false, now: now)
        XCTAssertTrue(status.text(now: now).hasPrefix("还没连上过家里的助手"))
    }

    /// 打开 App 时先显示上次的时间，不在核对结果出来前就喊「连不上」——那正是后台心跳方案会误报的地方
    func testReopeningShowsCheckingNotAnAlarm() {
        let status = ContactStatus.evaluate(lastContact: now.addingTimeInterval(-5 * 86400), lastAttemptFailed: false,
                                            checking: true, now: now)
        XCTAssertFalse(status.isProblem)
        XCTAssertEqual(status.text(now: now), "上次连上：5 天前，正在重新核对…")
    }

    func testAgoBuckets() {
        XCTAssertEqual(ContactStatus.ago(now.addingTimeInterval(-10), now: now), "不到 1 分钟前")
        XCTAssertEqual(ContactStatus.ago(now.addingTimeInterval(-600), now: now), "10 分钟前")
        XCTAssertEqual(ContactStatus.ago(now.addingTimeInterval(10), now: now), "不到 1 分钟前")   // 时钟偏差不出负数
    }
}
