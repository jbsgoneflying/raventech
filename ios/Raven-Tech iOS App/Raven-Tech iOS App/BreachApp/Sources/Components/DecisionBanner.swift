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
    var onGoNoGoTap: (() -> Void)?

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
                    // Ticker logo
                    TickerLogo(ticker: ticker, size: 40, cornerRadius: 12)

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
                    Button {
                        onGoNoGoTap?()
                    } label: {
                        HStack(spacing: 6) {
                            DecisionPill(isGo: isGo, size: .regular)
                            if onGoNoGoTap != nil {
                                Image(systemName: "info.circle")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .buttonStyle(.plain)
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
            // Ticker logo (updates as user types)
            TickerLogo(ticker: ticker.isEmpty ? "?" : ticker, size: 40, cornerRadius: 12)
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

/// GO/NO-GO breakdown sheet
struct GoNoGoBreakdownSheet: View {
    let decision: GoNoGoDecision?
    let ticker: String

    var body: some View {
        NavigationStack {
            List {
                // Overall status
                Section {
                    HStack {
                        if decision?.passed == true {
                            DecisionPill(isGo: true, size: .regular)
                        } else {
                            DecisionPill(isGo: false, size: .regular)
                        }

                        Spacer()

                        Text(decision?.status ?? "Unknown")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Overall Decision")
                }

                // Individual checks
                Section {
                    if let checks = decision?.checks, !checks.isEmpty {
                        ForEach(checks) { check in
                            checkRow(check)
                        }
                    } else {
                        Text("No checks available")
                            .foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Checks")
                }

                // Warnings
                if let warnings = decision?.warnings, !warnings.isEmpty {
                    Section {
                        ForEach(warnings, id: \.self) { warning in
                            HStack(spacing: 10) {
                                Image(systemName: "exclamationmark.triangle.fill")
                                    .foregroundStyle(.orange)
                                Text(warning)
                                    .font(.subheadline)
                            }
                        }
                    } header: {
                        Text("Warnings")
                    }
                }

                // Notes
                if let notes = decision?.notes, !notes.isEmpty {
                    Section {
                        ForEach(notes, id: \.self) { note in
                            Text(note)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } header: {
                        Text("Notes")
                    }
                }
            }
            .navigationTitle("\(ticker) GO/NO-GO")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    @ViewBuilder
    private func checkRow(_ check: GoNoGoCheck) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(check.label ?? check.code ?? "Check")
                    .font(.subheadline)
                    .fontWeight(.semibold)

                Spacer()

                checkStatePill(check.state)
            }

            if let explain = check.explain, !explain.isEmpty {
                Text(explain)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func checkStatePill(_ state: String?) -> some View {
        let stateText = state ?? "UNKNOWN"
        let isPassing = stateText == "PASS"
        let isFailing = stateText == "FAIL"
        let isWarning = stateText == "WARN"

        Text(stateText)
            .font(.caption2)
            .fontWeight(.bold)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(backgroundColor(isPassing: isPassing, isFailing: isFailing, isWarning: isWarning))
            .clipShape(Capsule())
            .foregroundStyle(foregroundColor(isPassing: isPassing, isFailing: isFailing, isWarning: isWarning))
    }

    private func backgroundColor(isPassing: Bool, isFailing: Bool, isWarning: Bool) -> Color {
        if isPassing { return Color(hex: "34C759").opacity(0.15) }
        if isFailing { return Color(hex: "FF3B30").opacity(0.15) }
        if isWarning { return Color.orange.opacity(0.15) }
        return Color.gray.opacity(0.15)
    }

    private func foregroundColor(isPassing: Bool, isFailing: Bool, isWarning: Bool) -> Color {
        if isPassing { return Color(hex: "34C759") }
        if isFailing { return Color(hex: "FF3B30") }
        if isWarning { return .orange }
        return .secondary
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
