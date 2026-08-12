# Site audit

What exists today across both surfaces, what has to survive the revamp, and
where every current page lands in the new design. Written at the Phase 1
checkpoint. Nothing has been changed on either surface.

Companion to [MOCKUP_INVENTORY.md](MOCKUP_INVENTORY.md), which covers the
design source.

---

## 1. Tooling

| Thing | Status |
| --- | --- |
| Shopify CLI | 4.6.1, **authenticated** |
| Shopify Admin API | Authenticated via MCP — read and write |
| Live theme | Pulled to `theme-current/` (gitignored) |
| App repo | `kylejames0513-bot/caddieinsight`, on branch `site-revamp` |
| Railway | Authenticated. Project `desirable-spontaneity`, service `SwingLab` |

### The CLI needs the permanent domain, not the vanity one

`shopify theme list --store caddieinsight.com` fails. The CLI appends
`.myshopify.com` to whatever you give it, producing the nonexistent
`caddieinsight.com.myshopify.com`, and reports it as an authorization failure —
which reads like a permissions problem and is not one.

The permanent domain is **`e0hbgh-ip.myshopify.com`** (an auto-generated handle;
the handle migration was deferred). Every CLI command needs it:

```bash
shopify theme list --store e0hbgh-ip.myshopify.com
```

### Themes on the store

| Theme | Role | ID | Updated |
| --- | --- | --- | --- |
| `caddieinsight-industry-20260812` | **live** | 154836009132 | 2026-08-12 03:04 |
| `caddieinsight-theme` | unpublished | 154835452076 | 2026-08-12 03:04 |
| `caddieinsight-instrument-20260811b-figure-report` | unpublished | 154831978668 | 2026-08-11 22:36 |
| `caddieinsight-instrument-20260811` | unpublished | 154814775468 | 2026-08-12 02:09 |
| `caddieinsight-theme-r6-livehero` | unpublished | 154799308972 | 2026-08-11 10:19 |

Four unpublished themes are already accumulating. The revamp adds a fifth; worth
tidying the dead ones at cutover.

---

## 2. The storefront today

### The repo is genuinely in sync with what is live

I pulled the live theme and compared it file by file against `storefront-theme/`.
This matters more than it sounds — a theme that has been edited in the admin
silently diverges from the repo, and the revamp would then overwrite work nobody
has in git.

- **Same 90 files.** The repo additionally holds `README.md` and three source
  PNGs whose `.webp` variants are what actually ship.
- **Every `.liquid` file is byte-identical.**
- **One JSON difference in the entire theme:** `templates/index.json`, where the
  repo carries `"block_order": []` and `"blocks": {}` on the `proof` section and
  the live copy omits them. Shopify normalises empty blocks away on save. It is
  not a content change.

So `storefront-theme/` is a trustworthy baseline. Nobody has been editing in the
theme editor.

One trap worth recording: `shopify theme pull` prepends a nine-line
auto-generated banner comment to every JSON template, so the pulled files are
JSONC and `json.loads` rejects them at character 0. Strip the comment before
diffing or you will conclude the whole theme has drifted.

### Structure

Online Store 2.0, JSON templates throughout. **35 sections, 6 snippets, 16
templates** plus 7 customer-account templates.

Sections already split cleanly into page-level (`main-*`, 18 of them, one per
template) and composable marketing sections (`hero`, `how-it-works`, `proof`,
`comparison`, `gear-showcase`, `plans-band`, `faq`, `cta-banner`, `coach-notes`,
`report-feature`, `related-products`, `trust-rail`), with `header-group.json`
and `footer-group.json` for the shell.

