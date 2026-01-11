import Foundation

enum AppConfig {
    enum BaseURL {
        /// Set this to your Tailscale HTTPS host for development.
        static let dev = URL(string: "https://raven-tech.tail530226.ts.net")!
        /// Point to your production host if/when you open beyond VPN.
        static let prod = URL(string: "https://app.raven-tech.co")!
    }
    
    /// API token for production access (bypasses invite gate).
    /// Set IOS_API_TOKEN on the server to match this value.
    static let apiToken: String? = "bcdb9bfd32ce6bb28e67c6aa61a13bfd607d07f9b59cce2dca656726191f1192"
}
