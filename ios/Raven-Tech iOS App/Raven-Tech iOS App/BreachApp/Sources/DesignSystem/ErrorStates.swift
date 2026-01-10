import SwiftUI

/// A full-screen error state view
struct ErrorStateView: View {
    let title: String
    let message: String
    var retryAction: (() -> Void)?
    var icon: String = "exclamationmark.triangle"

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: icon)
                .font(.system(size: 52))
                .foregroundStyle(.red.opacity(0.75))

            VStack(spacing: 8) {
                Text(title)
                    .font(.headline)
                    .fontWeight(.bold)

                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            if let retryAction = retryAction {
                Button(action: retryAction) {
                    HStack(spacing: 8) {
                        Image(systemName: "arrow.clockwise")
                        Text("Try Again")
                    }
                    .fontWeight(.semibold)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(Color.accentColor)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .padding(.top, 8)
            }
        }
        .padding()
    }
}

/// A compact inline error banner
struct ErrorBanner: View {
    let message: String
    var style: ErrorStyle = .error
    var dismissAction: (() -> Void)?

    enum ErrorStyle {
        case error
        case warning
        case info

        var backgroundColor: Color {
            switch self {
            case .error: return Color.red.opacity(0.08)
            case .warning: return Color.orange.opacity(0.08)
            case .info: return Color.blue.opacity(0.08)
            }
        }

        var borderColor: Color {
            switch self {
            case .error: return Color.red.opacity(0.20)
            case .warning: return Color.orange.opacity(0.20)
            case .info: return Color.blue.opacity(0.20)
            }
        }

        var iconColor: Color {
            switch self {
            case .error: return .red
            case .warning: return .orange
            case .info: return .blue
            }
        }

        var iconName: String {
            switch self {
            case .error: return "exclamationmark.triangle.fill"
            case .warning: return "exclamationmark.circle.fill"
            case .info: return "info.circle.fill"
            }
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: style.iconName)
                .foregroundStyle(style.iconColor)

            Text(message)
                .font(.subheadline)
                .foregroundStyle(.primary)

            Spacer()

            if let dismissAction = dismissAction {
                Button(action: dismissAction) {
                    Image(systemName: "xmark")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(12)
        .background(style.backgroundColor)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(style.borderColor, lineWidth: 1)
        )
    }
}

/// Network error view
struct NetworkErrorView: View {
    var retryAction: (() -> Void)?

    var body: some View {
        ErrorStateView(
            title: "Connection Error",
            message: "Unable to connect to the server. Please check your internet connection and try again.",
            retryAction: retryAction,
            icon: "wifi.slash"
        )
    }
}

/// Timeout error view
struct TimeoutErrorView: View {
    var retryAction: (() -> Void)?

    var body: some View {
        ErrorStateView(
            title: "Request Timed Out",
            message: "The server is taking too long to respond. This might be a complex calculation. Please try again.",
            retryAction: retryAction,
            icon: "clock.badge.exclamationmark"
        )
    }
}

/// Empty state view
struct EmptyStateView: View {
    let title: String
    let message: String
    var icon: String = "tray"
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: icon)
                .font(.system(size: 52))
                .foregroundStyle(.secondary.opacity(0.55))

            VStack(spacing: 8) {
                Text(title)
                    .font(.headline)
                    .fontWeight(.bold)

                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            if let actionTitle = actionTitle, let action = action {
                Button(action: action) {
                    Text(actionTitle)
                        .fontWeight(.semibold)
                        .padding(.horizontal, 24)
                        .padding(.vertical, 12)
                        .background(Color.accentColor)
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .padding(.top, 8)
            }
        }
        .padding()
    }
}

/// Feature disabled view
struct FeatureDisabledView: View {
    let featureName: String
    var message: String?

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "gear.badge.xmark")
                .font(.system(size: 44))
                .foregroundStyle(.orange.opacity(0.75))

            VStack(spacing: 6) {
                Text("\(featureName) Disabled")
                    .font(.headline)
                    .fontWeight(.bold)

                Text(message ?? "This feature is currently disabled on the server.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }
        }
        .padding()
        .frame(maxWidth: .infinity)
    }
}

#Preview {
    ScrollView {
        VStack(spacing: 24) {
            ErrorStateView(
                title: "Something went wrong",
                message: "We couldn't load the data. Please try again.",
                retryAction: {}
            )

            ErrorBanner(message: "Connection lost", style: .error, dismissAction: {})
            ErrorBanner(message: "Rate limit exceeded", style: .warning)
            ErrorBanner(message: "New version available", style: .info)

            NetworkErrorView(retryAction: {})

            EmptyStateView(
                title: "No Earnings This Week",
                message: "There are no earnings reports scheduled for this period.",
                icon: "calendar.badge.clock",
                actionTitle: "Check Next Week"
            ) {}

            FeatureDisabledView(featureName: "Engine 2")
        }
        .padding()
    }
}
