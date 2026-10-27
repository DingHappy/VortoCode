import XCTest

final class EntryUITests: XCTestCase {
    func testInvalidAddressIsRejectedAndOnlyCanonicalEntryIsSaved() {
        let app = XCUIApplication()
        app.launch()
        app.buttons["entry-settings"].tap()
        let address = app.textFields["entry-address"]
        XCTAssertTrue(address.waitForExistence(timeout: 5))
        if app.buttons["clear-entry-address"].isEnabled { app.buttons["clear-entry-address"].tap() }
        address.tap()
        address.typeText("http://192.168.1.97:8080")
        app.buttons["save-entry"].tap()
        XCTAssertTrue(app.staticTexts["entry-error"].waitForExistence(timeout: 3))
        app.buttons["clear-entry-address"].tap()
        address.tap()
        address.typeText("https://assistant.example.com/mobile/")
        app.buttons["save-entry"].tap()
        XCTAssertTrue(app.buttons["open-assistant"].waitForExistence(timeout: 3))
        XCTAssertEqual(app.staticTexts["saved-entry"].label, "https://assistant.example.com")
        app.terminate()
        app.launch()
        XCTAssertTrue(app.buttons["open-assistant"].waitForExistence(timeout: 3))
        XCTAssertEqual(app.staticTexts["saved-entry"].label, "https://assistant.example.com")
    }
}