**This is the right architecture for the revamp.** The mockups' sections map
onto the existing section library rather than requiring a new one — `hero`,
`how-it-works` (the Method's four steps), `report-feature` (Inside the report),
`gear-showcase` (the rack), `plans-band`, `comparison` (the Founders table) and
`faq` all already exist as schema-driven sections. Phase 4 is largely a restyle
and re-content of a structure that is already correct, plus new sections for the
Founders page and the policy shell.

`settings_schema.json` already seeds Industry tokens — `#f2f2f3` paper,
`#1d1f20` ink — and already marks the green field, steel accent and trace colour
as structural and not merchant-editable. That decision holds for the revamp.

### The rebrand renamed titles but not handles

This is the largest single finding on the storefront, and it changes a
conclusion I drew in Phase 0.

Everything user-visible says CaddieInsight. Everything addressable still says
swinglab:

| Kind | Handle (live URL) | Title (what people see) |
| --- | --- | --- |
| Page | `/pages/the-swinglab-method` | The CaddieInsight Method |
| Page | `/pages/how-swinglab-works` | How CaddieInsight Works |
| Collection | `/collections/swinglab-gear` | CaddieInsight Gear |
| Product | `/products/swinglab-pro` | CaddieInsight Pro |
| Product | `swinglab-performance-cap` (archived) | CaddieInsight Performance Cap |
| Menu | `swinglab-main` | CaddieInsight Main |
| Menu | `swinglab-footer` | CaddieInsight Footer |
| Snippet | `snippets/swinglab-tag-label.liquid` | — |

**I was wrong in Phase 0 about mockup `2b`.** I flagged its `HOME / SWINGLAB
GEAR /` breadcrumb as stale copy left over from the old brand. It is not — it is
an accurate rendering of the live collection handle. The mockup is telling the
truth about production.

That turns a copy fix into a URL decision, and hard rule 4 applies: renaming any
of these breaks a live URL and requires a 301. See Risks.

### Catalogue

- **7 active products**: `swinglab-pro` (the membership) and six training aids
  (Swing Path Mat, Tempo Trainer, Tempo Rope, Rotation Trainer, Connection Ball,
  Arm Link).
- **6 archived products** from the original dropship import, all zero inventory,
  no public URL.
- **2 collections**: `frontpage` (1 product) and `swinglab-gear` (12).
- **6 existing URL redirects**, all mapping verbose dropship product handles to
  the clean ones. They work and must be preserved.

Note the mockups name the rack's products slightly differently — `2a` shows
"Connection Ball / Tempo Trainer / Rotation Trainer" at $12.99 / $28.99 / $28.99,
and `2b` details the Rotation Trainer at $28.99. Those map to real products.

### Public URLs

From `/sitemap.xml`, plus the routes the theme serves. Every one of these must
keep working or get a 301.

**Pages** — `/pages/contact`, `/pages/data-sharing-opt-out`,
`/pages/the-swinglab-method`, `/pages/how-swinglab-works`, `/pages/about`,
`/pages/faq`, `/pages/shipping-returns`

**Products** — `/`, `/products/swinglab-pro`, plus the six gear handles listed
above

**Collections** — `/collections/all`, `/collections/frontpage`,
`/collections/swinglab-gear`

**Blog** — `/blogs/news`, which has **zero articles**. There is no blog content
to carry over. The brief's "Blog index" is a template with nothing in it.

**Theme-served** — `/search`, `/cart`, `/account` and the six account
sub-routes, `/404`, `/policies/*` (Shopify-hosted policy documents)

---

## 3. The app today

FastAPI on Starlette, server-rendered Jinja2. Entry point
`swinglab.web.app:create_app`. **64 routes.**

### The CSS architecture is the constraint that shapes Phase 5

There is **no shared stylesheet between the two surfaces**. The app does not
load `storefront-theme/assets/base.css`; its entire style layer is inline
`<style>` blocks inside `web_layout.html.j2`, which is **1,775 lines**.

The layout says so itself, and explains why:

> It lives here rather than in base.css because THIS SURFACE DOES NOT LOAD
> [it] … the app selectors were written into base.css, matched nothing, and …

Parity between surfaces is therefore maintained **by hand and by test**, with
comments through the layout insisting each token is "spelled EXACTLY as
`storefront-theme/assets/base.css` spells them". `make parity` enforces some of
this.

That is the real cost centre in Phase 5: any token change has to be made twice,
in two languages, and the tests are what stop them drifting.

### Templates

18 Jinja templates, 13,400 lines. The big ones are `web_layout` (1,775),
`report_guided` (1,592), `web_upload` (1,345), `report` (1,326) and `web_login`
(1,248).

### The deep green is already production

`web_layout.html.j2` already ships `<meta name="theme-color" content="#070f0b">`.
The colour I flagged in Phase 0 as "the one addition the mockups make to
Industry" is not new to the codebase at all — it is already the app's installed
theme colour. Promoting it to a named token is bookkeeping, not a change.

Fonts are already self-hosted on both surfaces: Barlow 400/500, Barlow Condensed
600, DM Mono 400/500, latin subset, no third-party origin at runtime.

**Note DM Mono.** Both surfaces ship it and the mockups use monospace for every
measured value and spec label, but it is **not in the Industry token set** —
`_ds_manifest.json` declares only Barlow and Barlow Condensed, and the design
system's lint rule rejects any other family. DM Mono is a CaddieInsight
extension in the same way `#070f0b` is, and `DESIGN_TOKENS.md` needs to say so.

### Doc rot found in passing

The font comment block in `web_layout.html.j2` describes the wrong typeface
entirely — it explains at length why the surface ships "Archivo variable
400-800" and "a static Archivo Expanded wdth-125/wght-800", including byte
counts. The files it loads are Barlow. The comment survived the v3→v4 rebrand
unedited. Worth correcting while we are in there.

Similarly, `swinglab/web/static/` still holds retired `swinglab-favicon.png`,
`swinglab-logo.png` and `swinglab-logo-inverse.png` alongside the live
`caddieinsight-*` marks.

### Deploy

| Setting | Value |
| --- | --- |
| Source | `kylejames0513-bot/caddieinsight`, branch **`main`** |
| Environments | **`production` only** |
| Domains | `app.caddieinsight.com`, `caddieinsight.up.railway.app` (:8080) |
| Builder | Railpack, build env V3, runtime V2 |
| Replicas | 1, `us-east4` |
| Volume | `/data` |
| Wait for CI | **`checkSuites: false`** |

Two things here need your attention — see Risks.

---

## 4. Where every current page lands

### Storefront

| Current URL | Template | New design | Action |
| --- | --- | --- | --- |
| `/` | `index.json` | `2a` (hero possibly `4b`) | Rebuild |
| `/products/{gear}` ×6 | `product.json` | `2b` / `2c` | Rebuild |
| `/products/swinglab-pro` | `product.membership.json` | `2a` plans + `3a` | Rebuild |
| `/collections/swinglab-gear` | `collection.json` | **No mockup** — extend | Design |
| `/collections/all`, `/frontpage` | `collection.json` | Same as above | Design |
| `/pages/about` | `page.about.json` | **No mockup** — extend | Design |
| `/pages/faq` | `page.faq.json` | **No mockup** — extend from `3b` shell | Design |
| `/pages/contact` | `page.contact.json` | `3c` | Rebuild |
| `/pages/shipping-returns` | `page.json` | `3b` shell | Rebuild |
| `/pages/the-swinglab-method` | `page.json` | `2a` Method section | Rebuild + redirect decision |
| `/pages/how-swinglab-works` | `page.json` | `2a` Method section | Rebuild + redirect decision |
| `/pages/data-sharing-opt-out` | `page.json` | `3b` shell | Restyle |
| `/policies/*` | Shopify-hosted | `3b` shell | Styled only where Shopify allows |
| `/cart` | `cart.json` | **No mockup** — extend from `3d` | Design |
| `/search` | `search.json` | **No mockup** — extend | Design |
| `/404` | `404.json` | **No mockup** — extend | Design |
| `/blogs/news`, `/blogs/news/*` | `blog.json`, `article.json` | **No mockup**, no content | Restyle only |
| `/account/*` ×7 | `customers/*.json` | **No mockup** | Restyle |
| — | — | `3a` Founders Pass | **New page** |
| Checkout | Shopify-hosted | `3d` is reference only | **Untouched** |

Nothing gets dropped, so no storefront page needs a redirect on account of the
redesign itself. The only redirect question is the `swinglab-*` handles.

### App

| Route | Template | New design |
| --- | --- | --- |
| `/session/{id}`, `/session/{id}/report` | `report_guided`, `report` | `1a` desktop, `1b` mobile |
| `/progress` | `web_progress` | `1c` desktop, `1d` mobile |
| `/drills` | `web_drills` | `1e` desktop, `1f` mobile |
| `/scorecard` | `web_scorecard` | `5a` |
| upload / capture | `web_upload` | `4a` |
| `/sessions`, `/today`, `/` | `web_sessions`, `web_today` | Extend from `1a`/`1c` |
| `/login`, `/signup`, `/reset` | `web_login` | **No mockup** — extend |
| `/onboarding` | `web_onboarding` | **No mockup** — extend |
| `/pricing` | `web_pricing` | `2a` plans + `3a` |
| `/shop` | `web_shop` | `2a` rack |
| `/account`, `/account/history/delete` | `web_account` | Extend |
| `/offline` | `web_offline` | Extend |
| `/sample-report` | served bundle | The artefact `2a` links to |

`/sample-report` is worth calling out: the storefront's second CTA points at it,
so it is the one place where a storefront promise is redeemed by an app-rendered
page. It has to match on both sides or the seam shows.

---

## 5. Content that must carry over

1. **The six URL redirects.** Dropship handles → clean handles. They are the
   only thing standing between old inbound links and a 404.
2. **All seven pages' body copy.** `/pages/shipping-returns` in particular
   carries real fulfilment terms, and `3b` shows a policy shell that is
   stricter than what is there now.
3. **Shopify-hosted policy documents** at `/policies/*`. Not theme content, not
   editable from the theme, and `docs/runbooks/store-policies.md` governs them.
4. **The catalogue.** 7 active products, their variants, inventory and the
   `swinglab-gear` collection.
5. **Customer accounts and order history** — untouched by the revamp, but the
   account templates get restyled around them.
6. **Nothing from the blog.** Zero articles.

---

## 6. Risks and blockers

**1 · There is no Railway preview environment.** The brief says "No production
deploy to Railway without my explicit approval; use a PR and preview instead."
The project has exactly one environment, `production`, deploying from `main`.
A PR will run CI but will not produce a deployed preview URL — that capability
does not exist today. Three options: (a) I create a Railway PR/staging
environment, which is an infrastructure change and needs your go-ahead; (b) app
review happens locally against `uvicorn` with screenshots in the PR; (c) accept
that app changes are unpreviewable until merge. I would take (a), and (b) in the
meantime. **This needs a decision before Phase 5.**

**2 · Railway does not wait for CI.** `checkSuites: false` means a merge to
`main` deploys immediately, whether or not tests passed. CLAUDE.md's "never
merge a red build" is currently a convention with nothing enforcing it. Cheap to
change and worth doing before this revamp starts landing.

**3 · The `swinglab-*` handles are a fork in the road.** Renaming them finishes
the rebrand and makes the URLs match the mockups; keeping them means
`caddieinsight.com/collections/swinglab-gear` stays the canonical gear URL
forever. Renaming is safe for the two pages and the collection — Shopify creates
the 301 automatically and I would add explicit redirects too. `swinglab-pro` is
the one I would leave alone: `shopify.app.toml` shows the orders/paid webhook is
the only thing that grants Pro, and `config.yaml`'s `first_sale_catalog_only`
allowlist references product handles. Renaming a product handle mid-revamp risks
the money path for no visual gain. **My recommendation: rename the two pages and
the collection, leave `swinglab-pro` and the archived products alone, add
explicit 301s for all of it.** Your call.

**4 · The storefront cannot be rendered locally.** No local Liquid render
exists, so theme changes are verifiable only through the pinned tests,
`make theme-zip`, and the unpublished preview theme. That is already the
documented reality (CLAUDE.md); flagging it because Phase 4 asks for 375 / 768 /
1440 verification, and all of that has to happen against the Shopify preview
URL, not locally.

**5 · Design-gate tests exist and will fight a token change.** There is a
`tests/test_theme_brand_filenames.py` holding a retired-filename list, plus the
parity suite. A palette move touches both surfaces and the tests that pin them.
Expected, not a problem — but it means Phase 2 is not merely documentation.

**6 · Archived product URLs.** Six products were archived without redirects.
Their old URLs 404 today. Not caused by the revamp, but Phase 6 is the natural
place to fix it.

---

## 7. What Phase 2 needs to settle

`DESIGN_TOKENS.md` has to record three things the Industry system does not:

1. **`#070f0b`**, the deep green field — already the app's `theme-color`, needs
   a name and a stated set of allowed roles.
2. **DM Mono**, shipped by both surfaces for measured values, absent from the
   Industry token set and forbidden by its lint rule.
3. **The two-surface duplication rule** — that every token exists twice, in
   `base.css` and inline in `web_layout.html.j2`, and which tests enforce the
   match.

---

## Where this leaves things

The storefront is in better shape than expected: repo and live are in sync, the
section architecture already matches the mockups' shape, and the tokens are
half-seeded. The app is the heavier lift, because a 1,775-line inline
stylesheet has to absorb the same token change by hand.

The two decisions I need from you are **the Railway preview environment** and
**the `swinglab-*` handles**. Everything else I can carry on my own defaults.
