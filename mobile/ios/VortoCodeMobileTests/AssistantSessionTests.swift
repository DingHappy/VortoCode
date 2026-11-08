import XCTest
import MobileCore
@testable import VortoCodeMobile

@MainActor
final class AssistantSessionTests: XCTestCase {
    private let entry = try! AssistantEntry("https://first.example.test")
    private let other = try! AssistantEntry("https://second.example.test")

    func testLogoutFailureClearsLocalLoginAndCanRetryExactSession() async throws {
        let h = Harness(); let credential = try makeCredential()
        h.saved = SavedSession(origin: entry.displayAddress, credential: credential)
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await ready(session)
        h.revokeError = .unavailable
        session.logout(entry); try await ready(session)
        XCTAssertNil(session.credential); XCTAssertNil(session.reading.instanceId); XCTAssertNil(h.saved)
        XCTAssertEqual(session.pendingLogouts.count, 1)
        let id = try XCTUnwrap(session.pendingLogouts.first?.id)
        h.revokeError = nil
        session.retryLogout(id); try await ready(session)
        XCTAssertTrue(session.pendingLogouts.isEmpty)
        XCTAssertEqual(h.revocations.map(\.origin), [entry.displayAddress, entry.displayAddress])
        XCTAssertTrue(h.revocations.allSatisfy { $0.token == credential.token })
        session.retryLogout(id)
        XCTAssertEqual(h.revocations.count, 2)
    }

    func testRetryOldLogoutCannotReplaceNewLoginOrUseItsToken() async throws {
        let h = Harness(); let old = try makeCredential(), new = try makeCredential("b")
        h.saved = SavedSession(origin: entry.displayAddress, credential: old)
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await ready(session)
        h.revokeError = .unavailable
        session.logout(entry); try await ready(session)
        let id = try XCTUnwrap(session.pendingLogouts.first?.id)
        h.authentication = { new }
        session.login(other); try await ready(session)
        let gate = Gate<Void>()
        h.revokeError = nil; h.revocation = { try await gate.wait() }
        session.retryLogout(id)
        try await until { gate.waiting }
        session.suspendReads()
        XCTAssertEqual(session.phase, .revoking)
        XCTAssertEqual(session.reading.goals.freshness, .historical)
        gate.resolve(())
        try await ready(session)
        XCTAssertEqual(session.credential?.token, new.token)
        XCTAssertEqual(session.reading.instanceId, new.instanceId)
        XCTAssertEqual(h.saved?.origin, other.displayAddress)
        XCTAssertEqual(h.revocations.last?.origin, entry.displayAddress)
        XCTAssertEqual(h.revocations.last?.token, old.token)
        XCTAssertTrue(session.pendingLogouts.isEmpty)
    }

    func testStorageClearFailureKeepsExistingLoginAndDoesNotRevoke() async throws {
        let h = Harness(); let credential = try makeCredential()
        h.saved = SavedSession(origin: entry.displayAddress, credential: credential)
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await ready(session)
        h.clearError = .secureStorage
        session.logout(entry)
        XCTAssertEqual(session.credential?.token, credential.token)
        XCTAssertNotNil(h.saved)
        XCTAssertEqual(session.reading.goals.freshness, .historical)
        XCTAssertTrue(h.revocations.isEmpty)
        XCTAssertFalse(session.busy)
        XCTAssertTrue(session.pendingLogouts.isEmpty)
    }

    func testUnauthorizedReadClearsViewAndKeychain() async throws {
        let h = Harness()
        h.saved = SavedSession(origin: entry.displayAddress, credential: try makeCredential())
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await ready(session)
        h.readError = .unauthorized
        await session.refreshAndWait(entry)
        XCTAssertNil(session.credential); XCTAssertNil(session.reading.instanceId); XCTAssertNil(h.saved)
        XCTAssertTrue(session.pendingLogouts.isEmpty)
        XCTAssertFalse(session.busy)
    }

    func testNetworkFailureRetainsHistoricalReadAndLogin() async throws {
        let h = Harness()
        h.saved = SavedSession(origin: entry.displayAddress, credential: try makeCredential())
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await ready(session)
        let checked = session.reading.goals.checkedDate
        h.readError = .unavailable
        await session.refreshAndWait(entry)
        XCTAssertNotNil(session.credential); XCTAssertNotNil(h.saved)
        XCTAssertNotNil(session.reading.goals.data)
        XCTAssertEqual(session.reading.goals.checkedDate, checked)
        XCTAssertEqual(session.reading.goals.freshness, .historical)
        XCTAssertEqual(session.note, MobileError.unavailable.localizedDescription)
    }

