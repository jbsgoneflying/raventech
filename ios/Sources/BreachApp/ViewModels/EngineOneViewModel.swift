import Foundation
import Combine

@MainActor
final class EngineOneViewModel: ObservableObject {
    @Published var ticker: String = ""
    @Published var response: BreachResponse?
    @Published var isLoading = false
    @Published var error: AppError?

    func load(client: APIClient) async {
        let tk = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !tk.isEmpty else {
            error = .badURL
            return
        }
        isLoading = true
        error = nil
        do {
            response = try await client.get("api/breach", query: ["ticker": tk, "n": "20", "years": "5", "k": "1.0"])
        } catch let appError as AppError {
            error = appError
        } catch {
            error = .network(error)
        }
        isLoading = false
    }
}
