# Internal TestFlight checklist (no web-app changes)

Goal: ship the SwiftUI app privately via **Internal TestFlight** (no public listing). This assumes networking is already reachable over your Tailscale HTTPS host.

## 1) Apple side
1) Join/confirm Apple Developer Program (paid).
2) In App Store Connect:
   - Create a new iOS app record (bundle id must match Xcode target).
   - Set “Age Rating” and basic metadata (can be minimal for TestFlight).

## 2) Xcode project wiring (using `/ios` sources)
1) Create a new **iOS App (SwiftUI)** project in Xcode.
2) Replace the starter files with the contents of `ios/Sources/BreachApp/`.
3) Set bundle identifier (e.g., `co.raventech.breachapp`) and select your Team for signing.
4) Open `AppConfig.swift` and set:
   - `BaseURL.dev` → `https://YOUR-TAILSCALE.ts.net`
   - `BaseURL.prod` → keep or point to your public host
5) Confirm the Deployment Target (iOS 17+ recommended).

## 3) Build & run locally (simulator/device)
1) Plug in your iPhone (or use simulator).
2) In Xcode, select your device and hit **Run**.
3) Tap **Settings → Run health + flags** to verify `/api/health` and `/api/flags` work.
4) Run Calendar → refresh; Engine 1 → enter ticker → run; SPX → refresh.

## 4) Archive and upload to TestFlight
1) In Xcode: **Product → Scheme → Edit Scheme…** ensure “Any iOS Device (arm64)” archive target.
2) **Product → Archive** (Release build).
3) In the Organizer window, select the archive → **Distribute App** → **App Store Connect** → **Upload**.
4) Wait for processing (a few minutes). You should see the build under TestFlight in App Store Connect.

## 5) Enable Internal Testing
1) In App Store Connect → TestFlight → select the new build.
2) Add yourself and any internal testers (up to 25) who are in your App Store Connect team.
3) Install from the TestFlight app on your iPhone.

## 6) If Apple review is requested (rare for internal-only)
- Provide a short reviewer note: “VPN-gated app; please use supplied account/token” or switch temporarily to a public base URL + app-token header if needed.
- If you stay VPN-only, reviewers may skip deep validation; internal builds typically need no review.

## 7) Versioning & icons
- Set `CFBundleShortVersionString` (e.g., `1.0`) and `CFBundleVersion` (increment build number).
- Add app icons and a minimal launch screen in Xcode Assets.

## 8) Post-upload smoke checks
- From TestFlight build details, check **crash** tab (should be empty).
- Install build → run the same health/flags check in Settings.

## If you need help during any step
Share the step you’re on and any Xcode/App Store Connect screenshot or error text; I’ll give exact click-by-click fixes.***
