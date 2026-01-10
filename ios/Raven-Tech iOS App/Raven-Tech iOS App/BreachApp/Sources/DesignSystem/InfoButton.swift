import SwiftUI

/// A small info button that triggers a sheet
struct InfoButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text("i")
                .font(.system(size: 11, weight: .bold))
                .frame(width: 22, height: 22)
                .foregroundStyle(RavenTheme.textMuted)
                .background(Color.white.opacity(0.60))
                .clipShape(Circle())
                .overlay(Circle().stroke(RavenTheme.border, lineWidth: 1))
                .shadow(color: Color.white.opacity(0.85), radius: 0, x: 0, y: 1)
        }
        .buttonStyle(.plain)
    }
}

/// A button style that provides haptic feedback
struct HapticButtonStyle: ButtonStyle {
    var feedbackStyle: UIImpactFeedbackGenerator.FeedbackStyle = .light

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1.0)
            .opacity(configuration.isPressed ? 0.9 : 1.0)
            .animation(.easeInOut(duration: 0.1), value: configuration.isPressed)
            .onChange(of: configuration.isPressed) { _, pressed in
                if pressed {
                    let generator = UIImpactFeedbackGenerator(style: feedbackStyle)
                    generator.impactOccurred()
                }
            }
    }
}

/// Primary action button matching web's `.primaryButton`
struct PrimaryButton: View {
    let title: String
    let action: () -> Void
    var isLoading: Bool = false
    var isDisabled: Bool = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                if isLoading {
                    ProgressView()
                        .scaleEffect(0.8)
                        .tint(.primary)
                }
                Text(title)
                    .fontWeight(.semibold)
            }
            .frame(height: 36)
            .padding(.horizontal, 14)
            .background(
                LinearGradient(
                    colors: [Color.white.opacity(0.92), Color.white.opacity(0.70)],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(RavenTheme.borderStrong, lineWidth: 1)
            )
            .shadow(color: RavenTheme.shadowGlass, radius: 15, x: 0, y: 10)
            .shadow(color: Color.white.opacity(0.85), radius: 0, x: 0, y: 1)
        }
        .buttonStyle(HapticButtonStyle())
        .disabled(isLoading || isDisabled)
        .opacity(isDisabled ? 0.6 : 1.0)
    }
}

/// Segmented control matching web's `.segmented`
struct SegmentedControl<T: Hashable>: View {
    let options: [(value: T, label: String)]
    @Binding var selected: T

    var body: some View {
        HStack(spacing: 6) {
            ForEach(options, id: \.value) { option in
                Button {
                    selected = option.value
                } label: {
                    Text(option.label)
                        .font(.caption)
                        .fontWeight(.semibold)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .foregroundStyle(
                            selected == option.value
                                ? Color.black.opacity(0.88)
                                : Color.black.opacity(0.62)
                        )
                        .background(
                            selected == option.value
                                ? Color.white.opacity(0.80)
                                : Color.clear
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(
                                    selected == option.value
                                        ? RavenTheme.border
                                        : Color.clear,
                                    lineWidth: 1
                                )
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(4)
        .background(Color.black.opacity(0.02))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(RavenTheme.border, lineWidth: 1)
        )
    }
}

#Preview {
    VStack(spacing: 20) {
        InfoButton(action: {})

        PrimaryButton(title: "Run", action: {})
        PrimaryButton(title: "Loading...", action: {}, isLoading: true)

        SegmentedControl(
            options: [
                (value: "mon", label: "Mon"),
                (value: "tue", label: "Tue"),
                (value: "wed", label: "Wed")
            ],
            selected: .constant("mon")
        )
    }
    .padding()
    .background(Color.gray.opacity(0.1))
}
