import XCTest
@testable import MobileCore

final class AssistantEntryTests: XCTestCase {
    func testEntryAcceptsOnlyCanonicalOriginsOrMobilePath() throws {
        for raw in ["https://ASSISTANT.example.com:443/", " https://assistant.example.com/mobile/ "] {
            let entry = try AssistantEntry(raw)
            XCTAssertEqual(entry.displayAddress, "https://assistant.example.com")
            XCTAssertEqual(entry.mobileURL.absoluteString, "https://assistant.example.com/mobile")
        }
    }
    func testCredentialsQueriesFragmentsAndOtherPathsAreRejected() {
        for raw in ["https://u:p@host.test", "https://host.test/?token=secret", "https://host.test/#token", "https://host.test/api/mobile", "https://host.test:0", "https://host.test:99999", "javascript:alert(1)", "file:///etc/passwd", "https://host.test\\@other.test", "https://host.test\n/mobile", String(repeating: "a", count: 2049)] {
            XCTAssertThrowsError(try AssistantEntry(raw))
        }
    }
    func testLoopbackRequiresExplicitDevelopmentFlagAndNeverAllowsLAN() throws {
        for host in ["127.0.0.1", "localhost", "[::1]"] {
            XCTAssertThrowsError(try AssistantEntry("http://\(host):5218"))
            XCTAssertEqual(try AssistantEntry("http://\(host):5218", allowLoopback: true).mobileURL.path, "/mobile")
        }
        for raw in ["http://192.168.1.97:8080", "http://10.0.0.97", "http://localhost.evil.test", "http://127.0.0.1.evil.test"] {
            XCTAssertThrowsError(try AssistantEntry(raw, allowLoopback: true))
        }
    }
    func testSameHostOnDifferentPortsRemainsDifferentEntry() throws {
        XCTAssertNotEqual(try AssistantEntry("https://host.test:8443"), try AssistantEntry("https://host.test:9443"))
    }
}
