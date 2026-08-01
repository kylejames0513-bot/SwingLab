# CaddieInsight product-interface contract

CaddieInsight is a proof-driven coaching workspace, not a library of swing
scores. The primary customer loop is:

1. Film a swing and identify the club, handedness, and camera angle.
2. Receive one evidence-backed coaching priority.
3. Practice one measured target with a short prescription.
4. Re-film in the same context and see whether the movement held.

The interface should make the next useful action obvious at every step. A page
may offer secondary routes, but it must not give several equal visual
priorities or make a golfer interpret implementation details before acting.

## Product promise and measurement boundary

The lead promise is **one swing priority, one practice plan, and proof when the
golfer re-films**. Product copy may describe phone-video timing and
two-dimensional body movement visible from the selected camera. It must not
claim measurement of club path, clubface angle, attack angle, dynamic loft,
launch, spin, carry, strike location, ball flight, or three-dimensional
biomechanics. It must not imply that a drill caused an outcome or promise a
score reduction.

Club context is required before analysis because coaching priority and matched
comparison depend on the club. Down-the-line analysis remains timing and
rhythm only. Wedge coaching stays neutral until the product captures shot
intent and lie.

## Primary journey

The public landing page leads with the product outcome, then shows an honest
example before asking a visitor to trust a claim. Synthetic examples must be
visibly identified as synthetic, generated through the real report engine, and
not presented as customer results or testimonials.

The upload flow asks for club, handedness, and camera angle before the video.
Transfer checks and advanced controls remain available without competing with
the core decision path. Required fields, browser hooks, field names, and API
contracts are compatibility surfaces.

Today is the signed-in home. It should answer four questions in order:

- What state is my analysis or Proof Cycle in?
- What is my one next move?
- What should I practice now?
- What recent evidence can I revisit?

Empty, queued, processing, failed, coaching-ready, re-film, and legacy states
must be distinguishable without inventing data. Paid identity is experiential:
a Pro golfer receives personal welcome copy and member context, while free
golfers receive the same coherent coaching loop without a false upgrade label.

## Interface system

The shared shell owns color, spacing, type, radius, elevation, status, chip,
card, action, form, and content-width tokens. Product pages should consume
those primitives before adding page-scoped rules. Layouts collapse cleanly at
the established 980-pixel navigation breakpoint and respect reduced-motion
preferences.

Every page retains the skip link and `main#MainContent`. Navigation dropdowns,
the mobile dialog and focus restoration, Pro-member hooks, authenticated
privacy headers, and the offline shell are behavioral contracts rather than
visual implementation details.

## Trust rules

- Show real product output whenever it explains the value better than
  decorative imagery.
- Label synthetic demonstrations at the point where they appear.
- Use generated photography only as atmosphere, never as an implied customer,
  testimonial, coaching result, or measured before-and-after.
- Prefer plain-language customer states to queue, worker, storage, or pipeline
  terminology.
- Do not expose personal swing data, account state, or reports through public
  caching.

## Release sequence

The interface is released in bounded, reversible slices:

1. shared shell, landing, upload, and Today;
2. status, report, history, account, and secondary-page consistency;
3. disclosed atmosphere imagery after the product surfaces are stable;
4. Shopify theme alignment after the app visual system is verified.

Each slice must preserve the Railway single-replica and `/data` runtime,
Shopify integration identifiers and webhook behavior, authentication and
entitlements, route/API schemas, and stored report compatibility.
