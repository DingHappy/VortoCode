import XCTest

final class NativeFlowUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }
    @MainActor
    func testRealNativeLoginReadRestoreAndLogout() async throws {
        guard let fixture = ProcessInfo.processInfo.environment["VORTO_FIXTURE_URL"], let url = URL(string: fixture) else {
            throw XCTSkip("Requires isolated loopback fixture, never a real account")
        }
        let (data, _) = try await URLSession.shared.data(from: url)
        let config = try JSONSerialization.jsonObject(with: data) as! [String: String]
        try await setMode("normal", fixture: url)
        let app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        if app.tabBars.buttons["设置"].waitForExistence(timeout: 2) {
            app.tabBars.buttons["设置"].tap(); app.buttons["native-logout"].tap()
        }
        XCTAssertTrue(app.buttons["entry-settings"].waitForExistence(timeout: 20))
        app.buttons["entry-settings"].tap()
        let field = app.textFields["entry-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        if app.buttons["clear-entry-address"].isEnabled { app.buttons["clear-entry-address"].tap() }
        field.tap(); field.typeText(config["origin"]!)
        app.buttons["save-entry"].tap()
        app.buttons["open-assistant"].tap()
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for title in ["Continue", "继续"] {
            if app.buttons[title].waitForExistence(timeout: 2) { app.buttons[title].tap(); break }
            if springboard.buttons[title].exists { springboard.buttons[title].tap(); break }
        }
        let email = app.webViews.textFields.firstMatch
        if email.waitForExistence(timeout: 15) {
            email.tap(); email.typeText(config["email"]!)
            let next = app.buttons["Next"].firstMatch
            let systemNext = springboard.buttons["Next"].firstMatch
            if next.waitForExistence(timeout: 2) && next.isEnabled { next.tap() }
            else if systemNext.exists && systemNext.isEnabled { systemNext.tap() }
            else { email.typeText("\t") }
            app.typeText(config["password"]! + "\n")
            // Some system keyboard configurations dismiss on Return instead of
            // submitting the web form. Submit explicitly only if still on login.
            if !app.webViews.buttons["允许"].waitForExistence(timeout: 2) {
                let submit = app.webViews.buttons["登录"]
                if submit.exists && submit.isHittable { submit.tap() }
            }
            // Synthetic credentials must not be saved into the simulator password manager.
            for label in ["Not Now", "暂不", "以后"] {
                if app.buttons[label].waitForExistence(timeout: 2) { app.buttons[label].tap(); break }
                if springboard.buttons[label].exists { springboard.buttons[label].tap(); break }
            }
        }
        let allow = app.webViews.buttons["允许"]
        try require(allow.waitForExistence(timeout: 20), "account-consent", app); allow.tap()
        try require(app.tabBars.buttons["目标"].waitForExistence(timeout: 25), "native-tabs", app)
        capture(app, name: "native-overview")
        app.tabBars.buttons["目标"].tap()
        try verifyGoalBrowsing(app)
        let goal = app.staticTexts["核对同账号手机入口"].firstMatch
        XCTAssertTrue(goal.waitForExistence(timeout: 10)); goal.tap()
        XCTAssertTrue(app.navigationBars["目标详情"].waitForExistence(timeout: 5))
        capture(app, name: "native-goal-detail")
        try await postFixture("goal-progress", payload: [:], fixture: url)
        app.buttons["native-refresh"].tap()
        let nextAction = app.staticTexts["goal-next-action"]
        try reveal(nextAction, app: app)
        let updated = NSPredicate(format: "label == %@", "在详情页核对最新进展")
        let updateResult = await XCTWaiter.fulfillment(of: [XCTNSPredicateExpectation(predicate: updated, object: nextAction)], timeout: 10)
        try require(updateResult == .completed, "detail-live-progress", app)
        capture(app, name: "native-detail-updated")
        try await setMode("empty-goals", fixture: url)
        app.buttons["native-refresh"].tap()
        app.swipeDown()
        try require(app.staticTexts["goal-not-in-read"].waitForExistence(timeout: 15), "goal-not-in-latest-read", app)
        capture(app, name: "native-detail-retained")
        try reveal(nextAction, app: app)
        try require(nextAction.label == "在详情页核对最新进展", "latest-detail-retained", app)
        try await setMode("normal", fixture: url)
        app.buttons["native-refresh"].tap()
        app.terminate(); app.launch()
        XCTAssertTrue(app.tabBars.buttons["目标"].waitForExistence(timeout: 20))
        app.tabBars.buttons["目标"].tap()
        XCTAssertTrue(app.staticTexts["核对同账号手机入口"].firstMatch.waitForExistence(timeout: 15))
        capture(app, name: "native-session-restored")
        // Real authenticated reads, with only the isolated server response altered.
        // The product has no test mode, seeded credentials or diagnostic endpoints.
        try await setMode("goals-unavailable", fixture: url)
        app.buttons["native-refresh"].tap()
        try require(app.staticTexts["目标：历史内容，等待重新核对"].waitForExistence(timeout: 15), "historical-goals", app)
        try require(app.staticTexts["核对同账号手机入口"].firstMatch.exists, "retained-goal", app)
        capture(app, name: "native-source-unavailable")
        app.staticTexts["核对同账号手机入口"].firstMatch.tap()
        try require(app.staticTexts["goal-history"].waitForExistence(timeout: 5), "historical-detail", app)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        try await setMode("read-failed", fixture: url)
        app.buttons["native-refresh"].tap()
        try require(app.staticTexts["暂时无法连接助手，请检查网络后刷新。"].waitForExistence(timeout: 15), "failed-read", app)
        try require(app.staticTexts["核对同账号手机入口"].firstMatch.exists, "failure-retains-goal", app)
        capture(app, name: "native-read-failed")
        try await setMode("normal", fixture: url)
        app.buttons["native-refresh"].tap()
        try require(app.staticTexts["各来源最近读取有效"].waitForExistence(timeout: 15), "read-recovered", app)
        capture(app, name: "native-read-recovered")
        app.tabBars.buttons["设置"].tap()
        try await setMode("logout-failed", fixture: url)
        app.buttons["native-logout"].tap()
        try require(app.buttons["retry-native-logout"].waitForExistence(timeout: 15), "logout-retry-offered", app)
        try require(app.buttons["open-assistant"].exists, "local-logout-on-failure", app)
        let retry = app.buttons["retry-native-logout"].firstMatch
        try reveal(retry, app: app)
        capture(app, name: "native-logout-pending")
        try await setMode("normal", fixture: url)
        retry.tap()
        try require(app.staticTexts["已确认这次远端会话撤销或失效。"].waitForExistence(timeout: 15), "logout-retry-confirmed", app)
        try require(!app.buttons["retry-native-logout"].exists, "logout-retry-removed", app)
        capture(app, name: "native-signed-out")
    }
    private func setMode(_ mode: String, fixture: URL) async throws {
        try await postFixture("mode", payload: ["mode":mode], fixture: fixture)
    }
    private func postFixture(_ path: String, payload: [String: String], fixture: URL) async throws {
        var request = URLRequest(url: fixture.deletingLastPathComponent().appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (_, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw NSError(domain: "NativeFixture", code: 1) }
    }
    @MainActor private func verifyGoalBrowsing(_ app: XCUIApplication) throws {
        let search = app.searchFields.firstMatch
        try require(search.waitForExistence(timeout: 10), "goal-search", app)
        search.tap(); search.typeText("oauth 授权\n")
        let blocked = app.staticTexts["核对 PT 签到连接"].firstMatch
        try reveal(blocked, app: app)
        try require(!app.staticTexts["核对同账号手机入口"].firstMatch.exists, "search-across-fields", app)
        capture(app, name: "native-goal-search")
        let clear = app.buttons["goal-clear-filters"]
        try reveal(clear, app: app); clear.tap()
        try require(app.buttons["goal-filter"].waitForExistence(timeout: 5), "goal-search-dismissed", app)
        app.buttons["goal-filter"].tap(); app.buttons["有阻塞"].firstMatch.tap()
        try require(app.staticTexts["goal-selected-filter"].waitForExistence(timeout: 5), "goal-filter-selected", app)
        try require(app.staticTexts["核对 PT 签到连接"].firstMatch.exists && !app.staticTexts["核对同账号手机入口"].firstMatch.exists, "blocked-filter", app)
        capture(app, name: "native-goal-filter")
        app.buttons["goal-filter"].tap(); app.buttons["已达成"].firstMatch.tap()
        try require(app.staticTexts["没有匹配的目标"].waitForExistence(timeout: 5), "filter-empty", app)
        try reveal(clear, app: app); clear.tap()
        try require(app.staticTexts["核对同账号手机入口"].firstMatch.waitForExistence(timeout: 5), "filter-reset", app)
    }
    @MainActor private func reveal(_ element: XCUIElement, app: XCUIApplication) throws {
        for _ in 0..<4 { if element.exists && element.isHittable { return }; app.swipeUp() }
        try require(element.exists && element.isHittable, "scroll-to-element", app)
    }
    @MainActor private func capture(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot()); attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }
    @MainActor private func require(_ ready: Bool, _ step: String, _ app: XCUIApplication) throws {
        if !ready { capture(app, name: "failed-" + step); XCTFail("Native flow stopped at " + step); throw NSError(domain: "NativeFlow", code: 1) }
    }
}
