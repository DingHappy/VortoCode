import XCTest
@testable import MobileCore

private func approvalCard(_ patch: [String: Any] = [:]) throws -> ChatCard {
    var raw: [String: Any] = ["id": "c", "text": "update", "risk": "normal", "status": "pending", "digest": "d",
        "operation_kind": "own_data", "required_key": "device", "tainted": false,
        "scope_options": ["once", "turn"], "scope_keys": ["once": "device", "turn": "approval"],
        "scope_limits": ["max_actions": 10, "ttl_seconds": 600]]
    raw.merge(patch) { _, value in value }
    return try MobileAPI.decoder.decode(ChatCard.self, from: JSONSerialization.data(withJSONObject: raw))
}

private func approvalTurn(_ cards: [ChatCard], rid: String = "r", state: String = "waiting", outcome: String? = nil) -> ChatTurn {
    ChatTurn(rid: rid, requestId: "q", state: state, reply: "", error: "", cards: cards, outcome: outcome)
}

final class ChatApprovalStateTests: XCTestCase {
    func testRepeatedTapAndOldCompletionCannotAffectNewAttempt() throws {
        var flow = ChatApprovalState(); let card = try approvalCard()
        let first = try XCTUnwrap(flow.begin(rid: "r", card: card, approve: true, scope: "turn"))
        XCTAssertNil(flow.begin(rid: "r", card: card, approve: false))
        // No POST started: cancelling Face ID is safe to retry explicitly.
        XCTAssertTrue(flow.failed(first)); XCTAssertFalse(flow.blocksDecisions)
        let second = try XCTUnwrap(flow.begin(rid: "r", card: card, approve: false))
        XCTAssertNotEqual(first.id, second.id)
        XCTAssertFalse(flow.failed(first)); XCTAssertFalse(flow.willSend(first))
        XCTAssertTrue(flow.isCurrent(second)); XCTAssertTrue(flow.willSend(second))
        XCTAssertFalse(flow.willSend(second))
    }

    func testUncertainWriteRequiresSuccessfulExplicitReadBeforeAnotherChoice() throws {
        var flow = ChatApprovalState(); let card = try approvalCard()
        let attempt = try XCTUnwrap(flow.begin(rid: "r", card: card, approve: true))
        XCTAssertTrue(flow.willSend(attempt)); flow.failed(attempt)
        XCTAssertTrue(flow.blocksDecisions); XCTAssertTrue(flow.canCheck)
        flow.observe(approvalTurn([card]))
        XCTAssertTrue(flow.blocksDecisions) // Passive polling does not permit retry.
        let check = try XCTUnwrap(flow.beginCheck())
        XCTAssertNil(flow.beginCheck()); XCTAssertNil(flow.begin(rid: "r", card: card, approve: false))
        flow.failed(check) // Failed GET keeps the decision blocked.
        XCTAssertTrue(flow.blocksDecisions)
        let freshCheck = try XCTUnwrap(flow.beginCheck())
        XCTAssertTrue(flow.checked(approvalTurn([card]), for: freshCheck))
        XCTAssertFalse(flow.blocksDecisions)
        XCTAssertNotNil(flow.begin(rid: "r", card: card, approve: false)) // Never auto-submitted.
    }

    func testConfirmedReceiptCannotReopenEvenIfGetStillSaysPending() throws {
        for (scope, approve) in [("once", true), ("turn", true), ("once", false)] {
            var flow = ChatApprovalState(); let card = try approvalCard()
            let attempt = try XCTUnwrap(flow.begin(rid: "r", card: card, approve: approve, scope: scope))
            XCTAssertTrue(flow.willSend(attempt))
            let status = approve ? "approved" : "denied"
            XCTAssertTrue(flow.receive(try approvalCard(["status": status, "approval_scope": scope]), for: attempt))
            flow.failed(attempt) // Progress GET failed after the POST receipt arrived.
            let check = try XCTUnwrap(flow.beginCheck())
            XCTAssertTrue(flow.checked(approvalTurn([card]), for: check))
            XCTAssertTrue(flow.receiptConfirmed); XCTAssertTrue(flow.blocksDecisions)
            flow.observe(approvalTurn([try approvalCard(["status": status])]))
            XCTAssertFalse(flow.blocksDecisions)
        }
    }

