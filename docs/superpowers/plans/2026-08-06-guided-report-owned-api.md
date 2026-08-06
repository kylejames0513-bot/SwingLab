# Guided Report Owned API and Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve every published guided report through one typed, owner-authorized API that returns either the exact `report-view-v1` projection or an explicit historical fallback, while keeping bundle internals and private media unreachable without both bearer ownership and a short-lived scoped grant.

**Architecture:** The API reads only the validated bundle produced by Guided Report Plans 1-2. A focused service distinguishes published structured jobs from historical jobs, the FastAPI adapter wraps the result in the shared `resource_version: 1` response envelope, and a five-minute HMAC grant scopes each media URL to the authenticated user, session, media key, checksum, and expiry. The existing generic report-file route remains compatible for historical HTML but denies structured JSON, manifests, checksums, staging data, and undeclared structured media.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, dataclasses, HMAC-SHA256, SQLite-backed `JobManager`, validated same-volume report bundles, deterministic OpenAPI JSON, and pytest.

## Global Constraints

- The approved contract is `docs/superpowers/specs/2026-08-06-guided-swing-report-design.md:545-909,911-1001,1017-1042,1082-1128,1147-1159`.
- Execute only after Guided Report Plans 1-2 are green. Consume these exact upstream interfaces without renaming them:
  - `swinglab.report_view.ReportViewV1`, `report_view_to_dict(view)`, `report_view_from_dict(payload)`, and `load_report_view(path)`;
  - `swinglab.report_artifacts.PublishedReportBundle`, `load_published_bundle(session_dir, *, report_rel, report_view_rel, manifest_rel, checksums_rel)`, and `resolve_media_path(bundle, media_key)`;
  - job fields `report_rel`, `report_view_rel`, `report_manifest_rel`, `report_checksums_rel`, and `structured_report`.
