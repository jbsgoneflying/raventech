import SwiftUI

/// A metric display card matching the web app's `.metricCard` / `.taCard`
struct MetricCard: View {
    let title: String
    let value: String
    var subtitle: String?
    var badge: Pill?
    var valueColor: Color?
    var onInfoTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center) {
                Text(title)
                    .font(.caption)
                    .fontWeight(.heavy)
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
                    .tracking(0.3)

                Spacer()

                if let onInfoTap = onInfoTap {
                    InfoButton(action: onInfoTap)
                }
            }

            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(value)
                    .font(.title3)
                    .fontWeight(.bold)
                    .monospacedDigit()
                    .foregroundStyle(valueColor ?? RavenTheme.textPrimary)

                if let badge = badge {
                    badge
                }
            }

            if let subtitle = subtitle {
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
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

/// A larger metric card for primary displays
struct MetricCardLarge: View {
    let title: String
    let value: String
    var subtitle: String?
    var secondaryValue: String?
    var onInfoTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
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

            Text(value)
                .font(.title2)
                .fontWeight(.bold)
                .monospacedDigit()

            if let secondary = secondaryValue {
                Text(secondary)
                    .font(.title3)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }

            if let subtitle = subtitle {
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
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

/// A grid layout for metric cards
struct MetricCardGrid<Content: View>: View {
    let columns: Int
    let content: Content

    init(columns: Int = 2, @ViewBuilder content: () -> Content) {
        self.columns = columns
        self.content = content()
    }

    var body: some View {
        LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: columns),
            spacing: 10
        ) {
            content
        }
    }
}

#Preview {
    VStack(spacing: 16) {
        MetricCard(
            title: "Breach Rate",
            value: "23.5%",
            subtitle: "Based on 45 events",
            onInfoTap: {}
        )

        MetricCardLarge(
            title: "Regime Score",
            value: "72.5 / 100",
            subtitle: "Bucket: NORMAL",
            secondaryValue: "Macro: 1.25x",
            onInfoTap: {}
        )

        MetricCardGrid(columns: 2) {
            MetricCard(title: "Put Wing", value: "1.2x")
            MetricCard(title: "Call Wing", value: "0.8x")
        }
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
