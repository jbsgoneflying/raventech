import SwiftUI

/// A floating tooltip for chart interactions
struct ChartTooltip: View {
    let title: String
    var subtitle: String?
    var rows: [(label: String, value: String)]?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .fontWeight(.black)
                .tracking(0.1)

            if let subtitle = subtitle {
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }

            if let rows = rows, !rows.isEmpty {
                Divider()
                    .padding(.vertical, 4)

                ForEach(rows, id: \.label) { row in
                    HStack {
                        Text(row.label)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text(row.value)
                            .font(.caption2)
                            .fontWeight(.semibold)
                            .monospacedDigit()
                    }
                }
            }
        }
        .padding(12)
        .frame(minWidth: 140)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.black.opacity(0.12), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.14), radius: 18, x: 0, y: 10)
    }
}

/// A tooltip positioned relative to a chart point
struct PositionedTooltip<Content: View>: View {
    let position: CGPoint
    let content: Content
    let containerSize: CGSize

    init(
        position: CGPoint,
        containerSize: CGSize,
        @ViewBuilder content: () -> Content
    ) {
        self.position = position
        self.containerSize = containerSize
        self.content = content()
    }

    var body: some View {
        content
            .position(adjustedPosition)
    }

    private var adjustedPosition: CGPoint {
        // Keep tooltip within bounds
        let tooltipWidth: CGFloat = 160
        let tooltipHeight: CGFloat = 100
        let padding: CGFloat = 12

        var x = position.x + padding
        var y = position.y - tooltipHeight / 2

        // Adjust if too close to right edge
        if x + tooltipWidth > containerSize.width - padding {
            x = position.x - tooltipWidth - padding
        }

        // Adjust if too close to edges
        x = max(padding, min(x, containerSize.width - tooltipWidth - padding))
        y = max(padding, min(y, containerSize.height - tooltipHeight - padding))

        return CGPoint(x: x + tooltipWidth / 2, y: y + tooltipHeight / 2)
    }
}

/// Overlay level indicator for charts
struct ChartLevelIndicator: View {
    let label: String
    let kind: ChartLevel.LevelKind
    var strike: Double?

    var body: some View {
        HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 2)
                .fill(kind.color)
                .frame(width: 16, height: 3)

            Text(label)
                .font(.caption2)
                .fontWeight(.semibold)
                .foregroundStyle(.secondary)

            if let strike = strike {
                Text(String(format: "%.0f", strike))
                    .font(.caption2)
                    .fontWeight(.bold)
                    .monospacedDigit()
            }
        }
    }
}

/// Legend toggle for chart overlays
struct ChartLegend: View {
    let items: [(id: String, label: String, isOn: Binding<Bool>)]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(items, id: \.id) { item in
                    ChipToggle(label: item.label, isOn: item.isOn)
                }
            }
        }
    }
}

// Preview disabled - requires ChartLevel.LevelKind from Charts module
