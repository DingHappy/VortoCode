import CryptoKit
import Foundation
import LocalAuthentication
import MobileCore
import Security

/// 这台手机的三把钥匙，都在 Secure Enclave 里生成，私钥不出设备：
/// - 日常钥匙：签随手记、撤销、拒绝、登记推送；
/// - 批准钥匙：签「批准」。创建时要求生物识别（biometryCurrentSet），所以批准必须过 Face ID —
///   这由硬件保证，换了指纹 / 面容会让它失效，需要重新登录绑定；
/// - 推送钥匙：只用来解密推送正文（ECIES），放在与通知扩展共享的钥匙串分组里。
///
/// 模拟器没有 Secure Enclave：Debug 构建退回软件钥匙，仅供模拟器联调；Release 构建直接报错。
enum DeviceKeys {
    static let service = "com.vortocode.mobile.keys"
    static let pushTag = Data("com.vortocode.mobile.push".utf8)

    enum KeyError: Error, LocalizedError {
        case unavailable, storage
        var errorDescription: String? {
            switch self {
            case .unavailable: return "这台设备没有可用的安全芯片，不能开启手机写入。"
            case .storage: return "暂时无法读写本机钥匙，请解锁设备后重试。"
            }
        }
    }

    /// 与通知扩展共享的钥匙串分组（Info.plist 的 VCKeychainGroup = $(AppIdentifierPrefix)…）。
    static var sharedGroup: String? {
        // 只有签过名的构建才带团队前缀（10 位团队 ID + "."）；未签名的模拟器构建不用共享分组
        guard let group = Bundle.main.object(forInfoDictionaryKey: "VCKeychainGroup") as? String,
              group.range(of: "^[A-Z0-9]{10}\\.", options: .regularExpression) != nil else { return nil }
        return group
    }

    // MARK: 签名钥匙（CryptoKit；Secure Enclave 的密钥句柄存在钥匙串里）

    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func loadBlob(_ account: String) throws -> Data? {
        var request = query(account)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw KeyError.storage }
        return data
    }

    private static func saveBlob(_ account: String, _ data: Data) throws {
        var add = query(account)
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess || status == errSecDuplicateItem else { throw KeyError.storage }
    }

    private static func accessControl(biometric: Bool) throws -> SecAccessControl {
        var flags: SecAccessControlCreateFlags = .privateKeyUsage
        if biometric { flags.insert(.biometryCurrentSet) }
        guard let control = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, flags, nil)
        else { throw KeyError.storage }
        return control
    }

    /// 返回这把钥匙的公钥（65 字节未压缩点的 base64）；不存在就先在 Secure Enclave 里生成。
    static func publicKey(_ key: SigningKey) throws -> String {
        let account = key == .approval ? "approval" : "device"
        if SecureEnclave.isAvailable {
            if let blob = try loadBlob(account) {
                // 只取公钥不触发生物识别：句柄可以不带认证上下文还原
                return try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: blob)
                    .publicKey.x963Representation.base64EncodedString()
            }
            let created = try SecureEnclave.P256.Signing.PrivateKey(
                compactRepresentable: false, accessControl: try accessControl(biometric: key == .approval))
            try saveBlob(account, created.dataRepresentation)
            return created.publicKey.x963Representation.base64EncodedString()
        }
        #if DEBUG
        if let blob = try loadBlob(account) {
            return try P256.Signing.PrivateKey(rawRepresentation: blob).publicKey.x963Representation.base64EncodedString()
        }
        let created = P256.Signing.PrivateKey()
        try saveBlob(account, created.rawRepresentation)
        return created.publicKey.x963Representation.base64EncodedString()
        #else
        throw KeyError.unavailable
        #endif
    }

    static func sign(_ message: Data, with key: SigningKey) throws -> Data {
        let account = key == .approval ? "approval" : "device"
        guard let blob = try loadBlob(account) else { throw KeyError.storage }
        if SecureEnclave.isAvailable {
            let context = LAContext()
            context.localizedReason = "批准这条动作"
            let privateKey = try SecureEnclave.P256.Signing.PrivateKey(
                dataRepresentation: blob, authenticationContext: key == .approval ? context : nil)
            return try privateKey.signature(for: message).derRepresentation
        }
        #if DEBUG
        return try P256.Signing.PrivateKey(rawRepresentation: blob).signature(for: message).derRepresentation
        #else
        throw KeyError.unavailable
        #endif
    }

    // MARK: 推送钥匙（Security 框架：通知扩展要用 SecKeyCreateDecryptedData 解密）

    private static var pushQuery: [String: Any] {
        var query: [String: Any] = [kSecClass as String: kSecClassKey, kSecAttrApplicationTag as String: pushTag,
                                    kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom]
        if let group = sharedGroup { query[kSecAttrAccessGroup as String] = group }
        return query
    }

    /// 推送钥匙的公钥（base64）。通知可能在锁屏时到达，所以这把钥匙在首次解锁后即可用。
    static func pushPublicKey() throws -> String {
        var lookup = pushQuery
        lookup[kSecReturnRef as String] = true
        var found: CFTypeRef?
        var key: SecKey
        if SecItemCopyMatching(lookup as CFDictionary, &found) == errSecSuccess, let existing = found {
            key = existing as! SecKey   // swiftlint:disable:this force_cast
        } else {
            var attributes: [String: Any] = [kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
                                             kSecAttrKeySizeInBits as String: 256]
            var privateAttributes: [String: Any] = [kSecAttrIsPermanent as String: true,
                                                    kSecAttrApplicationTag as String: pushTag]
            if let group = sharedGroup { privateAttributes[kSecAttrAccessGroup as String] = group }
            if SecureEnclave.isAvailable {
                guard let control = SecAccessControlCreateWithFlags(
                    nil, kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly, .privateKeyUsage, nil) else { throw KeyError.storage }
                attributes[kSecAttrTokenID as String] = kSecAttrTokenIDSecureEnclave
                privateAttributes[kSecAttrAccessControl as String] = control
            } else {
                #if !DEBUG
                throw KeyError.unavailable
                #endif
            }
            attributes[kSecPrivateKeyAttrs as String] = privateAttributes
            var error: Unmanaged<CFError>?
            guard let created = SecKeyCreateRandomKey(attributes as CFDictionary, &error) else { throw KeyError.storage }
            key = created
        }
        var error: Unmanaged<CFError>?
        guard let publicKey = SecKeyCopyPublicKey(key),
              let raw = SecKeyCopyExternalRepresentation(publicKey, &error) as Data? else { throw KeyError.storage }
        return raw.base64EncodedString()
    }
}

/// 交给 MobileAPI 的签名器：批准钥匙签名时由系统弹出 Face ID。
struct DeviceSigner: RequestSigner {
    func sign(_ message: Data, with key: SigningKey) async throws -> Data {
        try await Task.detached { try DeviceKeys.sign(message, with: key) }.value
    }
}
