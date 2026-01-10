import SwiftUI

/// Color extensions for dark mode adaptive colors
extension RavenTheme {
    // MARK: - Adaptive Colors (Light/Dark)

    /// Adaptive glass surface background
    static var adaptiveSurfaceGlass: Color {
        Color(uiColor: UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor.white.withAlphaComponent(0.08)
                : UIColor.white.withAlphaComponent(0.70)
        })
    }

    /// Adaptive elevated surface background
    static var adaptiveSurfaceElevated: Color {
        Color(uiColor: UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor.white.withAlphaComponent(0.12)
                : UIColor.white.withAlphaComponent(0.86)
        })
    }

    /// Adaptive border color
    static var adaptiveBorder: Color {
        Color(uiColor: UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor.white.withAlphaComponent(0.10)
                : UIColor.black.withAlphaComponent(0.10)
        })
    }

    /// Adaptive border strong color
    static var adaptiveBorderStrong: Color {
        Color(uiColor: UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor.white.withAlphaComponent(0.16)
                : UIColor.black.withAlphaComponent(0.14)
        })
    }

    /// Adaptive text primary color
    static var adaptiveTextPrimary: Color {
        Color(uiColor: UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor.white.withAlphaComponent(0.95)
                : UIColor(red: 11/255, green: 11/255, blue: 15/255, alpha: 1.0)
        })
    }

    /// Adaptive text muted color
    static var adaptiveTextMuted: Color {
        Color(uiColor: UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor.white.withAlphaComponent(0.55)
                : UIColor.black.withAlphaComponent(0.62)
        })
    }

    /// Adaptive shadow color
    static var adaptiveShadow: Color {
        Color(uiColor: UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor.black.withAlphaComponent(0.30)
                : UIColor.black.withAlphaComponent(0.08)
        })
    }
}

/// Dark mode adaptive glass surface
struct AdaptiveGlassSurface<Content: View>: View {
    let content: Content
    var cornerRadius: CGFloat = RavenTheme.radiusCard
    var padding: CGFloat = 16

    @Environment(\.colorScheme) var colorScheme

    init(
        cornerRadius: CGFloat = RavenTheme.radiusCard,
        padding: CGFloat = 16,
        @ViewBuilder content: () -> Content
    ) {
        self.content = content()
        self.cornerRadius = cornerRadius
        self.padding = padding
    }

    var body: some View {
        content
            .padding(padding)
            .background(materialStyle)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(RavenTheme.adaptiveBorder, lineWidth: 1)
            )
            .shadow(color: RavenTheme.adaptiveShadow, radius: 15, x: 0, y: 10)
    }

    @ViewBuilder
    private var materialStyle: some View {
        if colorScheme == .dark {
            Material.ultraThinMaterial
        } else {
            Material.ultraThinMaterial
        }
    }
}

/// View modifier to ensure proper dark mode adaptation
struct DarkModeAdaptiveModifier: ViewModifier {
    @Environment(\.colorScheme) var colorScheme

    func body(content: Content) -> some View {
        content
            .preferredColorScheme(nil) // Allow system setting
    }
}

extension View {
    func darkModeAdaptive() -> some View {
        modifier(DarkModeAdaptiveModifier())
    }
}

/// Preview helper to show both light and dark modes
struct DarkModePreview<Content: View>: View {
    let content: () -> Content

    init(@ViewBuilder content: @escaping () -> Content) {
        self.content = content
    }

    var body: some View {
        HStack(spacing: 0) {
            content()
                .preferredColorScheme(.light)
                .frame(maxWidth: .infinity)

            content()
                .preferredColorScheme(.dark)
                .frame(maxWidth: .infinity)
        }
    }
}

#Preview {
    DarkModePreview {
        VStack(spacing: 16) {
            AdaptiveGlassSurface {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Metric Card")
                        .font(.caption)
                        .fontWeight(.heavy)
                        .foregroundStyle(RavenTheme.adaptiveTextMuted)

                    Text("23.5%")
                        .font(.title3)
                        .fontWeight(.bold)
                        .foregroundStyle(RavenTheme.adaptiveTextPrimary)
                }
            }

            HStack {
                Pill(text: "GO", style: .good)
                Pill(text: "NO-GO", style: .bad)
                Pill(text: "NEUTRAL", style: .neutral)
            }
        }
        .padding()
        .background(Color(UIColor.systemBackground))
    }
}
