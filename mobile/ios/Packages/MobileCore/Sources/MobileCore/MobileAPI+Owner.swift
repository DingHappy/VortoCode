import Foundation

/// 主人终端的读写（`/api/mobile/native/*`）。读取沿用原生会话；写入另带设备签名。
/// 错误分开处理：写入的 401/403 不清除登录（那是签名或服务端开关的问题），只告诉用户这次没提交。
extension MobileAPI {
    public func ownerAccess(_ credential: NativeCredential) async throws -> OwnerAccessSnapshot {
        let value = try decode(OwnerAccessSnapshot.self, try await owner("access", method: "GET", credential: credential))
        try value.validate()
        return value
    }

    private func owner(_ path: String, method: String, credential: NativeCredential, body: Data? = nil,
                       signer: RequestSigner? = nil, key: SigningKey = .device, deviceId: String? = nil,
                       query: [URLQueryItem] = [], beforeSend: (@Sendable () async throws -> Void)? = nil) async throws -> Data {
        try Task.checkCancellation()
        try credential.validate()
        let route = "/api/mobile/native/" + path
        var url = entry.origin.appendingPathComponent(String(route.dropFirst()))
        if !query.isEmpty, var parts = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            parts.queryItems = query
            url = parts.url ?? url
        }
        var request = URLRequest(url: url)
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
            try Task.checkCancellation()
            request.setValue(timestamp, forHTTPHeaderField: "X-VC-Timestamp")
            request.setValue(nonce, forHTTPHeaderField: "X-VC-Nonce")
            request.setValue(signature.base64EncodedString(), forHTTPHeaderField: "X-VC-Signature")
        }
        try credential.validate()
        if let beforeSend { try await beforeSend() }
        try Task.checkCancellation()
        let data: Data, response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch is CancellationError { throw CancellationError() }
        catch { if Task.isCancelled { throw CancellationError() }; throw OwnerWriteError.unavailable }
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse, data.count <= 1_048_576 else { throw MobileError.invalidResponse }
        switch http.statusCode {
        case 200: return data
        case 401: throw signer == nil ? MobileError.unauthorized : OwnerWriteError.signatureRejected
        case 403: throw OwnerWriteError.writesDisabled
        case 400, 404, 409:
            let response = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            let detail = response?["detail"] as? String
            if http.statusCode == 409, path == "chat/send", response?["code"] as? String == "app_request_review_required" {
                throw OwnerWriteError.requestNeedsReview(String((detail ?? "请先核对旧请求").prefix(200)))
            }
            throw OwnerWriteError.rejected(String((detail ?? "请求没有被接受").prefix(200)))
        case 503:   // 对话没开通 / 常驻助理暂时连不上：照实说，不当成断网
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            if let detail { throw OwnerWriteError.rejected(String(detail.prefix(200))) }
            throw OwnerWriteError.unavailable
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

    public func staffActions(_ credential: NativeCredential, after: String? = nil) async throws -> StaffActionsPage {
        let query = after.map { [URLQueryItem(name: "after", value: $0)] } ?? []
        return try decode(StaffActionsPage.self, try await owner("staff/actions", method: "GET", credential: credential, query: query))
    }

    public func staffAction(_ credential: NativeCredential, taskId: String) async throws -> StaffActionCard {
        let page = try decode(StaffActionsPage.self, try await owner("staff/actions", method: "GET", credential: credential,
            query: [URLQueryItem(name: "task_id", value: taskId)]))
        guard page.sourceComplete, page.next == nil, page.cards.count == 1,
              let card = page.cards.first, card.taskId == taskId else { throw MobileError.invalidResponse }
        return card
    }

    public func staffDecide(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                            card: StaffActionCard, approve: Bool, reason: String = "",
                            beforeSend: (@Sendable () async throws -> Void)? = nil) async throws -> StaffActionReceipt {
        let reason = reason.trimmingCharacters(in: .whitespacesAndNewlines)
        guard card.canDecideOnPhone(), reason.unicodeScalars.count <= 2000, approve || !reason.isEmpty else {
            throw OwnerWriteError.rejected("这张卡不能在手机决定，或拒绝理由尚未填写")
        }
        let body: [String: Any] = ["task_id": card.taskId, "action_id": card.id, "digest": card.digest,
            "nonce": card.nonce, "round": card.round, "instance": card.instance, "runtime": card.runtime,
            "approve": approve, "reason": reason]
        let data = try await owner("staff/action", method: "POST", credential: credential, body: try json(body),
            signer: signer, key: .device, deviceId: deviceId, beforeSend: {
                guard card.canDecideOnPhone() else { throw OwnerWriteError.rejected("这张卡已经过期，请刷新核对") }
                if let beforeSend { try await beforeSend() }
            })
        return try decode(StaffActionReceipt.self, data)
    }

    public func undo(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                     change: OwnerChange) async throws -> UndoOutcome {
        let data = try await owner("undo", method: "POST", credential: credential, body: try json(["op_id": change.id]),
                                   signer: signer, deviceId: deviceId)
        return try decode(UndoOutcome.self, data)
    }

    public func timeline(_ credential: NativeCredential) async throws -> ActivityTimeline {
        try decode(ActivityTimeline.self, try await owner("timeline", method: "GET", credential: credential))
    }

    public func resumeGoalAdvance(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                                  goalId: String) async throws {
        _ = try await owner("goal-advance/resume", method: "POST", credential: credential,
                            body: try json(["goal_id": goalId]), signer: signer, deviceId: deviceId)
    }

    public func undoChange(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                           operationId: String) async throws -> UndoOutcome {
        let data = try await owner("undo", method: "POST", credential: credential,
                                   body: try json(["op_id": operationId]), signer: signer, deviceId: deviceId)
        return try decode(UndoOutcome.self, data)
    }

    // —— 任务单：助理拟好的派活，手机上核对后下发或撤回 ——

    public func dispatches(_ credential: NativeCredential) async throws -> [DispatchProposal] {
        struct Reply: Decodable { let dispatches: [DispatchProposal] }
        return try decode(Reply.self, try await owner("dispatches", method: "GET", credential: credential)).dispatches
    }

    /// 下发用批准钥匙（Face ID），撤回用日常钥匙。
    public func dispatch(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                         proposal: DispatchProposal, approve: Bool) async throws -> DecisionOutcome {
        var body: [String: Any] = ["dispatch_id": proposal.id, "approve": approve]
        if approve { body["digest"] = proposal.digest }
        let data = try await owner("dispatch", method: "POST", credential: credential, body: try json(body),
                                   signer: signer, key: approve ? .approval : .device, deviceId: deviceId)
        return try decode(DecisionOutcome.self, data)
    }

    // —— 对话：App 是主要聊天的又一个入口（ADR-012）——

    public func chatHistory(_ credential: NativeCredential) async throws -> ChatHistory {
        try decode(ChatHistory.self, try await owner("chat", method: "GET", credential: credential))
    }

    /// 发一句话。`requestId` 由调用方生成并在重试时复用：服务端只起一个回合。
    public func chatSend(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                         text: String, requestId: String) async throws -> ChatTurn {
        let data = try await owner("chat/send", method: "POST", credential: credential,
                                   body: try json(["request_id": requestId, "text": text]),
                                   signer: signer, deviceId: deviceId)
        return try decode(ChatTurn.self, data)
    }

    public func chatTurn(_ credential: NativeCredential, rid: String) async throws -> ChatTurn {
        try decode(ChatTurn.self, try await owner("chat/turn", method: "GET", credential: credential,
                                                  query: [URLQueryItem(name: "rid", value: rid)]))
    }

    /// 批准 / 拒绝一张卡。高风险卡的批准用批准钥匙（Face ID），其余用日常钥匙；用哪把也签进请求体。
    public func chatDecide(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                           rid: String, card: ChatCard, approve: Bool, scope: String = "once",
                           beforeSend: (@Sendable () async throws -> Void)? = nil) async throws -> ChatCard {
        guard scope == "once" || (approve && scope == "turn" && card.canAllowTurn) else { throw OwnerWriteError.rejected("批准范围待核对") }
        let key: SigningKey = approve && (card.needsFaceID || scope == "turn") ? .approval : .device
        var body: [String: Any] = ["rid": rid, "card_id": card.id, "approve": approve, "digest": card.digest,
                                   "key_kind": key == .approval ? "approval" : "device"]
        if scope != "once" { body["scope"] = scope }
        let data = try await owner("chat/card", method: "POST", credential: credential, body: try json(body),
                                   signer: signer, key: key, deviceId: deviceId, beforeSend: beforeSend)
        return try decode(ChatCard.self, data)
    }

    // —— 任务小组与旁聊（ADR-017 M3）——

    public func teams(_ credential: NativeCredential) async throws -> [TeamSummary] {
        try decode(TeamList.self, try await owner("teams", method: "GET", credential: credential)).teams
    }

    public func team(_ credential: NativeCredential, id: String) async throws -> TeamChat {
        try decode(TeamChat.self, try await owner("team", method: "GET", credential: credential,
                                                  query: [URLQueryItem(name: "id", value: id)]))
    }

    /// 在小组群里说话（等你时就是回复）。用批准钥匙签：会叫醒小组接着干活，所以要 Face ID。
    public func teamMessage(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                            teamId: String, text: String) async throws -> TeamChat.Team {
        let data = try await owner("team/message", method: "POST", credential: credential,
                                   body: try json(["team_id": teamId, "text": text]),
                                   signer: signer, key: .approval, deviceId: deviceId)
        return try decode(TeamOpResult.self, data).team
    }

    /// 验收通过 / 退回（退回要写原因）。都用批准钥匙（Face ID）。
    public func teamDecide(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                           teamId: String, accept: Bool, note: String) async throws -> TeamChat.Team {
        let data = try await owner("team/decide", method: "POST", credential: credential,
                                   body: try json(["team_id": teamId, "accept": accept, "note": note]),
                                   signer: signer, key: .approval, deviceId: deviceId)
        return try decode(TeamOpResult.self, data).team
    }

    /// 停止小组：往安全的方向走，用日常钥匙。
    public func teamStop(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                         teamId: String) async throws -> TeamChat.Team {
        let data = try await owner("team/stop", method: "POST", credential: credential,
                                   body: try json(["team_id": teamId]), signer: signer, deviceId: deviceId)
        return try decode(TeamOpResult.self, data).team
    }

    public func sideChats(_ credential: NativeCredential) async throws -> [SideChatSummary] {
        try decode(SideChatList.self, try await owner("side-chats", method: "GET", credential: credential)).chats
    }

    public func sideChat(_ credential: NativeCredential, sid: String) async throws -> SideChatTranscript {
        try decode(SideChatTranscript.self, try await owner("side-chat", method: "GET", credential: credential,
                                                            query: [URLQueryItem(name: "sid", value: sid)]))
    }

    /// 登记推送：APNs 设备令牌（十六进制）和解密推送正文用的公钥。App 每次启动都调。
    public func registerPush(_ credential: NativeCredential, signer: RequestSigner, deviceId: String,
                             apnsToken: String, pushKey: String, environment: String) async throws {
        _ = try await owner("push", method: "POST", credential: credential,
                            body: try json(["apns_token": apnsToken, "push_key": pushKey, "environment": environment]),
                            signer: signer, deviceId: deviceId)
    }
}
