# CaddieInsight for iOS

The native iPhone app for CaddieInsight: film your swing, upload the clip,
and get back the Caddie Brief, practice plan, and the full swing report —
all against the existing server's stable mobile API (`/api/v1/*`).

No third-party dependencies. SwiftUI, iOS 17+, one target.

## What's in the app

- **Connect** — pair the iPhone with a CaddieInsight server using a device
  token minted on the website (Account → Mobile app). The token is
  validated against `/api/v1/me` before anything is stored, and lives only
  in the iPhone's Keychain.
- **Today** — the latest session, the Caddie Brief (one focus, one cue,
  one drill), the 10/20/45-minute practice plan, and the practice
  check-in, from `GET /api/v1/today`.
- **Analyze** — pick a swing video, declare club / handedness / camera
  angle (prefilled from the golfer profile), and stream it to
  `POST /upload` as multipart with live progress. Uploads are staged on
  disk, so clip size is bounded by the server's limit, not by memory.
- **Sessions** — history from `GET /api/v1/sessions`; each session polls
  while queued/processing and settles into coaching-ready or
  re-film-required, mirroring the server's honest outcomes.
- **Report** — the session's `report.html` rendered in-app. Report files
  sit behind bearer auth, and `WKWebView` won't attach headers to
  subresources, so the view registers a `cireport://` URL-scheme handler
  that proxies every request (document, images, slow-mo clips) to the
  server with the Authorization header attached.
- **Account** — golfer profile editing (`PUT /api/v1/profile`, same
  vocabulary as web onboarding), links out to membership/billing on the
  web, and disconnect.

## Layout

```
ios/
├── CaddieInsight.xcodeproj      # Xcode 16 project (file-system-synced group)
└── CaddieInsight/
    ├── CaddieInsightApp.swift   # entry point
    ├── RootView.swift           # connect-or-tabs switch + shared UI pieces
    ├── Core/                    # API client, models, uploader, keychain, session
    ├── Features/                # Connect / Today / Upload / Sessions / Report / Account
    ├── Assets.xcassets          # AppIcon (generated), AccentColor (brand green)
    └── PrivacyInfo.xcprivacy    # privacy manifest (no tracking)
```

The app icon is generated from the same brand mark as the PWA icon:
`python store-assets/make_ios_app_icon.py` (requires Pillow).

## Building

1. Open `ios/CaddieInsight.xcodeproj` in **Xcode 16 or newer** (the
   project uses the file-system-synchronized format) on macOS.
2. In the target's *Signing & Capabilities*, pick your Apple Developer
   team. Change `PRODUCT_BUNDLE_IDENTIFIER` (`com.caddieinsight.app` by
   default) if your team can't claim it.
3. Run on a simulator or device. To use the app you need a reachable
   CaddieInsight server running with accounts enabled
   (`web.require_account: true` in `config.yaml`) — the `/api/v1/*`
   surface intentionally 404s without it.

### Pairing a device (how auth works)

Token issue/revoke is deliberately a cookie-only, same-origin surface on
the server — a leaked device token can never mint more tokens. The flow:

1. Log in to the website, open **Account → Mobile app**.
2. Name the device, tap **Create device token**; the token is shown once.
3. In the app, enter the server address and paste the token.

The server keeps the token hashed; revoking the device on the website
kills the app's access immediately (the app falls back to the Connect
screen with an explanation).

## Shipping to the App Store

See `docs/appstore/listing.md` for ready-to-paste App Store Connect
metadata and `docs/appstore/submission-checklist.md` for the step-by-step
path (certificates → TestFlight → review), including the App Review
demo-account requirement specific to this pairing flow.
