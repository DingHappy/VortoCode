// 由 design/tokens.json 生成（python scripts/design_tokens.py），不要手改。
// 和 Desktop 的 desktop/src/styles/tokens.css 出自同一份定义（ADR-017）。
// 只有纯色、字号、字重、间距、圆角和控件尺寸；Desktop 专用的布局宽度、字体栈、阴影、渐变不在这里。

/// 一个颜色在浅色和深色下的取值（sRGB，0–1）。SwiftUI 的 `Color` 见 DesignTokens+SwiftUI.swift。
public struct RGBA: Sendable, Equatable {
    public let red: Double, green: Double, blue: Double, alpha: Double
    public init(_ red: Double, _ green: Double, _ blue: Double, _ alpha: Double = 1) {
        self.red = red; self.green = green; self.blue = blue; self.alpha = alpha
    }
}

public struct ThemeColor: Sendable, Equatable {
    public let light: RGBA, dark: RGBA
    public init(light: RGBA, dark: RGBA) { self.light = light; self.dark = dark }
}

public enum VCColor {
    // 底色与表面
    public static let bg = ThemeColor(light: RGBA(0.9804, 0.9804, 0.9765, 1), dark: RGBA(0.102, 0.102, 0.098, 1))
    public static let sidebar = ThemeColor(light: RGBA(0.9529, 0.9529, 0.9451, 1), dark: RGBA(0.0784, 0.0784, 0.0745, 1))
    public static let surface = ThemeColor(light: RGBA(1, 1, 1, 1), dark: RGBA(0.1333, 0.1333, 0.1294, 1))
    public static let selected = ThemeColor(light: RGBA(0.9059, 0.9059, 0.8941, 1), dark: RGBA(0.1804, 0.1804, 0.1725, 1))
    public static let fill = ThemeColor(light: RGBA(0.9412, 0.9412, 0.9333, 1), dark: RGBA(0.149, 0.149, 0.1451, 1))
    public static let hover = ThemeColor(light: RGBA(0.9255, 0.9255, 0.9176, 1), dark: RGBA(0.1647, 0.1647, 0.1569, 1))
    public static let border = ThemeColor(light: RGBA(0.8941, 0.8941, 0.8824, 1), dark: RGBA(0.2078, 0.2078, 0.2, 1))
    public static let divider = ThemeColor(light: RGBA(0.9255, 0.9255, 0.9137, 1), dark: RGBA(0.1647, 0.1647, 0.1569, 1))
    public static let backdrop = ThemeColor(light: RGBA(0, 0, 0, 0.35), dark: RGBA(0, 0, 0, 0.55))
    // 文字
    public static let text = ThemeColor(light: RGBA(0.0941, 0.0941, 0.1059, 1), dark: RGBA(0.9255, 0.9255, 0.9176, 1))
    public static let textSecondary = ThemeColor(light: RGBA(0.2471, 0.2471, 0.2745, 1), dark: RGBA(0.7882, 0.7882, 0.7686, 1))
    public static let muted = ThemeColor(light: RGBA(0.4196, 0.4196, 0.4392, 1), dark: RGBA(0.6039, 0.6039, 0.5882, 1))
    // 主按钮（墨黑底）
    public static let ink = ThemeColor(light: RGBA(0.0941, 0.0941, 0.1059, 1), dark: RGBA(0.9255, 0.9255, 0.9176, 1))
    public static let onInk = ThemeColor(light: RGBA(1, 1, 1, 1), dark: RGBA(0.0941, 0.0941, 0.1059, 1))
    // 强调 / 进行中
    public static let accent = ThemeColor(light: RGBA(0.2, 0.3333, 0.8196, 1), dark: RGBA(0.5412, 0.6275, 0.949, 1))
    public static let accentBg = ThemeColor(light: RGBA(0.9333, 0.9451, 0.9882, 1), dark: RGBA(0.1216, 0.1451, 0.251, 1))
    // 状态：等你 / 失败 / 完成 / 无法确认
    public static let waitText = ThemeColor(light: RGBA(0.5725, 0.251, 0.0549, 1), dark: RGBA(0.9882, 0.8275, 0.302, 1))
    public static let waitBg = ThemeColor(light: RGBA(0.9961, 0.9529, 0.7804, 1), dark: RGBA(0.2275, 0.1804, 0.0706, 1))
    public static let waitDot = ThemeColor(light: RGBA(0.851, 0.4667, 0.0235, 1), dark: RGBA(0.9608, 0.6196, 0.0431, 1))
    public static let dangerText = ThemeColor(light: RGBA(0.7255, 0.1098, 0.1098, 1), dark: RGBA(0.9725, 0.4431, 0.4431, 1))
    public static let dangerBg = ThemeColor(light: RGBA(0.9922, 0.9255, 0.9255, 1), dark: RGBA(0.2314, 0.1137, 0.1137, 1))
    public static let dangerBorder = ThemeColor(light: RGBA(0.9608, 0.8157, 0.8157, 1), dark: RGBA(0.4196, 0.1725, 0.1725, 1))
    public static let doneText = ThemeColor(light: RGBA(0.0824, 0.502, 0.2392, 1), dark: RGBA(0.2902, 0.8706, 0.502, 1))
    public static let doneBg = ThemeColor(light: RGBA(0.9176, 0.9608, 0.9333, 1), dark: RGBA(0.0863, 0.1882, 0.1216, 1))
    public static let unknownText = ThemeColor(light: RGBA(0.3216, 0.3216, 0.3569, 1), dark: RGBA(0.6314, 0.6314, 0.6667, 1))
    public static let unknownBg = ThemeColor(light: RGBA(0.9412, 0.9412, 0.9333, 1), dark: RGBA(0.149, 0.149, 0.1451, 1))
    public static let focusRing = ThemeColor(light: RGBA(0.2, 0.3333, 0.8196, 0.45), dark: RGBA(0.5412, 0.6275, 0.949, 0.55))
    // 阴影：输入框、浮层两种，外加分段切换选中段的一层细影；卡片不用阴影
    public static let onTile = ThemeColor(light: RGBA(1, 1, 1, 1), dark: RGBA(1, 1, 1, 1))
    // 制品预览的纸面：发布的网页按白底写，深色模式下也保持白底，否则没写底色的页面黑字压黑底
    public static let paper = ThemeColor(light: RGBA(1, 1, 1, 1), dark: RGBA(1, 1, 1, 1))
}

