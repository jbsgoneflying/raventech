import Foundation
import Combine

@MainActor
final class SettingsViewModel: ObservableObject {
    @Published var flags: FlagsResponse?
    @Published var healthOK: Bool = false
    @Published var healthMessage: String = ""
    @Published var isLoading = false
    @Published var error: AppError?

    func load(client: APIClient) async {
        isLoading = true
        error = nil
        do {
            struct Health: Decodable { let ok: Bool? }
            let health: Health = try await client.get("api/health")
            healthOK = health.ok ?? false
            healthMessage = healthOK ? "Healthy" : "Health check failed"
            flags = try await client.get("api/flags")
        } catch let appError as AppError {
            error = appError
            healthMessage = appError.localizedDescription
        } catch {
            error = .network(error)
            healthMessage = error.localizedDescription
        }
        isLoading = false
    }
}
