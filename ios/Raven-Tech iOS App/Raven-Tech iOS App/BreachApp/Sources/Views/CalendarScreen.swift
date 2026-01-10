import SwiftUI

struct CalendarScreen: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var viewModel = CalendarViewModel()

    @State private var selectedEvent: CalendarEvent?
    @State private var selectedTicker: String?
    @State private var expandedTickers: (timing: String, date: String, tickers: [EarningsTicker])?

    var body: some View {
        NavigationStack {
            ZStack {
                // Background gradient
                backgroundGradient

                ScrollView {
                    VStack(spacing: 16) {
                        if viewModel.isLoading && viewModel.response == nil {
                            loadingView
                        } else if let error = viewModel.error {
                            errorView(error)
                        } else if let response = viewModel.response {
                            calendarContent(response)
                        } else {
                            emptyView
                        }
                    }
                    .padding(.bottom, 32)
                }
                .refreshable {
                    await viewModel.load(client: appState.apiClient)
                }
            }
            .navigationTitle("Calendar")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        Task { await viewModel.load(client: appState.apiClient) }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.caption.bold())
                    }
                    .disabled(viewModel.isLoading)
                }
            }
            .task {
                if viewModel.response == nil {
                    await viewModel.load(client: appState.apiClient)
                }
            }
            .sheet(item: $selectedEvent) { event in
                EventDetailSheet(event: event)
            }
            .sheet(item: $expandedTickersBinding) { expanded in
                TickerExpandSheet(
                    title: "\(expanded.timing) Earnings",
                    date: expanded.date,
                    timing: expanded.timing,
                    tickers: expanded.tickers.map(\.ticker)
                ) { ticker in
                    expandedTickers = nil
                    selectedTicker = ticker
                }
            }
            .sheet(item: $selectedTicker) { ticker in
                TickerDetailSheet(ticker: ticker) {
                    selectedTicker = nil
                }
            }
        }
    }

    // MARK: - Background

    private var backgroundGradient: some View {
        ZStack {
            Color(UIColor.systemBackground)

            RadialGradient(
                colors: [Color(hex: "007AFF").opacity(0.08), .clear],
                center: UnitPoint(x: 0.18, y: -0.10),
                startRadius: 0,
                endRadius: 700
            )

            RadialGradient(
                colors: [Color(hex: "34C759").opacity(0.05), .clear],
                center: UnitPoint(x: 0.85, y: 0.10),
                startRadius: 0,
                endRadius: 600
            )
        }
        .ignoresSafeArea()
    }

    // MARK: - Loading / Error / Empty

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text("Loading calendar…")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 100)
    }

    private func errorView(_ error: AppError) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.largeTitle)
                .foregroundStyle(.red.opacity(0.8))

            Text(error.localizedDescription)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Button("Retry") {
                Task { await viewModel.load(client: appState.apiClient) }
            }
            .buttonStyle(.bordered)
        }
        .padding()
        .frame(maxWidth: .infinity)
    }

    private var emptyView: some View {
        VStack(spacing: 12) {
            Image(systemName: "calendar")
                .font(.largeTitle)
                .foregroundStyle(.secondary)

            Text("No calendar data")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity)
        .padding(.top, 100)
    }

    // MARK: - Calendar Content

    @ViewBuilder
    private func calendarContent(_ response: CalendarResponse) -> some View {
        // Header with date range
        if let range = response.range {
            CalendarHeader(
                title: formatMonthYear(range.start),
                subtitle: "\(formatShortDate(range.start)) – \(formatShortDate(range.end))",
                onRefresh: {
                    Task { await viewModel.load(client: appState.apiClient) }
                }
            )
        }

        // Scan strip
        ScanStrip(
            earnings: aggregateEarnings(response.days),
            macro: aggregateMacro(response.days)
        )
        .padding(.horizontal)

        // Loading indicator overlay
        if viewModel.isLoading {
            HStack(spacing: 8) {
                ProgressView()
                    .scaleEffect(0.8)
                Text("Updating…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 8)
        }

        // Day cards grid
        LazyVGrid(
            columns: [
                GridItem(.flexible(), spacing: 12),
                GridItem(.flexible(), spacing: 12)
            ],
            spacing: 12
        ) {
            ForEach(response.days.filter { isWeekday($0.date) }) { day in
                CalendarDayCard(
                    day: day,
                    onEventTap: { event in
                        selectedEvent = event
                    },
                    onTickerTap: { ticker in
                        selectedTicker = ticker
                    },
                    onMoreTickers: { timing, tickers in
                        expandedTickers = (timing: timing, date: day.date ?? "", tickers: tickers)
                    }
                )
            }
        }
        .padding(.horizontal)

        // Meta info
        if let meta = response.meta {
            metaFooter(meta)
        }
    }

    // MARK: - Meta Footer

    @ViewBuilder
    private func metaFooter(_ meta: CalendarMeta) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if let counts = meta.counts {
                Text("Source: \(counts.earningsSource ?? "—") · \(counts.tickersEligible ?? 0) tickers")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            if let notes = meta.notes, !notes.isEmpty {
                ForEach(notes.prefix(2), id: \.self) { note in
                    Text(note)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .padding(.horizontal)
        .padding(.top, 8)
    }

    // MARK: - Helpers

    private func aggregateEarnings(_ days: [CalendarDay]) -> ScanStrip.EarningsScan {
        var bmo = 0, amc = 0, unk = 0
        for day in days {
            bmo += day.earnings?.bmo.count ?? 0
            amc += day.earnings?.amc.count ?? 0
            unk += day.earnings?.unk.count ?? 0
        }
        return ScanStrip.EarningsScan(total: bmo + amc + unk, bmo: bmo, amc: amc, unk: unk)
    }

    private func aggregateMacro(_ days: [CalendarDay]) -> ScanStrip.MacroScan {
        var fed = 0, econ = 0
        for day in days {
            for event in day.events {
                let kind = (event.kind ?? "").uppercased()
                if kind == "FED" { fed += 1 }
                if kind == "ECON" { econ += 1 }
            }
        }
        return ScanStrip.MacroScan(total: fed + econ, fed: fed, econ: econ)
    }

    private func isWeekday(_ dateStr: String?) -> Bool {
        guard let dateStr = dateStr else { return true }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateStr) else { return true }
        let weekday = Calendar.current.component(.weekday, from: date)
        return weekday >= 2 && weekday <= 6  // Mon-Fri
    }

    private func formatMonthYear(_ dateStr: String?) -> String {
        guard let dateStr = dateStr else { return "—" }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateStr) else { return "—" }
        let outFormatter = DateFormatter()
        outFormatter.dateFormat = "MMMM yyyy"
        return outFormatter.string(from: date)
    }

    private func formatShortDate(_ dateStr: String?) -> String {
        guard let dateStr = dateStr else { return "—" }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateStr) else { return "—" }
        let outFormatter = DateFormatter()
        outFormatter.dateFormat = "MMM d"
        return outFormatter.string(from: date)
    }

    // Binding wrapper for sheet
    private var expandedTickersBinding: Binding<ExpandedTickersData?> {
        Binding(
            get: {
                guard let exp = expandedTickers else { return nil }
                return ExpandedTickersData(timing: exp.timing, date: exp.date, tickers: exp.tickers)
            },
            set: { newValue in
                if let nv = newValue {
                    expandedTickers = (timing: nv.timing, date: nv.date, tickers: nv.tickers)
                } else {
                    expandedTickers = nil
                }
            }
        )
    }
}

