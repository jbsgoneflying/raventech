import Foundation
import Combine

@MainActor
final class CalendarViewModel: ObservableObject {
    @Published var response: CalendarResponse?
    @Published var isLoading = false
    @Published var error: AppError?

    func load(client: APIClient) async {
        isLoading = true
        error = nil
        do {
            response = try await client.get("api/calendar", query: ["view": "week", "includeEvents": "1"])
        } catch let appError as AppError {
            error = appError
        } catch {
            error = .network(error)
        }
        isLoading = false
    }
}
