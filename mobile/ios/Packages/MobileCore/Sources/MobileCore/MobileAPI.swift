import Foundation

private final class NoRedirects: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

public final class MobileAPI: @unchecked Sendable {
    public let entry: AssistantEntry
    private let session: URLSession
    public init(entry: AssistantEntry) {
        self.entry = entry
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false
        config.urlCache = nil; config.connectionProxyDictionary = [:]
        config.timeoutIntervalForRequest = 15; config.timeoutIntervalForResource = 20
        session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }
    deinit { session.invalidateAndCancel() }
    public static var decoder: JSONDecoder {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase; return decoder
    }
    private func request(_ path: String, method: String = "GET", token: String? = nil, body: Data? = nil) async throws -> Data {
        var request = URLRequest(url: entry.origin.appendingPathComponent("api/mobile/native/" + path))
        request.httpMethod = method; request.httpBody = body; request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        do {
            let (bytes, response) = try await session.bytes(for: request)
            guard let response = response as? HTTPURLResponse else { throw MobileError.invalidResponse }
            if [401,403].contains(response.statusCode) { throw MobileError.unauthorized }
            guard response.statusCode == 200 else { throw MobileError.unavailable }
            guard response.mimeType == "application/json" else { throw MobileError.invalidResponse }
            var data = Data()
            for try await byte in bytes {
                if data.count >= 1_048_576 { throw MobileError.invalidResponse }
                data.append(byte)
            }
            try Task.checkCancellation()
            return data
        } catch let error as MobileError { throw error }
        catch is CancellationError { throw CancellationError() }
        catch { if Task.isCancelled { throw CancellationError() }; throw MobileError.unavailable }
    }
    public func exchange(ticket: String, verifier: String, state: String) async throws -> NativeCredential {
        let body = try JSONSerialization.data(withJSONObject: ["ticket":ticket,"verifier":verifier,"state":state])
        let data = try await request("exchange", method: "POST", body: body)
        guard let value = try? Self.decoder.decode(NativeCredential.self, from: data) else { throw MobileError.invalidResponse }
        try value.validate(); return value
    }
    public func snapshot(_ credential: NativeCredential) async throws -> MobileSnapshot {
        try credential.validate()
        let data = try await request("snapshot", token: credential.token)
        guard let snapshot = try? Self.decoder.decode(MobileSnapshot.self, from: data) else { throw MobileError.invalidResponse }
        try snapshot.validate(instance: credential.instanceId); return snapshot
    }
    public func logout(_ credential: NativeCredential) async throws {
        let data = try await request("logout", method: "POST", token: credential.token)
        guard let body = try? JSONSerialization.jsonObject(with: data) as? [String: Bool], body["ok"] == true else { throw MobileError.invalidResponse }
    }
}
