# App Store listing — CaddieInsight

Ready-to-paste metadata for App Store Connect. Character limits are
Apple's; everything here fits them.

## Identity

| Field | Value |
| --- | --- |
| App name (30 chars max) | `CaddieInsight Swing Analysis` |
| Subtitle (30 chars max) | `Golf coaching from one video` |
| Bundle ID | `com.caddieinsight.app` (change to match your team) |
| SKU | `caddieinsight-ios-1` |
| Primary category | Sports |
| Secondary category | Health & Fitness |
| Age rating | 4+ |
| Price | Free (membership is bought on the website, not in-app) |

## Promotional text (170 chars max)

> Film a few swings, upload the clip, and get honest numbers plus one
> clear thing to practice — with a drill, a dosage, and a pass mark.

## Description (4000 chars max)

> CaddieInsight turns one phone video of your golf swing into coaching
> you can act on.
>
> Film yourself hitting balls — phone propped at hip height, face-on —
> and upload the clip. A few minutes later you get back per-swing
> metrics, a labeled key-position strip, quarter-speed slow motion, an
> annotated coach replay, and a full report you can open right in the
> app.
>
> Then the caddie gets to the point. One focus. Why it matters. One cue
> to feel. One drill with a dosage and a pass mark. No swirl of twelve
> tips — the single change most likely to move your ball-striking,
> chosen from what the camera actually measured.
>
> WHAT YOU GET
> • Per-swing metrics: tempo, backswing and downswing time, sway and
>   drift, finish balance — measured face-on, honestly reported
> • The Caddie Brief: one focus, one cue, one drill, one pass mark
> • A practice plan sized to your time: 10, 20, or 45 minutes
> • Practice check-ins so the next session holds you to the pass mark
> • Key-position strip (address / top / impact / finish), slow motion,
>   and the annotated coach replay in the full report
> • Session history with trends across comparable sessions
>
> HONEST BY DESIGN
> If a clip can't be read well enough for trustworthy coaching,
> CaddieInsight says so and asks for a re-film instead of guessing —
> and a matched re-film doesn't count against your monthly allowance.
>
> CONNECTING
> The app pairs with your CaddieInsight account: log in on the website,
> create a device token under Account → Mobile app, and paste it into
> the app once. You can revoke a device from the website at any time.
>
> A CaddieInsight account is required. Free accounts include a monthly
> analysis allowance; Pro membership is available on the website.

## Keywords (100 chars max)

`golf,swing,analysis,tempo,coach,drill,practice,slow motion,caddie,video,lesson,training`

## URLs

| Field | Value |
| --- | --- |
| Support URL | `https://caddieinsight.com/pages/support` (create if missing) |
| Marketing URL | `https://caddieinsight.com` |
| Privacy Policy URL | `https://caddieinsight.com/policies/privacy-policy` |

## App privacy (nutrition label)

Declare exactly what the privacy manifest
(`ios/CaddieInsight/PrivacyInfo.xcprivacy`) declares — data used for
**App Functionality**, linked to identity, **no tracking**:

- **Contact info → Email address** — the paired account's email.
- **User content → Photos or videos** — the swing clips you upload.
- **Health & fitness → Fitness** — swing metrics derived from your video.

"Data used to track you": **None**. No third-party SDKs, no ads, no
analytics beyond the product's own first-party events.

## Screenshots (6.9" and 6.5" required)

Suggested five-shot storyline, captured from the simulator with a demo
account:

1. Today view with a Caddie Brief — "One focus. One cue. One drill."
2. Upload form with a clip picked — "Film. Upload. Learn."
3. Session detail, coaching ready — "Honest numbers per swing."
4. Full report with the key-position strip — "Your swing, broken down."
5. Practice plan + check-in — "Practice with a pass mark."

## App Review notes (paste into the Review Notes field)

> The app requires a CaddieInsight account and pairs via a device token
> created on our website (this keeps token minting off the mobile
> attack surface). For review, use the demo account below — it is
> pre-loaded with an analyzed session so every screen has content:
>
> Server: `https://<your production host>`
> Email: `<demo account email>`
> Password: `<demo account password>`
>
> Steps: log in at the server URL in Safari → Account → Mobile app →
> Create device token → paste the server URL and token into the app's
> Connect screen. Membership upgrades are intentionally web-only
> (Reader-style external account content is not sold in-app), so no IAP
> is present.