- The durable internal files are `report-view.json`, `report-bundle-manifest.json`, and `report-bundle-checksums.json`. None may be returned by a file route or named in an API response.
- Coordinate with Mobile Backend Foundation Task 1 at `docs/superpowers/plans/2026-08-06-caddieinsight-mobile-backend-foundation.md:24-114`: extend its `swinglab/api/contracts.py`, OpenAPI exporter, snapshot, and lifecycle-safe app factory; do not create a second contract generator.
- Coordinate with Mobile Backend Foundation Task 4 at `docs/superpowers/plans/2026-08-06-caddieinsight-mobile-backend-foundation.md:244-313`: extend its `swinglab/api/mobile_routes.py`, `swinglab/web/mobile_resources.py`, and `/api/v1/capabilities` response; do not duplicate auth, progress, or practice services.
- Preserve Plan 3's `JobManager(...,
  guided_html_writer=write_report_document_html)` web composition while adding
  the API router. API setup must not replace or bypass the production report
  writer.
- Preserve strict bearer behavior from `swinglab/web/app.py:649-712`: any malformed or invalid `Authorization` header fails and never falls back to a valid browser cookie.
- Preserve non-enumerating ownership behavior from `swinglab/web/app.py:1113-1129`: missing and cross-account session IDs produce the same status and body.
- Exact report route: `GET /api/v1/sessions/{session_id}/report-view`.
- Exact media route: `GET /api/v1/sessions/{session_id}/report-media/{media_key}?expires=<unix>&grant=<base64url>`.
- A media request requires a valid bearer credential, current session ownership, a bundle-declared media key, and a valid unexpired HMAC grant. A signed URL alone is never authorization.
- Media grants live for exactly 300 seconds. The URL contains no user ID, filesystem path, filename, checksum, email, bearer token, or coaching text.
- API successes and report-specific errors use `Cache-Control: private, no-store`, `Pragma: no-cache`, and no permissive CORS header. Media adds `X-Content-Type-Options: nosniff`.
- API projection removes `relative_path` and `checksum_sha256` from every media entry and adds only `url`, `expires_at`, and `locked`. No other persisted report field is renamed, recomputed, or reordered.
- The authorized report response may contain only the allowlisted coaching fields in `ReportViewV1`. Logs, telemetry, notifications, unauthorized responses, and unrelated API payloads may contain none of that content.
- A structured job with a missing, malformed, unsupported, or checksum-invalid bundle is an unavailable structured report, never a legacy report. Legacy is allowed only when a completed owned job has `structured_report == False` and a safely resolved historical HTML report.
- Run focused tests after each task, regenerate OpenAPI after each contract change, and end each task with a focused commit.

---

## Task 1: Add the typed API envelope, lossless projection, and grant primitive

**Files:**

- Modify after Mobile Backend Foundation Task 1: `swinglab/api/contracts.py`
- Create: `swinglab/api/report_projection.py`
- Create: `swinglab/api/report_media.py`
- Create: `scripts/export_report_api_fixtures.py`
- Create: `tests/test_report_view_api_contracts.py`
- Create: `tests/test_report_media_grants.py`
- Create: `tests/fixtures/report_api/coaching-improve-clear.json`
- Create: `tests/fixtures/report_api/coaching-protect-clear.json`
- Create: `tests/fixtures/report_api/coaching-limited-rendered.json`
- Create: `tests/fixtures/report_api/coaching-limited-visual-unavailable.json`
- Create: `tests/fixtures/report_api/capture-only.json`
- Consume without modifying: `swinglab/report_view.py`
- Consume shared fixtures/helpers from Guided Report Plan 1: `tests/fixtures/report_view/*.json` and `tests/report_view_fixtures.py`

**Interfaces:**

- `ReportMediaResponse(key, role, mime_type, entitlement, url, expires_at, locked)` contains no internal path or checksum.
- `APIReportViewV1 = APICoachingReportView | APICaptureOnlyReportView`; both are complete Pydantic mirrors of `ReportViewV1`, except `media` is `tuple[ReportMediaResponse, ...]`.
- `StructuredReportViewResponse(resource_version=1, mode="structured", report_view: APIReportViewV1)`.
- `LegacyReportViewResponse(resource_version=1, mode="legacy", legacy_report_url: str)`.
- `ReportViewResponse = Annotated[StructuredReportViewResponse | LegacyReportViewResponse, Field(discriminator="mode")]`.
- `ReportMediaAccess(url: str | None, expires_at: int | None, locked: bool)` is internal adapter input.
- `project_report_view(view: ReportViewV1, media_access: Mapping[str, ReportMediaAccess]) -> APIReportViewV1` preserves all non-media values and array order exactly.
- `REPORT_MEDIA_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")`; projection and routing reject any key outside this single-segment form rather than placing an arbitrary persisted string in a URL path.
- `REPORT_MEDIA_GRANT_TTL_SECONDS: Final[int] = 300`.
- `IssuedReportMediaGrant(expires: int, grant: str)` is a frozen dataclass.
- `ReportMediaGrantSigner(secret: bytes, *, now: Callable[[], float] = time.time)` exposes `issue(*, user_id: str, session_id: str, media_key: str, checksum_sha256: str) -> IssuedReportMediaGrant` and `verify(*, user_id: str, session_id: str, media_key: str, checksum_sha256: str, expires: object, grant: object) -> None`.
- `export_report_api_fixtures(source_dir: Path, output_dir: Path) -> None` creates the five canonical structured API envelopes with deterministic test-only media URLs; native tests consume these checked-in fixtures.

- [ ] **Step 1: Write the failing complete-projection tests.**

  Add a parametrized test over every committed view fixture. It must validate the persisted fixture through `report_view_from_dict`, project every media key, validate the API Pydantic union, recursively reject internal keys, and compare the non-media tree byte-for-byte after canonical JSON serialization:

  ```python
  INTERNAL_KEYS = {"relative_path", "checksum_sha256"}

  def walk(value):
      if isinstance(value, dict):
          yield value
          for child in value.values():
              yield from walk(child)
      elif isinstance(value, list):
          for child in value:
              yield from walk(child)

  @pytest.mark.parametrize("fixture_path", sorted(FIXTURE_DIR.glob("*.json")))
  def test_api_projection_is_lossless_and_contains_no_internal_media_fields(fixture_path):
      persisted = json.loads(fixture_path.read_text(encoding="utf-8"))
      view = report_view_from_dict(persisted)
      access = {
          item["key"]: ReportMediaAccess(
              url=f"/owned/{item['key']}?expires=100&grant=test",
              expires_at=100,
              locked=False,
          )
          for item in persisted["media"]
      }

      projected = project_report_view(view, access).model_dump(mode="json")

      assert not any(INTERNAL_KEYS & set(node) for node in walk(projected))
      assert [item["key"] for item in projected["media"]] == [
          item["key"] for item in persisted["media"]
      ]
      assert strip_media_transport(projected) == strip_media_transport(persisted)
  ```

- [ ] **Step 2: Run the test and confirm the intended red state.**

  Run: `python -m pytest tests/test_report_view_api_contracts.py -q`

  Expected: collection fails because `swinglab.api.report_projection` and the report API models do not exist.

- [ ] **Step 3: Add the complete strict Pydantic tree.**

  Add `extra="forbid"` API models for the exact persisted structures: trust, context, next move, event provenance, measurement detail, rendered/unavailable evidence, phase summary, drill alternative, practice prescription, re-film target/protocol, capture guidance, optional section, capabilities, and both top-level outcome variants. Use the same `Literal` enums as `swinglab.report_view`; do not replace closed enums with `str`.

  The discriminated envelope must be shaped exactly as follows:

  ```python
  class StructuredReportViewResponse(ContractModel):
      resource_version: Literal[1] = 1
      mode: Literal["structured"] = "structured"
      report_view: APIReportViewV1

  class LegacyReportViewResponse(ContractModel):
      resource_version: Literal[1] = 1
      mode: Literal["legacy"] = "legacy"
      legacy_report_url: str

  ReportViewResponse = Annotated[
      StructuredReportViewResponse | LegacyReportViewResponse,
      Field(discriminator="mode"),
  ]
  ```

- [ ] **Step 4: Implement the pure projection and fail closed on drift.**

  Start from `report_view_to_dict(view)`, replace the ordered `media` array by key, and validate with `TypeAdapter(APIReportViewV1)`. Require the access-map keys to equal the persisted media keys exactly; missing or extra keys raise `ReportProjectionError` before any response is built.

  ```python
  def project_report_view(view, media_access):
      payload = report_view_to_dict(view)
      persisted_media = payload.pop("media")
      expected = [item["key"] for item in persisted_media]
      if set(expected) != set(media_access):
          raise ReportProjectionError("Report media projection did not match the bundle.")
      payload["media"] = [
          {
              "key": item["key"],
              "role": item["role"],
              "mime_type": item["mime_type"],
              "entitlement": item["entitlement"],
              "url": media_access[item["key"]].url,
              "expires_at": media_access[item["key"]].expires_at,
              "locked": media_access[item["key"]].locked,
          }
          for item in persisted_media
      ]
      return TypeAdapter(APIReportViewV1).validate_python(payload)
  ```

- [ ] **Step 5: Add explicit drift tests.**

  Assert an extra persisted field, unknown version, unknown reason code, missing media access, extra media access, reordered/renamed media key, or media key containing a slash, encoded separator, dot segment, whitespace, or more than 128 characters fails. Assert structured improve, protect, limited-rendered, visual-unavailable, and capture-only fixtures all validate.

- [ ] **Step 6: Run the focused contract suite.**

  Run: `python -m pytest tests/test_report_view_api_contracts.py tests/test_report_view_contract.py -q`

  Expected: all tests pass; no API model accepts an unknown version, enum, or field.

- [ ] **Step 7: Write and run the failing deterministic grant tests.**

  ```python
  def test_grant_is_scoped_and_expires():
      signer = ReportMediaGrantSigner(b"s" * 32, now=lambda: 1_000.0)
      issued = signer.issue(
          user_id="u1", session_id="j1", media_key="focus",
          checksum_sha256="a" * 64,
      )
      assert issued.expires == 1_300
      signer.verify(
          user_id="u1", session_id="j1", media_key="focus",
          checksum_sha256="a" * 64, expires=issued.expires, grant=issued.grant,
      )
      with pytest.raises(ReportMediaGrantInvalid):
          signer.verify(
              user_id="u2", session_id="j1", media_key="focus",
              checksum_sha256="a" * 64, expires=issued.expires,
              grant=issued.grant,
          )
  ```

  Add changed session, key, checksum, expiry, malformed base64, expiry beyond the accepted issuance window, and `now == expires` cases. Expiry is exclusive: a grant is valid only while `now < expires`.

  Run: `python -m pytest tests/test_report_media_grants.py -q`

  Expected: import failure because `ReportMediaGrantSigner` does not exist.

- [ ] **Step 8: Implement key derivation, issuance, and verification.**

  HMAC input is UTF-8 `report-media-v1\0{user_id}\0{session_id}\0{media_key}\0{checksum_sha256}\0{expires}`. Derive the signing key with `HMAC-SHA256(app_secret, b"caddieinsight-report-media-key-v1")`. Parse `expires` as a bounded decimal integer, reject grants that expire more than 300 seconds after the current issuance window, decode unpadded base64url strictly, and compare the expected 32-byte digest with `hmac.compare_digest`. Do not log the URL, query, digest, checksum, or user ID.

- [ ] **Step 9: Export and freeze the five API fixtures.**

  The exporter loads each server fixture with `report_view_from_dict`, projects it with deterministic test-only URLs under `/api/v1/sessions/fixture/report-media/<key>?expires=4102444800&grant=fixture-not-valid`, wraps it in `StructuredReportViewResponse`, and writes sorted compact JSON plus a trailing newline. It never invokes the real signer and labels the grant invalid through its fixed value.

  Run:

  ```bash
  python scripts/export_report_api_fixtures.py --source tests/fixtures/report_view --output tests/fixtures/report_api
  python -m pytest tests/test_report_view_api_contracts.py tests/test_report_media_grants.py -q
  ```

  Expected: all five generated envelopes validate and a second export is byte-identical.

- [ ] **Step 10: Commit the typed projection and grant primitive.**

  ```bash
  git add swinglab/api/contracts.py swinglab/api/report_projection.py swinglab/api/report_media.py scripts/export_report_api_fixtures.py tests/test_report_view_api_contracts.py tests/test_report_media_grants.py tests/fixtures/report_api
  git commit -m "feat: define owned report API contract"
  ```

## Task 2: Resolve structured and historical reports without ambiguous fallback

**Files:**

- Create: `swinglab/web/mobile_reports.py`
- Modify: `swinglab/web/jobs.py:85-107,175-215,970-1031` only if Guided Report Plan 2 has not already exposed a typed published-bundle lookup
- Create: `tests/test_mobile_report_resolution.py`
- Consume without modifying: `swinglab/report_artifacts.py`
- Reuse current ownership semantics from: `swinglab/web/app.py:1113-1138`

**Interfaces:**

- `StructuredOwnedReport(job: Job, bundle: PublishedReportBundle)`.
- `LegacyOwnedReport(job: Job, report_path: Path, report_url: str)`.
- `OwnedReport = StructuredOwnedReport | LegacyOwnedReport`.
- `MobileReportNotFound`, `MobileReportNotReady`, `MobileReportFailed`, `MobileReportUnavailable`, and `MobileReportVersionUnsupported` are internal exceptions translated by the router.
- `load_owned_report(jobs: JobManager, *, user_id: str, session_id: str) -> OwnedReport` performs owner and publication checks before reading files.

- [ ] **Step 1: Write the failing resolution matrix.**

  Build rows directly through `JobManager` and cover unknown ID, another account's ID, queued, processing, failed, completed structured coaching, completed structured capture-only, completed historical HTML, structured flag with a missing view, structured flag with a checksum mismatch, and historical row with no safe report.

  ```python
  def test_structured_corruption_never_downgrades_to_legacy(
      manager, published_job, owner_id
  ):
      (published_job.session_dir / published_job.report_view_rel).write_text(
          "{}", encoding="utf-8"
      )

      with pytest.raises(MobileReportUnavailable):
          load_owned_report(
              manager,
              user_id=owner_id,
              session_id=published_job.id,
          )
  ```

  Add one paired assertion proving an unknown session and a different owner's session both raise `MobileReportNotFound` with the same public message.

- [ ] **Step 2: Run the test and verify the red state.**

  Run: `python -m pytest tests/test_mobile_report_resolution.py -q`

  Expected: import fails because `swinglab.web.mobile_reports` does not exist.

- [ ] **Step 3: Implement owner-first resolution.**

  Resolve the `Job` from `JobManager`, compare `job.user_id` to `user_id` with the same non-enumerating outcome for absent/cross-account rows, then translate status before touching report files: `QUEUED`/`PROCESSING` raise `MobileReportNotReady`, `FAILED` raises `MobileReportFailed`, and only `DONE` continues. Branch only on persisted `job.structured_report`.

  For a structured job, require all four relative fields and call:

  ```python
  bundle = load_published_bundle(
      job.session_dir,
      report_rel=job.report_rel,
      report_view_rel=job.report_view_rel,
      manifest_rel=job.report_manifest_rel,
      checksums_rel=job.report_checksums_rel,
  )
  ```

  Map an unsupported view version separately from checksum/schema/path failures. Never open `report-view.json` directly in the router.

- [ ] **Step 4: Implement the historical branch.**

  Require `structured_report is False`, `job.report_rel` is present, and the resolved HTML is a regular non-symlink file beneath `job.session_dir`. Return the existing owned route `/session/{session_id}/report`; do not manufacture structured data from HTML or `metrics.json`.

- [ ] **Step 5: Run resolution and artifact-regression tests.**

  Run: `python -m pytest tests/test_mobile_report_resolution.py tests/test_report_artifacts.py tests/test_report_bundle.py tests/test_report_bundle_recovery.py tests/test_web.py -q`

  Expected: all pass; corrupt structured jobs never return `LegacyOwnedReport`.

- [ ] **Step 6: Commit the resolver.**

  ```bash
  git add swinglab/web/mobile_reports.py tests/test_mobile_report_resolution.py
  git commit -m "feat: resolve owned structured reports"
  ```

## Task 3: Serve the structured/legacy route and extend capabilities

**Files:**

- Create: `swinglab/api/report_routes.py`
- Modify after Mobile Backend Foundation Task 4: `swinglab/api/mobile_routes.py`
- Modify after Mobile Backend Foundation Task 4: `swinglab/web/mobile_resources.py`
- Modify: `swinglab/web/app.py:477-604,4076-4292`
- Modify: `swinglab/api/contracts.py`
- Create: `tests/test_report_view_api.py`
- Modify: `tests/test_mobile_capabilities.py`
- Consume unchanged: `tests/test_guided_report_web_composition.py` from Plan 3

**Interfaces:**

- `create_report_router(*, jobs: JobManager, users: UserStore, require_account: bool, media_signer: ReportMediaGrantSigner) -> APIRouter`.
- `project_media_access(owned: StructuredOwnedReport, *, user: User, signer: ReportMediaGrantSigner) -> dict[str, ReportMediaAccess]` issues URLs for every durable media entry in the validated persisted view. Optional content that was locked/unrendered is represented only by `capabilities`/`optional_sections` and receives no invented media entry.
- `GET /api/v1/sessions/{session_id}/report-view` uses `resolve_mobile_auth`, then `load_owned_report`.
- Structured success is `{resource_version: 1, mode: "structured", report_view: ...}`.
- Historical success is `{resource_version: 1, mode: "legacy", legacy_report_url: ...}`.
- `/api/v1/capabilities` adds `report_view_v1: bool` to the existing typed
  response. This plan exposes a default-false `report_view_v1_available` app
  composition input and ANDs it with actual route installation; Plan 6 later
  supplies its validated native switch. It is an entry-point availability
  signal, not an entitlement or a promise that a historical session is
  structured. The grant TTL remains fixed by this route/OpenAPI contract rather
  than adding a competing activation field.
- Stable error mapping:
  - missing/cross-account: 404 `report_not_found`;
  - queued/processing: 409 `report_not_ready`, retryable;
  - failed analysis: 409 `analysis_failed`, not retryable, with only safe generic copy and an opaque `reference_id` when one already exists;
  - unsupported persisted version: 409 `unsupported_report_version`;
  - corrupt/missing structured bundle: 503 `report_unavailable`, retryable only when recovery can replace the exact bundle.

- [ ] **Step 1: Write failing route tests for every response mode.**

  ```python
  def test_capture_only_is_structured_and_not_legacy(client, token, capture_job):
      response = client.get(
          f"/api/v1/sessions/{capture_job.id}/report-view",
          headers=bearer(token),
      )
      assert response.status_code == 200
      assert response.headers["cache-control"] == "private, no-store"
      assert response.json()["mode"] == "structured"
      assert response.json()["report_view"]["outcome"] == "capture_only"

  def test_old_report_returns_explicit_legacy_mode(client, token, legacy_job):
      payload = client.get(
          f"/api/v1/sessions/{legacy_job.id}/report-view",
          headers=bearer(token),
      ).json()
      assert payload == {
          "resource_version": 1,
          "mode": "legacy",
          "legacy_report_url": f"/session/{legacy_job.id}/report",
      }
  ```

  Add improve, protect, limited-rendered, and visual-unavailable structured cases. Validate every success with `TypeAdapter(ReportViewResponse)`.
  Add capabilities rows for `report_view_v1=False` and `True` by toggling the
  explicit composition input, and prove that it changes only that capability:
  fetching an existing valid structured bundle still returns `mode="structured"`
  in both cases.

- [ ] **Step 2: Add failing authorization and failure-boundary tests.**

  Prove missing/cross-account responses are identical, malformed bearer plus valid cookie is 401, cookie-only access follows the shared mobile-auth policy, queued/processing are retryable 409, failed is non-retryable `analysis_failed` 409 without the internal job error, unknown view version is stable 409, and a damaged structured bundle is 503 rather than legacy.

- [ ] **Step 3: Run the route tests and confirm the red state.**

  Run: `python -m pytest tests/test_report_view_api.py tests/test_mobile_capabilities.py tests/test_mobile_auth_context.py tests/test_guided_report_web_composition.py -q`

  Expected: 404 for the new route or import failure for `create_report_router`.

- [ ] **Step 4: Implement the router with one private response helper.**

  Resolve auth before report lookup, build media access through the signer from Task 1, project with `project_report_view`, validate the final response model, and attach no-store headers to success and error JSON. Translate every resolver exception through one `APIError` helper and do not catch `MobileReportNotFound` and then inspect other paths.

  ```python
  @router.get(
      "/api/v1/sessions/{session_id}/report-view",
      response_model=ReportViewResponse,
  )
  def report_view(session_id: str, request: Request):
      auth = resolve_mobile_auth(request, users, require_account)
      owned = load_owned_report(jobs, user_id=auth.user.id, session_id=session_id)
      if isinstance(owned, LegacyOwnedReport):
          return private_json(LegacyReportViewResponse(
              legacy_report_url=owned.report_url
          ).model_dump(mode="json"))
      access = project_media_access(
          owned,
          user=auth.user,
          signer=media_signer,
      )
      response = StructuredReportViewResponse(
          report_view=project_report_view(owned.bundle.view, access)
      )
      return private_json(response.model_dump(mode="json"))
  ```

  Implement `project_media_access` beside the router projection. Build each URL from the route name, session ID, exact media key, `expires`, and `grant`; set `locked=False` for each durable returned entry; and do not accept or expose the media relative path. Locked/unrendered optional content remains absent from `media` and retains its server-supplied optional-section state. The function does not recalculate plan access or add phantom media entries.

- [ ] **Step 5: Compose the focused router once.**

  Construct `ReportMediaGrantSigner` from the already-resolved application session secret and include the report router once after auth stores exist. Keep `swinglab.api.create_app` and `swinglab.web.app.create_app` lifecycle behavior identical when `start_background_workers=False`.

  Extend the shared app/mobile-resource composition with keyword-only
  `report_view_v1_available: bool = False`. The route may be installed while
  this input is false. No config lookup lives in the route module, so this plan
  remains executable before the rollout policy exists.

- [ ] **Step 6: Extend capabilities through the existing serializer.**

  Add the `report_view_v1` boolean to the existing Pydantic model and serializer.
  Set it to `report_view_v1_available and report_route_installed`; do not inspect
  config, the current user, cohort bucket, job rows, or report artifacts. Plan 6
  owns passing validated `ReportPresentationSettings.native_enabled` into this
  boundary. Clients still branch on each fetched report response's `mode` and
  `outcome`.

- [ ] **Step 7: Run route, auth, and legacy regressions.**

  Run: `python -m pytest tests/test_report_view_api.py tests/test_mobile_capabilities.py tests/test_mobile_api_tokens.py tests/test_foundation_contracts.py tests/test_guided_report_web_composition.py tests/test_web.py -q`

  Expected: all pass and every existing `/api/v1` route/key remains present.

- [ ] **Step 8: Commit the owned route.**

  ```bash
  git add swinglab/api/report_routes.py swinglab/api/mobile_routes.py swinglab/api/contracts.py swinglab/web/app.py swinglab/web/mobile_resources.py tests/test_report_view_api.py tests/test_mobile_capabilities.py
  git commit -m "feat: serve owned guided reports"
  ```

## Task 4: Add bearer-plus-grant media delivery and close generic-file escapes

**Files:**

- Modify: `swinglab/api/report_media.py`
- Modify: `swinglab/api/report_routes.py`
- Modify: `swinglab/web/app.py:3928-4016`
- Create: `tests/test_report_media_api.py`
- Modify: `tests/test_web.py:450-600,732-750,939-997`
- Modify: `tests/test_retention_disk.py:1-90`

**Interfaces:**

- Consumes `ReportMediaGrantSigner.issue(...)` and `.verify(...)` from Task 1 without changing their format or TTL.
- The media route calls `require_mobile_bearer(request, users, require_account)`; cookie-only authentication is rejected even when the browser cookie is valid.
- Media route returns only a validated path from `resolve_media_path(bundle, media_key)` and supports HTTP byte ranges needed by `expo-video`.

- [ ] **Step 1: Write failing media-route security tests.**

  Obtain a real media URL from the report-view response and prove:

  - bearer plus current grant returns 200 and the declared MIME type;
  - signed URL without bearer returns 401;
  - other account bearer plus grant returns the same 404 as a missing session;
  - expired grant returns 403 `report_media_grant_expired` and is retryable by refetching the view;
  - invalid grant returns non-enumerating 404;
  - undeclared/locked media key returns 404;
  - `Range: bytes=0-3` returns 206, exactly four bytes, and a correct `Content-Range`;
  - every media response is private/no-store and `nosniff`.

- [ ] **Step 2: Run media tests and confirm the red state.**

  Run: `python -m pytest tests/test_report_media_api.py -q`

  Expected: 404 because the report-media route is not registered.

- [ ] **Step 3: Implement the media route.**

  Require bearer authentication first, resolve the owned structured report, locate the media entry by exact key, verify the grant against that entry's checksum, then call `resolve_media_path`. Never accept a relative path or MIME type from the request. Return `FileResponse` using the manifest-validated MIME type. Keep Starlette's single-range behavior and test `200`, `206`, `416`, `Accept-Ranges`, `Content-Length`, and `Content-Range`; do not add a second file reader or load video bytes into memory.

- [ ] **Step 4: Lock down the generic session-file route for structured jobs.**

  Before the current `FileResponse` at `swinglab/web/app.py:4012`, deny by declared identity and case-insensitive/trailing-dot filename:

  - `report-view.json`;
  - `report-bundle-manifest.json`;
  - `report-bundle-checksums.json`;
  - any report attempt/staging directory;
  - any structured media file not returned by `resolve_media_path` for a key in the validated view.

  Preserve historical report behavior. For structured jobs, allow the declared HTML, currently allowed `metrics.json`, and manifest-declared web media only. Revalidate containment, regular-file status, and symlink absence immediately before sending.

- [ ] **Step 5: Add alias, traversal, and replacement regressions.**

  Test uppercase aliases, Windows trailing dots/spaces, URL-encoded traversal, symlink files and parents, hard-link/same-file aliases where supported, a media file swapped after view load, an undeclared file placed under `media/`, and a stale manifest checksum. Every case fails closed.

- [ ] **Step 6: Run media, web, retention, and range tests.**

  Run: `python -m pytest tests/test_report_media_api.py tests/test_web.py tests/test_retention_disk.py tests/test_report_artifacts.py tests/test_report_bundle.py -q`

  Expected: all pass; historical HTML/media remain readable and no structured internal artifact is reachable.

- [ ] **Step 7: Commit the media boundary.**

  ```bash
  git add swinglab/api/report_media.py swinglab/api/report_routes.py swinglab/web/app.py tests/test_report_media_api.py tests/test_web.py tests/test_retention_disk.py
  git commit -m "security: protect guided report media"
  ```

## Task 5: Freeze OpenAPI, privacy, logging, and compatibility behavior

**Files:**

- Modify: `docs/api/openapi-v1.json`
- Modify: `tests/test_mobile_openapi_contract.py`
- Create: `tests/test_report_view_api_privacy.py`
- Modify: `tests/test_access_log_privacy.py:1-80`
- Modify: `tests/test_mobile_api_tokens.py:166-215`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/mobile-api-tokens.md`

**Interfaces:**

- OpenAPI contains typed 200 responses for both report modes, the exact `APIError` bodies for every declared 4xx/5xx status, and binary `200`/`206` media responses; it never exposes an internal artifact path field.
- Access logs retain method/path/status but remove `expires` and `grant` query values before formatting.
- Privacy scans reject email, source filename, raw pose keys, internal relative paths, checksums, bearer tokens, report text, and metric values outside the authorized response body.

- [ ] **Step 1: Add the failing OpenAPI assertions.**

  ```python
  def test_report_view_openapi_is_typed_and_has_no_internal_paths(app):
      schema = app.openapi()
      operation = schema["paths"][
          "/api/v1/sessions/{session_id}/report-view"
      ]["get"]
      encoded = json.dumps(operation, sort_keys=True)
      assert "StructuredReportViewResponse" in encoded
      assert "LegacyReportViewResponse" in encoded
      assert "relative_path" not in encoded
      assert "checksum_sha256" not in encoded
  ```

- [ ] **Step 2: Add the failing privacy and query-log tests.**

  Send a request containing `?expires=1300&grant=secret-grant-value`, run it through `RedactAccessLogQueryFilter`, and assert only `/api/v1/sessions/j/report-media/focus` remains. Capture application logs around a successful structured response and malformed bundle; assert fixture priority text, measurement values, filename, internal paths, grant, bearer, and checksum are absent.

- [ ] **Step 3: Extend the bearer-scope regression.**

  Update `test_bearer_is_limited_to_owned_mobile_session_report_and_upload_routes` to exercise report-view and report-media, prove cross-account isolation, and prove a valid grant never makes `/account`, raw JSON, manifest, checksum, or scratch routes bearer-accessible.

- [ ] **Step 4: Run the tests and confirm OpenAPI drift.**

  Run: `python -m pytest tests/test_mobile_openapi_contract.py tests/test_report_view_api_privacy.py tests/test_access_log_privacy.py tests/test_mobile_api_tokens.py -q`

  Expected: snapshot comparison fails until the deterministic OpenAPI artifact is regenerated.

- [ ] **Step 5: Regenerate and compare OpenAPI twice.**

  ```powershell
  python scripts/export_openapi.py --output docs/api/openapi-v1.json
  python scripts/export_openapi.py --output openapi.first.json
  python scripts/export_openapi.py --output openapi.second.json
  python -c "from pathlib import Path; a=Path('openapi.first.json').read_bytes(); b=Path('openapi.second.json').read_bytes(); c=Path('docs/api/openapi-v1.json').read_bytes(); assert a == b == c"
  Remove-Item -LiteralPath openapi.first.json,openapi.second.json
  ```

  Do not stage either comparison file. If byte comparison fails, preserve them only long enough to inspect the diff, then remove those exact files.

- [ ] **Step 6: Add CI drift coverage and document the credential boundary.**

  Keep the existing exporter command and make changes under `swinglab/report_view.py`, `swinglab/report_artifacts.py`, `swinglab/api/report_*.py`, `swinglab/api/contracts.py`, or `scripts/export_report_api_fixtures.py` trigger both the API-fixture regeneration check and OpenAPI byte comparison. Document that media requires both `Authorization: Bearer ...` and the server-issued short-lived grant; neither credential belongs in a URL copied by the user.

- [ ] **Step 7: Run the complete Plan 4 gate.**

  ```bash
  python -m pytest tests/test_report_view_api_contracts.py tests/test_report_media_grants.py tests/test_mobile_report_resolution.py tests/test_report_view_api.py tests/test_report_media_api.py tests/test_report_view_api_privacy.py tests/test_mobile_openapi_contract.py tests/test_mobile_capabilities.py tests/test_mobile_auth_context.py tests/test_mobile_api_tokens.py tests/test_access_log_privacy.py tests/test_foundation_contracts.py tests/test_guided_report_web_composition.py tests/test_report_artifacts.py tests/test_web.py tests/test_retention_disk.py -q
  python -m pytest -q
  ```

  Expected: all focused and full Python tests pass; no pre-existing route or response key disappears.

- [ ] **Step 8: Commit the frozen API/security gate.**

  ```bash
  git add docs/api/openapi-v1.json docs/mobile-api-tokens.md tests .github/workflows/ci.yml
  git commit -m "test: freeze guided report API security"
  ```

## Owned API plan completion gate

- [ ] Validate all five structured fixture variants and one historical fallback through the real route.
- [ ] Prove a structured capture-only result is never mistaken for legacy.
- [ ] Prove disabling native entry/navigation changes `report_view_v1` only and never relabels an existing structured report as legacy.
- [ ] Prove structured corruption, unknown version, checksum mismatch, and missing required media never fall back to HTML.
- [ ] Prove media needs both current bearer ownership and a current scoped grant, including byte-range video requests.
- [ ] Prove raw view JSON, manifest, checksums, staging data, and undeclared media are denied through API and generic-file routes.
- [ ] Prove report responses and media are private/no-store and access-log query values are redacted.
- [ ] Regenerate OpenAPI twice identically and validate every response body with its Pydantic model.
- [ ] Record the focused commit and test evidence. Do not claim Railway deployment, cohort activation, native availability, or public sample publication.
