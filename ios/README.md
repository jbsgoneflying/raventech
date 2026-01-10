# Raven-Tech iOS (SwiftUI) scaffold

This folder contains SwiftUI-ready source files you can drop into a new Xcode iOS App project. It does **not** modify the existing web app.

## Quick start
1) In Xcode: **File → New → Project… → iOS App (SwiftUI)**. Name it `RavenTech` (or anything), keep SwiftUI + Swift.
2) Quit Xcode.
3) Copy the contents of `ios/Sources/BreachApp/` into your Xcode project’s source folder (e.g., replace `ContentView.swift` with the provided files).
4) Reopen Xcode. Set the bundle identifier and team.
5) In `AppConfig.swift`, set `BaseURL.dev` to your Tailscale HTTPS host (e.g., `https://node.ts.net`).
6) Run on device/simulator: the app will show Tabs (Calendar, Engine 1, SPX placeholder, Settings) and can call `/api/health` + `/api/flags` immediately.

## What’s included
- `APIClient` with async/await, tolerant JSON decoding, and HTML/redirect detection (to catch invite-gate redirects).
- Models for:
  - Flags (`/api/flags`)
  - Calendar (scan cards + days/events)
  - Engine 1 (summary/wing rec/events) using the golden payload shape
- View models & simple SwiftUI screens:
  - Calendar: scan cards + day list + macro/event detail placeholders
  - Engine 1: ticker input, summary cards, events list
  - SPX: placeholder view model + hook to `/api/spx-ic` later
  - Settings: base URL picker, health/flags fetch, clear cache
- Preview support hooks (you can point previews at `ios/Resources/PreviewPayloads/` or just use live calls).

## Networking notes
- App Transport Security requires **HTTPS**. Point `BaseURL.dev` at your Tailscale hostname with a valid cert.
- If the backend redirects to `/login`, `APIClient` will throw `AppError.serverHTML`, so you can see the issue instead of failing JSON decode.
- Add headers/tokens later if you move off VPN-only access.

## Building with the provided files
- Replace the default `ContentView` with `RootView`.
- Keep the `@main` entry `BreachApp` (provided).
- Targets: iOS 17+ recommended (for Charts/modern SwiftUI).

## Ship checklist (TestFlight internal)
- Set bundle id and signing team.
- Set app icons & launch screen.
- Set `BaseURL` to your Tailscale host for the build flavor you ship.
- Archive → Distribute via App Store Connect → TestFlight (internal).

## Where to add more
- `Models/Engine2Models.swift`: extend for `/api/spx-ic` & `/api/spx-levels`.
- `Views/SPXView.swift`: render risk surface + levels/heatmap.
- `Views/Calendar`: add month view if desired (week-first shipped).

## Safety
These sources are additive; they do not touch the backend or `static/`. Remove them to return the repo to pre-iOS state.***
