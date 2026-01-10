import SwiftUI

/// A tappable ticker tile for the calendar
struct TickerTile: View {
    let ticker: String
    var timing: String?
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            let generator = UIImpactFeedbackGenerator(style: .light)
            generator.impactOccurred()
            onTap?()
        } label: {
            VStack(spacing: 4) {
                // Logo placeholder (dark background for white logos)
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color(hex: "121216").opacity(0.92))
                    .frame(width: 44, height: 44)
                    .overlay(
                        Text(String(ticker.prefix(2)))
                            .font(.caption)
                            .fontWeight(.black)
                            .foregroundStyle(.white.opacity(0.9))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(Color.black.opacity(0.16), lineWidth: 1)
                    )

                Text(ticker)
                    .font(.system(size: 10, weight: .black))
                    .tracking(0.25)
                    .foregroundStyle(.primary)
            }
            .padding(6)
            .background(Color.white.opacity(0.80))
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.black.opacity(0.08), lineWidth: 1)
            )
        }
        .buttonStyle(TileButtonStyle())
    }
}

/// Button style with scale animation
struct TileButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
            .animation(.easeInOut(duration: 0.1), value: configuration.isPressed)
    }
}

/// A "+N more" button for overflow tickers
struct MoreTickersTile: View {
    let count: Int
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            Text("+\(count)")
                .font(.caption)
                .fontWeight(.heavy)
                .foregroundStyle(.secondary)
                .frame(width: 60, height: 60)
                .background(Color.white.opacity(0.55))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(
                            style: StrokeStyle(lineWidth: 1, dash: [4, 4])
                        )
                        .foregroundStyle(Color.black.opacity(0.18))
                )
        }
        .buttonStyle(TileButtonStyle())
    }
}

/// A grid of ticker tiles with overflow handling
struct TickerTileGrid: View {
    let tickers: [String]
    var maxVisible: Int = 4
    var onTickerTap: ((String) -> Void)?
    var onMoreTap: (() -> Void)?

    var body: some View {
        let visible = Array(tickers.prefix(maxVisible))
        let overflow = tickers.count - maxVisible

        LazyVGrid(
            columns: [
                GridItem(.flexible(), spacing: 6),
                GridItem(.flexible(), spacing: 6)
            ],
            spacing: 6
        ) {
            ForEach(visible, id: \.self) { ticker in
                TickerTile(ticker: ticker) {
                    onTickerTap?(ticker)
                }
            }

            if overflow > 0 {
                MoreTickersTile(count: overflow) {
                    onMoreTap?()
                }
            }
        }
    }
}

/// Sheet displaying all tickers for a timing group
struct TickerExpandSheet: View {
    let title: String
    let date: String
    let timing: String
    let tickers: [String]
    var onTickerTap: ((String) -> Void)?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVGrid(
                    columns: [
                        GridItem(.adaptive(minimum: 80), spacing: 12)
                    ],
                    spacing: 12
                ) {
                    ForEach(tickers, id: \.self) { ticker in
                        TickerTile(ticker: ticker) {
                            onTickerTap?(ticker)
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("\(timing) · \(date)")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}

#Preview {
    VStack(spacing: 20) {
        HStack {
            TickerTile(ticker: "AAPL", onTap: {})
            TickerTile(ticker: "MSFT", onTap: {})
            TickerTile(ticker: "GOOGL", onTap: {})
        }

        TickerTileGrid(
            tickers: ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"],
            maxVisible: 4,
            onTickerTap: { _ in },
            onMoreTap: {}
        )
        .frame(width: 200)

        MoreTickersTile(count: 12, onTap: {})
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