    func testContactStatusRemembersLastSuccessAndFlagsAFailedRefresh() async throws {
        UserDefaults.standard.removeObject(forKey: "vortocode.mobile.last-contact." + entry.displayAddress)
        let h = Harness()
        h.saved = SavedSession(origin: entry.displayAddress, credential: try makeCredential())
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await ready(session)
        XCTAssertEqual(session.contact, .connected)
        let reached = try XCTUnwrap(session.lastContact)
        h.readError = .unavailable
        await session.refreshAndWait(entry)
        XCTAssertEqual(session.contact, .unreachable(since: reached))          // 连不上时说清上次连上是什么时候
        let reopened = AssistantSession(services: h.services)
        h.readError = nil
        reopened.restore(entry)                                                // 重开 App：先拿到存下的时间
        XCTAssertEqual(reopened.lastContact?.timeIntervalSince1970 ?? 0, reached.timeIntervalSince1970, accuracy: 0.001)
        UserDefaults.standard.removeObject(forKey: "vortocode.mobile.last-contact." + entry.displayAddress)
    }

    func testExpiredLoginIsNotReportedAsTheAssistantBeingDown() async throws {
        let h = Harness()
        h.saved = SavedSession(origin: entry.displayAddress, credential: try makeCredential())
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await ready(session)
        h.readError = .unauthorized
        await session.refreshAndWait(entry)
        XCTAssertFalse(session.contact.isProblem)
    }

    func testExpiredAndWrongOriginSavedSessionsNeverRead() async throws {
        let h = Harness()
        h.saved = SavedSession(origin: other.displayAddress, credential: try makeCredential())
        let session = AssistantSession(services: h.services)
        session.restore(entry)
        XCTAssertNil(session.credential); XCTAssertEqual(h.reads, 0)
        h.saved = SavedSession(origin: entry.displayAddress, credential: try makeCredential(expired: true))
        session.restore(entry)
        XCTAssertNil(session.credential); XCTAssertNil(h.saved); XCTAssertEqual(h.reads, 0)
    }

    func testLateReadCannotOverwriteAnotherAssistant() async throws {
        let h = Harness(); let old = try makeCredential(), new = try makeCredential("b")
        let gate = Gate<MobileSnapshot>()
        h.saved = SavedSession(origin: entry.displayAddress, credential: old)
        h.read = { _ in try await gate.wait() }
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await until { gate.waiting }
        h.read = nil
        h.saved = SavedSession(origin: other.displayAddress, credential: new)
        session.restore(other); try await ready(session)
        gate.resolve(try makeSnapshot(old.instanceId))
        try await until { h.readCompletions == 2 }
        await Task.yield()
        XCTAssertEqual(session.credential?.token, new.token)
        XCTAssertEqual(session.reading.instanceId, new.instanceId)
    }

    func testLateAuthenticationIsRevokedWithoutRestoringAbandonedLogin() async throws {
        let h = Harness(); let late = try makeCredential()
        let gate = Gate<NativeCredential>()
        h.authentication = { try await gate.wait() }
        let session = AssistantSession(services: h.services)
        session.login(entry); try await until { gate.waiting }
        session.stop()
        gate.resolve(late)
        try await until { h.revocations.count == 1 }
        XCTAssertFalse(h.revocations[0].cancelled)
        XCTAssertNil(session.credential); XCTAssertNil(h.saved); XCTAssertNil(session.reading.instanceId)
        try await until { session.pendingLogouts.isEmpty }
    }

    func testFailedSaveDoesNotExposeLoginAndRetainsFailedCleanupForRetry() async throws {
        let h = Harness(); let credential = try makeCredential()
        h.authentication = { credential }; h.saveError = .secureStorage; h.revokeError = .unavailable
        let session = AssistantSession(services: h.services)
        session.login(entry); try await ready(session)
        try await until { h.revocations.count == 1 }
        XCTAssertNil(session.credential); XCTAssertNil(h.saved)
        XCTAssertEqual(session.note, MobileError.secureStorage.localizedDescription)
        XCTAssertEqual(session.pendingLogouts.count, 1)
    }

    func testAlreadyInvalidRemoteSessionFinishesLogout() async throws {
        let h = Harness()
        h.saved = SavedSession(origin: entry.displayAddress, credential: try makeCredential())
        let session = AssistantSession(services: h.services)
        session.restore(entry); try await ready(session)
        h.revokeError = .unauthorized
        session.logout(entry); try await ready(session)
        XCTAssertNil(session.credential); XCTAssertNil(h.saved)
        XCTAssertTrue(session.pendingLogouts.isEmpty)
    }

    func testAutomaticCleanupAndManualRetryCannotRevokeConcurrently() async throws {
        let h = Harness(); let credential = try makeCredential()
        let gate = Gate<Void>()
        h.authentication = { credential }; h.saveError = .secureStorage
        h.revocation = { try await gate.wait() }
        let session = AssistantSession(services: h.services)
        session.login(entry)
        try await until { gate.waiting }
        let id = try XCTUnwrap(session.pendingLogouts.first?.id)
        XCTAssertTrue(session.isRevoking(id))
        session.retryLogout(id)
        XCTAssertEqual(session.phase, .idle)
        XCTAssertEqual(h.revocations.count, 1)
        gate.resolve(())
        try await until { session.pendingLogouts.isEmpty }
        XCTAssertFalse(session.isRevoking(id))
        XCTAssertEqual(h.revocations.count, 1)
    }

