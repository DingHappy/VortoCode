import Foundation
import MobileCore

// Private stdin only; never print credentials or response/error bodies.
struct Input: Decodable { let origin: String; let credential: NativeCredential }
@main struct Probe {
    static func main() async {
        do {
            let input = try MobileAPI.decoder.decode(Input.self, from: FileHandle.standardInput.readDataToEndOfFile())
            let entry = try AssistantEntry(input.origin, allowLoopback: true)
            let api = MobileAPI(entry: entry)
            let snapshot = try await api.snapshot(input.credential)
            guard snapshot.sources.goals.data?.items.first?.objective == "核对同账号手机入口" else { exit(2) }
            try await api.logout(input.credential)
            do { _ = try await api.snapshot(input.credential); exit(3) }
            catch let error as MobileError { guard error == .unauthorized else { exit(4) } }
            print("PASS: Swift native API read, source validation and logout revocation")
        } catch { print("FAIL: Swift native API contract"); exit(1) }
    }
}