/// 字号：8 级，含首次使用标题；不用 12 号以下，不用半号
public enum VCFontSize {
    public static let greeting: Double = 34
    public static let title: Double = 22
    public static let heading: Double = 18
    public static let chat: Double = 15
    public static let body: Double = 14
    public static let small: Double = 13
    public static let caption: Double = 12
    public static let display: Double = 44
}

/// 字重：只用三种
public enum VCFontWeight {
    public static let regular: Double = 400
    public static let medium: Double = 500
    public static let strong: Double = 600
}

/// 间距：4 的倍数
public enum VCSpace {
    public static let space1: Double = 4
    public static let space2: Double = 8
    public static let space3: Double = 12
    public static let space4: Double = 16
    public static let space5: Double = 20
    public static let space6: Double = 24
    public static let space8: Double = 32
    public static let space10: Double = 40
}

/// 圆角
public enum VCRadius {
    public static let inner: Double = 6
    public static let control: Double = 8
    public static let card: Double = 14
    public static let bubble: Double = 18
    public static let input: Double = 22
    public static let full: Double = 999
}

/// 图标与控件尺寸
public enum VCControl {
    public static let icon: Double = 16
    public static let iconStep: Double = 18
    public static let iconStroke: Double = 1.75
    public static let controlH: Double = 32
    public static let controlHLg: Double = 40
    public static let navItemH: Double = 40
    public static let controlHForm: Double = 40
    public static let controlHHero: Double = 48
}