    private func ready(_ session: AssistantSession) async throws { try await until { !session.busy } }
    private func until(_ predicate: () -> Bool) async throws {
        let limit = ContinuousClock.now.advanced(by: .seconds(3))
        while !predicate() {
            if ContinuousClock.now >= limit { XCTFail("Lifecycle did not settle"); throw MobileError.unavailable }
            try await Task.sleep(for: .milliseconds(5))
        }
    }
}

@MainActor private final class Gate<Value> {
    private var continuation: CheckedContinuation<Value, Error>?
    var waiting: Bool { continuation != nil }
    func wait() async throws -> Value { try await withCheckedThrowingContinuation { continuation = $0 } }
    func resolve(_ value: Value) { let pending = continuation; continuation = nil; pending?.resume(returning: value) }
}

@MainActor private final class Harness {
    var saved: SavedSession?
    var saveError: MobileError?, clearError: MobileError?, readError: MobileError?, revokeError: MobileError?
    var authentication: (() async throws -> NativeCredential)?
    var read: ((NativeCredential) async throws -> MobileSnapshot)?
    var revocation: (() async throws -> Void)?
    var reads = 0, readCompletions = 0
    var revocations: [(origin: String, token: String, cancelled: Bool)] = []
    var services: AssistantServices {
        AssistantServices(load: { self.saved }, save: {
            if let error = self.saveError { throw error }; self.saved = $0
        }, clear: {
            if let error = self.clearError { throw error }; self.saved = nil
        }, authenticate: { _ in
            guard let authenticate = self.authentication else { throw MobileError.cancelled }
            return try await authenticate()
        }, cancelAuthentication: {}, read: { _, credential in
            self.reads += 1
            defer { self.readCompletions += 1 }
            if let error = self.readError { throw error }
            if let read = self.read { return try await read(credential) }
            return try makeSnapshot(credential.instanceId)
        }, revoke: { entry, credential in
            self.revocations.append((entry.displayAddress, credential.token, Task.isCancelled))
            if let error = self.revokeError { throw error }
            if let revocation = self.revocation { try await revocation() }
        })
    }
}

private func makeCredential(_ marker: String = "a", expired: Bool = false) throws -> NativeCredential {
    let raw: [String: Any] = ["schema_version":1, "token":"vcn_" + String(repeating: marker, count: 43),
        "instance_id":String(repeating: marker, count: 32), "name":"测试助手", "expires_at":Int(Date().timeIntervalSince1970) + (expired ? -1 : 3600)]
    return try MobileAPI.decoder.decode(NativeCredential.self, from: JSONSerialization.data(withJSONObject: raw))
}
private func makeSnapshot(_ instance: String) throws -> MobileSnapshot {
    func source(_ data: [String: Any]) -> [String: Any] { ["status":"current", "checked_at":"2026-10-26T10:00:00Z", "data":data] }
    let raw: [String: Any] = ["schema_version":1,"instance_id":instance,"sources":[
        "profile":source(["name":"测试助手"]), "main_chat":source(["sid":NSNull()]),
        "goals":source(["items":[],"truncated":false])]]
    return try MobileAPI.decoder.decode(MobileSnapshot.self, from: JSONSerialization.data(withJSONObject: raw))
}

#if targetEnvironment(simulator)
import CryptoKit
import Security

/// 模拟器的 Debug 构建用软件钥匙联调（README）。较新的模拟器报 Secure Enclave 可用，
/// 但要 Face ID 的批准钥匙建不出来，绑定整个失败、手机写入在模拟器上没法测。
final class DeviceKeysSimulatorTests: XCTestCase {
    private func verify(_ key: SigningKey) throws {
        let message = Data("vortocode-key-check".utf8)
        let publicKey = try P256.Signing.PublicKey(x963Representation: XCTUnwrap(Data(base64Encoded: DeviceKeys.publicKey(key))))
        let signature = try P256.Signing.ECDSASignature(derRepresentation: DeviceKeys.sign(message, with: key))
        XCTAssertTrue(publicKey.isValidSignature(signature, for: message))
    }

    func testSimulatorBindsBothSigningKeysWithoutSecureEnclave() throws {
        XCTAssertFalse(DeviceKeys.usesSecureEnclave)
        try verify(.device)
        try verify(.approval)
        XCTAssertFalse(try DeviceKeys.pushPublicKey().isEmpty)
    }

    func testUnreadableLeftoverKeyIsReplaced() throws {
        // 早先在模拟器上走 Secure Enclave 留下的句柄，软件钥匙读不了，要重建而不是一直报错
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: DeviceKeys.service, kSecAttrAccount as String: "device"]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = Data(repeating: 7, count: 11)
        XCTAssertEqual(SecItemAdd(add as CFDictionary, nil), errSecSuccess)
        try verify(.device)
    }
}
#endif
