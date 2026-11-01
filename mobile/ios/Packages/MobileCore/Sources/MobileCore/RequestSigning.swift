import CryptoKit
import Foundation
import Security

/// 主人终端写请求的签名合同，必须与服务端 `src/gateway/mobile_signing.py` 逐字节一致。
/// 被签名的是七行用 `\n` 连接的文本：协议标记、方法、路径、请求体 SHA-256、时间戳、nonce、设备 id。
public enum RequestSigning {
    public static let protocolTag = "vortocode-mobile-v1"

    public static func message(method: String, path: String, body: Data, timestamp: String, nonce: String,
                               deviceId: String) -> Data {
        let digest = SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined()
        return [protocolTag, method.uppercased(), path, digest, timestamp, nonce, deviceId]
            .joined(separator: "\n").data(using: .utf8)!
    }

    /// 16 字节随机数的 base64url（22 个字符），满足服务端 `[A-Za-z0-9_-]{16,64}`。
    public static func nonce() -> String {
        var bytes = [UInt8](repeating: 0, count: 16)
        precondition(SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess)
        return Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// 哪把钥匙签：日常写入用 `.device`；批准动作用 `.approval`（App 里这把钥匙要过生物识别）。
public enum SigningKey: Sendable { case device, approval }

/// App 用 Secure Enclave 实现；测试用软件钥匙。返回 DER 编码的 ECDSA-SHA256 签名。
public protocol RequestSigner: Sendable {
    func sign(_ message: Data, with key: SigningKey) async throws -> Data
}
