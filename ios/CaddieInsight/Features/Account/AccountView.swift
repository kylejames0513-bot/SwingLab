import SwiftUI

/// Account tab: golfer profile editing (mirrors the web onboarding
/// vocabulary exactly), plus device disconnect. Billing and history
/// deletion stay on the website by design.
struct AccountView: View {
    @Environment(AppSession.self) private var session

    @State private var displayName = ""
    @State private var experienceMode = "improve"
    @State private var handicapRange = "prefer_not_to_say"
    @State private var primaryGoal = "consistency"
    @State private var practiceMinutes = 20
    @State private var sessionsPerWeek = 2
    @State private var handedness = "right"
    @State private var cameraAngle = "face-on"
    @State private var preferredClub = "iron"
    @State private var reducedMotion = false
    @State private var marketingOptIn = false

    @State private var loaded = false
    @State private var saving = false
    @State private var savedFlash = false
    @State private var errorMessage: String?
    @State private var confirmDisconnect = false

    var body: some View {
        NavigationStack {
            Form {
                identitySection
                profileSection
                preferencesSection
                saveSection
                webSection
                disconnectSection
                aboutSection
            }
            .navigationTitle("Account")
            .task { await load() }
        }
    }

    // MARK: Sections

    private var identitySection: some View {
        Section("Signed in as") {
            LabeledContent("Email", value: session.accountEmail ?? "—")
        }
    }

    private var profileSection: some View {
        Section("Golfer profile") {
            TextField("Caddie calls you (optional)", text: $displayName)
            Picker("Where you are", selection: $experienceMode) {
                ForEach(GolfOptions.experienceModes) { Text($0.label).tag($0.key) }
            }
            Picker("Handicap", selection: $handicapRange) {
                ForEach(GolfOptions.handicapRanges) { Text($0.label).tag($0.key) }
            }
            Picker("Main goal", selection: $primaryGoal) {
                ForEach(GolfOptions.primaryGoals) { Text($0.label).tag($0.key) }
            }
            Picker("Practice block", selection: $practiceMinutes) {
                ForEach(GolfOptions.practiceMinutes, id: \.self) { Text("\($0) minutes").tag($0) }
            }
            Picker("Sessions a week", selection: $sessionsPerWeek) {
                ForEach(GolfOptions.sessionsPerWeek, id: \.self) { Text("\($0)").tag($0) }
            }
            Picker("Handedness", selection: $handedness) {
                ForEach(GolfOptions.hands) { Text($0.label).tag($0.key) }
            }
            Picker("Usual camera angle", selection: $cameraAngle) {
                ForEach(GolfOptions.angles) { Text($0.label).tag($0.key) }
            }
            Picker("Usual club", selection: $preferredClub) {
                ForEach(GolfOptions.clubs) { Text($0.label).tag($0.key) }
            }
        }
    }

    private var preferencesSection: some View {
        Section {
            Toggle("Reduce motion in reports", isOn: $reducedMotion)
            Toggle("Product news by email", isOn: $marketingOptIn)
        }
    }

    private var saveSection: some View {
        Section {
            Button {
                save()
            } label: {
                if saving {
                    ProgressView().frame(maxWidth: .infinity)
                } else if savedFlash {
                    Label("Saved", systemImage: "checkmark")
                        .frame(maxWidth: .infinity)
                } else {
                    Text("Save profile").frame(maxWidth: .infinity)
                }
            }
            .disabled(saving || !loaded)
            if let errorMessage {
                Text(errorMessage)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
    }

    private var webSection: some View {
        Section {
            if let base = session.client?.baseURL {
                Link(destination: base.appending(path: "/account")) {
                    Label("Membership, billing & devices", systemImage: "safari")
                }
                Link(destination: base.appending(path: "/progress")) {
                    Label("Progress dashboard", systemImage: "chart.line.uptrend.xyaxis")
                }
            }
        } footer: {
            Text("Membership changes, device tokens, and history deletion live on the website.")
        }
    }

    private var disconnectSection: some View {
        Section {
            Button("Disconnect this iPhone", role: .destructive) {
                confirmDisconnect = true
            }
            .confirmationDialog(
                "Disconnect this iPhone?",
                isPresented: $confirmDisconnect,
                titleVisibility: .visible
            ) {
                Button("Disconnect", role: .destructive) {
                    session.disconnect()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The token is removed from this iPhone. To fully revoke it, also remove the device on the website's account page.")
            }
        }
    }

    private var aboutSection: some View {
        Section {
            LabeledContent(
                "Version",
                value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
            )
        } footer: {
            Text("CaddieInsight measures what a phone camera can genuinely see, and says so when it can't.")
        }
    }

    // MARK: Data

    private func load() async {
        guard !loaded, let client = session.client else { return }
        do {
            let me = try await client.me()
            if let profile = me.profile {
                displayName = profile.displayName ?? ""
                experienceMode = profile.experienceMode
                handicapRange = profile.handicapRange
                primaryGoal = profile.primaryGoal
                practiceMinutes = profile.practiceMinutes
                sessionsPerWeek = profile.sessionsPerWeek
                handedness = profile.handedness
                cameraAngle = profile.cameraAngle
                preferredClub = profile.preferredClub
                reducedMotion = profile.reducedMotion
                marketingOptIn = profile.marketingEmailOptIn
            }
            loaded = true
        } catch {
            session.handle(error)
            errorMessage = (error as? LocalizedError)?.errorDescription
                ?? error.localizedDescription
            loaded = true
        }
    }

    private func save() {
        guard let client = session.client else { return }
        saving = true
        errorMessage = nil
        let trimmedName = displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        let update = GolferProfileUpdate(
            displayName: trimmedName.isEmpty ? nil : trimmedName,
            experienceMode: experienceMode,
            handicapRange: handicapRange,
            primaryGoal: primaryGoal,
            practiceMinutes: practiceMinutes,
            sessionsPerWeek: sessionsPerWeek,
            handedness: handedness,
            cameraAngle: cameraAngle,
            preferredClub: preferredClub,
            reducedMotion: reducedMotion,
            marketingEmailOptIn: marketingOptIn
        )
        Task {
            defer { saving = false }
            do {
                try await client.updateProfile(update)
                savedFlash = true
                try? await Task.sleep(for: .seconds(2))
                savedFlash = false
            } catch {
                session.handle(error)
                errorMessage = (error as? LocalizedError)?.errorDescription
                    ?? error.localizedDescription
            }
        }
    }
}
