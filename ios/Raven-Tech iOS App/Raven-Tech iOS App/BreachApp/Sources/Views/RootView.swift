import SwiftUI

struct RootView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        TabView(selection: $appState.selectedTab) {
            CalendarScreen()
                .tabItem {
                    Label("Calendar", systemImage: "calendar")
                }
                .tag(0)
            EngineOneScreen()
                .tabItem {
                    Label("Engine 1", systemImage: "chart.bar.doc.horizontal")
                }
                .tag(1)
            SPXScreen()
                .tabItem {
                    Label("SPX", systemImage: "chart.xyaxis.line")
                }
                .tag(2)
            SettingsScreen()
                .tabItem {
                    Label("Settings", systemImage: "gear")
                }
                .tag(3)
        }
        .onAppear {
            configureTabBarAppearance()
        }
    }

    private func configureTabBarAppearance() {
        let appearance = UITabBarAppearance()
        appearance.configureWithDefaultBackground()

        // Glass-like background
        appearance.backgroundEffect = UIBlurEffect(style: .systemUltraThinMaterial)
        appearance.backgroundColor = UIColor.white.withAlphaComponent(0.78)

        // Subtle shadow
        appearance.shadowColor = UIColor.black.withAlphaComponent(0.08)

        // Apply
        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }
}

struct RootView_Previews: PreviewProvider {
    static var previews: some View {
        RootView()
            .environmentObject(AppState())
    }
}
