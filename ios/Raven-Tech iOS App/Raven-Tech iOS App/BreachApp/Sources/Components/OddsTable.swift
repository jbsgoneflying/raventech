import SwiftUI

/// A row in the odds table
struct OddsRow: Identifiable {
    let id = UUID()
    let width: Double
    let n: Int?
    let breachEitherPct: Double?
    let breachPutPct: Double?
    let breachCallPct: Double?
    let avgAbsRetPct: Double?
}

/// Historical odds table matching web's odds display
struct OddsTable: View {
    let rows: [OddsRow]
    var meta: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Historical Odds")
                    .font(.caption)
                    .fontWeight(.heavy)
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)

                Spacer()

                if let meta = meta {
                    Text(meta)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }

            GlassSurface(padding: 0) {
                VStack(spacing: 0) {
                    // Header
                    HStack(spacing: 0) {
                        headerCell("Width", width: 60, alignment: .leading)
                        headerCell("N", width: 40)
                        headerCell("Either", width: 60)
                        headerCell("Put", width: 50)
                        headerCell("Call", width: 50)
                        headerCell("Avg |Ret|", width: 60)
                    }
                    .padding(.vertical, 10)
                    .padding(.horizontal, 12)
                    .background(Color.black.opacity(0.03))

                    Divider()

                    // Rows
                    ForEach(rows) { row in
                        HStack(spacing: 0) {
                            dataCell(String(format: "%.2f×", row.width), width: 60, alignment: .leading, isBold: true)
                            dataCell(row.n.map { String($0) } ?? "—", width: 40)
                            dataCell(formatPct(row.breachEitherPct), width: 60, highlight: true)
                            dataCell(formatPct(row.breachPutPct), width: 50)
                            dataCell(formatPct(row.breachCallPct), width: 50)
                            dataCell(formatPct(row.avgAbsRetPct), width: 60)
                        }
                        .padding(.vertical, 10)
                        .padding(.horizontal, 12)

                        if row.id != rows.last?.id {
                            Divider()
                                .padding(.horizontal, 12)
                        }
                    }
                }
            }

            Text("Breach = expiry close outside short strikes at distance (width × 1σ EM)")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    @ViewBuilder
    private func headerCell(_ text: String, width: CGFloat, alignment: Alignment = .trailing) -> some View {
        Text(text)
            .font(.caption2)
            .fontWeight(.bold)
            .foregroundStyle(.secondary)
            .frame(width: width, alignment: alignment)
    }

    @ViewBuilder
    private func dataCell(_ text: String, width: CGFloat, alignment: Alignment = .trailing, isBold: Bool = false, highlight: Bool = false) -> some View {
        Text(text)
            .font(.caption)
            .fontWeight(isBold ? .bold : .regular)
            .monospacedDigit()
            .foregroundStyle(highlight ? .primary : .secondary)
            .frame(width: width, alignment: alignment)
    }

    private func formatPct(_ value: Double?) -> String {
        guard let v = value else { return "—" }
        return String(format: "%.2f%%", v)
    }
}

/// Compact odds display for limited space
struct OddsCompact: View {
    let width10: OddsRow?
    let width15: OddsRow?
    let width20: OddsRow?

    var body: some View {
        VStack(spacing: 8) {
            if let row = width10 {
                oddsRow("1.0×", row)
            }
            if let row = width15 {
                oddsRow("1.5×", row)
            }
            if let row = width20 {
                oddsRow("2.0×", row)
            }
        }
    }

