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

        // VWAP and Net GEX - right under Regime/Macro
        vwapNetGexSection(ic: ic, levels: levels?.levels)
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

            // Use COMPOSITE + SLOPE mode (matches web app defaults)
            // Composite groups expiries into buckets (e.g., "0-5 DTE", "6-10 DTE", "20-40 DTE")
            // Slope uses slopeNetDollarGex for cleaner visualization
            let compositeData = buildCompositeHeatmapData(heatmap)

            if heatmap.enabled == true, !compositeData.strikes.isEmpty, !compositeData.labels.isEmpty, !compositeData.matrix.isEmpty {
                GEXHeatmap(
                    strikes: compositeData.strikes,
                    expiries: compositeData.labels,
                    matrix: compositeData.matrix,
                    spot: heatmap.spot,
                    boundaries: HeatmapBoundaries(
                        downsideStrike: heatmap.boundaries?.downsideAccelerationBoundaryStrike,
                        upsideStrike: heatmap.boundaries?.upsideAccelerationBoundaryStrike
                    )
                )
            } else {
                VStack(spacing: 8) {
                    Text("No heatmap data available")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)

                    if let notes = heatmap.notes, !notes.isEmpty {
                        Text(notes.first ?? "")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                }
                .padding()
            }

            // Metrics strip (from metrics)
            let metrics = heatmap.metrics
            let stability = heatmap.stability
            if metrics != nil || stability != nil {
                GEXMetricsStrip(
                    metrics: HeatmapMetrics(
                        downsideDistancePts: metrics?.downsideDistancePts,
                        upsideDistancePts: metrics?.upsideDistancePts,
                        downsideDistanceEm: metrics?.downsideDistanceEm,
                        upsideDistanceEm: metrics?.upsideDistanceEm
                    ),
                    stability: HeatmapStability(
                        label: stability?.label ?? "Unknown",
                        reasons: stability?.reasons ?? []
                    )
                )
            }
        }
    }

    /// Build heatmap data from composite buckets using slope values
    private func buildCompositeHeatmapData(_ heatmap: SPXGexHeatmap) -> (strikes: [Double], labels: [String], matrix: [[Double?]]) {
        // Get strikes from composite
        guard let strikes = heatmap.composite?.strikes, !strikes.isEmpty else {
            return ([], [], [])
        }

        // Get buckets from composite
        guard let buckets = heatmap.composite?.buckets, !buckets.isEmpty else {
            return ([], [], [])
        }

        // Build labels and matrix from buckets using SLOPE data
        var labels: [String] = []
        var matrix: [[Double?]] = []

        for bucket in buckets {
            // Use bucket label (e.g., "0-5 DTE", "6-10 DTE", "20-40 DTE")
            let label = bucket.label ?? bucket.key ?? "—"
            labels.append(label)

            // Use slopeNetDollarGex for slope mode (cleaner visualization)
            // Fall back to netDollarGex if slope not available
            let rowData = bucket.slopeNetDollarGex ?? bucket.netDollarGex ?? []
            matrix.append(rowData)
        }

        return (strikes, labels, matrix)
    }

    // MARK: - Key Metrics Grid (all cards in one section)

    @ViewBuilder
    private func vwapNetGexSection(ic: SPXICResponse, levels: SPXLiveLevels?) -> some View {
        VStack(spacing: 12) {
            // Row 1: VWAP & Net GEX
            MetricCardGrid(columns: 2) {
                // VWAP (from IC response)
                if let vwap = ic.current?.vwap, vwap.enabled == true {
                    MetricCard(
                        title: "VWAP (Daily)",
                        value: String(format: "%.2f", vwap.value ?? 0),
                        subtitle: vwapDistanceText(vwap),
                        onInfoTap: { showingInfoSheet = .custom(
                            title: "VWAP",
                            body: "Volume-weighted average price for the current session.",
                            bullets: ["Used to assess intraday positioning relative to fair value"],
                            deskView: nil
                        )}
                    )
                }

                // Net GEX
                MetricCard(
                    title: "Net GEX",
                    value: levels?.dealerGamma?.netGex.map { formatLargeNumber($0) } ?? "—",
                    subtitle: levels?.dealerGamma?.netGammaSign ?? "No data"
                )
            }

            // Row 2: Dealer Gamma (Weekly) & Dealer Gamma (Nearest)
            MetricCardGrid(columns: 2) {
                // Dealer Gamma (Weekly)
                MetricCard(
                    title: "Dealer Gamma (Weekly)",
                    value: formatDealerGammaValue(levels?.weeklyFriday?.dealerGamma),
                    subtitle: levels?.weeklyFriday.map { formatDealerGammaSubtitle($0) } ?? "No weekly data",
                    onInfoTap: {
                        if let weekly = levels?.weeklyFriday {
                            showingInfoSheet = .custom(
                                title: "Dealer Gamma (Weekly)",
                                body: "Net dealer gamma exposure for the weekly expiration.",
                                bullets: buildDealerGammaBullets(weekly),
                                deskView: nil
                            )
                        }
                    }
                )

                // Dealer Gamma (Nearest)
                MetricCard(
                    title: "Dealer Gamma (Nearest)",
                    value: formatDealerGammaValue(levels?.nearestDaily?.dealerGamma),
                    subtitle: levels?.nearestDaily.map { formatDealerGammaSubtitle($0) } ?? "No nearest data",
                    onInfoTap: {
                        if let nearest = levels?.nearestDaily {
                            showingInfoSheet = .custom(
                                title: "Dealer Gamma (Nearest)",
                                body: "Net dealer gamma exposure for the nearest expiration.",
                                bullets: buildDealerGammaBullets(nearest),
                                deskView: nil
                            )
                        }
                    }
                )
            }

            // Row 3: Hedging Pressure & Tail Ignition
            MetricCardGrid(columns: 2) {
                // Hedging Pressure (HPI)
                MetricCard(
                    title: "Hedging Pressure (HPI)",
                    value: levels?.hedgingPressure.map { formatHedgingPressureValue($0) } ?? "—",
                    subtitle: levels?.hedgingPressure.map { formatHedgingPressureSubtitle($0, levels: levels) } ?? "No HPI data",
                    onInfoTap: {
                        if let hp = levels?.hedgingPressure {
                            showingInfoSheet = .custom(
                                title: "Hedging Pressure (HPI)",
                                body: "Measures the directional bias of dealer hedging activity based on gamma exposure.",
                                bullets: buildHedgingPressureBullets(hp, levels: levels),
                                deskView: nil
                            )
                        }
                    }
                )

                // Tail Ignition
                MetricCard(
                    title: "Tail Ignition",
                    value: levels?.tailIgnition.map { formatTailIgnitionValue($0) } ?? "—",
                    subtitle: levels?.tailIgnition.map { formatTailIgnitionSubtitle($0) } ?? "No tail data",
                    onInfoTap: {
                        if let ti = levels?.tailIgnition {
                            showingInfoSheet = .custom(
                                title: "Tail Ignition",
                                body: "Risk of accelerated moves in the tails due to gamma positioning.",
                                bullets: buildTailIgnitionBullets(ti, levels: levels),
                                deskView: nil
                            )
                        }
                    }
                )
            }

            // Row 4: Vol Pressure (single card, full width would need adjustment or pair with another)
            MetricCardGrid(columns: 2) {
                // Vol Pressure
                MetricCard(
                    title: "Vol Pressure",
                    value: levels?.volPressure.map { formatVolPressureValue($0) } ?? "—",
                    subtitle: levels?.volPressure.map { formatVolPressureSubtitle($0) } ?? "No vol data",
                    onInfoTap: {
                        if let vp = levels?.volPressure {
                            showingInfoSheet = .custom(
                                title: "Vol Pressure",
                                body: "Volatility pressure based on put/call ratios, IV rank, and term structure.",
                                bullets: buildVolPressureBullets(vp),
                                deskView: nil
                            )
                        }
                    }
                )
            }
        }
    }

    // MARK: - Dealer Gamma Formatting

    private func formatDealerGammaValue(_ dg: DealerGammaData?) -> String {
        guard let dg = dg else { return "—" }
        let sign = dg.netGammaSign?.uppercased() ?? "—"
        let magnitude = dg.magnitudeBucket?.uppercased() ?? "—"
        return "\(sign) · \(magnitude)"
    }

    private func formatDealerGammaSubtitle(_ view: SPXLevelView) -> String {
        var parts: [String] = []
        if let expiry = view.expiry {
            parts.append("exp=\(expiry)")
        }
        if let oi = view.oiClusters {
            if let putWall = oi.putWall?.peakStrike {
                parts.append("put=\(Int(putWall))")
            }
            if let callWall = oi.callWall?.peakStrike {
                parts.append("call=\(Int(callWall))")
            }
        }
        return parts.isEmpty ? nil ?? "—" : parts.joined(separator: " · ")
    }

    private func buildDealerGammaBullets(_ view: SPXLevelView) -> [String] {
        var bullets: [String] = []
        if let expiry = view.expiry {
            bullets.append("Expiry: \(expiry)")
        }
        if let dg = view.dealerGamma {
            if let sign = dg.netGammaSign {
                bullets.append("Net Gamma Sign: \(sign)")
            }
            if let mag = dg.magnitudeBucket {
                bullets.append("Magnitude: \(mag)")
            }
        }
        if let oi = view.oiClusters {
            if let putWall = oi.putWall {
                bullets.append("Put Wall: \(Int(putWall.peakStrike ?? 0)) (OI: \(formatLargeNumber(putWall.totalOI ?? 0)))")
            }
            if let callWall = oi.callWall {
                bullets.append("Call Wall: \(Int(callWall.peakStrike ?? 0)) (OI: \(formatLargeNumber(callWall.totalOI ?? 0)))")
            }
        }
        if let warnings = view.dealerGamma?.warnings, !warnings.isEmpty {
            bullets.append(contentsOf: warnings)
        }
        return bullets
    }

    // MARK: - Hedging Pressure Formatting

    private func formatHedgingPressureValue(_ hp: HedgingPressure) -> String {
        var value = ""
        if let netGex = hp.netGex {
            value = formatLargeNumber(netGex)
        } else if let label = hp.label ?? hp.bucket {
            value = label
        } else {
            value = "—"
        }
        if let bp = hp.bpFromFlip {
            value += " @\(Int(bp))bp"
        }
        return value
    }

    private func formatHedgingPressureSubtitle(_ hp: HedgingPressure, levels: SPXLiveLevels?) -> String {
        var parts: [String] = []
        if let gamma = hp.gamma {
            parts.append("Γ=\(formatScientific(gamma))")
        }
        if let band = hp.bandPct {
            parts.append("band=±\(Int(band * 100))%")
        }
        if let strikes = hp.nStrikes {
            parts.append("strikes=\(strikes)")
        }
        return parts.isEmpty ? hp.reasons?.first ?? "—" : parts.joined(separator: " · ")
    }

    private func buildHedgingPressureBullets(_ hp: HedgingPressure, levels: SPXLiveLevels?) -> [String] {
        var bullets: [String] = []
        if let netGex = hp.netGex {
            bullets.append("Net $GEX: \(formatLargeNumber(netGex))")
        }
        if let gamma = hp.gamma {
            bullets.append("Gamma (Γ): \(formatScientific(gamma))")
        }
        if let bp = hp.bpFromFlip {
            bullets.append("BP from Flip: \(Int(bp))bp")
        }
        if let band = hp.bandPct {
            bullets.append("Band: ±\(Int(band * 100))%")
        }
        if let strikes = hp.nStrikes {
            bullets.append("Strikes analyzed: \(strikes)")
        }
        if let reasons = hp.reasons, !reasons.isEmpty {
            bullets.append(contentsOf: reasons)
        }
        // Add nearest info if available
        if let nearest = levels?.nearestDaily?.addons?.hedgingPressure {
            bullets.append("Nearest: \(formatLargeNumber(nearest.netGex ?? 0)) @\(Int(nearest.bpFromFlip ?? 0))bp")
        }
        return bullets
    }

    // MARK: - Tail Ignition Formatting

    private func formatTailIgnitionValue(_ ti: TailIgnition) -> String {
        var parts: [String] = []
        if let down = ti.downScore, let downBucket = ti.downBucket {
            parts.append("Down \(Int(down)) \(downBucket.uppercased())")
        }
        if let up = ti.upScore, let upBucket = ti.upBucket {
            parts.append("Up \(Int(up)) \(upBucket.uppercased())")
        }
        if parts.isEmpty {
            return ti.label ?? ti.bucket ?? "—"
        }
        return parts.joined(separator: " · ")
    }

    private func formatTailIgnitionSubtitle(_ ti: TailIgnition) -> String {
        var parts: [String] = []
        if let putWallPct = ti.putWallDistancePct {
            parts.append("put=\(String(format: "%.2f", putWallPct))%")
        }
        if let callWallPct = ti.callWallDistancePct {
            parts.append("call=\(String(format: "%.2f", callWallPct))%")
        }
        if let flipPct = ti.flipDistancePct {
            parts.append("flip=\(String(format: "%.2f", flipPct))%")
        }
        return parts.isEmpty ? ti.reasons?.first ?? "—" : "walls: " + parts.joined(separator: " · ")
    }

    private func buildTailIgnitionBullets(_ ti: TailIgnition, levels: SPXLiveLevels?) -> [String] {
        var bullets: [String] = []
        if let down = ti.downScore, let downBucket = ti.downBucket {
            bullets.append("Downside: \(Int(down)) (\(downBucket))")
        }
        if let up = ti.upScore, let upBucket = ti.upBucket {
            bullets.append("Upside: \(Int(up)) (\(upBucket))")
        }
        if let putWallPct = ti.putWallDistancePct {
            bullets.append("Put Wall Distance: \(String(format: "%.2f", putWallPct))%")
        }
        if let callWallPct = ti.callWallDistancePct {
            bullets.append("Call Wall Distance: \(String(format: "%.2f", callWallPct))%")
        }
        if let flipPct = ti.flipDistancePct {
            bullets.append("Gamma Flip Distance: \(String(format: "%.2f", flipPct))%")
        }
        if let reasons = ti.reasons, !reasons.isEmpty {
            bullets.append(contentsOf: reasons)
        }
        // Add nearest info if available
        if let nearest = levels?.nearestDaily?.addons?.tailIgnition {
            var nearestParts: [String] = []
            if let down = nearest.downScore, let downBucket = nearest.downBucket {
                nearestParts.append("Down \(Int(down)) \(downBucket)")
            }
            if let up = nearest.upScore, let upBucket = nearest.upBucket {
                nearestParts.append("Up \(Int(up)) \(upBucket)")
            }
            if !nearestParts.isEmpty {
                bullets.append("Nearest: " + nearestParts.joined(separator: " · "))
            }
        }
        return bullets
    }

    // MARK: - Vol Pressure Formatting

    private func formatVolPressureValue(_ vp: VolPressureData) -> String {
        var value = vp.label ?? vp.bucket ?? "—"
        if let z = vp.zScore {
            value += " · z=\(String(format: "%.2f", z))"
        }
        return value
    }

    private func formatVolPressureSubtitle(_ vp: VolPressureData) -> String {
        var parts: [String] = []
        if let iv7 = vp.iv7 {
            parts.append("iv7=\(String(format: "%.2f", iv7))")
        } else {
            parts.append("iv7=—")
        }
        if let rv10 = vp.rv10 {
            parts.append("rv10=\(String(format: "%.2f", rv10))")
        }
        if let term = vp.termStructure {
            parts.append("term=\(term)")
        } else {
            parts.append("term=—")
        }
        return parts.isEmpty ? vp.reasons?.first ?? "—" : parts.joined(separator: " · ")
    }

    private func buildVolPressureBullets(_ vp: VolPressureData) -> [String] {
        var bullets: [String] = []
        if let label = vp.label {
            bullets.append("Pressure: \(label)")
        }
        if let z = vp.zScore {
            bullets.append("Z-Score: \(String(format: "%.2f", z))")
        }
        if let iv7 = vp.iv7 {
            bullets.append("IV7: \(String(format: "%.2f", iv7))")
        }
        if let rv10 = vp.rv10 {
            bullets.append("RV10: \(String(format: "%.2f", rv10))")
        }
        if let term = vp.termStructure {
            bullets.append("Term Structure: \(term)")
        }
        if let pcRatio = vp.putCallRatio {
            bullets.append("Put/Call Ratio: \(String(format: "%.2f", pcRatio))")
        }
        if let reasons = vp.reasons, !reasons.isEmpty {
            bullets.append(contentsOf: reasons)
        }
        return bullets
    }

    // MARK: - Helper Formatters

    private func formatScientific(_ value: Double) -> String {
        if abs(value) >= 1e6 {
            return String(format: "%.2fe+%d", value / pow(10, floor(log10(abs(value)))), Int(floor(log10(abs(value)))))
        } else if abs(value) >= 1000 {
            return String(format: "%.2fK", value / 1000)
        } else {
            return String(format: "%.2f", value)
        }
    }

    // MARK: - Additional Metrics (kept for any overflow cards)

    @ViewBuilder
    private func additionalMetricsSection(_ levels: SPXLiveLevels) -> some View {
        // This section is now minimal - most cards moved to vwapNetGexSection
        // Only show if there's additional data not covered above
        EmptyView()
    }

    private func vwapDistanceText(_ vwap: Engine2VWAP) -> String? {
        guard let live = vwap.livePrice, let value = vwap.value, value > 0 else { return nil }
        let diff = live - value
        let pct = (diff / value) * 100
        if abs(pct) < 0.05 {
            return "At VWAP"
        } else if diff > 0 {
            return String(format: "%.2f pts above (%.2f%%)", diff, pct)
        } else {
            return String(format: "%.2f pts below (%.2f%%)", abs(diff), abs(pct))
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
