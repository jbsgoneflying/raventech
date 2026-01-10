import SwiftUI

/// A glass-morphism surface container matching the web app's `.surface` class
struct GlassSurface<Content: View>: View {
    let content: Content
    var cornerRadius: CGFloat = RavenTheme.radiusCard
    var padding: CGFloat = 16

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
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(RavenTheme.border, lineWidth: 1)
            )
            .shadow(color: RavenTheme.shadowCard, radius: 15, x: 0, y: 10)
    }
}

/// A lighter glass card for metric displays
struct GlassCard<Content: View>: View {
    let content: Content
    var cornerRadius: CGFloat = RavenTheme.radiusCard

    init(
        cornerRadius: CGFloat = RavenTheme.radiusCard,
        @ViewBuilder content: () -> Content
    ) {
        self.content = content()
        self.cornerRadius = cornerRadius
    }

    var body: some View {
        content
            .padding(14)
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(Color.black.opacity(0.08), lineWidth: 1)
            )
    }
}

/// View modifier for applying glass surface styling
struct GlassSurfaceModifier: ViewModifier {
    var cornerRadius: CGFloat = RavenTheme.radiusCard

    func body(content: Content) -> some View {
        content
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(RavenTheme.border, lineWidth: 1)
            )
            .shadow(color: RavenTheme.shadowCard, radius: 15, x: 0, y: 10)
    }
}

extension View {
    func glassSurface(cornerRadius: CGFloat = RavenTheme.radiusCard) -> some View {
        modifier(GlassSurfaceModifier(cornerRadius: cornerRadius))
    }
}
