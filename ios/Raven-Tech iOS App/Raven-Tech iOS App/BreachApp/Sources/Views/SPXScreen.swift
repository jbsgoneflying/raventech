import SwiftUI

struct SPXScreen: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var viewModel = SPXViewModel()

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("SPX IC").font(.headline)
                    Text(viewModel.icSummary).foregroundColor(.secondary)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text("SPX Levels").font(.headline)
                    Text(viewModel.levelsSummary).foregroundColor(.secondary)
                }
                Button("Refresh") {
                    Task { await viewModel.load(client: appState.apiClient) }
                }
                if viewModel.isLoading {
                    ProgressView()
                }
                if let err = viewModel.error {
                    Text(err.localizedDescription).foregroundColor(.red)
                }
            }
            .padding()
            .navigationTitle("SPX")
        }
    }
}

struct SPXScreen_Previews: PreviewProvider {
    static var previews: some View {
        SPXScreen().environmentObject(AppState())
    }
}
