import SwiftUI

struct SPXScreen: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var viewModel = SPXViewModel()

    @State private var showingInfoSheet: InfoContent?
    @State private var showingMacroEvents: Bool = false

    var body: some View {
        NavigationStack {
            ZStack {
                backgroundGradient

                ScrollView {
                    VStack(spacing: 16) {
                        // Control bar
                        controlBar
                            .padding(.horizontal)

                        // Error display
                        if let error = viewModel.error {
                            errorBanner(error)
                        }

                        // Disabled state
                        if viewModel.flags?.enableEngine2SpxIc == false {
                            disabledBanner
                        }

                        // Loading state
                        if viewModel.isLoading {
                            loadingView
                        }

                        // Results
                        if let ic = viewModel.ic {
                            resultsContent(ic: ic, levels: viewModel.levels)
                        } else if !viewModel.isLoading && viewModel.error == nil {
                            emptyState
                        }
                    }
                    .padding(.bottom, 32)
                }
            }
            .navigationTitle("SPX")
            .navigationBarTitleDisplayMode(.inline)
            .sheet(item: $showingInfoSheet) { content in
                content.sheet()
            }
            .sheet(isPresented: $showingMacroEvents) {
                MacroEventsSheet(
                    events: viewModel.ic?.current?.macro?.highImpactUS?.top ?? [],
                    multiplier: viewModel.ic?.current?.macro?.multiplier
                )
            }
        }
    }

    // MARK: - Background

    private var backgroundGradient: some View {
        ZStack {
            Color(UIColor.systemBackground)

            RadialGradient(
                colors: [Color(hex: "6366F1").opacity(0.08), .clear],
                center: UnitPoint(x: 0.18, y: -0.10),
                startRadius: 0,
                endRadius: 700
            )

            RadialGradient(
                colors: [Color(hex: "F97316").opacity(0.05), .clear],
                center: UnitPoint(x: 0.85, y: 0.10),
                startRadius: 0,
                endRadius: 600
            )
        }
        .ignoresSafeArea()
    }

    // MARK: - Control Bar

    private var controlBar: some View {
        VStack(spacing: 12) {
            // Underlying + Entry day
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Underlying")
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    Picker("Underlying", selection: $viewModel.underlying) {
                        Text("SPX").tag("SPX")
                        Text("SPY").tag("SPY")
                        Text("QQQ").tag("QQQ")
                    }
                    .pickerStyle(.segmented)
                }
                .frame(maxWidth: .infinity)

                VStack(alignment: .leading, spacing: 4) {
                    Text("Entry day")
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    Picker("Entry day", selection: $viewModel.entryDay) {
                        Text("Mon").tag("mon")
                        Text("Tue").tag("tue")
                        Text("Wed").tag("wed")
                    }
                    .pickerStyle(.segmented)
                }
                .frame(maxWidth: .infinity)
            }

            // Seasonality + Run button
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Seasonality")
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    Picker("Seasonality", selection: $viewModel.seasonalityMode) {
                        Text("All").tag("ALL")
                        Text("OPEX").tag("OPEX_WEEK")
                        Text("Non-OPEX").tag("NON_OPEX")
                    }
                    .pickerStyle(.segmented)
                }

                PrimaryButton(
                    title: "Run",
                    action: {
                        let generator = UIImpactFeedbackGenerator(style: .medium)
                        generator.impactOccurred()
                        Task { await viewModel.load(client: appState.apiClient) }
                    },
                    isLoading: viewModel.isLoading
                )
            }
        }
        .padding(14)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: RavenTheme.radiusControl, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: RavenTheme.radiusControl, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.08), radius: 15, x: 0, y: 10)
    }

    // MARK: - Error / Loading / Empty

    @ViewBuilder
    private func errorBanner(_ error: AppError) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)

            Text(error.localizedDescription)
                .font(.subheadline)
                .foregroundStyle(.primary)

            Spacer()
        }
        .padding(12)
        .background(Color.red.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.red.opacity(0.20), lineWidth: 1)
        )
        .padding(.horizontal)
    }

    private var disabledBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "gear.badge.xmark")
                .foregroundStyle(.orange)

            Text("Engine 2 is disabled on the server.")
                .font(.subheadline)

            Spacer()
        }
        .padding(12)
        .background(Color.orange.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.orange.opacity(0.20), lineWidth: 1)
        )
        .padding(.horizontal)
    }

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text("Loading Engine 2…")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "chart.xyaxis.line")
                .font(.system(size: 48))
                .foregroundStyle(.secondary.opacity(0.5))

            Text("Tap Run to analyze")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Text("SPX IC breach odds, regime scoring,\ndealer gamma, and GEX heatmaps")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 60)
    }

    // MARK: - Results Content

    @ViewBuilder
    private func resultsContent(ic: SPXICResponse, levels: SPXLevelsResponse?) -> some View {
        // Decision panel
        EngineTwoDecisionPanel(
            underlying: viewModel.underlying,
            regimeScore: ic.current?.regime?.score,
            regimeBucket: ic.current?.regime?.bucket,
            macroMultiplier: ic.current?.macro?.macroMultiplier,
            highImpactCount: ic.current?.macro?.highImpactCount,
            highImpactEvents: ic.current?.macro?.highImpactUS?.top,
            spot: ic.underlying?.last,
            asOfDate: ic.asOfDate,
            onInfoTap: { content in showingInfoSheet = content },
            onMacroTap: { showingMacroEvents = true }
        )
        .padding(.horizontal)

        // Odds summary
        oddsSummarySection(ic.oddsLikeNow)
            .padding(.horizontal)

        // Gamma chart section
        if let lv = levels?.levels {
            gammaChartSection(lv, priceSeries: levels?.priceSeries)
                .padding(.horizontal)
        }

        // GEX heatmap section
        if let heatmap = levels?.levels?.gexHeatmap {
            gexHeatmapSection(heatmap)
                .padding(.horizontal)
        }

        // Additional metrics cards
        if let lv = levels?.levels {
            additionalMetricsSection(lv)
                .padding(.horizontal)
        }

        // Notes
        if let notes = ic.notes, !notes.isEmpty {
            notesSection(notes)
                .padding(.horizontal)
        }
    }

    // MARK: - Odds Summary

    @ViewBuilder
    private func oddsSummarySection(_ odds: Engine2OddsLikeNow?) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Breach Odds")
                    .font(.caption)
                    .fontWeight(.heavy)
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)

                Spacer()

                InfoButton { showingInfoSheet = .historicalOdds }
            }
            .padding(.leading, 4)

            if let odds = odds {
                MetricCardGrid(columns: 3) {
                    MetricCard(
                        title: "1.0× EM",
                        value: formatPct(odds.width10?.breachEitherPct),
                        subtitle: "n=\(odds.width10?.n ?? 0)"
                    )

                    MetricCard(
                        title: "1.5× EM",
                        value: formatPct(odds.width15?.breachEitherPct),
                        subtitle: "n=\(odds.width15?.n ?? 0)"
                    )

                    MetricCard(
                        title: "2.0× EM",
                        value: formatPct(odds.width20?.breachEitherPct),
                        subtitle: "n=\(odds.width20?.n ?? 0)"
                    )
                }
            } else {
                Text("No odds data available")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding()
            }
        }
    }

    // MARK: - Gamma Chart

    @ViewBuilder
    private func gammaChartSection(_ levels: SPXLiveLevels, priceSeries: [SPXPricePoint]?) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Dealer Gamma Map")
                    .font(.caption)
                    .fontWeight(.heavy)
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)

                Spacer()

                InfoButton { showingInfoSheet = .dealerGamma }
            }
            .padding(.leading, 4)

            // Price chart with levels
            if let priceData = priceSeries, !priceData.isEmpty {
                let series = priceData.map { p in
                    PricePoint(date: p.date ?? "", close: p.close ?? 0)
                }
                let overlays = buildChartLevels(levels)

                PriceLineChart(series: series, overlayLevels: overlays)
            } else {
                Text("No price data available")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding()
                    .frame(maxWidth: .infinity)
                    .frame(height: 200)
                    .background(Color.white.opacity(0.55))
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }

            // Level legend
            levelLegend(levels)
        }
    }

    private func buildChartLevels(_ levels: SPXLiveLevels) -> [ChartLevel] {
        var overlays: [ChartLevel] = []

        if let putWall = levels.putWallStrike {
            overlays.append(ChartLevel(kind: .putWall, value: putWall, label: "Put wall"))
        }
        if let callWall = levels.callWallStrike {
            overlays.append(ChartLevel(kind: .callWall, value: callWall, label: "Call wall"))
        }
        if let gammaFlip = levels.gammaFlipStrike {
            overlays.append(ChartLevel(kind: .gammaFlip, value: gammaFlip, label: "Gamma flip"))
        }

        return overlays
    }

    @ViewBuilder
    private func levelLegend(_ levels: SPXLiveLevels) -> some View {
        HStack(spacing: 16) {
            if let putWall = levels.putWallStrike {
                ChartLevelIndicator(label: "Put wall", kind: ChartLevel.LevelKind.putWall, strike: putWall)
            }
            if let callWall = levels.callWallStrike {
                ChartLevelIndicator(label: "Call wall", kind: ChartLevel.LevelKind.callWall, strike: callWall)
            }
            if let gammaFlip = levels.gammaFlipStrike {
                ChartLevelIndicator(label: "Gamma flip", kind: ChartLevel.LevelKind.gammaFlip, strike: gammaFlip)
            }

            Spacer()
        }
    }

    // MARK: - GEX Heatmap

    @ViewBuilder
    private func gexHeatmapSection(_ heatmap: SPXGexHeatmap) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("GEX Heatmap")
                .font(.caption)
                .fontWeight(.heavy)
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                .padding(.leading, 4)

            if let strikes = heatmap.strikes,
               let expiries = heatmap.expiries,
               let matrix = heatmap.matrix,
               !strikes.isEmpty, !expiries.isEmpty, !matrix.isEmpty {
                GEXHeatmap(
                    strikes: strikes,
                    expiries: expiries,
                    matrix: matrix,
                    spot: heatmap.spot,
                    boundaries: HeatmapBoundaries(
                        downsideStrike: heatmap.downsideAccelStart,
                        upsideStrike: heatmap.upsideAccelStart
                    )
                )
            } else {
                Text("No heatmap data available")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding()
            }

            // Stability metrics
            if let stability = heatmap.stability {
                GEXMetricsStrip(
                    metrics: HeatmapMetrics(
                        downsideDistancePts: stability.downsideDistancePts,
                        upsideDistancePts: stability.upsideDistancePts,
                        downsideDistanceEm: stability.downsideDistanceEm,
                        upsideDistanceEm: stability.upsideDistanceEm
                    ),
                    stability: HeatmapStability(
                        label: stability.label ?? "Unknown",
                        reasons: stability.reasons ?? []
                    )
                )
            }
        }
    }

    // MARK: - Additional Metrics

    @ViewBuilder
    private func additionalMetricsSection(_ levels: SPXLiveLevels) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Additional Metrics")
                .font(.caption)
                .fontWeight(.heavy)
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                .padding(.leading, 4)

            MetricCardGrid(columns: 2) {
                // Net GEX
                if let netGex = levels.dealerGamma?.netGex {
                    MetricCard(
                        title: "Net GEX",
                        value: formatLargeNumber(netGex),
                        subtitle: levels.dealerGamma?.netGammaSign
                    )
                }

                // Hedging Pressure (from weeklyFriday addons)
                if let hp = levels.hedgingPressure {
                    MetricCard(
                        title: "Hedging Pressure",
                        value: hp.label ?? hp.bucket ?? "—",
                        subtitle: hp.reasons?.first,
                        onInfoTap: { showingInfoSheet = .custom(
                            title: "Hedging Pressure",
                            body: "Measures the directional bias of dealer hedging activity.",
                            bullets: hp.reasons,
                            deskView: nil
                        )}
                    )
                }

                // Tail Ignition (from weeklyFriday addons)
                if let ti = levels.tailIgnition {
                    MetricCard(
                        title: "Tail Ignition",
                        value: ti.label ?? ti.bucket ?? "—",
                        subtitle: ti.reasons?.first,
                        onInfoTap: { showingInfoSheet = .custom(
                            title: "Tail Ignition",
                            body: "Risk of accelerated moves in the tails due to gamma positioning.",
                            bullets: ti.reasons,
                            deskView: nil
                        )}
                    )
                }

                // Vol Pressure (from levels top level)
                if let vp = levels.volPressure, vp.enabled == true {
                    MetricCard(
                        title: "Vol Pressure",
                        value: vp.label ?? vp.bucket ?? "—",
                        subtitle: vp.putCallRatio.map { String(format: "P/C: %.2f", $0) },
                        onInfoTap: { showingInfoSheet = .custom(
                            title: "Vol Pressure",
                            body: "Volatility pressure based on put/call ratios and IV rank.",
                            bullets: vp.reasons,
                            deskView: nil
                        )}
                    )
                }
            }
        }
    }

    private func formatLargeNumber(_ value: Double) -> String {
        let absValue = abs(value)
        if absValue >= 1_000_000_000 {
            return String(format: "%.2fB", value / 1_000_000_000)
        } else if absValue >= 1_000_000 {
            return String(format: "%.2fM", value / 1_000_000)
        } else if absValue >= 1_000 {
            return String(format: "%.1fK", value / 1_000)
        } else {
            return String(format: "%.2f", value)
        }
    }

    // MARK: - Notes

    @ViewBuilder
    private func notesSection(_ notes: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Notes")
                .font(.caption)
                .fontWeight(.heavy)
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                .padding(.leading, 4)

            GlassSurface(padding: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(notes, id: \.self) { note in
                        HStack(alignment: .top, spacing: 8) {
                            Circle()
                                .fill(Color.black.opacity(0.10))
                                .frame(width: 6, height: 6)
                                .padding(.top, 5)

                            Text(note)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Helpers

    private func formatPct(_ value: Double?) -> String {
        guard let v = value else { return "—" }
        return String(format: "%.2f%%", v)
    }
}

// MARK: - Preview

struct SPXScreen_Previews: PreviewProvider {
    static var previews: some View {
        SPXScreen()
            .environmentObject(AppState())
    }
}
