import SwiftUI

/// Pairing screen: the golfer signs in on the website, mints a device
/// token under Account → Mobile app, and pastes it here together with the
/// server address. Nothing is stored until `/api/v1/me` accepts the pair.
struct ConnectView: View {
    @Environment(AppSession.self) private var session

    @State private var server: String = ""
    @State private var token: String = ""
    @State private var connecting = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header

                    if let reason = session.disconnectReason {
                        Label(reason, systemImage: "info.circle.fill")
                            .font(.subheadline)
                            .foregroundStyle(Brand.orange)
                            .brandCard()
                    }

                    steps

                    VStack(alignment: .leading, spacing: 12) {
                        Eyebrow(text: "Connect this iPhone")

                        TextField("Server address (https://…)", text: $server)
                            .textContentType(.URL)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .padding(12)
                            .background(Color(.tertiarySystemFill))
                            .clipShape(RoundedRectangle(cornerRadius: 10))

                        SecureField("Device token", text: $token)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .padding(12)
                            .background(Color(.tertiarySystemFill))
                            .clipShape(RoundedRectangle(cornerRadius: 10))

                        if let errorMessage {
                            Text(errorMessage)
                                .font(.footnote)
                                .foregroundStyle(.red)
                        }

                        Button(action: connect) {
                            if connecting {
                                ProgressView()
                                    .frame(maxWidth: .infinity)
                            } else {
                                Text("Connect")
                                    .font(.headline)
                                    .frame(maxWidth: .infinity)
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(connecting || server.isEmpty || token.isEmpty)
                    }
                    .brandCard()

                    Text("Your token is stored only in this iPhone's Keychain. You can revoke it any time from the same account page.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Welcome")
            .onAppear {
                if server.isEmpty {
                    server = session.serverURLString
                }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(Brand.productName)
                .font(.largeTitle.bold())
                .foregroundStyle(Brand.green)
            Text("Swing analysis and a one-decision caddie brief from a single phone video.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private var steps: some View {
        VStack(alignment: .leading, spacing: 10) {
            Eyebrow(text: "Get a device token")
            step(1, "Log in to your CaddieInsight account in any browser.")
            step(2, "Open Account and find the “Mobile app” section.")
            step(3, "Tap “Add this device”, name it, and copy the token it shows once.")
            step(4, "Paste the token below along with your server address.")
        }
        .brandCard()
    }

    private func step(_ number: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text("\(number)")
                .font(.caption.bold())
                .frame(width: 22, height: 22)
                .background(Brand.green.opacity(0.14))
                .foregroundStyle(Brand.green)
                .clipShape(Circle())
            Text(text)
                .font(.subheadline)
        }
    }

    private func connect() {
        connecting = true
        errorMessage = nil
        let server = server
        let token = token
        Task {
            defer { connecting = false }
            do {
                try await session.connect(server: server, token: token)
            } catch {
                errorMessage = (error as? LocalizedError)?.errorDescription
                    ?? error.localizedDescription
            }
        }
    }
}
