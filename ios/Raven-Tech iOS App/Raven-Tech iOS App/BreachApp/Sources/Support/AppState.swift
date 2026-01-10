import Foundation
import Combine

final class AppState: ObservableObject {
    @Published var baseURL: URL
    @Published var lastHealthOK: Bool = false
    @Published var lastHealthMessage: String?

    // Tab navigation
    @Published var selectedTab: Int = 0

    // Cross-screen ticker (set from Calendar, used by Engine 1)
    @Published var pendingTicker: String?

    init(baseURL: URL = AppConfig.BaseURL.dev) {
        self.baseURL = baseURL
    }

    var apiClient: APIClient {
        APIClient(baseURL: baseURL)
    }

    /// Navigate to Engine 1 with a ticker pre-filled
    func navigateToEngine1(ticker: String) {
        pendingTicker = ticker
        selectedTab = 1  // Engine 1 tab index
    }
}
