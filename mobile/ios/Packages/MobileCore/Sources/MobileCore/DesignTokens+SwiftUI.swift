// 把生成的 ThemeColor 接到 SwiftUI：跟随系统浅色 / 深色切换，和 Desktop 的 --vc-* 取值一致（ADR-017）。
// 这个文件是手写的；变量本身在 DesignTokens.swift（由 design/tokens.json 生成）。
#if canImport(SwiftUI)
import SwiftUI

#if canImport(UIKit)
import UIKit

extension ThemeColor {
    public var color: Color {
        let light = light, dark = dark
        return Color(uiColor: UIColor { traits in
            let value = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: value.red, green: value.green, blue: value.blue, alpha: value.alpha)
        })
    }
}
#elseif canImport(AppKit)
import AppKit

extension ThemeColor {
    public var color: Color {
        let light = light, dark = dark
        return Color(nsColor: NSColor(name: nil) { appearance in
            let value = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: value.red, green: value.green, blue: value.blue, alpha: value.alpha)
        })
    }
}
#endif
#endif
