import CryptoKit
import XCTest
@testable import MobileCore

final class RequestSigningTests: XCTestCase {
    /// 对照向量由服务端 `mobile_signing.message` 生成：两端必须逐字节一致，否则所有写入都会 401。
    func testMessageMatchesServerVector() {
        let body = Data(#"{"request_id":"cap-0001-abcd","text":"周五前交周报"}"#.utf8)
        let message = RequestSigning.message(method: "post", path: "/api/mobile/native/capture", body: body,
                                             timestamp: "1793550000", nonce: "abcdefghijklmnop",
                                             deviceId: "0123456789abcdef0123456789abcdef")
        XCTAssertEqual(String(decoding: message, as: UTF8.self),
                       "vortocode-mobile-v1\nPOST\n/api/mobile/native/capture\n"
                       + "31876fbeeb80ad230df6fdbf863aa415208d7b636654147e1ef0710d6f1c1fd9\n"
                       + "1793550000\nabcdefghijklmnop\n0123456789abcdef0123456789abcdef")
        XCTAssertEqual(SHA256.hash(data: message).map { String(format: "%02x", $0) }.joined(),
                       "ed5eefa6e199c34eb747a61e81ecd33aaa4a409ff76256f3f217ba1d24bc87d8")
    }

    func testNonceIsUrlSafeAndFresh() {
        let first = RequestSigning.nonce(), second = RequestSigning.nonce()
        XCTAssertNotEqual(first, second)
        XCTAssertNotNil(first.range(of: "^[A-Za-z0-9_-]{16,64}$", options: .regularExpression))
    }

    /// Secure Enclave 的 P256.Signing 与软件钥匙同一套格式：公钥 x963（65 字节），签名 DER。
    func testSignatureFormatIsWhatTheServerVerifies() throws {
        let key = P256.Signing.PrivateKey()
        XCTAssertEqual(key.publicKey.x963Representation.count, 65)
        XCTAssertEqual(key.publicKey.x963Representation.first, 0x04)
        let message = RequestSigning.message(method: "POST", path: "/api/mobile/native/undo", body: Data("{}".utf8),
                                             timestamp: "1", nonce: "abcdefghijklmnop", deviceId: String(repeating: "a", count: 32))
        let der = try key.signature(for: message).derRepresentation
        let parsed = try P256.Signing.ECDSASignature(derRepresentation: der)
        XCTAssertTrue(key.publicKey.isValidSignature(parsed, for: message))
    }

    func testDecodesServerReplies() throws {
        let change = try MobileAPI.decoder.decode(OwnerChange.self, from: Data("""
        {"id":"op-0123456789ab","ts":"2026-11-01T10:00:00+00:00","action":"personal_goal.create",
         "summary":"手机随手记「买牛奶」","tainted":false,"source":"mobile","status":"undone",
         "undone_at":"2026-11-01T10:01:00+00:00","undo_detail":"目标已撤回","not_recallable":["提醒已经推送过"]}
        """.utf8))
        XCTAssertEqual(change.statusLabel, "已撤销")
        XCTAssertEqual(change.notRecallable, ["提醒已经推送过"])

        let decision = try MobileAPI.decoder.decode(PendingDecision.self, from: Data("""
        {"id":"cdx-0123456789","kind":"message","thread":"Codex「写计算器」（/tmp/calc）","text":"继续实现 sub",
         "tainted":true,"created_at":"2026-11-01T10:00:00+00:00","action_digest":"\(String(repeating: "f", count: 64))"}
        """.utf8))
        XCTAssertEqual(decision.title, "发给 Codex")
        XCTAssertTrue(decision.tainted)

        let boot = try MobileAPI.decoder.decode(NativeBootstrap.self, from: Data("""
        {"schema_version":1,"instance_id":"\(String(repeating: "b", count: 32))",
         "device":{"id":"\(String(repeating: "c", count: 32))","name":"手机浏览器","created_at":1,"expires_at":2,"revoked_at":null,"scopes":["assistant:read"]},
         "capabilities":{"reads":["profile"],"writes":["capture","decide","undo"]}}
        """.utf8))
        XCTAssertNoThrow(try boot.validate(instance: String(repeating: "b", count: 32)))
        XCTAssertThrowsError(try boot.validate(instance: String(repeating: "d", count: 32)))
        XCTAssertTrue(boot.canWrite)
    }

    func testWriteErrorsNeverLookLikeALogout() {
        XCTAssertNotEqual(OwnerWriteError.signatureRejected.localizedDescription, MobileError.unauthorized.localizedDescription)
        XCTAssertEqual(OwnerWriteError.rejected("这条动作已经变了").localizedDescription, "这条动作已经变了")
    }
}
