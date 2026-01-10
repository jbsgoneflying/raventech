import SwiftUI

/// Style variants for pills matching web app's `.pill` classes
enum PillStyle {
    case good
    case bad
    case warn
    case neutral
    case fed
    case econ
    case opex
    case holiday
    case treasury
    case custom(foreground: Color, border: Color, background: Color)

    var foregroundColor: Color {
        switch self {
        case .good: return Color(hex: "34C759")
        case .bad: return Color(hex: "FF3B30")
        case .warn: return Color(hex: "FF9F0A")
        case .neutral: return Color.black.opacity(0.55)
        case .fed: return Color(hex: "FF9500")
        case .econ: return Color(hex: "0A84FF")
        case .opex: return Color(hex: "FF3B30").opacity(0.88)
        case .holiday: return Color(hex: "734EA6")
        case .treasury: return Color(hex: "30D158")
        case .custom(let fg, _, _): return fg
        }
    }

    var borderColor: Color {
        switch self {
        case .good: return Color(hex: "34C759").opacity(0.30)
        case .bad: return Color(hex: "FF3B30").opacity(0.32)
        case .warn: return Color(hex: "FF9F0A").opacity(0.28)
        case .neutral: return Color.black.opacity(0.10)
        case .fed: return Color(hex: "FF9500").opacity(0.28)
        case .econ: return Color(hex: "0A84FF").opacity(0.25)
        case .opex: return Color(hex: "FF3B30").opacity(0.22)
        case .holiday: return Color(hex: "734EA6").opacity(0.25)
        case .treasury: return Color(hex: "30D158").opacity(0.25)
        case .custom(_, let border, _): return border
        }
    }

    var backgroundColor: Color {
        switch self {
        case .custom(_, _, let bg): return bg
        default: return Color.white.opacity(0.70)
        }
    }
}

/// A pill/badge component matching web app's `.pill`
struct Pill: View {
    let text: String
    let style: PillStyle
    var size: PillSize = .regular

    enum PillSize {
        case mini
        case regular
        case large

        var font: Font {
            switch self {
            case .mini: return .system(size: 11, weight: .semibold)
            case .regular: return .caption
            case .large: return .subheadline
            }
        }

        var horizontalPadding: CGFloat {
            switch self {
            case .mini: return 8
            case .regular: return 10
            case .large: return 12
            }
        }

        var verticalPadding: CGFloat {
            switch self {
            case .mini: return 2
            case .regular: return 4
            case .large: return 6
            }
        }
    }

    var body: some View {
        Text(text)
            .font(size.font)
            .fontWeight(.semibold)
            .tracking(0.1)
            .padding(.horizontal, size.horizontalPadding)
            .padding(.vertical, size.verticalPadding)
            .foregroundStyle(style.foregroundColor)
            .background(style.backgroundColor)
            .clipShape(Capsule())
            .overlay(Capsule().stroke(style.borderColor, lineWidth: 1))
    }
}

/// GO/NO-GO decision pill with special styling
struct DecisionPill: View {
    let isGo: Bool
    var size: DecisionSize = .regular

    enum DecisionSize {
        case compact
        case regular
        case large
    }

    var body: some View {
        Text(isGo ? "GO" : "NO-GO")
            .font(fontSize)
            .fontWeight(.black)
            .tracking(1.5)
            .textCase(.uppercase)
            .padding(.horizontal, horizontalPadding)
            .padding(.vertical, verticalPadding)
            .foregroundStyle(isGo ? goForeground : noForeground)
            .background(isGo ? goBackground : noBackground)
            .clipShape(Capsule())
            .overlay(Capsule().stroke(isGo ? goBorder : noBorder, lineWidth: 1))
            .shadow(color: isGo ? goShadow : noShadow, radius: 12, x: 0, y: 5)
    }

    private var fontSize: Font {
        switch size {
        case .compact: return .caption2
        case .regular: return .caption
        case .large: return .subheadline
        }
    }

    private var horizontalPadding: CGFloat {
        switch size {
        case .compact: return 10
        case .regular: return 14
        case .large: return 18
        }
    }

    private var verticalPadding: CGFloat {
        switch size {
        case .compact: return 4
        case .regular: return 6
        case .large: return 8
        }
    }

    private let goForeground = Color(hex: "0C4626").opacity(0.96)
    private let goBorder = Color(hex: "2ECC71").opacity(0.40)
    private let goBackground = Color(hex: "2ECC71").opacity(0.14)
    private let goShadow = Color(hex: "2ECC71").opacity(0.10)

    private let noForeground = Color(hex: "56110C").opacity(0.96)
    private let noBorder = Color(hex: "E74C3C").opacity(0.40)
    private let noBackground = Color(hex: "E74C3C").opacity(0.14)
    private let noShadow = Color(hex: "E74C3C").opacity(0.10)
}

/// Chip toggle button matching web's `.chipToggle`
struct ChipToggle: View {
    let label: String
    @Binding var isOn: Bool

    var body: some View {
        Button {
            isOn.toggle()
        } label: {
            Text(label)
                .font(.caption)
                .fontWeight(.bold)
                .tracking(-0.1)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .foregroundStyle(isOn ? Color.black.opacity(0.92) : Color.black.opacity(0.72))
                .background(isOn ? Color.black.opacity(0.06) : Color.white.opacity(0.60))
                .clipShape(Capsule())
                .overlay(
                    Capsule()
                        .stroke(isOn ? Color.black.opacity(0.14) : Color.black.opacity(0.10), lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    VStack(spacing: 16) {
        HStack {
            Pill(text: "Good", style: .good)
            Pill(text: "Bad", style: .bad)
            Pill(text: "Warn", style: .warn)
            Pill(text: "Neutral", style: .neutral)
        }

        HStack {
            Pill(text: "FED", style: .fed, size: .mini)
            Pill(text: "ECON", style: .econ, size: .mini)
            Pill(text: "OPEX", style: .opex, size: .mini)
        }

        HStack {
            DecisionPill(isGo: true)
            DecisionPill(isGo: false)
        }

        HStack {
            ChipToggle(label: "Weekly", isOn: .constant(true))
            ChipToggle(label: "Nearest", isOn: .constant(false))
        }
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
