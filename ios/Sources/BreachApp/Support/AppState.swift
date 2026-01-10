import Foundation
import Combine

final class AppState: ObservableObject {
    @Published var baseURL: URL
    @Published var lastHealthOK: Bool = false
    @Published var lastHealthMessage: String?

    init(baseURL: URL = AppConfig.BaseURL.dev) {
        self.baseURL = baseURL
    }

    var apiClient: APIClient {
        APIClient(baseURL: baseURL)
    }
}
