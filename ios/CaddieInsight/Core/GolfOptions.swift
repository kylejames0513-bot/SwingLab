import Foundation

/// Picker vocabularies mirrored from the server's validation sets
/// (`swinglab/clubs.py`, `swinglab/levels.py`,
/// `swinglab/web/users.py` GOLFER_* constants). Keys are what goes on the
/// wire; labels are what the golfer reads.
enum GolfOptions {
    struct Option: Identifiable, Hashable {
        let key: String
        let label: String
        var id: String { key }
    }

    static let clubs: [Option] = [
        .init(key: "driver", label: "Driver"),
        .init(key: "fairway-wood", label: "Fairway wood"),
        .init(key: "hybrid", label: "Hybrid"),
        .init(key: "iron", label: "Iron"),
        .init(key: "wedge", label: "Wedge"),
    ]

    static let hands: [Option] = [
        .init(key: "right", label: "Right-handed"),
        .init(key: "left", label: "Left-handed"),
    ]

    static let angles: [Option] = [
        .init(key: "face-on", label: "Face-on"),
        .init(key: "dtl", label: "Down the line"),
    ]

    static let levels: [Option] = [
        .init(key: "", label: "Skip for now"),
        .init(key: "new", label: "New to golf"),
        .init(key: "improving", label: "Improving"),
        .init(key: "experienced", label: "Experienced"),
    ]

    static let experienceModes: [Option] = [
        .init(key: "start", label: "Just starting"),
        .init(key: "improve", label: "Improving my game"),
        .init(key: "compete", label: "Competing"),
    ]

    static let handicapRanges: [Option] = [
        .init(key: "new_to_golf", label: "New to golf"),
        .init(key: "30_plus", label: "30+"),
        .init(key: "20_to_29", label: "20–29"),
        .init(key: "15_to_19", label: "15–19"),
        .init(key: "10_to_14", label: "10–14"),
        .init(key: "under_10", label: "Under 10"),
        .init(key: "prefer_not_to_say", label: "Prefer not to say"),
    ]

    static let primaryGoals: [Option] = [
        .init(key: "consistency", label: "Consistency"),
        .init(key: "tempo", label: "Tempo"),
        .init(key: "weight_shift", label: "Weight shift"),
        .init(key: "strike_quality", label: "Strike quality"),
        .init(key: "balance", label: "Balance"),
        .init(key: "confidence", label: "Confidence"),
    ]

    static let practiceMinutes: [Int] = [10, 20, 45]
    static let sessionsPerWeek: [Int] = [1, 2, 3]

    static func label(for key: String?, in options: [Option]) -> String? {
        guard let key else { return nil }
        return options.first { $0.key == key }?.label
    }

    static func clubLabel(_ key: String?) -> String? { label(for: key, in: clubs) }

    static func angleLabel(_ key: String?) -> String? { label(for: key, in: angles) }
}
