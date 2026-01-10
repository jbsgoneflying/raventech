import SwiftUI

/// Displays a ticker's company logo or a styled fallback
struct TickerLogo: View {
    let ticker: String
    var size: CGFloat = 40
    var cornerRadius: CGFloat = 12

    @State private var logoLoaded = false
    @State private var logoImage: Image?

    // Map of known tickers to their company domains for logo lookup
    private static let domainMap: [String: String] = [
        "AAPL": "apple.com",
        "MSFT": "microsoft.com",
        "GOOGL": "google.com",
        "GOOG": "google.com",
        "AMZN": "amazon.com",
        "META": "meta.com",
        "NVDA": "nvidia.com",
        "TSLA": "tesla.com",
        "AMD": "amd.com",
        "NFLX": "netflix.com",
        "INTC": "intel.com",
        "CRM": "salesforce.com",
        "ORCL": "oracle.com",
        "ADBE": "adobe.com",
        "PYPL": "paypal.com",
        "CSCO": "cisco.com",
        "PEP": "pepsico.com",
        "KO": "coca-cola.com",
        "NKE": "nike.com",
        "DIS": "disney.com",
        "BA": "boeing.com",
        "JPM": "jpmorganchase.com",
        "GS": "goldmansachs.com",
        "V": "visa.com",
        "MA": "mastercard.com",
        "WMT": "walmart.com",
        "HD": "homedepot.com",
        "MCD": "mcdonalds.com",
        "SBUX": "starbucks.com",
        "XOM": "exxonmobil.com",
        "CVX": "chevron.com",
        "UNH": "unitedhealthgroup.com",
        "JNJ": "jnj.com",
        "PFE": "pfizer.com",
        "MRK": "merck.com",
        "ABBV": "abbvie.com",
        "LLY": "lilly.com",
        "TMO": "thermofisher.com",
        "ABT": "abbott.com",
        "MDT": "medtronic.com",
        "DHR": "danaher.com",
        "COST": "costco.com",
        "TGT": "target.com",
        "LOW": "lowes.com",
        "FDX": "fedex.com",
        "UPS": "ups.com",
        "CAT": "caterpillar.com",
        "DE": "deere.com",
        "MMM": "3m.com",
        "HON": "honeywell.com",
        "GE": "ge.com",
        "RTX": "rtx.com",
        "LMT": "lockheedmartin.com",
        "NOC": "northropgrumman.com",
        "T": "att.com",
        "VZ": "verizon.com",
        "TMUS": "t-mobile.com",
        "CMCSA": "comcast.com",
        "CHTR": "charter.com",
        "NOW": "servicenow.com",
        "SNOW": "snowflake.com",
        "PLTR": "palantir.com",
        "ZM": "zoom.us",
        "DOCU": "docusign.com",
        "SHOP": "shopify.com",
        "SQ": "squareup.com",
        "COIN": "coinbase.com",
        "UBER": "uber.com",
        "LYFT": "lyft.com",
        "ABNB": "airbnb.com",
        "DASH": "doordash.com",
        "ROKU": "roku.com",
        "SPOT": "spotify.com",
        "SNAP": "snap.com",
        "PINS": "pinterest.com",
        "TWTR": "twitter.com",
        "MU": "micron.com",
        "QCOM": "qualcomm.com",
        "AVGO": "broadcom.com",
        "TXN": "ti.com",
        "IBM": "ibm.com",
        "HPQ": "hp.com",
        "DELL": "dell.com",
        "SPX": "spglobal.com",
        "SPY": "ssga.com",
        "QQQ": "invesco.com",
    ]

    var body: some View {
        ZStack {
            if let image = logoImage {
                image
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: size * 0.7, height: size * 0.7)
                    .frame(width: size, height: size)
                    .background(Color.white)
                    .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
            } else {
                fallbackView
            }
        }
        .task {
            await loadLogo()
        }
    }

    private var fallbackView: some View {
        RoundedRectangle(cornerRadius: cornerRadius)
            .fill(gradientForTicker)
            .frame(width: size, height: size)
            .overlay(
                Text(String(ticker.prefix(2)).uppercased())
                    .font(.system(size: size * 0.35, weight: .black, design: .rounded))
                    .foregroundStyle(.white.opacity(0.9))
            )
    }

    private var gradientForTicker: LinearGradient {
        // Generate a consistent gradient based on ticker hash
        let hash = abs(ticker.uppercased().hashValue)
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

    private func loadLogo() async {
        guard let domain = Self.domainMap[ticker.uppercased()] else { return }

        // Use Clearbit Logo API (free, no auth needed for basic use)
        let urlString = "https://logo.clearbit.com/\(domain)"
        guard let url = URL(string: urlString) else { return }

        // Create a custom session with shorter timeout to fail fast
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 5
        config.timeoutIntervalForResource = 10
        config.waitsForConnectivity = false
        let session = URLSession(configuration: config)

        do {
            let (data, response) = try await session.data(from: url)
            guard let httpResponse = response as? HTTPURLResponse,
                  httpResponse.statusCode == 200,
                  let uiImage = UIImage(data: data) else { return }

            await MainActor.run {
                self.logoImage = Image(uiImage: uiImage)
                self.logoLoaded = true
            }
        } catch {
            // Silent fail - fallback gradient with initials will show
            // This is expected when offline or API is unreachable
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
