import XCTest
@testable import MobileCore

private func staffCard(_ patch: [String: Any] = [:]) throws -> StaffActionCard {
    var value: [String: Any] = ["id": "staff-action-test", "task_id": "task-test", "digest": "digest", "nonce": "nonce",
        "round": 1, "instance": "instance", "runtime": "runtime", "member_name": "事项专员", "role_title": "整理事项",
        "tool": "personal_goal_create", "kind": "own_data", "message": "整理当前事项", "args_text": "{\"text\":\"完整正文\"}",
        "tainted": false, "risk": "normal", "approval_level": "normal", "expires_at": 4_000_000_000.0,
        "status": "pending", "can_decide": true]
    value.merge(patch) { _, new in new }
    return try MobileAPI.decoder.decode(StaffActionCard.self, from: JSONSerialization.data(withJSONObject: value))
}

private func staffReceipt(_ patch: [String: Any] = [:]) throws -> StaffActionReceipt {
    var value: [String: Any] = ["action_id": "staff-action-test", "task_id": "task-test", "digest": "digest", "nonce": "nonce",
        "round": 1, "instance": "instance", "runtime": "runtime", "reason": "", "status": "approved", "replayed": false]
    value.merge(patch) { _, new in new }
    return try MobileAPI.decoder.decode(StaffActionReceipt.self, from: JSONSerialization.data(withJSONObject: value))
}

final class StaffApprovalTests: XCTestCase {
    func testUnknownHighExpiredOrInvalidCardsCannotStartMobileDecisions() throws {
        for patch: [String: Any] in [["kind": "write"], ["kind": "execute"], ["kind": "deliver"], ["kind": "future"],
            ["risk": "high"], ["approval_level": "desktop"], ["can_decide": false], ["nonce": ""],
            ["runtime": ""], ["status": "interrupted"], ["expires_at": 1]] {
            var state = StaffApprovalState()
            XCTAssertNil(state.begin(card: try staffCard(patch), approve: true))
            XCTAssertNil(state.begin(card: try staffCard(patch), approve: false, reason: "本次先不执行"))
        }
        XCTAssertTrue(try staffCard(["tainted": true]).canDecideOnPhone()) // Explicit choice still required.
    }

    func testAnUncertainPostNeverAutomaticallyReopensOrRetries() throws {
        var state = StaffApprovalState(); let card = try staffCard()
        let attempt = try XCTUnwrap(state.begin(card: card, approve: true))
        XCTAssertTrue(state.willSend(attempt)); XCTAssertFalse(state.willSend(attempt))
        state.failed(attempt)
        XCTAssertTrue(state.blocksDecisions); XCTAssertTrue(state.canCheck)
        XCTAssertNil(state.begin(card: card, approve: false, reason: "先不执行"))
        let checking = try XCTUnwrap(state.beginCheck())
        XCTAssertTrue(state.checked(card, for: checking))
        XCTAssertFalse(state.blocksDecisions) // An explicit fresh GET, still no POST.
        XCTAssertNotNil(state.begin(card: card, approve: false, reason: "先不执行"))
    }

    func testAConfirmedReceiptCannotBeReopenedByAStalePendingRead() throws {
        var state = StaffApprovalState(); let card = try staffCard()
        let attempt = try XCTUnwrap(state.begin(card: card, approve: true))
        XCTAssertTrue(state.willSend(attempt)); XCTAssertTrue(state.receive(try staffReceipt(), for: attempt))
        let checking = try XCTUnwrap(state.beginCheck())
        XCTAssertTrue(state.checked(card, for: checking)); XCTAssertTrue(state.blocksDecisions)
        let latest = try XCTUnwrap(state.beginCheck())
        XCTAssertTrue(state.checked(try staffCard(["status": "approved", "can_decide": false]), for: latest))
        XCTAssertFalse(state.blocksDecisions)
    }

