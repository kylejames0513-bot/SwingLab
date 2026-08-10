# International markets — Asia, Rest of World, and why both exist

**Changed 2026-08-10.** Until this date the store had two markets: `us`
(United States) and `international` — and `international` held **236
countries**, i.e. every country Shopify offers except the US. It was not an
international strategy, it was the default with nothing removed from it.

It is now three markets:

| Market | Handle | Countries | Sells |
| --- | --- | --- | --- |
| United States | `us` | 1 | Everything — "Domestic" zone |
| Asia | `asia` | 26 | Everything — "Asia" zone covers all 26 |
| Rest of World | `rest-of-world` | 210 | **Membership only** |

Coverage is unchanged at 237 countries. Nothing lost market access; the same
set was repartitioned.

## Why Rest of World is not simply deleted

The obvious reading of "only Asia" is to delete the other 210 countries. Do
not. **A country that belongs to no market cannot check out at all** — not
gear, not the membership, nothing. Deleting them would have silently ended
membership sales to the UK, EU, Canada and Australia, which is a revenue path
the store actively wants.

## The three gates, and which one does what

Product availability, checkout eligibility and shipping are separate
mechanisms. Rest of World being membership-only is enforced by the first, with
the third as a backstop:

1. **The market catalog** decides which products exist for a buyer at all.
   `MarketCatalog/97917829292` ("Rest of World — membership only") binds
   `Publication/193407615148` to the Rest of World market, and that publication
   contains exactly one product: `CaddieInsight Pro`. Gear is not browsable,
   not addable to a cart, and not orderable from those 210 countries.
2. **Market membership** decides whether a buyer can reach checkout at all.
   All 237 countries belong to a market, so nobody is locked out.
3. **A shipping rate** decides whether a *shippable* item can be bought. There
   is no Rest of World delivery zone, so even if gear reached a cart there, it
   could not be checked out.

Gate 1 is what the customer experiences; gate 3 is why a mistake in gate 1
cannot turn into an unfulfillable order. Asia and the US have **no** market
catalog, which means they get the full catalogue via the Online Store sales
channel — the restriction is opt-in per market, not opt-out.

**The publication is `autoPublish: false`.** A newly created product is
therefore *excluded* from Rest of World by default. That is the safe direction
for gear, but it also means **a future digital product will not sell to Rest of
World until it is explicitly added to `Publication/193407615148`**.

## What counts as Asia here

UN M49 **Eastern + South-eastern + Southern Asia**. Western Asia (the Middle
East, Türkiye, Israel, Cyprus) and Central Asia are *not* included — they sit
in Rest of World. Russia is Europe under M49 and is likewise in Rest of World.

```
East:  CN HK JP KR MO MN TW
SE:    BN ID KH LA MM MY PH SG TH TL VN
South: AF BD BT IN LK MV NP PK
```

**The Asia delivery zone is the same 26 countries.** It previously held 21,
leaving `AF BT MV PK TL` in the market with no shipping rate — membership-only
by accident. They were added to the zone on 2026-08-10 at the existing rates
(Standard $9, Express $18), so market and zone are now identical sets. Keep
them that way: a country in the Asia market with no rate is a silent
membership-only country, which is Rest of World's job, not Asia's.

Note that adding a country to the zone is a **delivery promise**, not a
configuration detail — `AF BT MV PK TL` now quote $9/$18 like anywhere else in
Asia, and no supplier transit time has been measured for them. If a supplier
cannot actually reach one, remove it from the *market* as well as the zone, so
it falls into Rest of World and buys the membership only.

One API trap: countries with provinces (China, Japan, India…) are rejected with
`"must have at least one province associated"` unless each entry passes
`includeAllProvinces: true`. Pass it on every country; it is harmless on the
ones that have no provinces.

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
      catalogs(first: 5) { nodes { title status publication { id
        products(first: 30) { nodes { title } } } } }
    }
  }
  deliveryProfiles(first: 5) { nodes { profileLocationGroups {
    locationGroupZones(first: 20) { nodes { zone { name countries {
      code { countryCode } } } } } } } }
}
```

Three invariants, all checkable from that one response:

- Market counts are **1 / 26 / 210**, summing to 237 with no overlap. Below
  237 means a country is stranded outside every market and cannot buy anything.
- The **Asia market and Asia zone are the same 26 codes**. A country in one but
  not the other is a silent behaviour change nobody asked for.
- **Rest of World is the only market with a catalog**, and its publication
  lists exactly `CaddieInsight Pro`. Anything else in that list is gear leaking
  into a market that cannot be shipped to.

## Reverting

Each piece reverts independently:

- **The market split** — re-add the 210 codes to `asia` via `marketUpdate`
  (`conditionsToAdd`) and delete `rest-of-world`. Markets are plain
  configuration and no orders are attached, so nothing is lost.
- **Membership-only** — delete `MarketCatalog/97917829292`, or set it to
  `DRAFT`. With no catalog on the market, Rest of World reverts to the full
  Online Store catalogue, and gate 3 (no shipping zone) becomes the only thing
  stopping a gear order.
- **The Asia zone** — drop `AF BT MV PK TL` back out of the zone if a supplier
  turns out not to reach them. Prefer removing them from the market too, so
  they land in Rest of World rather than becoming membership-only-by-accident
  inside Asia.

The `requiresShipping` fix on the Coach variants should **not** be reverted; it
was a defect independent of the market layout.
