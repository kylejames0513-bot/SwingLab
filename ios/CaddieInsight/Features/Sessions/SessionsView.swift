import SwiftUI

/// Session history: everything the account has analyzed, newest first.
struct SessionsView: View {
    @Environment(AppSession.self) private var session

    @State private var sessions: [SwingSession] = []
    @State private var loaded = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Group {
                if let errorMessage, sessions.isEmpty {
                    ScrollView {
                        ErrorBanner(message: errorMessage) { Task { await load() } }
                            .padding()
                    }
                } else if loaded && sessions.isEmpty {
                    ContentUnavailableView(
                        "No sessions yet",
                        systemImage: "list.bullet.rectangle",
                        description: Text("Upload a swing video from the Analyze tab — every analysis lands here.")
                    )
                } else {
                    List(sessions) { item in
                        NavigationLink(value: item) {
                            row(item)
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .navigationTitle("Sessions")
            .navigationDestination(for: SwingSession.self) { item in
                SessionDetailView(sessionID: item.id)
            }
            .refreshable { await load() }
            .task { await load() }
        }
    }

    private func row(_ item: SwingSession) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item.sourceName ?? "Swing session")
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                Spacer()
                StatusBadge(session: item)
            }
            HStack {
                ContextChips(session: item)
                Spacer()
                if let date = item.createdDate {
                    Text(date, format: .dateTime.month(.abbreviated).day().hour().minute())
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if item.swingsTotal > 0 {
                Text("\(item.swingsDone)/\(item.swingsTotal) swings analyzed")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private func load() async {
        guard let client = session.client else { return }
        do {
            sessions = try await client.sessions()
            errorMessage = nil
        } catch {
            session.handle(error)
            errorMessage = (error as? LocalizedError)?.errorDescription
                ?? error.localizedDescription
        }
        loaded = true
    }
}
