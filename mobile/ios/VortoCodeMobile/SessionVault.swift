import Foundation
import Security
import MobileCore

struct SavedSession: Codable { let origin: String; let credential: NativeCredential }
enum SessionVault {
    static let service = "com.vortocode.mobile.session"
    private static var query: [String: Any] {
        [kSecClass as String:kSecClassGenericPassword, kSecAttrService as String:service, kSecAttrAccount as String:"native-session"]
    }
    static func load() throws -> SavedSession? {
        var query = query; query[kSecReturnData as String] = true; query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw MobileError.secureStorage }
        guard let saved = try? JSONDecoder().decode(SavedSession.self, from: data) else { throw MobileError.secureStorage }
        return saved
    }
    static func save(_ value: SavedSession) throws {
        let data = try JSONEncoder().encode(value)
        let update = [kSecValueData as String:data, kSecAttrAccessible as String:kSecAttrAccessibleWhenUnlockedThisDeviceOnly] as [String: Any]
        var status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var add = query; for (key, value) in update { add[key] = value }
            status = SecItemAdd(add as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw MobileError.secureStorage }
    }
    static func clear() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw MobileError.secureStorage }
    }
}