    func testWrongReceiptIdentityOutcomeOrScopeNeverBecomesConfirmation() throws {
        for patch: [String: Any] in [["id": "other", "status": "approved", "approval_scope": "turn"],
            ["digest": "other", "status": "approved", "approval_scope": "turn"],
            ["status": "denied", "approval_scope": "turn"], ["status": "pending", "approval_scope": "turn"],
            ["status": "approved", "approval_scope": "once"]] {
            var flow = ChatApprovalState()
            let attempt = try XCTUnwrap(flow.begin(rid: "r", card: approvalCard(), approve: true, scope: "turn"))
            XCTAssertTrue(flow.willSend(attempt))
            XCTAssertFalse(flow.receive(try approvalCard(patch), for: attempt))
            XCTAssertFalse(flow.receiptConfirmed); XCTAssertTrue(flow.blocksDecisions); XCTAssertTrue(flow.canCheck)
        }
    }

    func testResetOrFreshCardInvalidatesOldSignatureAndRead() throws {
        var flow = ChatApprovalState(); let card = try approvalCard()
        let first = try XCTUnwrap(flow.begin(rid: "r", card: card, approve: true))
        flow.reset()
        let next = try XCTUnwrap(flow.begin(rid: "new", card: card, approve: false))
        XCTAssertFalse(flow.willSend(first)); XCTAssertFalse(flow.failed(first))
        XCTAssertFalse(flow.checked(approvalTurn([], rid: "r"), for: first))
        flow.observe(approvalTurn([], rid: "r"))
        XCTAssertTrue(flow.isCurrent(next))
        flow.observe(approvalTurn([try approvalCard(["digest": "fresh"])], rid: "new"))
        XCTAssertFalse(flow.willSend(next)); XCTAssertFalse(flow.blocksDecisions)
    }

    func testLostTurnClosesStaleCardsAndWrongTurnDoesNotUnlockRetry() throws {
        var flow = ChatApprovalState(); let card = try approvalCard()
        let attempt = try XCTUnwrap(flow.begin(rid: "r", card: card, approve: true))
        XCTAssertTrue(flow.willSend(attempt)); flow.failed(attempt)
        let check = try XCTUnwrap(flow.beginCheck())
        XCTAssertFalse(flow.checked(approvalTurn([], rid: "other"), for: check))
        XCTAssertTrue(flow.blocksDecisions)
        flow.observe(approvalTurn([card], outcome: "unknown"))
        XCTAssertFalse(flow.blocksDecisions)
        XCTAssertFalse(flow.receive(try approvalCard(["status": "approved"]), for: attempt))
    }

    func testLocalRangeValidationDoesNotStartAnUnsupportedDecision() throws {
        var flow = ChatApprovalState()
        XCTAssertNil(flow.begin(rid: "r", card: try approvalCard(["operation_kind": "deliver"]), approve: true, scope: "turn"))
        XCTAssertNil(flow.begin(rid: "r", card: try approvalCard(), approve: false, scope: "turn"))
        XCTAssertNil(flow.begin(rid: "r", card: try approvalCard(["status": "approved"]), approve: true))
        XCTAssertFalse(flow.blocksDecisions)
    }
}

private final class ApprovalRequests: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [URLRequest] = []
    private var hold = false
    private var started: XCTestExpectation?
    func reset(hold: Bool = false, started: XCTestExpectation? = nil) {
        lock.lock(); defer { lock.unlock() }; values = []; self.hold = hold; self.started = started
    }
    func add(_ request: URLRequest) {
        lock.lock(); values.append(request); let notify = started; lock.unlock()
        notify?.fulfill()
    }
    var holdsResponse: Bool { lock.lock(); defer { lock.unlock() }; return hold }
    var requests: [URLRequest] { lock.lock(); defer { lock.unlock() }; return values }
}

