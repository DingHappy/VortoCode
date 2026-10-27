import Foundation
import MobileCore

/// Lifecycle tests control delayed responses and storage failures through these
/// dependencies. Production uses system authentication and the device Keychain.
@MainActor
struct AssistantServices {
    let load: () throws -> SavedSession?
    let save: (SavedSession) throws -> Void
    let clear: () throws -> Void
    let authenticate: (AssistantEntry) async throws -> NativeCredential
    let cancelAuthentication: () -> Void
    let read: (AssistantEntry, NativeCredential) async throws -> MobileSnapshot
    let revoke: (AssistantEntry, NativeCredential) async throws -> Void

    static func live() -> Self {
        let auth = NativeAuthentication()
        return Self(load: SessionVault.load, save: SessionVault.save, clear: SessionVault.clear,
                    authenticate: auth.login, cancelAuthentication: auth.cancel,
                    read: { try await MobileAPI(entry: $0).snapshot($1) },
                    revoke: { try await MobileAPI(entry: $0).logout($1) })
    }
}
