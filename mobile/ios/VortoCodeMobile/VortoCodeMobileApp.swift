import SwiftUI

@main
struct VortoCodeMobileApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    var body: some Scene {
        WindowGroup { HomeView() }
    }
}
