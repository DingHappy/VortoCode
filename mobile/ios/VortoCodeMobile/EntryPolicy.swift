import MobileCore

enum EntryPolicy {
    static var allowsLoopback: Bool {
        #if DEBUG && targetEnvironment(simulator)
        true
        #else
        false
        #endif
    }
    static func parse(_ raw: String) throws -> AssistantEntry {
        try AssistantEntry(raw, allowLoopback: allowsLoopback)
    }
}
