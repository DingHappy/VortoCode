import XCTest
@testable import MobileCore

final class MobileModelsTests: XCTestCase {
    private let instance = String(repeating: "a", count: 32)
    private func wire() -> [String: Any] {
        let stamp = "2026-10-26T10:00:00.123456+00:00"
        func source(_ data: [String: Any]) -> [String: Any] { ["status":"current","checked_at":stamp,"data":data] }
        return ["schema_version":1,"instance_id":instance,"sources":[
            "profile": source(["name":"助手"]), "main_chat":source(["sid":NSNull()]),
            "goals":source(["items":[["id":"personal-1","objective":"核对原生端","status":"active","next_action":"查看依据","blocker":"","revision":1]],"truncated":false])]]
    }
    private func decode(_ raw: [String: Any]) throws -> MobileSnapshot { try MobileAPI.decoder.decode(MobileSnapshot.self, from: JSONSerialization.data(withJSONObject: raw)) }
    func testSnapshotMatchesOriginalContract() throws {
        let snapshot = try decode(wire()); try snapshot.validate(instance: instance)
        XCTAssertEqual(snapshot.sources.goals.data?.items.first?.statusLabel, "跟进中")
        XCTAssertNotNil(snapshot.sources.profile.checkedDate)
        XCTAssertThrowsError(try snapshot.validate(instance: String(repeating:"b",count:32)))
        var raw = wire(); raw["schema_version"] = 2
        XCTAssertThrowsError(try decode(raw).validate(instance: instance))
    }
    func testUnavailableSourcesCannotCarryFreshDataOrTimestamps() throws {
        var raw = wire(), sources = raw["sources"] as! [String: Any]
        sources["goals"] = ["status":"unavailable","checked_at":NSNull(),"data":NSNull()]; raw["sources"] = sources
        try decode(raw).validate(instance: instance)
        sources["goals"] = ["status":"unavailable","checked_at":"2026-10-26T10:00:00Z","data":NSNull()]; raw["sources"] = sources
        XCTAssertThrowsError(try decode(raw).validate(instance: instance))
    }
    func testNativeCredentialRejectsOtherTokenKindsAndDoesNotPrintSecrets() throws {
        let token = "vcn_" + String(repeating:"x",count:43)
        let raw: [String: Any] = ["schema_version":1,"token":token,"instance_id":instance,"name":"助手","expires_at":Int(Date().timeIntervalSince1970)+1000]
        let credential = try MobileAPI.decoder.decode(NativeCredential.self, from: JSONSerialization.data(withJSONObject: raw))
        try credential.validate(); XCTAssertFalse(String(reflecting:credential).contains(token))
        var bad = raw; bad["token"] = "vcm_" + String(repeating:"x",count:43)
        let invalid = try MobileAPI.decoder.decode(NativeCredential.self, from: JSONSerialization.data(withJSONObject: bad))
        XCTAssertThrowsError(try invalid.validate())
    }
}