private final class ApprovalProtocol: URLProtocol, @unchecked Sendable {
    static let log = ApprovalRequests()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.log.add(request)
        if Self.log.holdsResponse { return }
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(#"{"id":"c","text":"update","risk":"normal","status":"approved","digest":"d","approval_scope":"turn"}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private actor DeferredApprovalSigner: RequestSigner {
    private var continuation: CheckedContinuation<Data, Never>?
    private var entered = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func sign(_ message: Data, with key: SigningKey) async throws -> Data {
        await withCheckedContinuation { continuation in
            self.continuation = continuation; entered = true
            waiters.forEach { $0.resume() }; waiters = []
        }
    }
    func waitUntilSigning() async {
        if entered { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func finish() { continuation?.resume(returning: Data([1])); continuation = nil }
}

private actor ApprovalIdentity {
    var current = true
    private(set) var checks = 0
    func invalidate() { current = false }
    func check() throws {
        checks += 1
        guard current else { throw CancellationError() }
    }
}

final class ChatApprovalTransportTests: XCTestCase {
    private func api() throws -> MobileAPI {
        ApprovalProtocol.log.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [ApprovalProtocol.self]
        return MobileAPI(entry: try AssistantEntry("https://example.invalid"), session: URLSession(configuration: config))
    }
    private func submit(_ api: MobileAPI, signer: DeferredApprovalSigner, identity: ApprovalIdentity) throws -> Task<ChatCard, Error> {
        let credential = NativeCredential(schemaVersion: 1, token: "vcn_" + String(repeating: "a", count: 43),
            instanceId: String(repeating: "b", count: 32), name: "test", expiresAt: Int(Date().timeIntervalSince1970) + 600)
        let card = try approvalCard()
        return Task {
            try await api.chatDecide(credential, signer: signer, deviceId: String(repeating: "c", count: 32),
                rid: "r", card: card, approve: true, scope: "turn", beforeSend: { try await identity.check() })
        }
    }

    func testIdentityChangedWhileSigningPreventsAnyHttpRequest() async throws {
        let api = try api(); let signer = DeferredApprovalSigner(); let identity = ApprovalIdentity()
        let task = try submit(api, signer: signer, identity: identity)
        await signer.waitUntilSigning()
        await identity.invalidate(); await signer.finish()
        do { _ = try await task.value; XCTFail("stale approval must stop") } catch is CancellationError {} catch { XCTFail("unexpected: \(error)") }
        XCTAssertTrue(ApprovalProtocol.log.requests.isEmpty)
    }

    func testCancelledTaskStopsEvenIfSignerIgnoresCancellation() async throws {
        let api = try api(); let signer = DeferredApprovalSigner(); let identity = ApprovalIdentity()
        let task = try submit(api, signer: signer, identity: identity)
        await signer.waitUntilSigning(); task.cancel(); await signer.finish()
        do { _ = try await task.value; XCTFail("cancelled approval must stop") } catch is CancellationError {} catch { XCTFail("unexpected: \(error)") }
        XCTAssertTrue(ApprovalProtocol.log.requests.isEmpty)
        let checks = await identity.checks; XCTAssertEqual(checks, 0)
    }

    func testValidIdentityIsRecheckedAfterSigningBeforeOnePost() async throws {
        let api = try api(); let signer = DeferredApprovalSigner(); let identity = ApprovalIdentity()
        let task = try submit(api, signer: signer, identity: identity)
        await signer.waitUntilSigning()
        let before = await identity.checks; XCTAssertEqual(before, 0)
        XCTAssertTrue(ApprovalProtocol.log.requests.isEmpty)
        await signer.finish()
        let response = try await task.value
        XCTAssertEqual(response.status, "approved")
        let requests = ApprovalProtocol.log.requests; XCTAssertEqual(requests.count, 1)
        XCTAssertEqual(requests.first?.httpMethod, "POST")
        XCTAssertEqual(requests.first?.url?.path, "/api/mobile/native/chat/card")
        let after = await identity.checks; XCTAssertEqual(after, 1)
    }

    func testCancelAfterPostStartedIsCancellationWithNoRetry() async throws {
        let api = try api(); let signer = DeferredApprovalSigner(); let identity = ApprovalIdentity()
        let started = expectation(description: "HTTP POST started")
        ApprovalProtocol.log.reset(hold: true, started: started)
        let task = try submit(api, signer: signer, identity: identity)
        await signer.waitUntilSigning(); await signer.finish()
        await fulfillment(of: [started], timeout: 2)
        task.cancel()
        do { _ = try await task.value; XCTFail("cancelled POST must stop") } catch is CancellationError {} catch { XCTFail("unexpected: \(error)") }
        XCTAssertEqual(ApprovalProtocol.log.requests.count, 1)
    }
}