// MARK: - Supporting Types

struct ExpandedTickersData: Identifiable {
    let id = UUID()
    let timing: String
    let date: String
    let tickers: [EarningsTicker]
}

extension String: Identifiable {
    public var id: String { self }
}

// MARK: - Event Detail Sheet

struct EventDetailSheet: View {
    let event: CalendarEvent

    var body: some View {
        NavigationStack {
            List {
                Section("Details") {
                    row("Title", event.title ?? event.short ?? "Event")
                    row("Kind", event.kind)
                    row("Date", event.date)
                    row("Time (ET)", event.timeEt)

                    if let importance = event.importance {
                        HStack {
                            Text("Importance")
                            Spacer()
                            importancePill(importance)
                        }
                    }
                }

                if let playbook = event.playbook {
                    if let deskView = playbook.deskView, !deskView.isEmpty {
                        Section("Desk View") {
                            ForEach(deskView, id: \.self) { item in
                                Text(item)
                                    .font(.subheadline)
                            }
                        }
                    }

                    if let watch = playbook.watch, !watch.isEmpty {
                        Section("Watch") {
                            ForEach(watch, id: \.self) { item in
                                Text(item)
                                    .font(.subheadline)
                            }
                        }
                    }
                }
            }
            .navigationTitle(event.short ?? "Event")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func row(_ label: String, _ value: String?) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value ?? "—")
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func importancePill(_ importance: Int) -> some View {
        let style: PillStyle = importance >= 3 ? .bad : importance >= 2 ? .warn : .neutral
        Pill(text: "\(importance)", style: style, size: .mini)
    }
}

// MARK: - Ticker Detail Sheet

struct TickerDetailSheet: View {
    let ticker: String
    var onRunEngine1: (() -> Void)?

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                // Logo placeholder
                RoundedRectangle(cornerRadius: 20)
                    .fill(Color(hex: "121216").opacity(0.92))
                    .frame(width: 80, height: 80)
                    .overlay(
                        Text(String(ticker.prefix(2)))
                            .font(.title2)
                            .fontWeight(.black)
                            .foregroundStyle(.white.opacity(0.9))
                    )

                Text(ticker)
                    .font(.title2)
                    .fontWeight(.bold)

                // Quick actions
                VStack(spacing: 12) {
                    Button {
                        onRunEngine1?()
                    } label: {
                        HStack {
                            Image(systemName: "chart.bar.doc.horizontal")
                            Text("Run Engine 1")
                        }
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.accentColor)
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    }

                    Text("Analyze earnings history and breach probability")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .padding()

                Spacer()
            }
            .padding(.top, 32)
            .navigationTitle("Ticker Details")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium])
        .presentationDragIndicator(.visible)
    }
}

// MARK: - Preview

struct CalendarScreen_Previews: PreviewProvider {
    static var previews: some View {
        CalendarScreen()
            .environmentObject(AppState())
    }
}
