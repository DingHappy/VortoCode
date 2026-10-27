import Foundation

public enum EntryError: Error, LocalizedError {
    case invalid
    case insecure
    public var errorDescription: String? {
        switch self {
        case .invalid: return "请输入助手入口地址，例如 https://assistant.example.com；不要填写密码、令牌或其他页面地址。"
        case .insecure: return "助手入口需要 HTTPS。普通局域网 HTTP 地址不能用于手机登录。"
        }
    }
}

/// Only a public address is persisted. No account identity or credential is derived from it.
public struct AssistantEntry: Hashable, Sendable, Identifiable {
    public let origin: URL
    public var id: String { origin.absoluteString }
    public var mobileURL: URL { origin.appendingPathComponent("mobile") }
    public var displayAddress: String { origin.absoluteString }
    public var host: String { origin.host ?? "" }

    public init(_ raw: String, allowLoopback: Bool = false) throws {
        let raw = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty, raw.utf8.count <= 2048,
              !raw.unicodeScalars.contains(where: { CharacterSet.whitespacesAndNewlines.contains($0) || CharacterSet.controlCharacters.contains($0) }),
              !raw.contains("\\"), var parts = URLComponents(string: raw),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              ["", "/", "/mobile", "/mobile/"].contains(parts.path),
              parts.port == nil || (1...65535).contains(parts.port!) else { throw EntryError.invalid }
        let scheme = parts.scheme?.lowercased()
        let loopback = ["127.0.0.1", "localhost", "::1", "[::1]"].contains(host.lowercased())
        guard scheme == "https" || (scheme == "http" && allowLoopback && loopback) else { throw EntryError.insecure }
        parts.scheme = scheme
        parts.host = host.lowercased()
        parts.path = ""
        if scheme == "https" && parts.port == 443 || scheme == "http" && parts.port == 80 { parts.port = nil }
        guard let url = parts.url, url.host != nil else { throw EntryError.invalid }
        origin = url
    }
}
