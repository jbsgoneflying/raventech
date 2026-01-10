import SwiftUI

struct RootView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        TabView {
            CalendarScreen()
                .tabItem {
                    Label("Calendar", systemImage: "calendar")
                }
            EngineOneScreen()
                .tabItem {
                    Label("Engine 1", systemImage: "chart.bar.doc.horizontal")
                }
            SPXScreen()
                .tabItem {
                    Label("SPX", systemImage: "chart.xyaxis.line")
                }
            SettingsScreen()
                .tabItem {
                    Label("Settings", systemImage: "gear")
                }
        }
    }
}

struct RootView_Previews: PreviewProvider {
    static var previews: some View {
        RootView()
            .environmentObject(AppState())
    }
}
