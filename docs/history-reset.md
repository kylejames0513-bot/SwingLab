# Swing-history reset

## Product contract

**Delete swing history / Start over** gives an authenticated golfer a clean
coaching baseline without destroying the account or creating a new allowance.
It is intentionally narrower than account deletion or a Shopify privacy
redaction.

The customer surface is independently gated by
`web.history_reset_enabled`. Release the schema, quota receipts, recovery
journal, and epoch-aware writers with this gate `false`; only a later release
may turn it on. Once activated, rollback must stop at that compatibility-floor
release and must never target a binary that ignores these receipts and epochs.

Deleted:

- every terminal job owned by the account and its validated session directory;
- generated reports, retained media, metrics, and Proof Cycle sidecars in those
  directories;
- legacy practice check-ins, Proof Cycle practice evidence, and transfer checks;
- product events that are both account-owned and linked to a deleted session;
- already-delivered Shopify privacy-export cache that still contains the old
  swing history. An undelivered matching export blocks the reset until an
  operator completes delivery, preserving Shopify's replay receipt contract.

Preserved:

- the account, password/email/Shopify authentication, and golfer profile;
- Free/Pro entitlements, purchases, grants, and Shopify customer linkage;
- browser sessions, personal mobile API tokens, and OAuth/browser-session rows;
- digest and marketing preferences;
- auth throttling/security receipts;
- the current month's used allowance and refilm-courtesy accounting.

Account-level product events such as account verification or a Pro click remain;
session-linked events disappear with their session. A golfer who needs the
entire account erased must use the separate privacy/account-deletion workflow.

## Browser security

The management surface is cookie-only. Any `Authorization` header is rejected,
and the mutation requires a same-origin form post, a short-lived nonce, and the
exact phrase `START OVER`. Password accounts must re-enter the current password;
failures share the existing per-IP and per-account login throttle. Accounts
without a password must have authenticated by email or Shopify within the last
15 minutes, otherwise they must sign out and authenticate again.

Every authenticated browser session and signed confirmation captures
`history_epoch`, and the commit compares the confirmation generation plus the
account `auth_epoch`. The successful browser is advanced to the new generation;
an older signed cookie must authenticate again before it can even mint another
confirmation. Replaying its original form is also fenced by the transactional
comparison, so it cannot remove swings created after the first reset. If another
reset, password recovery, or ownership recovery changes either epoch while a
reset is being prepared, the database rolls back, staged files return, and the
golfer must review the current state or log in again.

Owned HTML and report files use private/no-store responses. A successful reset
returns `Clear-Site-Data: "cache"` so an installed browser shell drops cached
owned resources. `/api/v1/me` exposes the additive `history_epoch`; guarded
practice, Proof Cycle, transfer, and product-event writes fail with conflict if
they began before the latest epoch.

Weekly digest composition, its one-send claim, and email delivery share a
history-delivery guard with reset. A send that already started finishes before
the deletion can commit; after commit, the scheduler can compose only from new
history. Digest consent itself remains preserved.

## Transaction and crash recovery

The single-replica job manager performs a journaled reset:

1. Acquire the Shopify privacy lock, then the history-delivery guard, then the
   job-manager lock.
2. Reject the operation if any owned job is queued or processing.
3. Validate every persisted job id and real directory; reject symlinks and paths
   outside the sessions root.
4. Write a `prepared` journal entry and atomically rename each directory into
   `.history-trash/<operation-id>/` on the same volume.
5. Start `BEGIN IMMEDIATE`, recheck the exact owned-job set, run related-row
   deletion and `history_epoch` advancement on the same SQLite connection,
   archive monthly quota contributions, delete job rows, mark the journal
   `committed`, and commit.
6. Purge the committed quarantine and journal row. If cleanup cannot finish,
   keep the row for health visibility and retry at startup.

Any failure before commit rolls back SQLite and restores the prepared
directories. On startup, prepared operations are restored; committed operations
finish deletion. The reset never follows symlinks and never recursively deletes
an unresolved or out-of-root path.

## Quota semantics

`analysis_usage_monthly` stores only a domain-separated SHA-256 hash of the
internal user id, the UTC month boundary, coaching-ready/refilm counts, and
bounded expiry metadata.
Before terminal jobs are removed—whether by a customer reset or automatic
retention—their current-month contributions are merged into that receipt.
Allowance is calculated from live jobs plus the receipt, with the existing one
refilm courtesy applied across the combined total. Receipts expire after their
month can no longer affect quota.

## Operations

- `/healthz.history_cleanup_pending` should normally be `0`.
- A nonzero value does not make logical history visible again, but means secure
  filesystem cleanup or journal recovery still needs attention.
- Backup creation fails while any journal row remains, because reset quarantine
  is deliberately excluded from backup artifacts.
- Do not scale beyond one application replica or replace `/data/sessions` until
  reset/job coordination and durable state have been externalized.
