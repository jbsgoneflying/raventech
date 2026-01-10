import SwiftUI

struct EngineOneScreen: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var viewModel = EngineOneViewModel()

    var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("Ticker")) {
                    TextField("AAPL", text: $viewModel.ticker)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                    Button {
                        Task { await viewModel.load(client: appState.apiClient) }
                    } label: {
                        viewModel.isLoading ? AnyView(ProgressView()) : AnyView(Text("Run"))
                    }
                    .disabled(viewModel.isLoading)
                }

                if let err = viewModel.error {
                    Section {
                        Text(err.localizedDescription).foregroundColor(.red)
                    }
                }

                if let resp = viewModel.response {
                    summarySection(resp)
                    wingSection(resp.wingRecommendation)
                    quartersSection(resp.quarters)
                    eventsSection(resp.events)
                }
            }
            .navigationTitle("Engine 1")
        }
    }

    private func summarySection(_ resp: BreachResponse) -> some View {
        Section(header: Text("Summary")) {
            let s = resp.summary
            summaryRow("Breach rate", pct: s?.breachRatePct)
            summaryRow("Avg overshoot (up)", pct: s?.avgUpOvershootPct)
            summaryRow("Avg realized / implied", value: s?.avgRealizedAllPct, suffix: "% vs \(s?.avgImpliedAllPct ?? 0)%")
            if let used = s?.eventsUsed {
                Text("Events used: \(used)").font(.subheadline)
            }
        }
    }

    private func summaryRow(_ label: String, pct: Double?) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(formatPct(pct))
                .monospacedDigit()
        }
    }

    private func summaryRow(_ label: String, value: Double?, suffix: String = "") -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value.map { String(format: "%.2f", $0) } ?? "—")
                .monospacedDigit()
            if !suffix.isEmpty {
                Text(suffix).foregroundColor(.secondary).font(.caption)
            }
        }
    }

    private func wingSection(_ rec: WingRecommendation?) -> some View {
        Section(header: Text("Wing recommendation")) {
            Text(rec?.recommendationLabel ?? "—").font(.headline)
            if let call = rec?.callWingMultiple, let put = rec?.putWingMultiple {
                Text("Calls: \(String(format: "%.2fx", call)) · Puts: \(String(format: "%.2fx", put))")
            }
            if let gate = rec?.tradeGate {
                Text("Gate: \(gate)").foregroundColor(.secondary)
            }
            if let rationale = rec?.rationale {
                Text(rationale).font(.caption)
            }
        }
    }

    private func quartersSection(_ quarters: [String: QuarterStats]?) -> some View {
        Section(header: Text("Quarter seasonality")) {
            if let quarters = quarters, !quarters.isEmpty {
                ForEach(quarters.keys.sorted(), id: \.self) { key in
                    let q = quarters[key]
                    HStack {
                        Text(key)
                        Spacer()
                        Text(formatPct(q?.breachRatePct))
                            .monospacedDigit()
                    }
                    if let rec = q?.recommendation {
                        Text("Rec: \(rec)").font(.caption).foregroundColor(.secondary)
                    }
                }
            } else {
                Text("No quarter data")
            }
        }
    }

    private func eventsSection(_ events: [BreachEvent]) -> some View {
        Section(header: Text("Events")) {
            if events.isEmpty {
                Text("No events")
            } else {
                ForEach(events) { ev in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(ev.earnDate ?? "—")
                            Spacer()
                            if let breach = ev.breach, breach {
                                Text("Breach").foregroundColor(.red).font(.caption)
                            }
                        }
                        if let implied = ev.impliedMovePct, let realized = ev.realizedMovePct {
                            Text("Implied \(formatPct(implied)) · Realized \(formatPct(realized))")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    private func formatPct(_ value: Double?) -> String {
        guard let v = value else { return "—" }
        return String(format: "%.2f%%", v)
    }
}

struct EngineOneScreen_Previews: PreviewProvider {
    static var previews: some View {
        EngineOneScreen().environmentObject(AppState())
    }
}
