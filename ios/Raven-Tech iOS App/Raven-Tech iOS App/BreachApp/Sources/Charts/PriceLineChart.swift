import SwiftUI

/// A Canvas-based price line chart with overlay levels and touch interaction
struct PriceLineChart: View {
    let series: [PricePoint]
    var overlayLevels: [ChartLevel] = []
    var config: PriceChartConfig = PriceChartConfig()

    @State private var selectedIndex: Int?
    @State private var isDragging: Bool = false
    @GestureState private var dragLocation: CGPoint?

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size
            let scaling = computeScaling(size: size)

            ZStack {
                // Main chart canvas
                Canvas { context, canvasSize in
                    drawChart(context: context, size: canvasSize, scaling: scaling)
                }

                // Crosshair overlay
                if config.showCrosshair, let index = selectedIndex, index < series.count {
                    crosshairOverlay(index: index, scaling: scaling, size: size)
                }

                // Tooltip
                if let index = selectedIndex, index < series.count {
                    tooltipView(index: index, scaling: scaling, size: size)
                }
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .updating($dragLocation) { value, state, _ in
                        state = value.location
                    }
                    .onChanged { value in
                        isDragging = true
                        let index = scaling.indexAtX(value.location.x, count: series.count)
                        if index != selectedIndex {
                            selectedIndex = index
                            let generator = UISelectionFeedbackGenerator()
                            generator.selectionChanged()
                        }
                    }
                    .onEnded { _ in
                        isDragging = false
                        // Keep selection visible briefly then clear
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                            if !isDragging {
                                withAnimation(.easeOut(duration: 0.2)) {
                                    selectedIndex = nil
                                }
                            }
                        }
                    }
            )
        }
        .frame(height: 260)
        .background(Color.white.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
    }

    // MARK: - Drawing

    private func drawChart(context: GraphicsContext, size: CGSize, scaling: ChartScaling) {
        // Draw price line
        drawPriceLine(context: context, scaling: scaling)

        // Draw overlay levels
        if config.showOverlays {
            drawOverlayLevels(context: context, size: size, scaling: scaling)
        }
    }

    private func drawPriceLine(context: GraphicsContext, scaling: ChartScaling) {
        guard series.count > 1 else { return }

        var path = Path()
        for (index, point) in series.enumerated() {
            let x = scaling.xPosition(for: index, count: series.count)
            let y = scaling.yPosition(for: point.close)

            if index == 0 {
                path.move(to: CGPoint(x: x, y: y))
            } else {
                path.addLine(to: CGPoint(x: x, y: y))
            }
        }

        context.stroke(
            path,
            with: .color(config.lineColor),
            style: StrokeStyle(
                lineWidth: config.lineWidth,
                lineCap: .round,
                lineJoin: .round
            )
        )
    }

    private func drawOverlayLevels(context: GraphicsContext, size: CGSize, scaling: ChartScaling) {
        for level in overlayLevels {
            let y = scaling.yPosition(for: level.value)

            // Skip if outside visible range
            guard y >= config.padding.top && y <= size.height - config.padding.bottom else { continue }

            var path = Path()
            path.move(to: CGPoint(x: config.padding.leading, y: y))
            path.addLine(to: CGPoint(x: size.width - config.padding.trailing, y: y))

            context.stroke(
                path,
                with: .color(level.kind.color),
                style: StrokeStyle(
                    lineWidth: 1.4,
                    lineCap: .round,
                    dash: level.kind.dashPattern
                )
            )
        }
    }

    // MARK: - Crosshair

    @ViewBuilder
    private func crosshairOverlay(index: Int, scaling: ChartScaling, size: CGSize) -> some View {
        let point = series[index]
        let x = scaling.xPosition(for: index, count: series.count)
        let y = scaling.yPosition(for: point.close)

        // Vertical line
        Path { path in
            path.move(to: CGPoint(x: x, y: config.padding.top))
            path.addLine(to: CGPoint(x: x, y: size.height - config.padding.bottom))
        }
        .stroke(Color.black.opacity(0.20), lineWidth: 1)

        // Horizontal line
        Path { path in
            path.move(to: CGPoint(x: config.padding.leading, y: y))
            path.addLine(to: CGPoint(x: size.width - config.padding.trailing, y: y))
        }
        .stroke(Color.black.opacity(0.20), lineWidth: 1)

        // Dot at intersection
        Circle()
            .fill(Color(hex: "0F172A").opacity(0.85))
            .frame(width: 8, height: 8)
            .position(x: x, y: y)
    }

    // MARK: - Tooltip

    @ViewBuilder
    private func tooltipView(index: Int, scaling: ChartScaling, size: CGSize) -> some View {
        let point = series[index]
        let x = scaling.xPosition(for: index, count: series.count)

        // Position tooltip to avoid edges
        let tooltipX = x < size.width / 2 ? x + 80 : x - 80
        let tooltipY = config.padding.top + 40

        ChartTooltip(
            title: formatDate(point.date),
            subtitle: formatPrice(point.close),
            rows: nearbyLevelRows(for: point.close)
        )
        .position(x: tooltipX, y: tooltipY)
        .transition(.opacity.combined(with: .scale(scale: 0.95)))
    }

    // MARK: - Helpers

    private func computeScaling(size: CGSize) -> ChartScaling {
        let closes = series.map(\.close)
        let levelValues = overlayLevels.map(\.value)
        let allValues = closes + levelValues

        var minY = allValues.min() ?? 0
        var maxY = allValues.max() ?? 100

        // Add padding to Y range
        let yPad = (maxY - minY) * 0.06
        minY -= yPad
        maxY += yPad

        return ChartScaling(
            minX: 0,
            maxX: Double(series.count - 1),
            minY: minY,
            maxY: maxY,
            width: size.width,
            height: size.height,
            padding: config.padding
        )
    }

    private func formatDate(_ dateString: String) -> String {
        // Return short date format
        String(dateString.prefix(10))
    }

    private func formatPrice(_ price: Double) -> String {
        String(format: "%.2f", price)
    }

    private func nearbyLevelRows(for price: Double) -> [(label: String, value: String)]? {
        guard !overlayLevels.isEmpty else { return nil }

        return overlayLevels.prefix(3).map { level in
            (label: level.label, value: String(format: "%.0f", level.value))
        }
    }
}

