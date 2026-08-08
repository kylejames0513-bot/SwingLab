import SwiftUI

struct RootView: View {
    @Environment(AppSession.self) private var session

    var body: some View {
        if session.isConnected {
            MainTabView()
        } else {
            ConnectView()
        }
    }
}

struct MainTabView: View {
    @State private var selectedTab: Tab = .today

    enum Tab: Hashable {
        case today, analyze, sessions, account
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            TodayView(switchToAnalyze: { selectedTab = .analyze })
                .tabItem { Label("Today", systemImage: "figure.golf") }
                .tag(Tab.today)

            UploadView()
                .tabItem { Label("Analyze", systemImage: "video.badge.plus") }
                .tag(Tab.analyze)

            SessionsView()
                .tabItem { Label("Sessions", systemImage: "list.bullet.rectangle") }
                .tag(Tab.sessions)

            AccountView()
                .tabItem { Label("Account", systemImage: "person.crop.circle") }
                .tag(Tab.account)
        }
    }
}

// MARK: - Small shared pieces

struct StatusBadge: View {
    let session: SwingSession

    private var descriptor: (text: String, color: Color) {
        switch session.phase {
        case .queued:
            ("Queued", .secondary)
        case .processing:
            ("Analyzing", Brand.orange)
        case .done:
            session.coachingReady
                ? ("Coaching ready", Brand.green)
                : ("Re-film needed", Brand.orange)
        case .failed:
            ("Failed", .red)
        case .unknown:
            (self.session.status.capitalized, .secondary)
        }
    }

    var body: some View {
        Text(descriptor.text)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(descriptor.color.opacity(0.14))
            .foregroundStyle(descriptor.color)
            .clipShape(Capsule())
    }
}

struct ContextChips: View {
    let session: SwingSession

    private var chips: [String] {
        [
            GolfOptions.clubLabel(session.club),
            GolfOptions.angleLabel(session.angle),
            session.hand == "left" ? "Left-handed" : "Right-handed",
        ].compactMap { $0 }
    }

    var body: some View {
        HStack(spacing: 6) {
            ForEach(chips, id: \.self) { chip in
                Text(chip)
                    .font(.caption2)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color(.tertiarySystemFill))
                    .clipShape(Capsule())
            }
        }
    }
}

struct ErrorBanner: View {
    let message: String
    var retry: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(.subheadline)
                .foregroundStyle(.red)
            if let retry {
                Button("Try again", action: retry)
                    .font(.subheadline.weight(.semibold))
            }
        }
        .brandCard()
    }
}
