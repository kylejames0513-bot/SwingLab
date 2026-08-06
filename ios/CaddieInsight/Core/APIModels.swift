import Foundation

// Codable mirrors of the server's stable mobile payloads
// (`/api/v1/*` in `swinglab/web/app.py`). Snake-case JSON keys are
// converted by the client's decoder, so property names here are the
// camel-case spellings of the server fields. Fields the app does not
// use are deliberately omitted — unknown JSON keys are ignored.

struct MeResponse: Decodable {
    let identity: Identity
    let profile: GolferProfile?
}

struct Identity: Decodable {
    let id: String
    let email: String
    let emailVerified: Bool
}

struct GolferProfile: Codable, Equatable {
    var displayName: String?
    var experienceMode: String
    var handicapRange: String
    var primaryGoal: String
    var practiceMinutes: Int
    var sessionsPerWeek: Int
    var handedness: String
    var cameraAngle: String
    var preferredClub: String
    var reducedMotion: Bool
    var marketingEmailOptIn: Bool
    var isComplete: Bool
}

/// The complete profile document `PUT /api/v1/profile` requires — every
/// field is mandatory on the wire except `displayName`.
struct GolferProfileUpdate: Encodable {
    var displayName: String?
    var experienceMode: String
    var handicapRange: String
    var primaryGoal: String
    var practiceMinutes: Int
    var sessionsPerWeek: Int
    var handedness: String
    var cameraAngle: String
    var preferredClub: String
    var reducedMotion: Bool
    var marketingEmailOptIn: Bool
}

struct SwingSession: Decodable, Identifiable, Hashable {
    let id: String
    let status: String
    let createdAt: String
    let sourceName: String?
    let hand: String
    let angle: String
    let club: String?
    let level: String?
    let fast: Bool
    let log: [String]
    let error: String?
    let report: String?
    let swingsDone: Int
    let swingsTotal: Int
    let queuePosition: Int?
    let coachingEligible: Bool?
    let outcome: String?
    let reportUrl: String?
    let metricsUrl: String?

    enum Phase {
        case queued, processing, done, failed, unknown
    }

    var phase: Phase {
        switch status {
        case "queued": .queued
        case "processing": .processing
        case "done": .done
        case "failed": .failed
        default: .unknown
        }
    }

    var isActive: Bool { phase == .queued || phase == .processing }
    var coachingReady: Bool { outcome == "coaching_ready" }
    var refilmRequired: Bool { outcome == "refilm_required" }

    var createdDate: Date? { ISODates.parse(createdAt) }
}

struct CaddieBrief: Decodable, Equatable {
    struct Focus: Decodable, Equatable {
        let key: String?
        let name: String?
        let value: String?
        let benchmark: String?
        let why: String?
        let cue: String?
    }

    struct Drill: Decodable, Equatable {
        let id: String
        let name: String
        let aim: String
        let dosage: String
        let passMark: String
    }

    let focus: Focus
    let drill: Drill?
    let trend: String?
    let warning: String?
    let refilmRequired: Bool
    let recurringSessions: Int
    let remainingIssues: Int
}

struct PracticePlanChoice: Decodable, Identifiable, Equatable {
    let minutes: Int
    let title: String
    let detail: String
    let selected: Bool
    let drillName: String
    let aim: String
    let dosage: String
    let passMark: String

    var id: Int { minutes }
}

struct TodayResponse: Decodable {
    let profile: GolferProfile?
    let latestSession: SwingSession?
    let caddieBrief: CaddieBrief?
    let practicePlan: [PracticePlanChoice]
    let practiceCheckedIn: Bool
}

struct PracticeCheckin: Decodable, Equatable {
    let sessionId: String
    let completedAt: Double
}

struct UploadResponse: Decodable {
    let id: String
    let url: String
}

/// The server emits Python `datetime.isoformat()` strings — RFC 3339 with
/// six fractional digits (e.g. `2026-08-06T12:34:56.789012+00:00`), which
/// `ISO8601DateFormatter` rejects. Parse with the fractional variant first,
/// then retry with the fraction stripped.
enum ISODates {
    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let plain: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func parse(_ raw: String) -> Date? {
        if let date = plain.date(from: raw) { return date }
        if let date = fractional.date(from: raw) { return date }
        // Trim a long fraction down to milliseconds for the formatter.
        if let dotIndex = raw.firstIndex(of: ".") {
            let tail = raw[raw.index(after: dotIndex)...]
            if let endOfFraction = tail.firstIndex(where: { !$0.isNumber }) {
                let digits = tail[..<endOfFraction].prefix(3)
                let rebuilt = raw[..<dotIndex] + "." + digits + raw[endOfFraction...]
                return fractional.date(from: String(rebuilt))
            }
        }
        return nil
    }
}
