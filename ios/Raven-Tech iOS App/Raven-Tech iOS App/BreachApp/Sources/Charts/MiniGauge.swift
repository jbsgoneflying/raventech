import SwiftUI

/// A mini arc gauge for displaying scores and percentages
struct MiniGauge: View {
    let value: Double  // 0-100 or percentage
    var maxValue: Double = 100
    var label: String?
    var style: GaugeStyle = .neutral

    enum GaugeStyle {
        case positive
        case neutral
        case negative

        var color: Color {
            switch self {
            case .positive: return Color(hex: "2ECC71").opacity(0.85)
            case .neutral: return Color(hex: "788CFF").opacity(0.85)
            case .negative: return Color(hex: "FF4D4D").opacity(0.85)
            }
        }
    }

    var body: some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height * 0.8)
            let radius = min(size.width, size.height) * 0.42

            // Arc angles (semi-circle from left to right)
            let startAngle = Angle.degrees(180)
            let endAngle = Angle.degrees(0)

            // Background arc
            let bgPath = Path { path in
                path.addArc(
                    center: center,
                    radius: radius,
                    startAngle: startAngle,
                    endAngle: endAngle,
                    clockwise: false
                )
            }
            context.stroke(
                bgPath,
                with: .color(Color.black.opacity(0.08)),
                style: StrokeStyle(lineWidth: 6, lineCap: .round)
            )

            // Value arc
            let fraction = min(1, max(0, value / maxValue))
            let valueEndAngle = Angle.degrees(180 - 180 * fraction)
            let valuePath = Path { path in
                path.addArc(
                    center: center,
                    radius: radius,
                    startAngle: startAngle,
                    endAngle: valueEndAngle,
                    clockwise: false
                )
            }
            context.stroke(
                valuePath,
                with: .color(style.color.opacity(0.22)),
                style: StrokeStyle(lineWidth: 6, lineCap: .round)
            )

            // Dot at current position
            let dotAngle = Angle.degrees(180 - 180 * fraction)
            let dotX = center.x + radius * CGFloat(cos(dotAngle.radians))
            let dotY = center.y + radius * CGFloat(sin(dotAngle.radians))

            let dotRect = CGRect(x: dotX - 4, y: dotY - 4, width: 8, height: 8)
            context.fill(
                Circle().path(in: dotRect),
                with: .color(Color.primary)
            )
        }
        .frame(width: 60, height: 36)
    }
}

/// A horizontal bar gauge
struct MiniBarGauge: View {
    let value: Double  // 0-100 or percentage
    var maxValue: Double = 100
    var style: MiniGauge.GaugeStyle = .neutral

    var body: some View {
        GeometryReader { geo in
            let fraction = min(1, max(0, value / maxValue))

            ZStack(alignment: .leading) {
                // Background
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.black.opacity(0.08))

                // Fill
                RoundedRectangle(cornerRadius: 4)
                    .fill(style.color.opacity(0.55))
                    .frame(width: geo.size.width * CGFloat(fraction))
            }
        }
        .frame(height: 8)
    }
}

/// A circular progress indicator
struct CircularProgress: View {
    let value: Double  // 0-100
    var maxValue: Double = 100
    var lineWidth: CGFloat = 4
    var style: MiniGauge.GaugeStyle = .neutral

    var body: some View {
        ZStack {
            // Background circle
            Circle()
                .stroke(Color.black.opacity(0.08), lineWidth: lineWidth)

            // Progress arc
            Circle()
                .trim(from: 0, to: CGFloat(min(1, value / maxValue)))
                .stroke(
                    style.color,
                    style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
        }
    }
}

/// Confidence dots indicator (like web's `.taDot`)
struct ConfidenceDots: View {
    let filled: Int
    var total: Int = 5

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<total, id: \.self) { index in
                Circle()
                    .fill(index < filled ? Color.black.opacity(0.55) : Color.clear)
                    .frame(width: 8, height: 8)
                    .overlay(
                        Circle()
                            .stroke(Color.black.opacity(0.10), lineWidth: 1)
                    )
            }
        }
    }
}

/// A score display with gauge visualization
struct ScoreCard: View {
    let title: String
    let score: Double
    var maxScore: Double = 100
    var bucket: String?
    var style: MiniGauge.GaugeStyle = .neutral
    var onInfoTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(title)
                    .font(.caption)
                    .fontWeight(.heavy)
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)

                Spacer()

                if let onInfoTap = onInfoTap {
                    InfoButton(action: onInfoTap)
                }
            }

            HStack(alignment: .center, spacing: 12) {
                MiniGauge(value: score, maxValue: maxScore, style: style)

                VStack(alignment: .leading, spacing: 4) {
                    Text(String(format: "%.1f / %.0f", score, maxScore))
                        .font(.title3)
                        .fontWeight(.bold)
                        .monospacedDigit()

                    if let bucket = bucket {
                        Text("Bucket: \(bucket)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
    }
}

/// A percentage bar with label
struct PercentageBar: View {
    let label: String
    let value: Double  // As percentage (0-100)
    var style: MiniGauge.GaugeStyle = .neutral

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Spacer()

                Text(String(format: "%.1f%%", value))
                    .font(.caption)
                    .fontWeight(.semibold)
                    .monospacedDigit()
            }

            MiniBarGauge(value: value, style: style)
        }
    }
}

// MARK: - Preview

#Preview {
    VStack(spacing: 20) {
        HStack(spacing: 20) {
            MiniGauge(value: 25, style: .positive)
            MiniGauge(value: 50, style: .neutral)
            MiniGauge(value: 75, style: .negative)
        }

        HStack(spacing: 20) {
            MiniBarGauge(value: 30, style: .positive)
            MiniBarGauge(value: 60, style: .neutral)
            MiniBarGauge(value: 90, style: .negative)
        }
        .frame(height: 8)

        HStack {
            CircularProgress(value: 45, lineWidth: 3)
                .frame(width: 30, height: 30)

            ConfidenceDots(filled: 3, total: 5)
        }

        ScoreCard(
            title: "Regime Score",
            score: 72.5,
            bucket: "NORMAL",
            style: .neutral,
            onInfoTap: {}
        )

        VStack(spacing: 8) {
            PercentageBar(label: "Breach rate", value: 23.5, style: .negative)
            PercentageBar(label: "Win rate", value: 76.5, style: .positive)
        }
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
