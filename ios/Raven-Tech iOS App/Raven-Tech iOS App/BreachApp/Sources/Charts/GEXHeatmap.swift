import SwiftUI

/// A Canvas-based GEX heatmap showing net dollar gamma by strike and expiry
struct GEXHeatmap: View {
    let strikes: [Double]
    let expiries: [String]
    let matrix: [[Double?]]
    var spot: Double?
    var boundaries: HeatmapBoundaries?
    var config: HeatmapConfig = HeatmapConfig()

    @State private var selectedCell: (row: Int, col: Int)?
    @State private var isDragging: Bool = false

    private let padding = EdgeInsets(top: 10, leading: 74, bottom: 26, trailing: 10)

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size

            ZStack {
                // Main heatmap canvas
                Canvas { context, canvasSize in
                    drawHeatmap(context: context, size: canvasSize)
                }

                // Tooltip overlay
                if let cell = selectedCell {
                    tooltipOverlay(cell: cell, size: size)
                }
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        isDragging = true
                        let cell = cellAt(point: value.location, size: size)
                        if cell != selectedCell {
                            selectedCell = cell
                            if cell != nil {
                                let generator = UISelectionFeedbackGenerator()
                                generator.selectionChanged()
                            }
                        }
                    }
                    .onEnded { _ in
                        isDragging = false
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                            if !isDragging {
                                withAnimation(.easeOut(duration: 0.2)) {
                                    selectedCell = nil
                                }
                            }
                        }
                    }
            )
        }
        .frame(height: max(240, CGFloat(expiries.count) * cellHeight + padding.top + padding.bottom))
    }

    // MARK: - Drawing

    private var cellHeight: CGFloat { 16 }

    private func drawHeatmap(context: GraphicsContext, size: CGSize) {
        guard !strikes.isEmpty, !expiries.isEmpty, !matrix.isEmpty else { return }

        let plotWidth = size.width - padding.leading - padding.trailing
        let plotHeight = size.height - padding.top - padding.bottom
        let cellWidth = plotWidth / CGFloat(strikes.count)
        let rowHeight = plotHeight / CGFloat(expiries.count)

        // Find max absolute value for color scaling
        let maxAbs = computeMaxAbsolute()

        // Draw cells
        for (row, rowValues) in matrix.enumerated() {
            for (col, value) in rowValues.enumerated() {
                let x = padding.leading + CGFloat(col) * cellWidth
                let y = padding.top + CGFloat(row) * rowHeight

                let rect = CGRect(x: x, y: y, width: cellWidth, height: rowHeight - 1)
                let color = colorFor(value: value, maxAbs: maxAbs)

                let path = RoundedRectangle(cornerRadius: config.cellCornerRadius)
                    .path(in: rect)

                context.fill(path, with: .color(color))
                context.stroke(path, with: .color(Color.black.opacity(0.06)), lineWidth: 0.7)
            }
        }

        // Draw spot line
        if config.showSpotLine, let spotValue = spot {
            drawVerticalLine(context: context, strike: spotValue, size: size, color: Color.black.opacity(0.32), label: "Spot")
        }

        // Draw boundary lines
        if config.showBoundaries {
            if let downStrike = boundaries?.downsideStrike {
                drawVerticalLine(context: context, strike: downStrike, size: size, color: Color(hex: "DC2626").opacity(0.55), label: nil, dashed: true)
            }
            if let upStrike = boundaries?.upsideStrike {
                drawVerticalLine(context: context, strike: upStrike, size: size, color: Color(hex: "16A34A").opacity(0.55), label: nil, dashed: true)
            }
        }

        // Draw Y-axis labels (expiries)
        for (row, expiry) in expiries.enumerated() {
            let y = padding.top + CGFloat(row) * rowHeight + rowHeight / 2
            let label = String(expiry.suffix(5)) // MM-DD format

            context.draw(
                Text(label)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(Color.black.opacity(0.52)),
                at: CGPoint(x: padding.leading - 8, y: y),
                anchor: .trailing
            )
        }

        // Draw X-axis labels (strikes) - every Nth strike
        let tickEvery = max(1, strikes.count / 6)
        for (col, strike) in strikes.enumerated() {
            guard col % tickEvery == 0 else { continue }

            let x = padding.leading + CGFloat(col) * cellWidth + cellWidth / 2
            let y = size.height - 10

            context.draw(
                Text(String(format: "%.0f", strike))
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(Color.black.opacity(0.52)),
                at: CGPoint(x: x, y: y),
                anchor: .center
            )
        }
    }

    private func drawVerticalLine(
        context: GraphicsContext,
        strike: Double,
        size: CGSize,
        color: Color,
        label: String?,
        dashed: Bool = false
    ) {
        guard let x = xPositionFor(strike: strike, size: size) else { return }

        var path = Path()
        path.move(to: CGPoint(x: x, y: padding.top))
        path.addLine(to: CGPoint(x: x, y: size.height - padding.bottom))

        let style = dashed
            ? StrokeStyle(lineWidth: 1.4, dash: [6, 6])
            : StrokeStyle(lineWidth: 1.2)

        context.stroke(path, with: .color(color), style: style)

        if let label = label {
            context.draw(
                Text(label)
                    .font(.system(size: 11, weight: .black))
                    .foregroundColor(Color.black.opacity(0.46)),
                at: CGPoint(x: x + 6, y: padding.top + 10),
                anchor: .leading
            )
        }
    }

    // MARK: - Color Scaling

    private func computeMaxAbsolute() -> Double {
        var maxAbs: Double = 0
        for row in matrix {
            for value in row {
                if let v = value {
                    maxAbs = max(maxAbs, abs(v))
                }
            }
        }
        return maxAbs > 0 ? maxAbs : 1
    }

    private func colorFor(value: Double?, maxAbs: Double) -> Color {
        guard let v = value else { return config.missingColor }

        // Compress dynamic range using log scale
        let t = log10(1 + abs(v) / 1e6)
        let tMax = log10(1 + maxAbs / 1e6)
        let normalized = tMax > 0 ? t / tMax : 0

        // Interpolate between blue (negative) and orange (positive)
        let intensity = min(1, normalized)

        if v < 0 {
            // Blue for negative
            return Color(
                hue: 210 / 360,
                saturation: 0.72,
                brightness: 0.82 - intensity * 0.34
            ).opacity(0.95)
        } else {
            // Orange for positive
            return Color(
                hue: 20 / 360,
                saturation: 0.72,
                brightness: 0.82 - intensity * 0.34
            ).opacity(0.95)
        }
    }

    // MARK: - Interaction

    private func cellAt(point: CGPoint, size: CGSize) -> (row: Int, col: Int)? {
        let plotWidth = size.width - padding.leading - padding.trailing
        let plotHeight = size.height - padding.top - padding.bottom
        let cellWidth = plotWidth / CGFloat(strikes.count)
        let rowHeight = plotHeight / CGFloat(expiries.count)

        let col = Int((point.x - padding.leading) / cellWidth)
        let row = Int((point.y - padding.top) / rowHeight)

        guard row >= 0, row < expiries.count, col >= 0, col < strikes.count else { return nil }
        return (row, col)
    }

    private func xPositionFor(strike: Double, size: CGSize) -> CGFloat? {
        guard !strikes.isEmpty else { return nil }

        let plotWidth = size.width - padding.leading - padding.trailing
        let cellWidth = plotWidth / CGFloat(strikes.count)

        // Find nearest strike
        var bestIndex: Int?
        var bestDist: Double?
        for (i, s) in strikes.enumerated() {
            let d = abs(s - strike)
            if bestDist == nil || d < bestDist! {
                bestDist = d
                bestIndex = i
            }
        }

        guard let index = bestIndex else { return nil }
        return padding.leading + CGFloat(index) * cellWidth + cellWidth / 2
    }

    // MARK: - Tooltip

    @ViewBuilder
    private func tooltipOverlay(cell: (row: Int, col: Int), size: CGSize) -> some View {
        let plotWidth = size.width - padding.leading - padding.trailing
        let plotHeight = size.height - padding.top - padding.bottom
        let cellWidth = plotWidth / CGFloat(strikes.count)
        let rowHeight = plotHeight / CGFloat(expiries.count)

        let x = padding.leading + CGFloat(cell.col) * cellWidth + cellWidth / 2
        let y = padding.top + CGFloat(cell.row) * rowHeight + rowHeight / 2

        let value = matrix[safe: cell.row]?[safe: cell.col] ?? nil
        let strike = strikes[safe: cell.col]
        let expiry = expiries[safe: cell.row]

        let tooltipX = x < size.width / 2 ? x + 100 : x - 100
        let tooltipY = max(60, min(y, size.height - 80))

        ChartTooltip(
            title: "Net $GEX",
            subtitle: "\(expiry ?? "—") · strike \(strike.map { String(format: "%.0f", $0) } ?? "—")",
            rows: [
                ("Value", formatGEX(value))
            ]
        )
        .position(x: tooltipX, y: tooltipY)
        .transition(.opacity.combined(with: .scale(scale: 0.95)))
    }

    private func formatGEX(_ value: Double?) -> String {
        guard let v = value else { return "—" }
        let abs = Swift.abs(v)
        let sign = v < 0 ? "-" : "+"

        if abs >= 1e12 { return "\(sign)$\(String(format: "%.2f", abs / 1e12))T" }
        if abs >= 1e9 { return "\(sign)$\(String(format: "%.2f", abs / 1e9))B" }
        if abs >= 1e6 { return "\(sign)$\(String(format: "%.2f", abs / 1e6))M" }
        if abs >= 1e3 { return "\(sign)$\(String(format: "%.2f", abs / 1e3))K" }
        return "\(sign)$\(String(format: "%.0f", abs))"
    }
}

