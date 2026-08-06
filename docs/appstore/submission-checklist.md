# App Store submission checklist — CaddieInsight iOS

The path from this repository to a live App Store listing. Everything
code-side is already in `ios/`; the steps below are the Apple-side work
an operator does once, then per release.

## One-time setup

- [ ] **Apple Developer Program** — enroll the publishing entity
      (individual or organization, $99/yr) at developer.apple.com.
- [ ] **App Store Connect app record** — My Apps → “+” → New App:
      platform iOS, name and bundle ID from `docs/appstore/listing.md`.
      If `com.caddieinsight.app` is taken for your team, change
      `PRODUCT_BUNDLE_IDENTIFIER` in the Xcode target to match the
      record you create.
- [ ] **Signing** — open `ios/CaddieInsight.xcodeproj` in Xcode 16+,
      select the target → Signing & Capabilities → choose your team.
      Automatic signing handles certificates and profiles.
- [ ] **Production server** — a reachable HTTPS deployment with
      `web.require_account: true` (the `/api/v1/*` mobile surface and
      the account page's Mobile app card both require it) and
      `PUBLIC_BASE_URL` set (the same-origin check that guards token
      minting compares against it).
- [ ] **Demo account for App Review** — a real account on the production
      server with at least one completed, coaching-eligible session, so
      reviewers see the Today brief, report, and practice plan populated.
      Fill its credentials into the Review Notes template in
      `listing.md`. Review WILL exercise the pairing flow; make sure the
      account page shows the Mobile app card.
- [ ] **Support + privacy pages** — the listing needs live Support and
      Privacy Policy URLs. The storefront already hosts policy pages;
      add a support page if one doesn't exist yet.

## Per release

- [ ] Bump `MARKETING_VERSION` (user-facing) and
      `CURRENT_PROJECT_VERSION` (build number) in the target settings.
- [ ] Product → Archive with an iOS device (or “Any iOS Device”)
      selected, then Distribute App → App Store Connect → Upload.
      Xcode validates the privacy manifest and icon on the way up.
- [ ] **TestFlight first** — the build appears under TestFlight after
      processing; smoke-test on a real device against production:
      pair, upload a clip, watch it process, open the report, check in.
- [ ] **Screenshots** — capture the five-shot storyline from
      `listing.md` on a 6.9" simulator (iPhone Pro Max class); Apple
      scales the rest, or capture 6.5" separately.
- [ ] Fill in the version's metadata from `listing.md`, attach the
      build, complete the App Privacy answers (mirror the manifest —
      email, videos, fitness data; App Functionality; no tracking).
- [ ] Answer export compliance "standard encryption, exempt" — the
      project already sets `ITSAppUsesNonExemptEncryption = NO`, so the
      question is pre-answered on upload.
- [ ] Submit for review with the Review Notes (server URL + demo
      credentials + pairing steps) filled in.

## Review-risk notes (why this app is shaped the way it is)

- **Account required, no in-app registration.** Signup happens on the
  web. That's allowed; guideline 5.1.1 requires apps to *also* offer
  account deletion visibility — the app's Account tab links to the
  website account page, where history deletion lives. Keep that page
  reachable from the demo account.
- **No IAP.** Membership is sold on the website and never referenced as
  purchasable from inside the app (the Account tab links to "Membership,
  billing & devices" for management, which is permitted for account
  management surfaces). Don't add "Upgrade to Pro" buttons in-app
  without adding StoreKit.
- **Token pairing.** Reviewers occasionally flag paste-a-token flows as
  friction; the Review Notes explain the security rationale (tokens
  can't mint tokens). Keep the website flow to three taps.
- **Video upload.** Uses the system photo picker (no library permission
  prompt) and never touches the camera directly, so there are no
  permission-purpose strings to defend.

## After approval

- Releases default to manual — press Release when the server is ready.
- Watch the first days' crash reports in App Store Connect → TestFlight
  → Crashes and Xcode Organizer.
- Server-side, the mobile API is versioned (`resource_version: 1` on
  every payload); additive changes are safe for shipped builds, breaking
  changes need a `/api/v2`.
