# International markets — Asia, Rest of World, and why both exist

**Changed 2026-08-10.** Until this date the store had two markets: `us`
(United States) and `international` — and `international` held **236
countries**, i.e. every country Shopify offers except the US. It was not an
international strategy, it was the default with nothing removed from it.

It is now three markets:

| Market | Handle | Countries | Ships gear? |
| --- | --- | --- | --- |
| United States | `us` | 1 | Yes — "Domestic" zone |
| Asia | `asia` | 26 | 21 of the 26 — "Asia" zone |
| Rest of World | `rest-of-world` | 210 | **No** |

Coverage is unchanged at 237 countries. Nothing lost market access; the same
set was repartitioned.

## Why Rest of World is not simply deleted

The obvious reading of "only Asia" is to delete the other 210 countries. Do
not. **A country that belongs to no market cannot check out at all** — not
gear, not the membership, nothing. Deleting them would have silently ended
membership sales to the UK, EU, Canada and Australia, which is a revenue path
the store actively wants.

Market membership and shipping are separate gates, and only the second one is
about gear:

- **Market membership** decides whether a buyer can reach checkout.
- **A shipping rate** decides whether a *shippable* item can be bought.

A cart of digital-only items needs no shipping rate, so Rest of World buyers
check out fine for the membership and hit "no shipping available" the moment
gear enters the cart. That is the intended behaviour, and it is why the two
lists deliberately do not match.

## What counts as Asia here

UN M49 **Eastern + South-eastern + Southern Asia**. Western Asia (the Middle
East, Türkiye, Israel, Cyprus) and Central Asia are *not* included — they sit
in Rest of World. Russia is Europe under M49 and is likewise in Rest of World.

```
East:  CN HK JP KR MO MN TW
SE:    BN ID KH LA MM MY PH SG TH TL VN
South: AF BD BT IN LK MV NP PK
```

**Five of those 26 have no shipping rate**: `AF BT MV PK TL`. They are in the
Asia market but not the Asia delivery zone, so they behave exactly like Rest
of World — membership yes, gear no. Add them to the zone if a supplier can
actually reach them; until then this is honest rather than broken.

## The membership defect fixed alongside it

`SL-COACH-1MO` and `SL-COACH-12MO` had `inventoryItem.requiresShipping =
true`, while every other variant of `CaddieInsight Pro` was correctly `false`.
A membership flagged as physical needs a shipping rate to check out, so the
Coach tier was **unbuyable from every country outside the US and the 21-country
Asia zone**, and buyers inside those zones were quoted postage on a digital
product. Both are now `false`.

This is the same class of bug as the market split itself: a digital product
must never require shipping, or the delivery zones silently become the
membership's country list.

## The wildcard that does not exist

The natural way to build Rest of World is a catch-all — one market that claims
every country no other market names. Shopify rejects it on this store:

```
marketCreate(conditions: {regionsCondition: {applicationLevel: ALL}})
→ "Matching all is not supported for driver type region"
```

So `rest-of-world` is an **explicit 210-country list**, with the maintenance
cost that implies: **when Shopify adds a new country to its catalogue, it lands
in no market and cannot check out** until someone adds it. There is no error
and no notification — it simply never appears. If a region reports being unable
to buy, check market membership before anything else.

## Verifying

```graphql
query {
  markets(first: 20) {
    nodes {
      name handle status
      conditions { regionsCondition { regions(first: 250) { nodes {
        ... on MarketRegionCountry { code }
      } } } }
    }
  }
}
```

The three counts must be 1 / 26 / 210 and sum to 237 with no overlap. A total
below 237 means a country is stranded outside every market.

## Reverting

Re-adding the 210 codes to `asia` via `marketUpdate` (`conditionsToAdd`) and
deleting `rest-of-world` restores the previous state exactly. Markets are
plain configuration — no orders, prices or catalogues are attached to these
two, so a revert loses nothing. The `requiresShipping` fix should **not** be
reverted; it was a defect independent of the market layout.
