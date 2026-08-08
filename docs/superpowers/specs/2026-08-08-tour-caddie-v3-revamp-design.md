# Tour Caddie v3 — Full Site & App Revamp

**Status:** Cloud-agent implementation design (autonomous; no interactive approval gate)

**Date:** 2026-08-08

## Summary

Complete gutting pass on CaddieInsight: new logo lockup (flagstick + precision
arc), distinctly photoreal Pro / Founders Pass campaign art, signed-in chrome
that never overlaps, Shopify hub sync that reads as one product with the store,
and a guided report that feels like a private AI tour caddie — not a lab dump.

## Goals

1. **Pro/premium AI golfer feel** site-wide (storefront + app + report).
2. **Legit logos and membership photography** for Pro and Founders Pass.
3. **Shopify hub sync** surfaced cleanly in account + storefront session bridge.
4. **Signed-in header/boxes** with no overlap, clipping, or cramped wrap.
5. **Guided report 100x** — richer caddie voice, video theater, training dosage.
6. **Delete unnecessary GitHub cruft** (tracked `dist/` zip, `.superpowers/sdd`
   notes, orphan atmosphere webp, stale hero PNG, egg-info if present).

## Non-goals

- External LLM APIs for coaching claims
- Renaming `swinglab` package / Shopify handles
- Live Shopify Admin theme push

## Visual direction

Stay inside **Turf Instrument** (cool mist field, forest ink, one amber kinetic
accent, Archivo/Sora + mono evidence). Raise craft: new mark reads golf +
measurement; Pro and Founders photos are clearly different scenes (dawn range
vs private-club dusk).

## Success criteria

- New logo + favicon shipped to theme assets and app static
- Plans band shows new Pro / Founders photoreal cards (1536×1024)
- Signed-in overlay header + member rail + hero padding contracts hold
- Account Store hub shows sync state when linked
- Guided report leads with priority, evidence theater, and training block
- Focused pytest suites for storefront + report pass
- Unnecessary tracked binaries/notes removed from the repo
