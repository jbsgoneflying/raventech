import SwiftUI

/// A shimmer effect for loading states
struct ShimmerModifier: ViewModifier {
    @State private var phase: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .overlay(
                GeometryReader { geo in
                    LinearGradient(
                        colors: [
                            Color.white.opacity(0),
                            Color.white.opacity(0.45),
                            Color.white.opacity(0)
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(width: geo.size.width * 2)
                    .offset(x: -geo.size.width + (phase * geo.size.width * 2))
                }
            )
            .mask(content)
            .onAppear {
                withAnimation(.linear(duration: 1.5).repeatForever(autoreverses: false)) {
                    phase = 1
                }
            }
    }
}

extension View {
    func shimmer() -> some View {
        modifier(ShimmerModifier())
    }
}

/// A placeholder view for loading states
struct LoadingPlaceholder: View {
    var height: CGFloat = 60
    var cornerRadius: CGFloat = 14

    var body: some View {
        RoundedRectangle(cornerRadius: cornerRadius)
            .fill(Color.black.opacity(0.06))
            .frame(height: height)
            .shimmer()
    }
}

/// A skeleton card for metric cards
struct SkeletonMetricCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            RoundedRectangle(cornerRadius: 4)
                .fill(Color.black.opacity(0.10))
                .frame(width: 80, height: 12)

            RoundedRectangle(cornerRadius: 6)
                .fill(Color.black.opacity(0.12))
                .frame(width: 60, height: 24)

            RoundedRectangle(cornerRadius: 4)
                .fill(Color.black.opacity(0.06))
                .frame(height: 12)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous)
                .stroke(Color.black.opacity(0.05), lineWidth: 1)
        )
        .shimmer()
    }
}

/// A skeleton for the decision banner
struct SkeletonDecisionBanner: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.black.opacity(0.10))
                    .frame(width: 40, height: 40)

                VStack(alignment: .leading, spacing: 4) {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.black.opacity(0.12))
                        .frame(width: 80, height: 16)

                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.black.opacity(0.06))
                        .frame(width: 100, height: 12)
                }

                Spacer()
            }

            HStack(spacing: 12) {
                RoundedRectangle(cornerRadius: 20)
                    .fill(Color.black.opacity(0.08))
                    .frame(width: 70, height: 28)

                RoundedRectangle(cornerRadius: 20)
                    .fill(Color.black.opacity(0.06))
                    .frame(width: 80, height: 28)
            }
        }
        .padding(14)
        .background(Color.white.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous))
        .shimmer()
    }
}

/// A skeleton for calendar day cards
struct SkeletonCalendarCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.black.opacity(0.12))
                    .frame(width: 40, height: 20)

                Spacer()
            }

            HStack(spacing: 6) {
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.black.opacity(0.06))
                    .frame(width: 50, height: 20)

                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.black.opacity(0.06))
                    .frame(width: 60, height: 20)
            }

            VStack(spacing: 4) {
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.black.opacity(0.08))
                    .frame(height: 32)

                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.black.opacity(0.08))
                    .frame(height: 32)
            }

            Spacer()
        }
        .padding(10)
        .frame(minHeight: 170)
        .background(Color.white.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: RavenTheme.radiusCard, style: .continuous))
        .shimmer()
    }
}

/// A loading overlay for full-screen loading states
struct LoadingOverlay: View {
    let message: String

    var body: some View {
        ZStack {
            Color.black.opacity(0.20)
                .ignoresSafeArea()

            VStack(spacing: 16) {
                ProgressView()
                    .scaleEffect(1.3)
                    .tint(.white)

                Text(message)
                    .font(.subheadline)
                    .fontWeight(.semibold)
                    .foregroundStyle(.white)
            }
            .padding(28)
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 20))
            .shadow(color: .black.opacity(0.20), radius: 30, x: 0, y: 15)
        }
    }
}

#Preview {
    VStack(spacing: 16) {
        SkeletonDecisionBanner()

        LazyVGrid(
            columns: [GridItem(.flexible()), GridItem(.flexible())],
            spacing: 10
        ) {
            SkeletonMetricCard()
            SkeletonMetricCard()
        }

        LazyVGrid(
            columns: [GridItem(.flexible()), GridItem(.flexible())],
            spacing: 12
        ) {
            SkeletonCalendarCard()
            SkeletonCalendarCard()
        }

        LoadingPlaceholder(height: 100)
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
