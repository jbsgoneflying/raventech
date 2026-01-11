import Foundation

struct APIClient {
    let baseURL: URL
    private let session: URLSession
    private let decoder: JSONDecoder

    init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
        let dec = JSONDecoder()
        dec.keyDecodingStrategy = .convertFromSnakeCase
        self.decoder = dec
    }

    func get<T: Decodable>(
        _ path: String,
        query: [String: String?] = [:],
        timeout: TimeInterval? = nil
    ) async throws -> T {
        let data = try await getData(path, query: query, timeout: timeout)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            // Debug: print the actual decoding error
            print("🔴 Decoding error for \(T.self):")
            print("   \(error)")
            if let decodingError = error as? DecodingError {
                switch decodingError {
                case .keyNotFound(let key, let context):
                    print("   Key not found: \(key.stringValue) at \(context.codingPath.map(\.stringValue).joined(separator: "."))")
                case .typeMismatch(let type, let context):
                    print("   Type mismatch: expected \(type) at \(context.codingPath.map(\.stringValue).joined(separator: "."))")
                case .valueNotFound(let type, let context):
                    print("   Value not found: \(type) at \(context.codingPath.map(\.stringValue).joined(separator: "."))")
                case .dataCorrupted(let context):
                    print("   Data corrupted at \(context.codingPath.map(\.stringValue).joined(separator: "."))")
                @unknown default:
                    break
                }
            }
            
            // If the body looks like HTML, surface that.
            if let body = String(data: data, encoding: .utf8),
               body.lowercased().contains("<html") || body.lowercased().contains("<!doctype") {
                throw AppError.serverHTML(body)
            }
            throw AppError.decoding(error)
        }
    }

    func getData(
        _ path: String,
        query: [String: String?] = [:],
        timeout: TimeInterval? = nil
    ) async throws -> Data {
        guard var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false) else {
            throw AppError.badURL
        }
        let items = query.compactMap { key, value -> URLQueryItem? in
            guard let value = value else { return nil }
            return URLQueryItem(name: key, value: value)
        }
        if !items.isEmpty {
            components.queryItems = items
        }
        guard let url = components.url else { throw AppError.badURL }

        var request = URLRequest(url: url)
        request.timeoutInterval = timeout ?? 45

        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw AppError.unknown }

            // HTML detection (e.g., invite gate redirect)
            if let contentType = http.value(forHTTPHeaderField: "Content-Type"), contentType.contains("text/html") {
                let body = String(data: data, encoding: .utf8) ?? ""
                throw AppError.serverHTML(body)
            }
            guard (200...299).contains(http.statusCode) else {
                throw AppError.httpStatus(http.statusCode)
            }

            return data
        } catch let err as AppError {
            throw err
        } catch {
            throw AppError.network(error)
        }
    }
}
