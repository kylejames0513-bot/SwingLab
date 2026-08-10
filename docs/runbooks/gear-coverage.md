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

## State as of 2026-08-10 — the gate is OFF

> **Correction (2026-08-10).** An earlier revision of this section (dated
> 2026-08-08) stated that "the app currently recommends no gear at all, for
> any measured flag, and `/shop` serves an empty catalogue." That was true
> when written and is **false now**: `shop.first_sale_catalog_only` was set
> to `false` in `config.yaml` on 2026-08-09 (owner decision), so the whole
> live catalogue is promotable and `/shop` serves it. This section misled a
> full planning pass by reading as current state. Verify against
> `config.yaml` before trusting any table here.

With the gate off, recommendation follows tags alone. The stocked/unstocked
split is still real and still ratcheted by `tests/test_gear_coverage.py`:

| `gear_tag` | Drills | Stocked? |
| --- | --- | --- |
| `swinglab:tempo` | 3 | yes — Tempo Trainer, Tempo Rope |
| `swinglab:consistency` | 2 | yes — Tempo Trainer, Tempo Rope, Rotation Trainer, Connection Ball, Arm Link |
| `swinglab:arm-extension` | 3 | yes — Connection Ball, Arm Link |
| `swinglab:sequence` | 2 | check the snapshot — this tag was missing from earlier revisions of this table entirely |
| `swinglab:general` | 2 | yes — all six (browse-only, never auto-recommended) |
| `swinglab:sway` | 2 | **no** |
| `swinglab:hip-slide` | 2 | **no** |
| `swinglab:head-dip` | 2 | **no** |
| `swinglab:balance` | 2 | **no** |

The four unstocked tags are a *sourcing* gap, not a configuration one: drills
for sway, hip slide, head dip and balance prescribe aids the store does not
carry, and the report goes quiet at exactly the moment the coaching gets
specific. Sourcing decisions live with the operator and
`docs/first-sale-launch.md`'s evidence rules.

## If the first-sale gate is ever re-enabled

`first_sale_catalog_only: true` would re-empty the catalogue instantly: the
`shop.first_sale_candidate_tags` allowlist still names three products archived
in the 2026-08-03 restock, and no live product carries
`caddieinsight:fulfillment-verified`. Before flipping it back on, do the
per-SKU evidence work (supplier agreement, landed cost, US sample order,
order-routing test, tracking and returns proof, measured delivery SLA — see
`docs/first-sale-launch.md`), tag the verified SKUs, replace the allowlist
entries, and re-run `scripts/refresh_gear_catalog.py`. A verification tag
without the evidence behind it is fabricated proof, applied to fulfillment
instead of measurement.
