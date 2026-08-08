# Gear coverage — keeping the flywheel joined up

The product's loop is: measure the swing, name one priority, prescribe one
drill, sell the aid that drill needs, re-film against the pass mark. The joint
between "prescribe one drill" and "sell the aid" is a string. `swinglab/drills.py`
gives every drill a `gear_tag`; `swinglab/web/shop.py` matches that tag against
Shopify product tags. Nothing checked that the two ends met, so the loop could
break without producing an error — the report just went quiet at the exact
moment the coaching got specific.

`tests/test_gear_coverage.py` now checks it, against a committed snapshot of the
live `swinglab-gear` collection.

## Refreshing the snapshot

`tests/fixtures/gear_catalog.json` is generated, not hand-written. After any
change to product tags, availability, or collection membership:

```
SHOPIFY_STORE_DOMAIN=caddieinsight.com python scripts/refresh_gear_catalog.py
```

then commit the diff. The script calls `shop._fetch()` — the same public
Storefront query the running app makes, with no Admin API token — so the
snapshot is what customers are actually served rather than what the admin says
should exist. It refuses to write an empty snapshot, because a Shopify outage
committed as "no products" would turn every coverage assertion vacuous and go
green.

## The two layers, and why one gap is not like the other

A `gear_tag` can fail to reach a customer in two independent ways, and the fix
is completely different in each case.

**Stocked** — does an available product in the collection carry the tag at all?
A gap here is a *sourcing* problem: nothing to sell.

**Recommendable** — does `shop.recommend()`, under the shipped `config.yaml`,
actually return that product? A gap here is a *configuration* problem. When
`shop.first_sale_catalog_only` is on, a product must additionally carry
`shop.first_sale_verified_tag` **and** one of `shop.first_sale_candidate_tags`.
A correctly tagged, in-stock, published product is still withheld without both.

The second layer exists because the first one is reassuring on its own and can
be entirely beside the point. That is precisely what happened here.

## State as of 2026-08-08

Measured by running `shop.recommend()` against the live catalogue with the
shipped `config.yaml`:

| `gear_tag` | Drills | Stocked? | Recommended today |
| --- | --- | --- | --- |
| `swinglab:tempo` | 3 | yes — Tempo Trainer, Tempo Rope | **nothing** |
| `swinglab:consistency` | 2 | yes — Tempo Trainer, Tempo Rope, Rotation Trainer, Connection Ball, Arm Link | **nothing** |
| `swinglab:arm-extension` | 3 | yes — Connection Ball, Arm Link | **nothing** |
| `swinglab:general` | 2 | yes — all six | n/a — browse-only, never auto-recommended |
| `swinglab:sway` | 2 | **no** | nothing |
| `swinglab:hip-slide` | 2 | **no** | nothing |
| `swinglab:head-dip` | 2 | **no** | nothing |
| `swinglab:balance` | 2 | **no** | nothing |

The right-hand column is the one that matters: **the app currently recommends
no gear at all, for any measured flag, and `/shop` serves an empty catalogue.**

The cause is not tagging. `shop.first_sale_candidate_tags` in `config.yaml`
names three products — the clip-on swing metronome, the anti-sway hip
resistance band, and the alignment-stick set. All three were archived in the
2026-08-03 restock that replaced the SwingLab-era dropship candidates with the
branded CI-\* SKUs. No live product carries any of those tags, and no live
product carries `caddieinsight:fulfillment-verified`. The gate therefore
filters the whole catalogue to nothing.

The allowlist did not break. The store moved and the allowlist stayed put.

## Closing the recommendation gap — an operator decision

This one cannot be closed from code, and should not be closed casually.
`caddieinsight:fulfillment-verified` is not a formatting detail: per
`docs/first-sale-launch.md`, it asserts that an operator holds a supplier
agreement, landed cost and margin, a US sample order, an order-routing test,
branded tracking and return-address proof, and a measured delivery SLA for
that SKU. Applying it to a product without that evidence would make the app
promote a SKU on a claim nobody has checked — which is the "no fabricated
proof" rule, applied to fulfillment rather than to measurement.

So the question for the operator is per-SKU and factual: *for which of the six
live products does that evidence actually exist?* Then, for those SKUs only:

1. Add a candidate tag per verified SKU to `shop.first_sale_candidate_tags` in
   `config.yaml` (the current three entries name archived products and should
   go).
2. Tag those products in Shopify with the matching candidate tag and with
   `caddieinsight:fulfillment-verified`.
3. Re-run the refresh script, delete the now-stale `UNRECOMMENDABLE` entries in
   `tests/test_gear_coverage.py`, and watch the tests go green.

If the honest answer is that no SKU has that evidence yet, the correct action
is to leave the gate closed and accept that gear recommendations are off —
but to know that is the case, rather than believing the loop is running.

## Closing the sourcing gaps

`swinglab:sway`, `swinglab:hip-slide`, `swinglab:head-dip` and
`swinglab:balance` have drills and no product. Two routes:

- **Source.** An alignment-stick set and a hip resistance band already existed
  in the archived catalogue and map cleanly onto the sway and hip-slide drills;
  a full-length mirror serves head-dip. Re-sourcing branded equivalents closes
  three of the four.
- **Collapse.** Point the drills at gear that exists. Be careful here: the
  Rotation Trainer plausibly supports the hip-slide drills, but retagging it
  is an editorial claim that the aid supports that drill, and the standing rule
  (`docs/first-sale-launch.md`) is that the app may never say a product fixes a
  swing issue — only that it can support the prescribed drill. Stretching a
  product to fill a hole is how a trustworthy recommendation becomes an
  ordinary upsell.

Balance is worth leaving alone. Feet-together swings and hold-the-finish need
no equipment, and a category where the honest answer is "you do not need to buy
anything" is not a gap to be closed.

## Working with the ledgers

`UNSTOCKED` and `UNRECOMMENDABLE` in `tests/test_gear_coverage.py` record the
gaps that exist today, each with its reason. They ratchet in both directions:

- a tag that loses coverage fails the build;
- a tag listed as a known gap that *regains* coverage also fails the build,
  until its entry is deleted.

That second direction is the point. A waiver list that only ever grows
documents a problem; one that fails when the problem is fixed forces itself to
shrink. Adding an entry is a decision to be argued for in review, not a way to
get a red build green.
