import SwiftUI

/// Core design tokens matching the web app's static/styles.css :root
enum RavenTheme {
    // MARK: - Glass Surfaces
    static var surfaceGlass: Color { Color.white.opacity(0.70) }
    static var surfaceElevated: Color { Color.white.opacity(0.86) }
    static let surfaceSolid = Color.white

    // MARK: - Neutrals
    static var border: Color { Color.black.opacity(0.10) }
    static var borderStrong: Color { Color.black.opacity(0.14) }
    static var line: Color { Color.black.opacity(0.08) }
    static let textPrimary = Color(hex: "0b0b0f")
    static var textMuted: Color { Color.black.opacity(0.62) }
    static var textMuted2: Color { Color.black.opacity(0.48) }

    // MARK: - Accents
    static let accentBlue = Color(hex: "007AFF")
    static let accentGreen = Color(hex: "34C759")
    static let accentAmber = Color(hex: "FF9F0A")
    static let accentRed = Color(hex: "FF3B30")

    // MARK: - Semantic Colors
    static var positive: Color { Color(hex: "2ECC71").opacity(0.14) }
    static var neutral: Color { Color(hex: "788CFF").opacity(0.12) }
    static var negative: Color { Color(hex: "FF4D4D").opacity(0.12) }

    // MARK: - Radii
    static let radiusCard: CGFloat = 18
    static let radiusControl: CGFloat = 16
    static let radiusSmall: CGFloat = 12

    // MARK: - Shadows
    static var shadowCard: Color { Color.black.opacity(0.08) }
    static var shadowGlass: Color { Color.black.opacity(0.08) }

    // MARK: - Spacing
    static let spacingXS: CGFloat = 4
    static let spacingSM: CGFloat = 8
    static let spacingMD: CGFloat = 12
    static let spacingLG: CGFloat = 16
    static let spacingXL: CGFloat = 24

    // MARK: - Typography
    static let fontCaption: Font = .caption
    static let fontBody: Font = .subheadline
    static let fontTitle: Font = .title3
    static let fontLarge: Font = .title2
}

// MARK: - Color Extension for Hex
extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch hex.count {
        case 3: // RGB (12-bit)
            (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
        case 6: // RGB (24-bit)
            (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8: // ARGB (32-bit)
            (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default:
            (a, r, g, b) = (255, 0, 0, 0)
        }
        let redValue = Double(r) / 255
        let greenValue = Double(g) / 255
        let blueValue = Double(b) / 255
        let alphaValue = Double(a) / 255
        self.init(.sRGB, red: redValue, green: greenValue, blue: blueValue, opacity: alphaValue)
    }
}
