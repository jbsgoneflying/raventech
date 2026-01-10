import SwiftUI

/// A glass card representing a single day in the calendar
struct CalendarDayCard: View {
    let day: CalendarDay
    var onEventTap: ((CalendarEvent) -> Void)?
    var onTickerTap: ((String) -> Void)?
    var onMoreTickers: ((String, [EarningsTicker]) -> Void)?  // timing, tickers

    @State private var showExpandedTickers: String?  // "BMO", "AMC", "UNK"

    private var isWeekend: Bool {
        guard let dateStr = day.date else { return false }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateStr) else { return false }
        let weekday = Calendar.current.component(.weekday, from: date)
        return weekday == 1 || weekday == 7
    }

    private var dayOfWeek: String {
        guard let dateStr = day.date else { return "" }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateStr) else { return "" }
        let weekdayFormatter = DateFormatter()
        weekdayFormatter.dateFormat = "EEE"
        return weekdayFormatter.string(from: date).uppercased()
    }

    private var dayNumber: String {
        guard let dateStr = day.date else { return "—" }
        return String(dateStr.suffix(2))
    }

    private var totalEarnings: Int {
        (day.earnings?.bmo.count ?? 0) +
        (day.earnings?.amc.count ?? 0) +
        (day.earnings?.unk.count ?? 0)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header
            HStack(alignment: .firstTextBaseline) {
                HStack(spacing: 8) {
                    Text(dayOfWeek)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.secondary)
                        .tracking(0.3)

                    Text(dayNumber)
                        .font(.system(size: 16, weight: .heavy))
                        .tracking(-0.2)
                }

                Spacer()

                if totalEarnings > 0 {
                    Text("\(totalEarnings)")
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.black.opacity(0.05))
                        .clipShape(Capsule())
                }
            }

            // Events
            if !day.events.isEmpty {
                eventPills
            }

            // Earnings groups
            if let earnings = day.earnings {
                earningsSection(label: "BMO", tickers: earnings.bmo)
                earningsSection(label: "AMC", tickers: earnings.amc)
                earningsSection(label: "UNK", tickers: earnings.unk)
            }

            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, minHeight: 170, alignment: .topLeading)
        .background(isWeekend ? Color.white.opacity(0.58) : .ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.08), radius: 15, x: 0, y: 10)
    }

    // MARK: - Event Pills

    @ViewBuilder
    private var eventPills: some View {
        FlowLayout(spacing: 6) {
            ForEach(day.events) { event in
                Button {
                    onEventTap?(event)
                } label: {
                    Pill(
                        text: event.short ?? event.title ?? "Event",
                        style: pillStyleFor(event: event),
                        size: .mini
                    )
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func pillStyleFor(event: CalendarEvent) -> PillStyle {
        switch (event.kind ?? "").uppercased() {
        case "FED": return .fed
        case "ECON": return .econ
        case "OPEX": return .opex
        case "HOLIDAY": return .holiday
        case "EARLY_CLOSE": return .neutral
        case "TREASURY": return .treasury
        default: return .neutral
        }
    }

    // MARK: - Earnings Section

    @ViewBuilder
    private func earningsSection(label: String, tickers: [EarningsTicker]) -> some View {
        let filtered = tickers.filter { !$0.ticker.trimmingCharacters(in: .whitespaces).isEmpty }
        guard !filtered.isEmpty else { return EmptyView().eraseToAnyView() }

        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(label)
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(.secondary)
                    .tracking(0.25)

                Text("\(filtered.count)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            earningsGrid(label: label, tickers: filtered)
        }
        .eraseToAnyView()
    }

    @ViewBuilder
    private func earningsGrid(label: String, tickers: [EarningsTicker]) -> some View {
        let visible = Array(tickers.prefix(4))
        let overflow = tickers.count - 4

        LazyVGrid(
            columns: [
                GridItem(.flexible(), spacing: 6),
                GridItem(.flexible(), spacing: 6)
            ],
            spacing: 6
        ) {
            ForEach(visible, id: \.id) { ticker in
                miniTickerTile(ticker.ticker) {
                    onTickerTap?(ticker.ticker)
                }
            }

            if overflow > 0 {
                Button {
                    onMoreTickers?(label, tickers)
                } label: {
                    Text("+\(overflow)")
                        .font(.caption2)
                        .fontWeight(.heavy)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)
                        .frame(height: 32)
                        .background(Color.white.opacity(0.55))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
                                .foregroundStyle(Color.black.opacity(0.15))
                        )
                }
                .buttonStyle(TileButtonStyle())
            }
        }
    }

    @ViewBuilder
    private func miniTickerTile(_ ticker: String, onTap: @escaping () -> Void) -> some View {
        Button(action: onTap) {
            Text(ticker.uppercased())
                .font(.caption2)
                .fontWeight(.black)
                .tracking(0.2)
                .frame(maxWidth: .infinity)
                .frame(height: 32)
                .background(Color.white.opacity(0.80))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.black.opacity(0.08), lineWidth: 1)
                )
        }
        .buttonStyle(TileButtonStyle())
    }
}

// MARK: - Flow Layout

struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let result = FlowResult(in: proposal.width ?? 0, subviews: subviews, spacing: spacing)
        return result.size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = FlowResult(in: bounds.width, subviews: subviews, spacing: spacing)
        for (index, subview) in subviews.enumerated() {
            subview.place(at: CGPoint(x: bounds.minX + result.positions[index].x,
                                       y: bounds.minY + result.positions[index].y),
                          proposal: .unspecified)
        }
    }

    struct FlowResult {
        var size: CGSize = .zero
        var positions: [CGPoint] = []

        init(in width: CGFloat, subviews: Subviews, spacing: CGFloat) {
            var x: CGFloat = 0
            var y: CGFloat = 0
            var rowHeight: CGFloat = 0

            for subview in subviews {
                let size = subview.sizeThatFits(.unspecified)

                if x + size.width > width, x > 0 {
                    x = 0
                    y += rowHeight + spacing
                    rowHeight = 0
                }

                positions.append(CGPoint(x: x, y: y))
                rowHeight = max(rowHeight, size.height)
                x += size.width + spacing
            }

            self.size = CGSize(width: width, height: y + rowHeight)
        }
    }
}

// MARK: - Type Erasure Helper

extension View {
    func eraseToAnyView() -> AnyView {
        AnyView(self)
    }
}

#Preview {
    ScrollView {
        LazyVGrid(
            columns: [GridItem(.flexible()), GridItem(.flexible())],
            spacing: 12
        ) {
            CalendarDayCard(
                day: CalendarDay(
                    date: "2025-01-06",
                    earnings: EarningsGroups(
                        bmo: [EarningsTicker(ticker: "AAPL"), EarningsTicker(ticker: "MSFT")],
                        amc: [EarningsTicker(ticker: "GOOGL")],
                        unk: []
                    ),
                    events: []
                ),
                onEventTap: { _ in },
                onTickerTap: { _ in }
            )

            CalendarDayCard(
                day: CalendarDay(
                    date: "2025-01-07",
                    earnings: nil,
                    events: []
                )
            )
        }
        .padding()
    }
    .background(Color.gray.opacity(0.1))
}

// MARK: - Helper initializer for CalendarDay

extension CalendarDay {
    init(date: String?, earnings: EarningsGroups?, events: [CalendarEvent]) {
        self.date = date
        self.earnings = earnings
        self.events = events
    }
}

extension EarningsGroups {
    init(bmo: [EarningsTicker], amc: [EarningsTicker], unk: [EarningsTicker]) {
        self.bmo = bmo
        self.amc = amc
        self.unk = unk
    }
}
