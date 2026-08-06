import Foundation
import Observation

/// Pairing state for the app: which server we talk to and the device token
/// that authenticates us there. The token lives in the Keychain; the server
/// address and account email are ordinary defaults.
///
/// Device tokens are minted on the website (Account → Mobile app) because
/// token issue/revoke is deliberately a cookie-only surface server-side — a
/// stolen device token can never mint more tokens.
@Observable
@MainActor
final class AppSession {
    private static let tokenAccount = "mobile-device-token"
    private static let serverKey = "server_url"
    private static let emailKey = "account_email"

    private(set) var client: APIClient?
    private(set) var accountEmail: String?
    /// Set when the server rejects our token so the connect screen can say why.
    var disconnectReason: String?

    var serverURLString: String {
        UserDefaults.standard.string(forKey: Self.serverKey) ?? ""
    }

    var isConnected: Bool { client != nil }

    init() {
        if let raw = UserDefaults.standard.string(forKey: Self.serverKey),
           let url = Self.normalizedServerURL(raw),
           let token = Keychain.load(account: Self.tokenAccount) {
            client = APIClient(baseURL: url, token: token)
            accountEmail = UserDefaults.standard.string(forKey: Self.emailKey)
        }
    }

    /// Accepts "caddieinsight.example.com" or a full https URL; rejects
    /// anything that is not http(s) with a host.
    static func normalizedServerURL(_ raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") {
            text = "https://" + text
        }
        guard let components = URLComponents(string: text),
              let scheme = components.scheme?.lowercased(),
              scheme == "https" || scheme == "http",
              let host = components.host, !host.isEmpty else {
            return nil
        }
        var trimmed = components
        trimmed.path = trimmed.path == "/" ? "" : trimmed.path
        return trimmed.url
    }

    /// Validates the pair against `/api/v1/me` before storing anything.
    func connect(server: String, token: String) async throws {
        guard let url = Self.normalizedServerURL(server) else {
            throw APIError.invalidServerURL
        }
        let cleanToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanToken.isEmpty, !cleanToken.contains(" ") else {
            throw APIError.unauthorized
        }
        let candidate = APIClient(baseURL: url, token: cleanToken)
        let me = try await candidate.me()

        Keychain.store(cleanToken, account: Self.tokenAccount)
        UserDefaults.standard.set(url.absoluteString, forKey: Self.serverKey)
        UserDefaults.standard.set(me.identity.email, forKey: Self.emailKey)
        accountEmail = me.identity.email
        disconnectReason = nil
        client = candidate
    }

    func disconnect(reason: String? = nil) {
        Keychain.delete(account: Self.tokenAccount)
        UserDefaults.standard.removeObject(forKey: Self.emailKey)
        client = nil
        accountEmail = nil
        disconnectReason = reason
    }

    /// Central handling for a rejected token: fall back to the connect
    /// screen with an explanation instead of failing every tab separately.
    func handle(_ error: Error) {
        if case APIError.unauthorized = error {
            disconnect(
                reason: "This device's access was revoked or expired. Add the device again from your account page on the website, then reconnect."
            )
        }
    }
}
