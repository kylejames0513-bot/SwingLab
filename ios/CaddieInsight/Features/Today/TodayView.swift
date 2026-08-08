import SwiftUI

/// The home tab: latest session state, the Caddie Brief, the current
/// practice plan, and the one-tap practice check-in — a native rendering
/// of `GET /api/v1/today`.
struct TodayView: View {
    @Environment(AppSession.self) private var session
    let switchToAnalyze: () -> Void

    @State private var today: TodayResponse?
    @State private var loading = false
    @State private var errorMessage: String?
    @State private var checkinBusy = false
    @State private var checkinDone = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let errorMessage {
                        ErrorBanner(message: errorMessage) { Task { await load() } }
                    } else if let today {
                        content(today)
                    } else if loading {
                        ProgressView("Fetching your caddie…")
                            .frame(maxWidth: .infinity)
                            .padding(.top, 60)
                    }
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle(greeting)
            .refreshable { await load() }
            .task { await load() }
            .navigationDestination(for: SwingSession.self) { item in
                SessionDetailView(sessionID: item.id)
            }
        }
    }

    private var greeting: String {
        if let name = today?.profile?.displayName, !name.isEmpty {
            return "Hi, \(name)"
        }
        return "Today"
    }

    @ViewBuilder
    private func content(_ today: TodayResponse) -> some View {
        if let latest = today.latestSession {
            NavigationLink(value: latest) {
                latestSessionCard(latest)
            }
            .buttonStyle(.plain)

            if let brief = today.caddieBrief {
                CaddieBriefCard(brief: brief)
            }

            if !today.practicePlan.isEmpty {
                practicePlanCard(today.practicePlan)
            }

            if latest.phase == .done, latest.coachingReady, today.caddieBrief != nil {
                checkinCard(latest, alreadyCheckedIn: today.practiceCheckedIn || checkinDone)
            }
        } else {
            emptyState
        }
    }

    private func latestSessionCard(_ latest: SwingSession) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Eyebrow(text: "Latest session")
                Spacer()
                StatusBadge(session: latest)
            }
            Text(latest.sourceName ?? "Swing session")
                .font(.headline)
                .lineLimit(1)
            HStack {
                ContextChips(session: latest)
                Spacer()
                if let date = latest.createdDate {
                    Text(date, style: .relative)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if latest.isActive {
                Text("Analysis in progress — open the session to watch it move.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .brandCard()
    }

    private func practicePlanCard(_ plan: [PracticePlanChoice]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Eyebrow(text: "Practice plan")
            if let drill = plan.first {
                Text(drill.drillName)
                    .font(.headline)
                Text(drill.aim)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            ForEach(plan) { choice in
                HStack(alignment: .top, spacing: 10) {
                    Text("\(choice.minutes)m")
                        .font(.subheadline.bold())
                        .frame(width: 44, height: 30)
                        .background(
                            choice.selected
                                ? Brand.green.opacity(0.16)
                                : Color(.tertiarySystemFill)
                        )
                        .foregroundStyle(choice.selected ? Brand.green : .secondary)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(choice.title)
                            .font(.subheadline.weight(.semibold))
                        Text(choice.detail)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            if let drill = plan.first {
                Label("Pass mark: \(drill.passMark)", systemImage: "checkmark.seal")
                    .font(.footnote)
                    .foregroundStyle(Brand.green)
            }
        }
        .brandCard()
    }

    private func checkinCard(_ latest: SwingSession, alreadyCheckedIn: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Eyebrow(text: "Practice check-in")
            if alreadyCheckedIn {
                Label("Practice logged for this session. Nice work — film a fresh clip when you're ready to measure the change.", systemImage: "checkmark.circle.fill")
                    .font(.subheadline)
                    .foregroundStyle(Brand.green)
            } else {
                Text("Did the drill? Log it so your caddie can hold you to the pass mark next session.")
                    .font(.subheadline)
                Button {
                    checkIn(sessionID: latest.id)
                } label: {
                    if checkinBusy {
                        ProgressView()
                    } else {
                        Text("I practiced this")
                            .font(.subheadline.weight(.semibold))
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(checkinBusy)
            }
        }
        .brandCard()
    }

    private var emptyState: some View {
        VStack(spacing: 14) {
            Image(systemName: "figure.golf")
                .font(.system(size: 44))
                .foregroundStyle(Brand.green)
            Text("No sessions yet")
                .font(.headline)
            Text("Film yourself hitting a few balls — phone at hip height, face-on, whole body and club in frame — then upload the clip for your first coaching report.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Analyze a swing video", action: switchToAnalyze)
                .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    private func load() async {
        guard let client = session.client else { return }
        loading = true
        defer { loading = false }
        do {
            today = try await client.today()
            errorMessage = nil
            checkinDone = false
        } catch {
            session.handle(error)
            if today == nil {
                errorMessage = (error as? LocalizedError)?.errorDescription
                    ?? error.localizedDescription
            }
        }
    }

    private func checkIn(sessionID: String) {
        guard let client = session.client else { return }
        checkinBusy = true
        Task {
            defer { checkinBusy = false }
            do {
                try await client.recordPracticeCheckin(sessionID: sessionID)
                checkinDone = true
            } catch {
                session.handle(error)
                errorMessage = (error as? LocalizedError)?.errorDescription
                    ?? error.localizedDescription
            }
        }
    }
}

/// The Caddie Brief: one focus, why it matters, one cue, one drill.
struct CaddieBriefCard: View {
    let brief: CaddieBrief

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Eyebrow(text: "Caddie brief")

            if let warning = brief.warning {
                Label(warning, systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(Brand.orange)
            }

            if let name = brief.focus.name {
                VStack(alignment: .leading, spacing: 4) {
                    Text(name)
                        .font(.title3.bold())
                        .foregroundStyle(Brand.green)
                    HStack(spacing: 12) {
                        if let value = brief.focus.value {
                            Text(value)
                                .font(.subheadline.weight(.semibold))
                        }
                        if let benchmark = brief.focus.benchmark {
                            Text(benchmark)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            if let why = brief.focus.why {
                Text(why)
                    .font(.subheadline)
            }

            if let cue = brief.focus.cue {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "quote.opening")
                        .foregroundStyle(Brand.orange)
                    Text(cue)
                        .font(.subheadline.italic())
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Brand.orange.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }

            if let drill = brief.drill {
                Divider()
                VStack(alignment: .leading, spacing: 4) {
                    Text(drill.name)
                        .font(.subheadline.weight(.semibold))
                    Text(drill.dosage)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            if let trend = brief.trend {
                Label(trend, systemImage: "chart.line.uptrend.xyaxis")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            if brief.recurringSessions > 1 {
                Text("This focus has come up \(brief.recurringSessions) sessions in a row.")
                    .font(.footnote)
                    .foregroundStyle(Brand.orange)
            }
        }
        .brandCard()
    }
}
