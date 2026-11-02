import Foundation

/// 主人终端的读写（`/api/mobile/native/*`）。读取沿用原生会话；写入另带设备签名。
/// 错误分开处理：写入的 401/403 不清除登录（那是签名或服务端开关的问题），只告诉用户这次没提交。
extension MobileAPI {
    private func owner(_ path: String, method: String, credential: NativeCredential, body: Data? = nil,
                       signer: RequestSigner? = nil, key: SigningKey = .device, deviceId: String? = nil) async throws -> Data {
        try credential.validate()
        let route = "/api/mobile/native/" + path
        var request = URLRequest(url: entry.origin.appendingPathComponent(String(route.dropFirst())))
        request.httpMethod = method
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer " + credential.token, forHTTPHeaderField: "Authorization")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let signer, let deviceId {
            let timestamp = String(Int(Date().timeIntervalSince1970)), nonce = RequestSigning.nonce()
            let message = RequestSigning.message(method: method, path: route, body: body ?? Data(),
                                                 timestamp: timestamp, nonce: nonce, deviceId: deviceId)
            let signature = try await signer.sign(message, with: key)
            request.setValue(timestamp, forHTTPHeaderField: "X-VC-Timestamp")
            request.setValue(nonce, forHTTPHeaderField: "X-VC-Nonce")
            request.setValue(signature.base64EncodedString(), forHTTPHeaderField: "X-VC-Signature")
        }
        let data: Data, response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch is CancellationError { throw CancellationError() }
        catch { throw OwnerWriteError.unavailable }
        guard let http = response as? HTTPURLResponse, data.count <= 1_048_576 else { throw MobileError.invalidResponse }
        switch http.statusCode {
        case 200: return data
        case 401: throw signer == nil ? MobileError.unauthorized : OwnerWriteError.signatureRejected
        case 403: throw OwnerWriteError.writesDisabled
        case 400, 404, 409:
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw OwnerWriteError.rejected(String((detail ?? "请求没有被接受").prefix(200)))
        default: throw OwnerWriteError.unavailable
        }
    }

    private func json(_ value: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        guard let value = try? Self.decoder.decode(type, from: data) else { throw MobileError.invalidResponse }
        return value
    }

    public func bootstrap(_ credential: NativeCredential) async throws -> NativeBootstrap {
        let value = try decode(NativeBootstrap.self, try await owner("bootstrap", method: "GET", credential: credential))
        try value.validate(instance: credential.instanceId)
        return value
    }

    /// 第一次绑定日常钥匙和批准钥匙（65 字节未压缩 P-256 公钥的 base64）。同一对钥匙重试是安全的。
    public func bindKeys(_ credential: NativeCredential, deviceKey: String, approvalKey: String) async throws -> KeyBinding {
        try decode(KeyBinding.self, try await owner("keys", method: "POST", credential: credential,
                                                    body: try json(["device_key": deviceKey, "approval_key": approvalKey])))
    }

    public func decisions(_ credential: NativeCredential) async throws -> [PendingDecision] {
        struct Reply: Decodable { let decisions: [PendingDecision] }
        return try decode(Reply.self, try await owner("decisions", method: "GET", credential: credential)).decisions
    }

    /// 批准用批准钥匙（触发 Face ID），拒绝用日常钥匙。签的是用户在卡片上看到的那条动作的摘要。
    public func decide(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                       decision: PendingDecision, approve: Bool) async throws -> DecisionOutcome {
        var body: [String: Any] = ["action_id": decision.id, "approve": approve]
        if approve { body["action_digest"] = decision.actionDigest }
        let data = try await owner("decide", method: "POST", credential: credential, body: try json(body),
                                   signer: signer, key: approve ? .approval : .device, deviceId: deviceId)
        return try decode(DecisionOutcome.self, data)
    }

    /// 随手记。`requestId` 由调用方生成并在重试时复用：服务端只建一次、只记一次撤销。
    public func capture(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                        text: String, requestId: String) async throws -> CaptureOutcome {
        let data = try await owner("capture", method: "POST", credential: credential,
                                   body: try json(["request_id": requestId, "text": text]),
                                   signer: signer, deviceId: deviceId)
        return try decode(CaptureOutcome.self, data)
    }

    public func changes(_ credential: NativeCredential) async throws -> [OwnerChange] {
        struct Reply: Decodable { let ops: [OwnerChange] }
        return try decode(Reply.self, try await owner("changes", method: "GET", credential: credential)).ops
    }

    public func undo(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                     change: OwnerChange) async throws -> UndoOutcome {
        let data = try await owner("undo", method: "POST", credential: credential, body: try json(["op_id": change.id]),
                                   signer: signer, deviceId: deviceId)
        return try decode(UndoOutcome.self, data)
    }

    /// 登记推送：APNs 设备令牌（十六进制）和解密推送正文用的公钥。App 每次启动都调。
    public func registerPush(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                             apnsToken: String, pushKey: String, environment: String) async throws {
        _ = try await owner("push", method: "POST", credential: credential,
                            body: try json(["apns_token": apnsToken, "push_key": pushKey, "environment": environment]),
                            signer: signer, deviceId: deviceId)
    }
}
