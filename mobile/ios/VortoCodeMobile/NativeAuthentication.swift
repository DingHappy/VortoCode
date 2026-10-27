import AuthenticationServices
import CryptoKit
import Security
import MobileCore

@MainActor
final class NativeAuthentication: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?
    private var generation = 0
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows).first(where: \.isKeyWindow) ?? ASPresentationAnchor()
    }
    private func random() throws -> String {
        var data = Data(count: 32)
        let result = data.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 32, $0.baseAddress!) }
        guard result == errSecSuccess else { throw MobileError.unavailable }
        return Self.base64(data)
    }
    private static func base64(_ data: Data) -> String { data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
    func cancel() { generation += 1; session?.cancel(); session = nil }
    func login(_ entry: AssistantEntry) async throws -> NativeCredential {
        generation += 1; let owner = generation
        let verifier = try random(), state = try random()
        let challenge = Self.base64(Data(SHA256.hash(data: Data(verifier.utf8))))
        var url = URLComponents(url: entry.origin.appendingPathComponent("api/mobile/native/authorize"), resolvingAgainstBaseURL: false)!
        url.queryItems = [URLQueryItem(name:"challenge",value:challenge), URLQueryItem(name:"state",value:state)]
        let callback: URL = try await withCheckedThrowingContinuation { continuation in
            let auth = ASWebAuthenticationSession(url: url.url!, callbackURLScheme: "vortocode-mobile-auth") { callback, error in
                if let callback { continuation.resume(returning: callback) }
                else { continuation.resume(throwing: MobileError.cancelled) }
            }
            auth.presentationContextProvider = self
            auth.prefersEphemeralWebBrowserSession = false
            session = auth
            if !auth.start() { continuation.resume(throwing: MobileError.unavailable); session = nil }
        }
        guard owner == generation else { throw MobileError.cancelled }
        session = nil
        try Task.checkCancellation()
        let parts = URLComponents(url: callback, resolvingAgainstBaseURL: false)
        let items = parts?.queryItems ?? []
        guard parts?.scheme == "vortocode-mobile-auth", parts?.host == "callback", parts?.path == "", parts?.user == nil, parts?.password == nil, parts?.port == nil, parts?.fragment == nil,
              !items.contains(where: { $0.name == "error" }),
              items.filter({$0.name == "state"}).count == 1, items.first(where: {$0.name == "state"})?.value == state,
              items.filter({$0.name == "ticket"}).count == 1, let ticket = items.first(where: {$0.name == "ticket"})?.value,
              ticket.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil else { throw MobileError.unauthorized }
        return try await MobileAPI(entry: entry).exchange(ticket: ticket, verifier: verifier, state: state)
    }
}
