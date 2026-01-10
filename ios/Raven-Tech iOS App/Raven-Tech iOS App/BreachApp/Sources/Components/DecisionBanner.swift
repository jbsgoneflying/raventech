import SwiftUI

/// Decision banner showing GO/NO-GO verdict with supporting chips
struct DecisionBanner: View {
    let ticker: String
    var isGo: Bool?
    var bias: Bias?
    var confidence: Int?  // 0-5
    var chips: [String]?
    var asOfDate: String?
    var spot: Double?

    enum Bias: String {
        case bullish = "BULLISH"
        case bearish = "BEARISH"
        case neutral = "NEUTRAL"

        var pillStyle: PillStyle {
            switch self {
            case .bullish: return .good
            case .bearish: return .bad
            case .neutral: return .neutral
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Top row: ticker + meta
            HStack(alignment: .center) {
                HStack(spacing: 10) {
                    // Ticker logo placeholder
                    RoundedRectangle(cornerRadius: 12)
                        .fill(Color(hex: "121216").opacity(0.92))
                        .frame(width: 40, height: 40)
                        .overlay(
                            Text(String(ticker.prefix(2)))
                                .font(.caption)
                                .fontWeight(.black)
                                .foregroundStyle(.white.opacity(0.9))
                        )

                    VStack(alignment: .leading, spacing: 2) {
                        Text(ticker)
                            .font(.headline)
                            .fontWeight(.bold)

                        if let asOf = asOfDate {
                            Text("as of \(asOf)")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Spacer()

                if let spot = spot {
                    Text(String(format: "%.2f", spot))
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
            }

            // Middle row: GO/NO-GO + bias + confidence
            HStack(spacing: 12) {
                if let isGo = isGo {
                    DecisionPill(isGo: isGo, size: .regular)
                }

                if let bias = bias {
                    Pill(text: bias.rawValue, style: bias.pillStyle, size: .regular)
                }

                if let conf = confidence {
                    ConfidenceDots(filled: conf, total: 5)
                }

                Spacer()
            }

            // Bottom row: chips
            if let chips = chips, !chips.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(chips, id: \.self) { chip in
                            Text(chip)
                                .font(.caption2)
                                .fontWeight(.semibold)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Color.black.opacity(0.02))
                                .clipShape(Capsule())
                                .overlay(
                                    Capsule()
                                        .stroke(Color.black.opacity(0.08), lineWidth: 1)
                                )
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .padding(14)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.08), radius: 15, x: 0, y: 10)
    }
}

/// Header for Engine 1 with ticker input
struct EngineOneHeader: View {
    @Binding var ticker: String
    var isLoading: Bool
    var onRun: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            // Ticker logo placeholder (updates as user types)
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(hex: "121216").opacity(0.92))
                .frame(width: 40, height: 40)
                .overlay(
                    Text(String(ticker.prefix(2).uppercased()))
                        .font(.caption)
                        .fontWeight(.black)
                        .foregroundStyle(.white.opacity(0.9))
                )
                .animation(.easeInOut(duration: 0.15), value: ticker)

            // Ticker input
            TextField("AAPL", text: $ticker)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                .font(.headline)
                .fontWeight(.semibold)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color.white.opacity(0.86))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color.black.opacity(0.08), lineWidth: 1)
                )

            // Run button
            PrimaryButton(
                title: "Run",
                action: onRun,
                isLoading: isLoading
            )
        }
        .padding(12)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: RavenTheme.radiusControl, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: RavenTheme.radiusControl, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.08), radius: 15, x: 0, y: 10)
    }
}

/// Quarter seasonality card
struct QuarterSeasonalityCard: View {
    let quarter: String
    let breachRate: Double?
    var recommendation: String?
    var isCurrentQuarter: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(quarter)
                    .font(.subheadline)
                    .fontWeight(.bold)

                if isCurrentQuarter {
                    Circle()
                        .fill(Color.accentColor)
                        .frame(width: 6, height: 6)
                }

                Spacer()

                if let rate = breachRate {
                    Text(String(format: "%.1f%%", rate))
                        .font(.subheadline)
                        .fontWeight(.bold)
                        .monospacedDigit()
                }
            }

            if let rec = recommendation {
                Text(rec)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
        .padding(12)
        .background(isCurrentQuarter ? Color.accentColor.opacity(0.08) : Color.white.opacity(0.60))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(
                    isCurrentQuarter ? Color.accentColor.opacity(0.20) : Color.black.opacity(0.08),
                    lineWidth: 1
                )
        )
    }
}

/// Event row for the events list
struct BreachEventRow: View {
    let event: BreachEvent

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(event.earnDate ?? "—")
                    .font(.subheadline)
                    .fontWeight(.semibold)

                Spacer()

                if event.breach == true {
                    Pill(text: "Breach", style: .bad, size: .mini)
                } else {
                    Pill(text: "Hold", style: .good, size: .mini)
                }
            }

            HStack(spacing: 16) {
                if let implied = event.impliedMovePct {
                    HStack(spacing: 4) {
                        Text("Implied")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(String(format: "%.2f%%", implied))
                            .font(.caption)
                            .fontWeight(.semibold)
                            .monospacedDigit()
                    }
                }

                if let realized = event.realizedMovePct {
                    HStack(spacing: 4) {
                        Text("Realized")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(String(format: "%.2f%%", realized))
                            .font(.caption)
                            .fontWeight(.semibold)
                            .monospacedDigit()
                            .foregroundStyle(realized > (event.impliedMovePct ?? 0) ? Color(hex: "FF3B30") : Color(hex: "34C759"))
                    }
                }
            }
        }
        .padding(12)
        .background(event.breach == true ? Color(hex: "FF3B30").opacity(0.06) : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

#Preview {
    VStack(spacing: 16) {
        EngineOneHeader(
            ticker: .constant("AAPL"),
            isLoading: false,
            onRun: {}
        )

        DecisionBanner(
            ticker: "AAPL",
            isGo: true,
            bias: .bullish,
            confidence: 3,
            chips: ["Regime: NORMAL", "Q1 seasonality", "n=45"],
            asOfDate: "2025-01-10",
            spot: 185.50
        )

        HStack(spacing: 10) {
            QuarterSeasonalityCard(
                quarter: "Q1",
                breachRate: 22.5,
                recommendation: "Standard wings",
                isCurrentQuarter: true
            )
            QuarterSeasonalityCard(
                quarter: "Q2",
                breachRate: 28.3,
                recommendation: "Wider wings"
            )
        }
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