// MARK: - Metrics Strip

struct GEXMetricsStrip: View {
    let metrics: HeatmapMetrics?
    let stability: HeatmapStability?

    var body: some View {
        HStack(spacing: 10) {
            metricCard(
                title: "Downside gamma-flip",
                value: formatDistance(metrics?.downsideDistancePts),
                subtitle: formatEM(metrics?.downsideDistanceEm)
            )

            metricCard(
                title: "Upside gamma-flip",
                value: formatDistance(metrics?.upsideDistancePts),
                subtitle: formatEM(metrics?.upsideDistanceEm)
            )

            stabilityCard()
        }
    }

    @ViewBuilder
    private func metricCard(title: String, value: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .fontWeight(.heavy)
                .foregroundStyle(.secondary)

            HStack(baseline: .firstTextBaseline, spacing: 4) {
                Text(value)
                    .font(.subheadline)
                    .fontWeight(.heavy)
                    .monospacedDigit()

                Text("pts")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Text(subtitle)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .monospacedDigit()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.62))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
    }

    @ViewBuilder
    private func stabilityCard() -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Weekly stability")
                .font(.caption)
                .fontWeight(.heavy)
                .foregroundStyle(.secondary)

            if let stability = stability {
                Text(stability.label)
                    .font(.subheadline)
                    .fontWeight(.black)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .foregroundStyle(stability.style.color)
                    .background(stability.style.backgroundColor)
                    .clipShape(Capsule())
                    .overlay(
                        Capsule()
                            .stroke(stability.style.borderColor, lineWidth: 1)
                    )
            } else {
                Text("—")
                    .font(.subheadline)
                    .fontWeight(.heavy)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.62))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
    }

    private func formatDistance(_ pts: Double?) -> String {
        guard let p = pts else { return "—" }
        return String(format: "%.2f", p)
    }

    private func formatEM(_ em: Double?) -> String {
        guard let e = em else { return "—" }
        return String(format: "(%.2f× EM)", e)
    }
}

// MARK: - Safe Collection Access

private extension Collection {
    subscript(safe index: Index) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

// MARK: - Preview

#Preview {
    VStack(spacing: 16) {
        GEXHeatmap(
            strikes: stride(from: 4700, through: 4900, by: 10).map { Double($0) },
            expiries: ["01-10", "01-12", "01-17", "01-19", "01-24"],
            matrix: (0..<5).map { _ in
                (0..<21).map { _ in
                    Double.random(in: -1e9...1e9)
                }
            },
            spot: 4800,
            boundaries: HeatmapBoundaries(downsideStrike: 4750, upsideStrike: 4850)
        )

        GEXMetricsStrip(
            metrics: HeatmapMetrics(
                downsideDistancePts: 45.5,
                upsideDistancePts: 52.3,
                downsideDistanceEm: 0.85,
                upsideDistanceEm: 0.98
            ),
            stability: HeatmapStability(label: "Stable", reasons: ["Symmetric boundaries"])
        )
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
