import Foundation
import Combine

@MainActor
final class SPXViewModel: ObservableObject {
    @Published var isLoading = false
    @Published var error: AppError?
    @Published var icSummary: String = "Not loaded"
    @Published var levelsSummary: String = "Not loaded"

    func load(client: APIClient) async {
        isLoading = true
        error = nil
        defer { isLoading = false }
        do {
            let icData = try await client.getData("api/spx-ic", query: ["underlying": "SPX", "entry_day": "mon", "years": "3"])
            icSummary = summarize(jsonData: icData) ?? "Loaded (/api/spx-ic)"
            let levelsData = try await client.getData("api/spx-levels", query: ["underlying": "SPX", "view": "weekly"])
            levelsSummary = summarize(jsonData: levelsData) ?? "Loaded (/api/spx-levels)"
        } catch let appError as AppError {
            error = appError
        } catch {
            error = .network(error)
        }
    }

    private func summarize(jsonData: Data) -> String? {
        guard let obj = try? JSONSerialization.jsonObject(with: jsonData),
              let dict = obj as? [String: Any] else { return nil }
        let keys = dict.keys.sorted()
        let head = keys.prefix(6).joined(separator: ", ")
        return "Keys: \(head)\(keys.count > 6 ? " …" : "")"
    }
}
