import SwiftUI

/// Displays a ticker's company logo or a styled fallback
/// Updates reactively as user types - shows initials immediately, loads logo in background
struct TickerLogo: View {
    let ticker: String
    var size: CGFloat = 40
    var cornerRadius: CGFloat = 12

    @State private var logoImage: Image?
    @State private var loadedTicker: String = ""

    // Normalized ticker for consistent display
    private var displayTicker: String {
        ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    }

    var body: some View {
        ZStack {
            // Always show fallback first for immediate feedback
            fallbackView

            // Overlay the loaded logo if we have one for the CURRENT ticker
            if let image = logoImage, loadedTicker == displayTicker {
                image
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: size * 0.8, height: size * 0.8)
                    .frame(width: size, height: size)
                    .background(Color.white)
                    .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: displayTicker)
        .onChange(of: displayTicker) { oldValue, newValue in
            // Immediately clear logo when ticker changes so fallback shows
            if newValue != loadedTicker {
                logoImage = nil
            }
        }
        .task(id: displayTicker) {
            await loadLogoDebounced()
        }
    }

    private var fallbackView: some View {
        RoundedRectangle(cornerRadius: cornerRadius)
            .fill(gradientForTicker)
            .frame(width: size, height: size)
            .overlay(
                Text(String(displayTicker.prefix(2)))
                    .font(.system(size: size * 0.35, weight: .black, design: .rounded))
                    .foregroundStyle(.white.opacity(0.9))
            )
    }

    private var gradientForTicker: LinearGradient {
        // Generate a consistent gradient based on ticker hash
        let hash = abs(displayTicker.hashValue)
        let hue1 = Double(hash % 360) / 360.0
        let hue2 = Double((hash / 360) % 360) / 360.0

        return LinearGradient(
            colors: [
                Color(hue: hue1, saturation: 0.6, brightness: 0.4),
                Color(hue: hue2, saturation: 0.7, brightness: 0.3)
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private func loadLogoDebounced() async {
        let t = displayTicker
        guard !t.isEmpty, t != "?" else { return }

        // Debounce: wait a bit before fetching to avoid spamming on every keystroke
        try? await Task.sleep(nanoseconds: 300_000_000) // 300ms

        // Check if ticker changed while we were waiting
        guard t == displayTicker else { return }

        // FMP serves ticker logos from a stable static URL (no API key required)
        let urlString = "https://financialmodelingprep.com/image-stock/\(t).png"
        guard let url = URL(string: urlString) else { return }

        // Create a custom session with shorter timeout
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 5
        config.timeoutIntervalForResource = 10
        config.waitsForConnectivity = false
        let session = URLSession(configuration: config)

        do {
            let (data, response) = try await session.data(from: url)

            // Check again if ticker changed during fetch
            guard t == displayTicker else { return }

            guard let httpResponse = response as? HTTPURLResponse,
                  httpResponse.statusCode == 200,
                  let uiImage = UIImage(data: data) else { return }

            await MainActor.run {
                // Only update if ticker still matches
                if t == displayTicker {
                    self.logoImage = Image(uiImage: uiImage)
                    self.loadedTicker = t
                }
            }
        } catch {
            // Silent fail - fallback initials will show
        }
    }
}

/// Compact ticker chip with logo
struct TickerChip: View {
    let ticker: String
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: 6) {
                TickerLogo(ticker: ticker, size: 24, cornerRadius: 6)

                Text(ticker.uppercased())
                    .font(.caption)
                    .fontWeight(.semibold)
            }
            .padding(.trailing, 8)
            .background(Color.white.opacity(0.60))
            .clipShape(Capsule())
            .overlay(
                Capsule()
                    .stroke(Color.black.opacity(0.08), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    VStack(spacing: 20) {
        HStack(spacing: 12) {
            TickerLogo(ticker: "AAPL", size: 48)
            TickerLogo(ticker: "MSFT", size: 48)
            TickerLogo(ticker: "NVDA", size: 48)
            TickerLogo(ticker: "TSLA", size: 48)
        }

        HStack(spacing: 12) {
            TickerLogo(ticker: "META", size: 40)
            TickerLogo(ticker: "AMZN", size: 40)
            TickerLogo(ticker: "NFLX", size: 40)
            TickerLogo(ticker: "UNKNOWN", size: 40)
        }

        HStack(spacing: 8) {
            TickerChip(ticker: "AAPL")
            TickerChip(ticker: "MSFT")
            TickerChip(ticker: "TEST")
        }
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
