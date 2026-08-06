import SwiftUI

@main
struct CaddieInsightApp: App {
    @State private var session = AppSession()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .tint(Brand.green)
        }
    }
}
