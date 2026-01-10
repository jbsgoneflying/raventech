import Foundation
import Combine

@MainActor
final class CalendarViewModel: ObservableObject {
    @Published var response: CalendarResponse?
    @Published var isLoading = false
    @Published var error: AppError?

    func load(client: APIClient) async {
        isLoading = true
        self.error = nil
        do {
            response = try await client.get("api/calendar", query: ["view": "week", "includeEvents": "1"])
        } catch let appError as AppError {
            self.error = appError
        } catch {
            self.error = .network(error)
        }
        isLoading = false
    }
}
