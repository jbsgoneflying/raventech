import Foundation

enum AppError: LocalizedError, Identifiable {
    case badURL
    case network(Error)
    case httpStatus(Int)
    case decoding(Error)
    case serverHTML(String)   // e.g., redirected to /login
    case unknown

    var id: String { localizedDescription }

    var errorDescription: String? {
        switch self {
        case .badURL:
            return "Invalid URL"
        case .network(let err):
            return err.localizedDescription
        case .httpStatus(let code):
            return "HTTP error \(code)"
        case .decoding:
            return "Unable to decode response"
        case .serverHTML(let body):
            return "Server returned HTML instead of JSON (possible redirect). \(body.prefix(200))"
        case .unknown:
            return "Unknown error"
        }
    }
}