// MARK: - Legend

struct PriceChartLegend: View {
    let levels: [ChartLevel]
    @Binding var visibleKinds: Set<ChartLevel.LevelKind>

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(uniqueKinds, id: \.rawValue) { kind in
                    ChipToggle(
                        label: labelFor(kind),
                        isOn: Binding(
                            get: { visibleKinds.contains(kind) },
                            set: { isOn in
                                if isOn {
                                    visibleKinds.insert(kind)
                                } else {
                                    visibleKinds.remove(kind)
                                }
                            }
                        )
                    )
                }
            }
        }
    }

    private var uniqueKinds: [ChartLevel.LevelKind] {
        Array(Set(levels.map(\.kind))).sorted { $0.rawValue < $1.rawValue }
    }

    private func labelFor(_ kind: ChartLevel.LevelKind) -> String {
        switch kind {
        case .putWall: return "Put wall"
        case .callWall: return "Call wall"
        case .gammaFlip: return "Gamma flip"
        case .cluster: return "Clusters"
        case .gammaPeak: return "Gamma peaks"
        }
    }
}

// MARK: - Preview

#Preview {
    VStack {
        PriceLineChart(
            series: (0..<90).map { i in
                PricePoint(
                    date: "2024-01-\(String(format: "%02d", (i % 28) + 1))",
                    close: 4800 + Double.random(in: -50...50) + Double(i) * 0.5
                )
            },
            overlayLevels: [
                ChartLevel(kind: .putWall, value: 4750, label: "Put wall"),
                ChartLevel(kind: .callWall, value: 4900, label: "Call wall"),
                ChartLevel(kind: .gammaFlip, value: 4820, label: "Gamma flip")
            ]
        )
        .padding()
    }
    .background(Color.gray.opacity(0.1))
}
