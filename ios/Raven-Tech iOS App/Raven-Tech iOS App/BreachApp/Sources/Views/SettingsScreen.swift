import SwiftUI

struct SettingsScreen: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var viewModel = SettingsViewModel()

    private var targets: [URL] {
        [AppConfig.BaseURL.dev, AppConfig.BaseURL.prod]
    }

    var body: some View {
        NavigationStack {
            ZStack {
                backgroundGradient

                ScrollView {
                    VStack(spacing: 16) {
                        // Base URL Section
                        GlassSurface {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Server")
                                    .font(.caption)
                                    .fontWeight(.heavy)
                                    .foregroundStyle(.secondary)
                                    .textCase(.uppercase)

                                VStack(spacing: 8) {
                                    ForEach(targets, id: \.self) { url in
                                        serverOption(url)
                                    }
                                }
                            }
                        }
                        .padding(.horizontal)

                        // Health Section
                        GlassSurface {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Health")
                                    .font(.caption)
                                    .fontWeight(.heavy)
                                    .foregroundStyle(.secondary)
                                    .textCase(.uppercase)

                                HStack {
                                    Text("Status")
                                        .font(.subheadline)

                                    Spacer()

                                    if viewModel.isLoading {
                                        ProgressView()
                                            .scaleEffect(0.8)
                                    } else {
                                        HStack(spacing: 6) {
                                            Circle()
                                                .fill(viewModel.healthOK ? Color(hex: "34C759") : Color(hex: "FF3B30"))
                                                .frame(width: 8, height: 8)

                                            Text(viewModel.healthMessage)
                                                .font(.subheadline)
                                                .fontWeight(.semibold)
                                                .foregroundStyle(viewModel.healthOK ? Color(hex: "34C759") : Color(hex: "FF3B30"))
                                        }
                                    }
                                }

                                PrimaryButton(
                                    title: "Check Health",
                                    action: {
                                        HapticFeedback.light()
                                        Task { await viewModel.load(client: appState.apiClient) }
                                    },
                                    isLoading: viewModel.isLoading
                                )
                            }
                        }
                        .padding(.horizontal)

                        // Feature Flags Section
                        if let flags = viewModel.flags {
                            GlassSurface {
                                VStack(alignment: .leading, spacing: 12) {
                                    Text("Feature Flags")
                                        .font(.caption)
                                        .fontWeight(.heavy)
                                        .foregroundStyle(.secondary)
                                        .textCase(.uppercase)

                                    VStack(spacing: 8) {
                                        flagRow("Engine 2 (SPX IC)", flags.enableEngine2SpxIc)
                                        Divider()
                                        flagRow("Benzinga Events", flags.enableBenzinga)
                                        Divider()
                                        flagRow("Event Risk Overlay", flags.benzingaEnableEventRisk)
                                    }
                                }
                            }
                            .padding(.horizontal)
                        }

                        // Error Display
                        if let err = viewModel.error {
                            ErrorBanner(
                                message: err.localizedDescription,
                                style: .error
                            )
                            .padding(.horizontal)
                        }

                        // App Info
                        GlassSurface(padding: 12) {
                            VStack(alignment: .center, spacing: 8) {
                                Image(systemName: "bird")
                                    .font(.title2)
                                    .foregroundStyle(.secondary)

                                Text("Raven Tech")
                                    .font(.headline)
                                    .fontWeight(.bold)

                                Text("Breach Algo v1.0")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .padding(.horizontal)

                        Spacer(minLength: 32)
                    }
                    .padding(.top, 8)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    // MARK: - Background

    private var backgroundGradient: some View {
        ZStack {
            Color(UIColor.systemBackground)

            RadialGradient(
                colors: [Color(hex: "6366F1").opacity(0.06), .clear],
                center: UnitPoint(x: 0.18, y: -0.10),
                startRadius: 0,
                endRadius: 700
            )
        }
        .ignoresSafeArea()
    }

    // MARK: - Server Option

    @ViewBuilder
    private func serverOption(_ url: URL) -> some View {
        let isSelected = appState.baseURL == url

        Button {
            HapticFeedback.selection()
            appState.baseURL = url
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(url == AppConfig.BaseURL.prod ? "Production" : "Development")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundStyle(.primary)

                    Text(url.host ?? url.absoluteString)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Color.accentColor)
                }
            }
            .padding(12)
            .background(isSelected ? Color.accentColor.opacity(0.08) : Color.white.opacity(0.55))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(
                        isSelected ? Color.accentColor.opacity(0.20) : Color.black.opacity(0.08),
                        lineWidth: 1
                    )
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Flag Row

    @ViewBuilder
    private func flagRow(_ label: String, _ value: Bool?) -> some View {
        HStack {
            Text(label)
                .font(.subheadline)

            Spacer()

            Pill(
                text: value == true ? "ON" : "OFF",
                style: value == true ? .good : .neutral,
                size: .mini
            )
        }
    }
}

struct SettingsScreen_Previews: PreviewProvider {
    static var previews: some View {
        SettingsScreen()
            .environmentObject(AppState())
    }
}