    @ViewBuilder
    private func oddsRow(_ label: String, _ row: OddsRow) -> some View {
        HStack {
            Text(label)
                .font(.caption)
                .fontWeight(.bold)
                .frame(width: 40, alignment: .leading)

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(formatPct(row.breachEitherPct))
                    .font(.subheadline)
                    .fontWeight(.semibold)
                    .monospacedDigit()

                Text("n=\(row.n ?? 0)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color.white.opacity(0.60))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func formatPct(_ value: Double?) -> String {
        guard let v = value else { return "—" }
        return String(format: "%.2f%%", v)
    }
}

/// Engine 2 decision panel
struct EngineTwoDecisionPanel: View {
    let underlying: String
    var regimeScore: Double?
    var regimeBucket: String?
    var macroMultiplier: Double?
    var highImpactCount: Int?
    var highImpactEvents: [String]?
    var spot: Double?
    var asOfDate: String?
    var onInfoTap: ((InfoContent) -> Void)?
    var onMacroTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(underlying) — Engine 2")
                        .font(.headline)
                        .fontWeight(.bold)

                    if let asOf = asOfDate {
                        Text("as of \(asOf)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }

                Spacer()

                if let spot = spot {
                    VStack(alignment: .trailing, spacing: 2) {
                        Text("Spot")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(String(format: "%.2f", spot))
                            .font(.subheadline)
                            .fontWeight(.bold)
                            .monospacedDigit()
                    }
                }
            }

            // Metrics row
            HStack(spacing: 10) {
                // Regime
                miniCard(
                    title: "Regime",
                    value: regimeScore.map { String(format: "%.1f", $0) } ?? "—",
                    subtitle: regimeBucket,
                    onInfo: { onInfoTap?(.regimeScore) }
                )

                // Macro - tappable to show events
                macroCard
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

    @ViewBuilder
    private var macroCard: some View {
        Button {
            onMacroTap?()
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("Macro")
                        .font(.caption2)
                        .fontWeight(.heavy)
                        .foregroundStyle(.secondary)
                        .textCase(.uppercase)

                    Spacer()

                    if highImpactCount ?? 0 > 0 {
                        Image(systemName: "chevron.right")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }

                Text(macroMultiplier.map { String(format: "%.2fx", $0) } ?? "—")
                    .font(.title3)
                    .fontWeight(.bold)
                    .monospacedDigit()
                    .foregroundStyle(.primary)

                if let count = highImpactCount {
                    Text("\(count) events")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.white.opacity(0.60))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.black.opacity(0.06), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func miniCard(
        title: String,
        value: String,
        subtitle: String?,
        onInfo: @escaping () -> Void
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(title)
                    .font(.caption2)
                    .fontWeight(.heavy)
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)

                Spacer()

                Button(action: onInfo) {
                    Text("i")
                        .font(.system(size: 10, weight: .bold))
                        .frame(width: 18, height: 18)
                        .foregroundStyle(.secondary)
                        .background(Color.white.opacity(0.60))
                        .clipShape(Circle())
                        .overlay(Circle().stroke(Color.black.opacity(0.08), lineWidth: 1))
                }
                .buttonStyle(.plain)
            }

            Text(value)
                .font(.title3)
                .fontWeight(.bold)
                .monospacedDigit()

            if let sub = subtitle {
                Text(sub)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.60))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.black.opacity(0.06), lineWidth: 1)
        )
    }
}

/// Sheet to display macro events
struct MacroEventsSheet: View {
    let events: [String]
    let multiplier: Double?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if let mult = multiplier {
                        HStack {
                            Text("Macro Multiplier")
                                .font(.subheadline)
                            Spacer()
                            Text(String(format: "%.2fx", mult))
                                .font(.subheadline)
                                .fontWeight(.bold)
                                .monospacedDigit()
                        }
                    }
                }

                Section("High Impact Events") {
                    if events.isEmpty {
                        Text("No high-impact events this week")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(events, id: \.self) { event in
                            HStack(spacing: 12) {
                                Image(systemName: "calendar.badge.exclamationmark")
                                    .foregroundStyle(.orange)

                                Text(event)
                                    .font(.subheadline)
                            }
                        }
                    }
                }

                Section {
                    Text("These events may increase expected move volatility. The macro multiplier adjusts breach probability estimates accordingly.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Macro Events")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium])
        .presentationDragIndicator(.visible)
    }
}

#Preview {
    VStack(spacing: 20) {
        EngineTwoDecisionPanel(
            underlying: "SPX",
            regimeScore: 72.5,
            regimeBucket: "NORMAL",
            macroMultiplier: 1.25,
            highImpactCount: 3,
            spot: 4850.25,
            asOfDate: "2025-01-10"
        )

        OddsTable(
            rows: [
                OddsRow(width: 1.0, n: 85, breachEitherPct: 18.5, breachPutPct: 8.2, breachCallPct: 10.3, avgAbsRetPct: 1.85),
                OddsRow(width: 1.5, n: 85, breachEitherPct: 8.2, breachPutPct: 3.5, breachCallPct: 4.7, avgAbsRetPct: 1.85),
                OddsRow(width: 2.0, n: 85, breachEitherPct: 3.5, breachPutPct: 1.2, breachCallPct: 2.3, avgAbsRetPct: 1.85)
            ],
            meta: "bucket=NORMAL · lookback=2y"
        )

        OddsCompact(
            width10: OddsRow(width: 1.0, n: 85, breachEitherPct: 18.5, breachPutPct: nil, breachCallPct: nil, avgAbsRetPct: nil),
            width15: OddsRow(width: 1.5, n: 85, breachEitherPct: 8.2, breachPutPct: nil, breachCallPct: nil, avgAbsRetPct: nil),
            width20: OddsRow(width: 2.0, n: 85, breachEitherPct: 3.5, breachPutPct: nil, breachCallPct: nil, avgAbsRetPct: nil)
        )
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