    func testEveryBindingAndTheExactChoiceAreRequiredInTheReceipt() throws {
        for patch: [String: Any] in [["task_id": "other"], ["action_id": "other"], ["digest": "other"], ["nonce": "other"],
            ["round": 2], ["instance": "other"], ["runtime": "other"], ["status": "denied"], ["reason": "other"]] {
            var state = StaffApprovalState()
            let attempt = try XCTUnwrap(state.begin(card: try staffCard(), approve: true))
            XCTAssertTrue(state.willSend(attempt)); XCTAssertFalse(state.receive(try staffReceipt(patch), for: attempt))
            XCTAssertTrue(state.blocksDecisions); XCTAssertFalse(state.receiptConfirmed)
        }
    }

    func testAccountResetInvalidatesOldSignaturesAndMissingRejectReasonCannotStart() throws {
        var state = StaffApprovalState(); let card = try staffCard()
        XCTAssertNil(state.begin(card: card, approve: false))
        let old = try XCTUnwrap(state.begin(card: card, approve: true)); state.reset()
        let fresh = try XCTUnwrap(state.begin(card: card, approve: false, reason: "本次先不执行"))
        XCTAssertFalse(state.willSend(old)); XCTAssertFalse(state.failed(old)); XCTAssertTrue(state.isCurrent(fresh))
    }

    func testStaffWaitsAppearWithFullSourceFlagsAndDesktopOnlyCardsStayReadonly() throws {
        let ordinary = try staffCard(["tainted": true])
        let high = try staffCard(["id": "high", "kind": "execute", "risk": "high", "approval_level": "desktop", "can_decide": false])
        let items = WaitingItem.collect(cards: [], decisions: [], dispatches: [], staff: [ordinary, high])
        XCTAssertEqual(items.count, 2); XCTAssertEqual(items[0].kind, .staff); XCTAssertTrue(items[0].tainted)
        XCTAssertTrue(items[1].title.contains("电脑")); XCTAssertEqual(ordinary.argsText, "{\"text\":\"完整正文\"}")
    }
}

private final class StaffRequestLog: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [URLRequest] = []
    func reset() { lock.lock(); defer { lock.unlock() }; values = [] }
    func add(_ request: URLRequest) { lock.lock(); defer { lock.unlock() }; values.append(request) }
    var requests: [URLRequest] { lock.lock(); defer { lock.unlock() }; return values }
}

