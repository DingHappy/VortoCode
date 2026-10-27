import Foundation

/// Presentation state is separate from the wire contract: an unavailable server
/// source cannot carry data, but the UI may retain an explicitly historical read.
public struct ReadSource<Value: Codable & Sendable>: Sendable {
    public enum Freshness: Sendable { case current, historical, unavailable }
    public private(set) var freshness: Freshness = .unavailable
    public private(set) var data: Value?
    public private(set) var checkedDate: Date?

    mutating func accept(_ source: Source<Value>) {
        if source.status == "current" {
            data = source.data
            checkedDate = source.checkedDate
            freshness = .current
        } else {
            markHistorical()
        }
    }

    mutating func markHistorical() {
        freshness = data == nil ? .unavailable : .historical
    }
}

public struct MobileReadState: Sendable {
    public private(set) var instanceId: String?
    public private(set) var profile = ReadSource<Profile>()
    public private(set) var mainChat = ReadSource<MainChat>()
    public private(set) var goals = ReadSource<Goals>()
    public var allCurrent: Bool {
        profile.freshness == .current && mainChat.freshness == .current && goals.freshness == .current
    }
    public init() {}

    public mutating func accept(_ snapshot: MobileSnapshot, instance: String) throws {
        // Validate before any mutation; never mix retained records between assistants.
        try snapshot.validate(instance: instance)
        if instanceId != snapshot.instanceId { self = MobileReadState() }
        instanceId = snapshot.instanceId
        profile.accept(snapshot.sources.profile)
        mainChat.accept(snapshot.sources.mainChat)
        goals.accept(snapshot.sources.goals)
    }

    public mutating func markHistorical() {
        profile.markHistorical()
        mainChat.markHistorical()
        goals.markHistorical()
    }
}
