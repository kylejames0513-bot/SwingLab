# ADR 0001: CaddieInsight naming and compatibility boundaries

- Status: Accepted
- Date: 2026-07-27

## Context

The product is presented to customers as CaddieInsight, while its repository,
Python package, console command, database, deployment service, Shopify merchant
identifiers, and historical assets contain the earlier SwingLab name. Renaming
all of them at once would combine product branding with import, deployment,
data, webhook, storefront, and asset migrations.

The live Railway application and Shopify purchase bridge must continue working
through the foundation migration.

## Decision

1. **CaddieInsight is the customer-facing name.** New user-facing copy,
   documentation, and architecture language use CaddieInsight.
2. **The Python distribution and import namespace remain `swinglab`
   temporarily.** The console command remains `swinglab`, and existing module
   imports remain valid.
3. **Operational identifiers remain stable.** `SWINGLAB_*` environment
   variables, `swinglab.db`, service and volume names, `/data/sessions`, and the
   GitHub repository name are compatibility contracts.
4. **Shopify identifiers remain stable.** Existing product handles, SKUs,
   `swinglab:*` tags, collection paths, webhook URLs, and theme references are
   merchant contracts rather than customer-facing branding.
5. **Historical asset filenames remain stable.** Storefront and CDN references
   may depend on names that include `swinglab`.
6. **The root Dockerfile remains the Railway contract.** This foundation does
   not add a competing Railway manifest or change secrets, DNS, volumes, data,
   or production settings.
7. **New internal boundaries are additive.** `swinglab.analysis`,
   `swinglab.api`, and `swinglab.integrations.shopify` provide stable facades.
   Existing implementations and import paths remain until later migrations can
   leave tested compatibility shims.

## Consequences

- Customer naming is consistent without risking live imports or purchases.
- Some internal and merchant-facing identifiers intentionally continue to say
  SwingLab; this is documented debt, not accidental branding drift.
- Future module moves must preserve old imports through shims until a versioned
  removal plan is approved.
- Internal renames, repository renames, Shopify identifier changes, and data
  migrations must be separate, independently deployable, and independently
  reversible decisions.
- This decision does not itself deploy anything or mutate external systems.
