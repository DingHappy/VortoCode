import XCTest
@testable import MobileCore

/// 抽查生成的变量和 Desktop 的 tokens.css 同值；完整一致性由 tests/unit/test_design_tokens.py 检查生成结果。
final class DesignTokensTests: XCTestCase {
    func testSharedColorsMatchDesktopValues() {
        // --vc-bg: #fafaf9 / #1a1a19
        XCTAssertEqual(VCColor.bg.light, RGBA(0.9804, 0.9804, 0.9765, 1))
        XCTAssertEqual(VCColor.bg.dark, RGBA(0.102, 0.102, 0.098, 1))
        // 状态色两种主题都有，且浅深不同
        for color in [VCColor.waitText, VCColor.waitBg, VCColor.doneText, VCColor.dangerText, VCColor.unknownText] {
            XCTAssertNotEqual(color.light, color.dark)
        }
        // 没有深色值的变量（制品纸面）两种主题一样
        XCTAssertEqual(VCColor.paper.light, VCColor.paper.dark)
        XCTAssertEqual(VCColor.backdrop.dark.alpha, 0.55)
    }

    func testScalesFollowTheDesignSystemRules() {
        XCTAssertEqual(VCRadius.card, 14)
        XCTAssertEqual(VCRadius.input, 22)
        XCTAssertEqual(VCFontSize.chat, 15)
        XCTAssertEqual(VCFontSize.caption, 12)
        for space in [VCSpace.space1, VCSpace.space2, VCSpace.space3, VCSpace.space4, VCSpace.space6, VCSpace.space10] {
            XCTAssertEqual(space.truncatingRemainder(dividingBy: 4), 0)
        }
    }
}
