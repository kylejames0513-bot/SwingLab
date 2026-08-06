import SwiftUI

/// Brand palette mirrored from `config.yaml` (`brand.primary_color`,
/// `brand.accent_color`) and the storefront night background. Keep these in
/// sync with the web product when rebranding.
enum Brand {
    static let green = Color(red: 0x1A / 255, green: 0x5C / 255, blue: 0x38 / 255)
    static let orange = Color(red: 0xE8 / 255, green: 0x72 / 255, blue: 0x0C / 255)
    static let night = Color(red: 0x07 / 255, green: 0x13 / 255, blue: 0x0D / 255)
    static let cream = Color(red: 0xF7 / 255, green: 0xF5 / 255, blue: 0xF0 / 255)

    static let productName = "CaddieInsight"
}

struct CardBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

extension View {
    func brandCard() -> some View { modifier(CardBackground()) }
}

struct Eyebrow: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(.caption2.weight(.bold))
            .tracking(1.2)
            .foregroundStyle(Brand.orange)
    }
}
