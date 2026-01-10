import SwiftUI
import UIKit

/// Centralized haptic feedback utilities
enum HapticFeedback {
    /// Light impact for subtle interactions
    static func light() {
        let generator = UIImpactFeedbackGenerator(style: .light)
        generator.prepare()
        generator.impactOccurred()
    }

    /// Medium impact for primary actions (Run button, etc.)
    static func medium() {
        let generator = UIImpactFeedbackGenerator(style: .medium)
        generator.prepare()
        generator.impactOccurred()
    }

    /// Heavy impact for significant events (GO/NO-GO verdict)
    static func heavy() {
        let generator = UIImpactFeedbackGenerator(style: .heavy)
        generator.prepare()
        generator.impactOccurred()
    }

    /// Success notification (trade confirmed, data loaded)
    static func success() {
        let generator = UINotificationFeedbackGenerator()
        generator.prepare()
        generator.notificationOccurred(.success)
    }

    /// Warning notification
    static func warning() {
        let generator = UINotificationFeedbackGenerator()
        generator.prepare()
        generator.notificationOccurred(.warning)
    }

    /// Error notification
    static func error() {
        let generator = UINotificationFeedbackGenerator()
        generator.prepare()
        generator.notificationOccurred(.error)
    }

    /// Selection changed (picker changes, crosshair snaps)
    static func selection() {
        let generator = UISelectionFeedbackGenerator()
        generator.prepare()
        generator.selectionChanged()
    }

    /// Rigid impact for chart crosshair snapping
    static func rigid() {
        let generator = UIImpactFeedbackGenerator(style: .rigid)
        generator.prepare()
        generator.impactOccurred()
    }

    /// Soft impact for subtle UI feedback
    static func soft() {
        let generator = UIImpactFeedbackGenerator(style: .soft)
        generator.prepare()
        generator.impactOccurred()
    }
}

/// View modifier that adds haptic feedback on tap
struct HapticTapModifier: ViewModifier {
    let style: UIImpactFeedbackGenerator.FeedbackStyle

    func body(content: Content) -> some View {
        content.simultaneousGesture(
            TapGesture().onEnded {
                let generator = UIImpactFeedbackGenerator(style: style)
                generator.impactOccurred()
            }
        )
    }
}

extension View {
    /// Add haptic feedback on tap
    func hapticOnTap(_ style: UIImpactFeedbackGenerator.FeedbackStyle = .light) -> some View {
        modifier(HapticTapModifier(style: style))
    }
}

/// View modifier for GO/NO-GO verdict haptics
struct VerdictHapticModifier: ViewModifier {
    let isGo: Bool?

    func body(content: Content) -> some View {
        content
            .onChange(of: isGo) { _, newValue in
                guard let isGo = newValue else { return }
                if isGo {
                    HapticFeedback.success()
                } else {
                    HapticFeedback.warning()
                }
            }
    }
}

extension View {
    /// Add haptic feedback when verdict changes
    func hapticOnVerdict(_ isGo: Bool?) -> some View {
        modifier(VerdictHapticModifier(isGo: isGo))
    }
}
