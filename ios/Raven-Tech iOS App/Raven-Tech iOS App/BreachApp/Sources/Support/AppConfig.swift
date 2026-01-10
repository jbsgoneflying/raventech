import Foundation

enum AppConfig {
    enum BaseURL {
        /// Set this to your Tailscale HTTPS host for development.
        static let dev = URL(string: "https://raven-tech.tail530226.ts.net")!
        /// Point to your production host if/when you open beyond VPN.
        static let prod = URL(string: "https://app.raven-tech.co")!
    }
}
