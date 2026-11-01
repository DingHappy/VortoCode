import Foundation
import Security
import UserNotifications

/// 解密推送正文：服务端只给 APNs 占位文字，真正的内容在 `enc` 里（ECIES，见服务端 gateway/mobile_push.py）。
/// 用与 App 共享钥匙串分组里的推送钥匙解开后替换正文；解不开就保留占位文字，绝不显示密文。
final class NotificationService: UNNotificationServiceExtension {
    private var handler: ((UNNotificationContent) -> Void)?
    private var content: UNMutableNotificationContent?

    override func didReceive(_ request: UNNotificationRequest,
                             withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        handler = contentHandler
        content = request.content.mutableCopy() as? UNMutableNotificationContent
        guard let content else { contentHandler(request.content); return }
        if let sealed = request.content.userInfo["enc"] as? String, let text = Self.decrypt(sealed) {
            content.body = text
        }
        contentHandler(content)
    }

    override func serviceExtensionTimeWillExpire() {
        if let handler, let content { handler(content) }
    }

    static func decrypt(_ sealed: String) -> String? {
        guard let blob = Data(base64Encoded: sealed), blob.count > 65 + 16 else { return nil }
        var query: [String: Any] = [kSecClass as String: kSecClassKey,
                                    kSecAttrApplicationTag as String: Data("com.vortocode.mobile.push".utf8),
                                    kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
                                    kSecReturnRef as String: true]
        if let group = Bundle.main.object(forInfoDictionaryKey: "VCKeychainGroup") as? String,
           group.range(of: "^[A-Z0-9]{10}\\.", options: .regularExpression) != nil {
            query[kSecAttrAccessGroup as String] = group
        }
        var found: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &found) == errSecSuccess, let found else { return nil }
        let key = found as! SecKey   // swiftlint:disable:this force_cast
        var error: Unmanaged<CFError>?
        guard let plain = SecKeyCreateDecryptedData(key, .eciesEncryptionStandardX963SHA256AESGCM,
                                                    blob as CFData, &error) as Data? else { return nil }
        return String(data: plain, encoding: .utf8)
    }
}
