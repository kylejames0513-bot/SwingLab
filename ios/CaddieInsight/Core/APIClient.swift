import Foundation

enum APIError: LocalizedError {
    case invalidServerURL
    case unauthorized
    case server(status: Int, message: String)
    case transport(Error)
    case decoding(Error)

    var errorDescription: String? {
        switch self {
        case .invalidServerURL:
            "That server address doesn't look right. Enter the full https:// address of your CaddieInsight service."
        case .unauthorized:
            "This device's access token was rejected. Add the device again from your account page and reconnect."
        case .server(_, let message):
            message
        case .transport(let error):
            error.localizedDescription
        case .decoding:
            "The server sent a response this app version doesn't understand. Check that the app and server are up to date."
        }
    }
}

/// Bearer-token client for the server's stable mobile API (`/api/v1/*`).
/// One instance is bound to one paired server + device token.
final class APIClient: Sendable {
    let baseURL: URL
    let token: String
    private let session: URLSession

    init(baseURL: URL, token: String) {
        self.baseURL = baseURL
        self.token = token
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 30
        configuration.waitsForConnectivity = true
        session = URLSession(configuration: configuration)
    }

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    // MARK: Requests

    private func request(path: String, method: String = "GET", body: Data? = nil) -> URLRequest {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = method
        request.httpBody = body
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.transport(URLError(.badServerResponse))
        }
        switch http.statusCode {
        case 200...299:
            do {
                return try Self.decoder.decode(T.self, from: data)
            } catch {
                throw APIError.decoding(error)
            }
        case 401:
            throw APIError.unauthorized
        default:
            throw APIError.server(
                status: http.statusCode,
                message: Self.serverMessage(from: data, status: http.statusCode)
            )
        }
    }

    /// FastAPI error bodies are `{"detail": "human-readable reason"}`.
    static func serverMessage(from data: Data, status: Int) -> String {
        if let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = payload["detail"] as? String, !detail.isEmpty {
            return detail
        }
        return "The server replied with an error (HTTP \(status))."
    }

    // MARK: Envelopes

    private struct SessionsEnvelope: Decodable { let sessions: [SwingSession] }
    private struct BriefEnvelope: Decodable { let caddieBrief: CaddieBrief }
    private struct CheckinsEnvelope: Decodable { let checkins: [PracticeCheckin] }
    private struct CheckinEnvelope: Decodable { let checkin: PracticeCheckin }
    private struct ProfileEnvelope: Decodable { let profile: GolferProfile? }

    // MARK: API surface

    func me() async throws -> MeResponse {
        try await send(request(path: "/api/v1/me"), as: MeResponse.self)
    }

    func today() async throws -> TodayResponse {
        try await send(request(path: "/api/v1/today"), as: TodayResponse.self)
    }

    func sessions() async throws -> [SwingSession] {
        try await send(request(path: "/api/v1/sessions"), as: SessionsEnvelope.self).sessions
    }

    func session(id: String) async throws -> SwingSession {
        try await send(request(path: "/api/v1/sessions/\(id)"), as: SwingSession.self)
    }

    func brief(sessionID: String) async throws -> CaddieBrief {
        try await send(
            request(path: "/api/v1/sessions/\(sessionID)/brief"),
            as: BriefEnvelope.self
        ).caddieBrief
    }

    func practiceCheckins() async throws -> [PracticeCheckin] {
        try await send(
            request(path: "/api/v1/practice-checkins"),
            as: CheckinsEnvelope.self
        ).checkins
    }

    @discardableResult
    func recordPracticeCheckin(sessionID: String) async throws -> PracticeCheckin {
        let body = try JSONEncoder().encode(["session_id": sessionID])
        return try await send(
            request(path: "/api/v1/practice-checkins", method: "POST", body: body),
            as: CheckinEnvelope.self
        ).checkin
    }

    @discardableResult
    func updateProfile(_ update: GolferProfileUpdate) async throws -> GolferProfile? {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let body = try encoder.encode(update)
        return try await send(
            request(path: "/api/v1/profile", method: "PUT", body: body),
            as: ProfileEnvelope.self
        ).profile
    }
}
