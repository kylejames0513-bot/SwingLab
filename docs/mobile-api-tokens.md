# Mobile API tokens

CaddieInsight’s native client uses a personal device token only after the
account owner signs in through the normal browser flow. It is not a replacement
for a Shopify Customer Account, a browser session, or an operator token.

## Issue and manage a device

From an authenticated same-origin browser session:

- `POST /api/v1/mobile-tokens` with `{"label":"Kyle's iPhone"}` issues one
  opaque `ciat_<selector>.<secret>` credential. The raw value is returned only
  in that no-store response; save it directly to the platform keychain.
- `GET /api/v1/mobile-tokens` returns lifecycle metadata only: selector,
  label, created/last-used/expiry/revocation timestamps, and whether it is
  active.
- `DELETE /api/v1/mobile-tokens/{selector}` revokes one owned device token.

Issuance, listing, and revocation require the cookie-authenticated browser
session and same-origin validation. A bearer credential cannot mint, list, or
revoke device tokens. The service stores the selector and SHA-256 digest of the
complete credential, never the raw secret.

There can be at most five active device tokens per account. Tokens expire after
90 days, can be revoked individually, and are bound to the account’s
`auth_epoch`; password reset or ownership recovery invalidates tokens issued
under the prior epoch.

## Bearer scope

Send the token as `Authorization: Bearer <token>` only to the owned mobile
surface:

- `/api/v1/me`, profile, Today, sessions, Caddie Briefs, and practice
  check-ins;
- owned legacy `/api/session/{id}` and `/api/sessions` resources;
- owned `/session/{id}`, report, and permitted report-file routes; and
- `POST /upload` for multipart mobile uploads. The `club` form field is
  required and must be exactly `driver`, `fairway-wood`, `hybrid`, `iron`, or
  `wedge`. A missing, blank, or unsupported value returns HTTP `400` with
  `{"detail":"club must be one of: driver, fairway-wood, hybrid, iron, wedge"}`.

`GET /api/v1/me` includes an additive `identity.history_epoch`. Native clients
should discard cached session/report/practice state and refetch owned resources
when that number changes. A browser **Delete swing history / Start over** action
advances the epoch, but intentionally does not revoke device tokens or advance
`auth_epoch`; the account owner can keep using the same connected device after
clearing its stale local history.

An invalid or malformed `Authorization` header fails with `401`; it never
falls back to an accompanying browser cookie. Cookie-authenticated mutations
keep the existing Origin/Referer CSRF validation. No CORS policy is added or
relaxed for this feature, so a browser cannot turn a copied token into a
cross-origin credential.

## Privacy and incident response

Shopify customer-data exports include device lifecycle metadata, while the
device-record portion omits token digests, secrets, and epoch values. User
deletion and Shopify customer redaction erase associated device-token records.
Deleting swing history is narrower than account deletion or Shopify redaction:
it preserves device tokens, membership, purchases, identity links, and the
golfer profile while removing swing/report/practice history.
If a token may have been exposed, revoke it from the same browser management
route; if account ownership is in question, use the established
password/ownership recovery flow to advance the auth epoch and invalidate all
older device tokens.
