# CaddieInsight native client

Expo client for the owned mobile surface.

**Status — it builds, it has never been interacted with.** Verified here:
strict TypeScript passes, `expo-doctor` passes 18/18, and it bundles cleanly
for both iOS and Android (`expo export`). Not verified: nobody has opened a
screen, tapped anything, or pointed it at a live server. Treat the screens as
unproven until you have run it on a phone.

That distinction matters — "it typechecks" hid a missing `expo-asset`
dependency that failed the moment a bundler touched it.

## What exists

| Path | |
| --- | --- |
| `app/_layout.tsx` | Expo Router root, TanStack Query provider, brand chrome |
| `app/index.tsx` | Today — headline, three context readings, link to history |
| `app/sessions.tsx` | Swing history list, branching on server-supplied state |
| `app/connect.tsx` | Paste a device token issued from the browser |
| `src/api/client.ts` | Bearer client; 401 clears the credential |
| `src/api/types.ts` | Hand-written response shapes (see below) |
| `src/auth/token.ts` | Keychain read/write via `expo-secure-store` |

## Getting the beta onto your phone

Fastest path, no developer account and no build service:

```bash
cd mobile
npm install
npx expo start
```

Install **Expo Go** from the App Store or Play Store, then scan the QR code in
the terminal. Every package here runs inside Expo Go — including
`expo-secure-store`, so the device-token flow works — which is why the
scaffold deliberately avoids native modules that would force a custom build.

Your phone and this machine must be on the same network. If the QR code fails
to connect, `npx expo start --tunnel` routes around it.

Point it at a server with `EXPO_PUBLIC_API_BASE_URL`, or edit
`expo.extra.apiBaseUrl` in `app.json`. It defaults to production — fine for a
first look at real data, wrong once you start changing things.

```bash
EXPO_PUBLIC_API_BASE_URL=https://app.caddieinsight.com npx expo start
```

### When Expo Go stops being enough

Expo Go runs the JavaScript but cannot add native modules. The moment you add
in-app camera capture — which is both the missing product and Apple's answer
to guideline 4.2 — you need a development build:

```bash
npx eas build --profile development --platform ios
```

That needs an Expo account and, for a device install, an Apple Developer
Program membership.

### Checks

```bash
npm run typecheck                 # strict TypeScript
npx expo-doctor                   # SDK/dependency alignment
npx expo export --platform ios    # proves it bundles
```

Run the export one before claiming the app works. Typechecking alone missed a
missing dependency that broke the bundler outright.

## Connecting a device

A device token can only be minted by an authenticated **same-origin browser
session** — a bearer credential cannot mint, list, or revoke tokens, by
design. The app therefore cannot issue its own credential. `connect.tsx` opens
the account page, the golfer creates a token there, and pastes it back.

That paste step is the cost of not letting the app hold a credential capable
of minting more credentials. Do not "improve" it by adding an issuing endpoint
to the bearer surface; `tests/test_openapi_export.py` asserts no such helper
ships here.

Tokens expire after 90 days, cap at five active per account, and are bound to
the account's `auth_epoch` — a password reset invalidates every token issued
under the previous epoch. See `docs/mobile-api-tokens.md`.

## The types are hand-written, and that is a stopgap

`docs/superpowers/plans/2026-08-06-guided-report-native-integration.md` calls
for `openapi-typescript` types generated from `docs/api/openapi-v1.json`. That
document is now exported and committed (`scripts/export_openapi.py`), and its
paths, methods, and parameters are accurate — but **not one `/api/` operation
declares a 200 response schema**, because the handlers return bare
`JSONResponse` rather than declared response models. Generating from it today
produces route names with `unknown` bodies.

So `src/api/types.ts` mirrors the contract in `docs/mobile-api-tokens.md` by
hand. The risk is the usual one: the server can change a field's shape and
nothing here fails to compile. `tests/test_openapi_export.py` narrows it to
field-level drift by asserting the client's route table resolves against the
exported document.

**To close this properly:** give the `/api/` handlers Pydantic response
models, re-export, then replace `types.ts` with generated output. That is a
backend change, and it is the real remaining prerequisite from the plan.

## Not built yet

The native-integration plan's report screen is not here. It needs private
media with a bearer attached to short-lived owner-scoped URLs, `expo-image`
and `expo-video` with caching disabled, and grant-expiry refresh — none of
which can be developed blind. It wants a running client and a real report
first.

Also absent, and deliberately: the Maestro journeys and Jest suites the plan
specifies. Writing tests for screens that have never rendered would assert
guesses, and a green suite over unrun code is worse than an honest gap.