private final class StaffApprovalProtocol: URLProtocol, @unchecked Sendable {
    static let log = StaffRequestLog()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.log.add(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
            headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(#"{"task_id":"task-test","action_id":"staff-action-test","digest":"digest","nonce":"nonce","round":1,"instance":"instance","runtime":"runtime","reason":"","status":"approved","replayed":false}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private actor StaffTestSigner: RequestSigner {
    enum Stopped: Error { case beforeNetwork }
    private let stop: Bool
    private var continuation: CheckedContinuation<Data, Never>?
    private var entered = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var message = Data()
    private var approvalKey = false
    init(stop: Bool = false) { self.stop = stop }
    func sign(_ message: Data, with key: SigningKey) async throws -> Data {
        self.message = message
        if case .approval = key { approvalKey = true }
        if stop { throw Stopped.beforeNetwork }
        return await withCheckedContinuation { continuation in
            self.continuation = continuation; entered = true
            waiters.forEach { $0.resume() }; waiters = []
        }
    }
    func waitUntilSigning() async {
        if entered { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func finish() { continuation?.resume(returning: Data([1])); continuation = nil }
    func captured() -> (Data, Bool) { (message, approvalKey) }
}

private actor StaffTestIdentity {
    private var current = true
    private(set) var checks = 0
    func invalidate() { current = false }
    func check() throws {
        checks += 1
        guard current else { throw CancellationError() }
    }
}

final class StaffApprovalTransportTests: XCTestCase {
    private let device = String(repeating: "c", count: 32)
    private var credential: NativeCredential {
        .init(schemaVersion: 1, token: "vcn_" + String(repeating: "a", count: 43),
            instanceId: String(repeating: "b", count: 32), name: "test", expiresAt: 4_000_000_000)
    }
    private func api() throws -> MobileAPI {
        StaffApprovalProtocol.log.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StaffApprovalProtocol.self]
        return MobileAPI(entry: try AssistantEntry("https://example.invalid"), session: URLSession(configuration: config))
    }
    private func submit(_ api: MobileAPI, signer: StaffTestSigner, identity: StaffTestIdentity) throws -> Task<StaffActionReceipt, Error> {
        let card = try staffCard(), credential = credential, device = device
        return Task {
            try await api.staffDecide(credential, signer: signer, deviceId: device, card: card, approve: true,
                beforeSend: { try await identity.check() })
        }
    }

    func testExactOnceChoiceAndAllBindingsAreSignedWithDeviceKey() async throws {
        let api = try api(), card = try staffCard()
        for approve in [true, false] {
            let signer = StaffTestSigner(stop: true), reason = approve ? "" : "本次先不执行"
            do {
                _ = try await api.staffDecide(credential, signer: signer, deviceId: device, card: card, approve: approve, reason: reason)
                XCTFail("test signer must stop before HTTP")
            } catch StaffTestSigner.Stopped.beforeNetwork {} catch { XCTFail("unexpected: \(error)") }
            let (message, approvalKey) = await signer.captured()
            XCTAssertFalse(approvalKey)
            let parts = String(decoding: message, as: UTF8.self).components(separatedBy: "\n")
            XCTAssertEqual(parts.count, 7)
            guard parts.count == 7 else { continue }
            let body: [String: Any] = ["task_id": card.taskId, "action_id": card.id, "digest": card.digest, "nonce": card.nonce,
                "round": card.round, "instance": card.instance, "runtime": card.runtime, "approve": approve, "reason": reason]
            let expected = RequestSigning.message(method: "POST", path: "/api/mobile/native/staff/action",
                body: try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]),
                timestamp: parts[4], nonce: parts[5], deviceId: device)
            XCTAssertEqual(message, expected) // No extra same-category permission in the signed request.
        }
        XCTAssertTrue(StaffApprovalProtocol.log.requests.isEmpty)
    }

    func testChangedIdentityOrCancellationDuringSigningPreventsHttp() async throws {
        for cancel in [false, true] {
            let api = try api(), signer = StaffTestSigner(), identity = StaffTestIdentity()
            let task = try submit(api, signer: signer, identity: identity)
            await signer.waitUntilSigning()
            if cancel { task.cancel() } else { await identity.invalidate() }
            await signer.finish() // A signer ignoring cancellation must still be harmless.
            do { _ = try await task.value; XCTFail("stale decision must stop") }
            catch is CancellationError {} catch { XCTFail("unexpected: \(error)") }
            XCTAssertTrue(StaffApprovalProtocol.log.requests.isEmpty)
        }
    }

    func testValidIdentityIsRecheckedAfterSigningBeforeOnePostAndReceipt() async throws {
        let api = try api(), signer = StaffTestSigner(), identity = StaffTestIdentity()
        let task = try submit(api, signer: signer, identity: identity)
        await signer.waitUntilSigning()
        XCTAssertTrue(StaffApprovalProtocol.log.requests.isEmpty)
        await signer.finish()
        let receipt = try await task.value, card = try staffCard()
        XCTAssertEqual(receipt.identity, card.identity); XCTAssertEqual(receipt.status, "approved")
        let checks = await identity.checks; XCTAssertEqual(checks, 1)
        let requests = StaffApprovalProtocol.log.requests; XCTAssertEqual(requests.count, 1)
        XCTAssertEqual(requests.first?.httpMethod, "POST")
        XCTAssertEqual(requests.first?.url?.path, "/api/mobile/native/staff/action")
        XCTAssertNotNil(requests.first?.value(forHTTPHeaderField: "X-VC-Signature"))
    }
}
