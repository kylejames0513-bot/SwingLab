import SwiftUI

/// One analysis session: live progress while the server works, then the
/// outcome — Caddie Brief and the full report when coaching is ready, or
/// an honest re-film explanation when it isn't.
struct SessionDetailView: View {
    @Environment(AppSession.self) private var session
    let sessionID: String

    @State private var detail: SwingSession?
    @State private var brief: CaddieBrief?
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let errorMessage, detail == nil {
                    ErrorBanner(message: errorMessage) { Task { await refresh() } }
                } else if let detail {
                    header(detail)
                    switch detail.phase {
                    case .queued, .processing, .unknown:
                        progressCard(detail)
                    case .done:
                        outcome(detail)
                    case .failed:
                        failureCard(detail)
                    }
                } else {
                    ProgressView("Loading session…")
                        .frame(maxWidth: .infinity)
                        .padding(.top, 60)
                }
            }
            .padding()
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Session")
        .navigationBarTitleDisplayMode(.inline)
        .task { await pollWhileActive() }
        .refreshable { await refresh() }
    }

    // MARK: Pieces

    private func header(_ detail: SwingSession) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(detail.sourceName ?? "Swing session")
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                StatusBadge(session: detail)
            }
            HStack {
                ContextChips(session: detail)
                Spacer()
                if let date = detail.createdDate {
                    Text(date, format: .dateTime.month(.abbreviated).day().hour().minute())
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .brandCard()
    }

    private func progressCard(_ detail: SwingSession) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Eyebrow(text: detail.phase == .queued ? "In line" : "Analyzing")
            if detail.phase == .queued {
                if let position = detail.queuePosition, position > 0 {
                    Text("Waiting behind \(position) other \(position == 1 ? "analysis" : "analyses").")
                        .font(.subheadline)
                } else {
                    Text("Your clip is next — analysis starts any moment.")
                        .font(.subheadline)
                }
            } else {
                if detail.swingsTotal > 0 {
                    ProgressView(
                        value: Double(detail.swingsDone),
                        total: Double(max(detail.swingsTotal, 1))
                    )
                    Text("Swing \(min(detail.swingsDone + 1, detail.swingsTotal)) of \(detail.swingsTotal)")
                        .font(.subheadline)
                } else {
                    ProgressView()
                    Text("Finding your swings…")
                        .font(.subheadline)
                }
            }
            if let last = detail.log.last {
                Text(last)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Text("This screen refreshes on its own — you can leave and come back.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .brandCard()
    }

    @ViewBuilder
    private func outcome(_ detail: SwingSession) -> some View {
        if detail.refilmRequired {
            VStack(alignment: .leading, spacing: 10) {
                Eyebrow(text: "Re-film needed")
                Text("This clip couldn't be read well enough for trustworthy coaching, so no numbers are being guessed at.")
                    .font(.subheadline)
                Text("Film again with your whole body and club in frame, steady phone at hip height, and sound on — the re-film doesn't count against your monthly allowance when it matches this session's club and angle.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .brandCard()
        } else if let brief {
            CaddieBriefCard(brief: brief)
        }

        if detail.swingsTotal > 0 {
            VStack(alignment: .leading, spacing: 6) {
                Eyebrow(text: "Swings")
                Text("\(detail.swingsTotal) \(detail.swingsTotal == 1 ? "swing" : "swings") analyzed")
                    .font(.subheadline)
            }
            .brandCard()
        }

        if let reportPath = detail.reportUrl {
            NavigationLink {
                ReportView(reportPath: reportPath)
            } label: {
                Label("Open the full report", systemImage: "doc.richtext")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
        }

        if let errorMessage {
            Text(errorMessage)
                .font(.footnote)
                .foregroundStyle(.red)
        }
    }

    private func failureCard(_ detail: SwingSession) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Eyebrow(text: "Analysis failed")
            Text(detail.error ?? "Something went wrong while analyzing this clip.")
                .font(.subheadline)
            Text("This one didn't count against your allowance. Try uploading the clip again — or a fresh one.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .brandCard()
    }

    // MARK: Data

    private func refresh() async {
        guard let client = session.client else { return }
        do {
            let updated = try await client.session(id: sessionID)
            detail = updated
            errorMessage = nil
            if updated.phase == .done, updated.coachingReady, brief == nil {
                brief = try? await client.brief(sessionID: sessionID)
            }
        } catch {
            session.handle(error)
            errorMessage = (error as? LocalizedError)?.errorDescription
                ?? error.localizedDescription
        }
    }

    /// Poll every 2.5s while the job is queued/processing; stop as soon as
    /// it settles (or the view goes away — the task is view-scoped).
    private func pollWhileActive() async {
        await refresh()
        while !Task.isCancelled, detail?.isActive ?? false {
            try? await Task.sleep(for: .seconds(2.5))
            guard !Task.isCancelled else { return }
            await refresh()
        }
    }
}
