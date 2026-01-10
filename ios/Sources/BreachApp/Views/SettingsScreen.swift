import SwiftUI

struct SettingsScreen: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var viewModel = SettingsViewModel()

    private var targets: [URL] {
        [AppConfig.BaseURL.dev, AppConfig.BaseURL.prod]
    }

    var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("Base URL")) {
                    Picker("Host", selection: $appState.baseURL) {
                        ForEach(targets, id: \.self) { url in
                            Text(url.absoluteString).tag(url)
                        }
                    }
                }

                Section(header: Text("Health")) {
                    HStack {
                        Text("Status")
                        Spacer()
                        if viewModel.isLoading {
                            ProgressView()
                        } else {
                            Text(viewModel.healthMessage)
                                .foregroundColor(viewModel.healthOK ? .green : .red)
                        }
                    }
                    Button("Run health + flags") {
                        Task { await viewModel.load(client: appState.apiClient) }
                    }
                }

                if let flags = viewModel.flags {
                    Section(header: Text("Flags")) {
                        flagRow("ENABLE_ENGINE2_SPX_IC", flags.enableEngine2SpxIc)
                        flagRow("ENABLE_BENZINGA", flags.enableBenzinga)
                        flagRow("BENZINGA_EVENT_RISK", flags.benzingaEnableEventRisk)
                    }
                }

                if let err = viewModel.error {
                    Section {
                        Text(err.localizedDescription).foregroundColor(.red)
                    }
                }
            }
            .navigationTitle("Settings")
        }
    }

    private func flagRow(_ label: String, _ value: Bool?) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value == true ? "ON" : "OFF").foregroundColor(.secondary)
        }
    }
}

struct SettingsScreen_Previews: PreviewProvider {
    static var previews: some View {
        SettingsScreen().environmentObject(AppState())
    }
}
