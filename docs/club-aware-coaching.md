# Club-aware coaching release and compatibility floor

CaddieInsight requires a canonical club for every analysis. This release makes
that context safe to use in coaching while preserving every existing report
and Proof Cycle.

## What changes

The reader supports two immutable priority rules:

- **Rule 1** is the original stable order.
- **Rule 2** may reorder issues only inside the same severity tier: Driver
  emphasizes finish balance; Fairway wood and Hybrid emphasize head-height
  stability; Iron emphasizes centered-turn measurements; Wedge keeps rule-1
  order until shot intent and lie are captured.

Measurements, flags, thresholds, severity, drills, and pass marks do not change.
A major issue always remains ahead of a club-preferred warning. Down-the-line
analysis remains timing and rhythm only.

Every new Proof target records the priority rule that selected it. Readers
replay that stored rule, accept only rules 1 and 2, and hide an unsupported or
unverifiable target instead of silently migrating it. Generated reports also
carry an additive priority-rule meta marker so their dynamic result card, gear
match, and weekly plan replay the same focus after activation or rollback;
pre-marker reports are rule 1, while malformed or unsupported markers fail
closed. The artifact version, report format, metrics schema, API contracts, and
SQLite schema are unchanged.

## Comparison boundary

When rule 2 is active, recurrence, progress, and weekly-plan context are scoped
to the same club, handedness, and camera angle. A changed context starts or
continues a different baseline; it cannot advance the old Proof Cycle.

CaddieInsight measures phone-video timing and two-dimensional body movement as
seen from the selected camera. It does not measure club path, clubface angle,
attack angle, dynamic loft, launch, spin, carry, strike location, ball flight,
or three-dimensional biomechanics, and it does not claim a drill caused a
measured change.

## Release and rollback

The compatibility floor was merged at `7de2d5f` and verified on Railway as
deployment `5708586587` with `coaching.club_aware_enabled: false`. The shipped
configuration now sets the literal YAML boolean to `true`. Strings, numbers,
null, or missing values still remain on rule 1.

Rollback returns to `7de2d5f`. That floor understands both rule versions:
rule-2 reports, dynamic cards, digests, and Proof targets remain readable and
verifiable while new baselines return to rule 1. `/healthz` exposes only the
non-sensitive enabled state and selected rule version so activation and
rollback can be verified without creating or reading a golfer's session.
