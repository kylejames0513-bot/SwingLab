# Guided Report Native Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the approved action-first guided report in the Expo iOS/Android client from the owned `resource_version: 1` report response, including private media, capture-only recovery, explicit historical fallback, accessibility, and native end-to-end proof without reimplementing coaching.

**Architecture:** The generated OpenAPI types and one runtime discriminant guard feed a network-only TanStack Query. A feature-local `ReportScreen` branches only on server-supplied `mode`, `outcome`, and trust state, then composes focused components in the approved reading order. Images use `expo-image` with caching disabled and videos use `expo-video` with caching disabled; both attach the SecureStore bearer to the short-lived owner-scoped URL, refresh before/after grant expiry, and have no public or signed-only fallback.

**Tech Stack:** Expo SDK 57, React Native 0.86, React 19.2, Expo Router, strict TypeScript, generated `openapi-typescript` types, `openapi-fetch`, TanStack Query, Expo Image, Expo Video, Expo SecureStore, Expo WebBrowser, Jest Expo, React Native Testing Library, and Maestro development-build journeys.

## Global Constraints

- `mobile/` does not exist in the current checkout. Do not scaffold it in this plan. Expo Coaching Client Task 1 at `docs/superpowers/plans/2026-08-06-caddieinsight-expo-coaching-client.md:34-89` creates and pins it first.
- Execute after Guided Report Owned API Plan 4 is green and its deterministic `docs/api/openapi-v1.json` is committed.
- Execute after Expo Coaching Client Tasks 1-6. This plan supersedes only the Brief/report portion of Expo Task 7 at `docs/superpowers/plans/2026-08-06-caddieinsight-expo-coaching-client.md:375-425`; Task 7's Practice, matched re-film, and Progress scope remains required.
- Respect `/api/v1/capabilities.report_view_v1` as Plan 6's native entry-point switch. When false, Today and other new-build navigation omit the guided-report entry; they do not synthesize `mode="legacy"`. The owned API remains authoritative for existing valid structured bundles.
- Create `mobile/app/brief/[sessionId].tsx` and `mobile/src/features/analysis/BriefScreen.tsx` through this plan. Do not create the generic planned `mobile/src/features/analysis/EvidenceSection.tsx`; the focused report components below replace it.
- Exact response union:
  - `{resource_version: 1, mode: "structured", report_view: ReportViewV1}`;
  - `{resource_version: 1, mode: "legacy", legacy_report_url: string}`.
- Generate `mobile/src/api/schema.generated.ts` from `docs/api/openapi-v1.json`. Never hand-edit it and never create handwritten shadow copies of the response tree.
- Runtime code may validate version/discriminant/reason-code membership for fail-closed behavior. It may not derive phase status, priority, confidence, drill, pass mark, capability, or entitlement.
- Personal report JSON and media are network-only. Do not persist them in TanStack Query storage, AsyncStorage, SecureStore, Expo FileSystem, snapshots, logs, crash reports, telemetry, notifications, or device backups.
- The only persistent report-adjacent state is the existing non-secret owned session ID and navigation intent. A `history_epoch` change, 401, sign-out, history reset, or account deletion removes it before another account renders.
- Every image/video request requires both `Authorization: Bearer ...` and the server-issued `expires`/`grant` URL. A grant without bearer is never retried as public access, and a bearer is never placed in a URL.
- Use Expo SDK 57's documented request-header support: [`expo-image` `ImageSource.headers` and `cachePolicy="none"`](https://docs.expo.dev/versions/v57.0.0/sdk/image/), and [`expo-video` `VideoSource.headers` with `useCaching: false`](https://docs.expo.dev/versions/v57.0.0/sdk/video/). The feature cannot pass integration review, and Tasks 4-5 cannot begin, until the Task 3 real-device feasibility gate passes.
- If either iOS or Android fails to attach the bearer to image or byte-range video requests, stop the plan and return to security design. Do not download media into a persistent local file, weaken the server to signed-only URLs, add a bearer query parameter, or substitute a public URL.
- Focused evidence is one large image/timeline, not a shrunk multi-panel strip. `alt_text`, phase method, tracking state, observed/reference/boundary labels, and reason strings come from the server.
- The main screen order is priority or protected strength, observation, cue, focused evidence, phase summary, one complete practice card, then one complete re-film card. Full drill prescription and pass mark each appear once.
- Capture-only renders no diagnosis, phases, drill, corrective annotation, proof target, coach replay, or commerce. It renders only the server's recovery copy, checklist, safe media, and actions.
- Legacy mode never scrapes HTML or metrics into native coaching fields. It presents an explicit historical-report card and opens only the server-owned same-origin HTML URL in the system browser without appending bearer, grant, or customer data.
- Meet Dynamic Type at every iOS accessibility size, Android font scale 2.0, VoiceOver/TalkBack order, reduced motion, non-color status, 44-by-44-point iOS targets, and 48-by-48-dp Android targets.
- Run focused tests after every task, then the full mobile gate, and end each task with a focused commit.

---

## Task 1: Generate the exact contract and add a network-only report query

**Files:**

- Regenerate, never hand-edit: `mobile/src/api/schema.generated.ts`
- Create: `mobile/src/features/report/reportTypes.ts`
- Create: `mobile/src/features/report/reportApi.ts`
- Create: `mobile/src/features/report/useReportView.ts`
- Modify: `mobile/src/api/queryKeys.ts`
- Modify: `mobile/src/api/client.ts`
- Create: `mobile/tests/report/reportContract.test.ts`
- Create: `mobile/tests/report/reportApi.test.ts`
- Create: `mobile/tests/report/reportFixtures.ts`
- Consume repository fixtures: `tests/fixtures/report_api/*.json`

**Interfaces:**

- `ReportViewAPIResponse` is an alias extracted from the generated 200 response for `/api/v1/sessions/{session_id}/report-view`.
- `StructuredReportResponse` and `LegacyReportResponse` are `Extract<ReportViewAPIResponse, {mode: ...}>`, not copied object types.
- `ReportMediaResponse = StructuredReportResponse['report_view']['media'][number]`; native media props derive from this alias rather than restating the server object.
- `ReportFixtureName = 'coaching-improve-clear' | 'coaching-protect-clear' | 'coaching-limited-rendered' | 'coaching-limited-visual-unavailable' | 'capture-only'` and `loadReportFixture(name: ReportFixtureName) -> StructuredReportResponse` read the checked-in backend projection fixtures in Jest only. `renderReportResponse(response)` wraps `ReportScreen` with the standard query/auth/navigation test providers.
- `parseReportViewResponse(value: unknown) -> ReportViewAPIResponse` rejects unknown resource/view versions, modes, outcomes, trust states, reason codes, phase IDs/statuses, evidence states, and media roles before rendering.
- `requireRecord(value: unknown, label: string) -> Record<string, unknown>`, `requireRelativeOwnedPath(value: unknown, label: string) -> asserts value is string`, and `validateReportDiscriminants(report: Record<string, unknown>) -> void` are private fail-closed guard helpers. The path guard accepts only `/session/<opaque-id>/report` with no scheme, authority, query, fragment, backslash, dot segment, or encoded separator.
- `getReportView(sessionId: string, signal?: AbortSignal) -> Promise<ReportViewAPIResponse>` uses the existing `apiRequest` bearer/error policy.
- `useReportView(sessionId: string, historyEpoch: number)` uses query key `['report-view', historyEpoch, sessionId]`, `networkMode: 'online'`, `staleTime: 0`, `gcTime: 0`, and no persister.
- `UnsupportedReportResultError(version: string)` maps to an update-required screen, not partial rendering.

- [ ] **Step 1: Regenerate the client type and assert no shadow contract exists.**

  From `mobile/`, run:

  ```bash
  npm run api:generate
  npm run api:check
  ```

  Expected: `schema.generated.ts` changes for the new response/media routes and the byte-comparison passes after generation.

- [ ] **Step 2: Write the failing generated-type and runtime-guard tests.**

  ```ts
  type ReportOperation = paths['/api/v1/sessions/{session_id}/report-view']['get'];
  export type ReportViewAPIResponse =
    ReportOperation['responses'][200]['content']['application/json'];

  test.each([
    [{ resource_version: 2, mode: 'legacy', legacy_report_url: '/x' }, 'resource'],
    [{ resource_version: 1, mode: 'structured', report_view: { version: 'report-view-v2' } }, 'version'],
  ])('rejects unsupported report responses', (payload, expected) => {
    expect(() => parseReportViewResponse(payload)).toThrow(expected);
  });
  ```

  Load all five API fixtures and assert the parser accepts them without changing array order or server copy. Clone each fixture once with an unknown reason code, phase status, evidence state, and media role; assert each clone is rejected.

- [ ] **Step 3: Run the tests and verify the red state.**

  Run: `npm test -- reportContract --runInBand`

  Expected: module resolution fails because `reportTypes.ts` does not exist.

- [ ] **Step 4: Implement a discriminant-only runtime guard.**

  Use frozen `Set` values that exactly mirror the generated closed enums. Validate object/array shape only far enough to prove the server version and all branch discriminants are known; do not calculate any product state.

  ```ts
  export function parseReportViewResponse(value: unknown): ReportViewAPIResponse {
    const root = requireRecord(value, 'report response');
    if (root.resource_version !== 1) throw new UnsupportedReportResultError(String(root.resource_version));
    if (root.mode === 'legacy') {
      requireRelativeOwnedPath(root.legacy_report_url, 'legacy_report_url');
      return root as ReportViewAPIResponse;
    }
    if (root.mode !== 'structured') throw new UnsupportedReportResultError(String(root.mode));
    const report = requireRecord(root.report_view, 'report_view');
    if (report.version !== 'report-view-v1') {
      throw new UnsupportedReportResultError(String(report.version));
    }
    validateReportDiscriminants(report);
    return root as ReportViewAPIResponse;
  }
  ```

- [ ] **Step 5: Write the failing transport/query tests.**

  Assert exact URL encoding, bearer injection through the shared transport, abort propagation, no automatic report persistence, one bounded GET retry, 401 credential/cache clearing, 404 safe not-found, 409 not-ready, non-retryable 409 `analysis_failed`, 409 unsupported, 503 retryable unavailable, and query-key separation by both session ID and `history_epoch`.

- [ ] **Step 6: Implement the query.**

  Call `parseReportViewResponse` on the raw JSON before returning it to TanStack Query. Set `meta: { private: true, persist: false }`. Do not use `placeholderData`, previous-account data, offline-first mode, or local fallback JSON.

- [ ] **Step 7: Run contract and transport checks.**

  Run: `npm run api:check && npm run typecheck && npm test -- reportContract reportApi --runInBand`

  Expected: all pass; changing the backend OpenAPI or any fixture discriminant breaks the gate.

- [ ] **Step 8: Commit the report transport.**

  ```bash
  git add mobile/src/api mobile/src/features/report mobile/tests/report
  git commit -m "feat: add typed native report transport"
  ```

## Task 2: Render the coaching-ready action-first hierarchy

**Files:**

- Create: `mobile/app/brief/[sessionId].tsx`
- Create: `mobile/src/features/analysis/BriefScreen.tsx`
- Create: `mobile/src/features/report/ReportScreen.tsx`
- Create: `mobile/src/features/report/StructuredReportScreen.tsx`
- Create: `mobile/src/features/report/NextMoveCard.tsx`
- Create: `mobile/src/features/report/JourneyPreview.tsx`
- Create: `mobile/src/features/report/FocusedEvidenceCard.tsx`
- Create: `mobile/src/features/report/PhaseBreakdown.tsx`
- Create: `mobile/src/features/report/PracticeCard.tsx`
- Create: `mobile/src/features/report/RefilmCard.tsx`
- Create: `mobile/src/features/report/OptionalReportSections.tsx`
- Create: `mobile/src/features/report/reportAnchors.ts`
- Create: `mobile/src/features/report/reportActions.ts`
- Modify: `mobile/src/features/today/NextActionCard.tsx`
- Create: `mobile/tests/brief/brief.test.tsx`
- Create: `mobile/tests/report/structuredReport.test.tsx`
- Create: `mobile/tests/report/reportHierarchy.test.tsx`

**Interfaces:**

- `BriefScreen` is a compatibility export of `ReportScreen`; it does not fetch `/brief`, report HTML, or raw metrics.
- `ReportScreen({sessionId})` owns loading/error/response-mode branching and passes a structured view unchanged to `StructuredReportScreen`.
- `Today/NextActionCard` exposes the guided-report entry only when the typed capabilities response has `report_view_v1 === true`; a false value never rewrites a report response or selects the legacy component.
- `reportAnchors = { practice: 'practice', refilm: 'refilm' }` is used only for in-screen focus/scroll; it does not become navigation state.
- `PhaseBreakdown` renders server array order and supplied status/expanded state. Face-on has five rows; DTL has one `timing_rhythm` row because the response says so.
- `PracticeCard` renders exactly the three authored `summary_steps`, then setup, feel cue, dosage, equipment, and optional full steps/alternatives.
- `RefilmCard` is the only component that renders `refilm.target.text`.
- `openReportPractice(sessionId: string)`, `openMatchedRefilm(sessionId: string)`, and `openReplacementVideo(sessionId: string)` navigate with only the owned session ID and a fixed intent. The destination retrieves the generated report type from the in-memory query or refetches it, then hands the exact server drill/context/checklist/target into Expo Task 7's Practice or capture flow; report copy and thresholds never enter route parameters or persisted navigation state.
- `openReportSupport() -> void` uses the existing non-sensitive support destination. `CaptureOnlyReportScreen` dispatches only the server action enum to these fixed adapters; a server-supplied label never becomes a route or function name.

- [ ] **Step 1: Write the failing hierarchy tests from the server fixtures.**

  ```tsx
  test('renders one canonical drill and pass mark in server order', async () => {
    const fixture = loadReportFixture('coaching-improve-clear');
    renderReportResponse(fixture);

    expect(await screen.findByRole('header', { name: fixture.report_view.next_move.title })).toBeTruthy();
    expect(screen.getAllByText(fixture.report_view.practice.name)).toHaveLength(1);
    expect(screen.getAllByText(fixture.report_view.refilm.target.text)).toHaveLength(1);
    expect(screen.getAllByTestId('phase-row').map(row => row.props.accessibilityLabel)).toEqual(
      fixture.report_view.phases.map(phase => `${phase.label}, ${phase.status_label}`),
    );
  });
  ```

  Assert opening order is title, observation, cue, journey preview; the journey preview links to Practice/Re-film but does not repeat drill instructions or the target text.

- [ ] **Step 2: Add failing protect, limited, DTL, free, and Pro cases.**

  Assert protect mode says the supplied `eyebrow`/strength copy rather than hardcoded `Priority`; limited rows show supplied `Not measured` reasons; DTL renders no face-on phase IDs/body-reference copy; locked optional sections do not attempt media; and no component invents a score or target. Assert `report_view_v1=false` suppresses the Today entry without rendering `LegacyReportCard`, while `true` exposes it for an owned session.

- [ ] **Step 3: Run component tests and verify the red state.**

  Run: `npm test -- brief structuredReport reportHierarchy --runInBand`

  Expected: route/component imports fail.

- [ ] **Step 4: Implement the route and thin compatibility export.**

  `mobile/app/brief/[sessionId].tsx` validates one string route parameter and renders `<BriefScreen sessionId={sessionId} />`. `BriefScreen.tsx` contains only:

  ```ts
  export { ReportScreen as BriefScreen } from '../report/ReportScreen';
  ```

  The Today action navigates to this route only with an owned session ID returned by the server and only while the current typed capability has `report_view_v1 === true`.

- [ ] **Step 5: Implement the structured reading order.**

  Use one vertical `ScrollView`/`SectionList`, semantic headings, server copy, and ordinary disclosure buttons. Expand only the supplied `expanded_by_default` phase. Do not use a horizontal pager or require swipes.

- [ ] **Step 6: Implement canonical Practice and Re-film cards.**

  Verify `summary_steps.length === 3` defensively; a violated server contract routes to the safe unsupported-result state rather than truncating or merging. Call the fixed `reportActions` adapters with only `sessionId`. At the destination, require the same owned report query and pass its server `drill_id`, checklist/target, club, hand, and angle into Expo Task 7's Practice/matched-capture interfaces. Do not serialize those fields into navigation.

- [ ] **Step 7: Run structured UI tests and static copy guards.**

  Run: `npm test -- brief structuredReport reportHierarchy --runInBand && npm run typecheck && npm run lint`

  Expected: all pass; a test scan finds no hardcoded priority title, phase status, drill, threshold, or pass-mark sentence in report components.

- [ ] **Step 8: Commit the structured report UI.**

  ```bash
  git add mobile/app/brief mobile/src/features/analysis/BriefScreen.tsx mobile/src/features/report mobile/src/features/today/NextActionCard.tsx mobile/tests/brief mobile/tests/report
  git commit -m "feat: render action-first native reports"
  ```

## Task 3: Prove and implement bearer-authenticated private image/video transport

**Files:**

- Modify: `mobile/package.json`
- Modify: `mobile/package-lock.json`
- Create: `mobile/src/features/report/privateMediaSource.ts`
- Create: `mobile/src/features/report/PrivateReportImage.tsx`
- Create: `mobile/src/features/report/PrivateReportVideo.tsx`
- Modify: `mobile/src/features/report/FocusedEvidenceCard.tsx`
- Modify: `mobile/src/features/report/OptionalReportSections.tsx`
- Create: `mobile/tests/report/privateMediaSource.test.ts`
- Create: `mobile/tests/report/privateReportImage.test.tsx`
- Create: `mobile/tests/report/privateReportVideo.test.tsx`
- Create: `mobile/e2e/private-report-media.yaml`
- Create: `mobile/e2e/fixtures/private-report-media.json`
- Create: `mobile/e2e/fixtures/private-report-image.png`
- Create: `mobile/e2e/fixtures/private-report-video.mp4`
- Create: `mobile/e2e/support/privateReportMediaServer.mjs`

**Interfaces:**

- `PrivateReportMedia = Pick<ReportMediaResponse, 'key'|'role'|'mime_type'|'url'|'expires_at'|'locked'>`.
- `buildPrivateMediaSource(media, credentialStore, apiBaseUrl) -> Promise<{uri: string; headers: {Authorization: string}}>` rejects locked/null/expired/cross-origin/non-HTTPS production URLs and never returns a source without a bearer.
- `PrivateReportImage({media, altText, onGrantExpired})` uses `expo-image` with `cachePolicy="none"`, `transition={0}` under reduced motion, and the authenticated source.
- `PrivateReportVideo({media, label, onGrantExpired})` uses `VideoSource {uri, headers, useCaching: false}`, does not autoplay, and releases the player on unmount.
- `refreshExpiredReportMedia(sessionId) -> Promise<void>` invalidates/refetches only the current report query; it never retries the media URL without auth.
- `privateReportMediaServer.mjs` uses only `node:http`, synthetic checked-in image/video bytes, an ephemeral random bearer/grant, and an in-memory request ledger. It serves report JSON and media only when both values match, implements one bounded byte range for the MP4, exposes a test-only redacted ledger, and never writes credentials or query strings to disk/stdout.

- [ ] **Step 1: Install the SDK-compatible image package.**

  From `mobile/`, run `npx expo install expo-image`. Keep the SDK-resolved version and lockfile; do not use React Native's default disk-caching image component for private report evidence.

- [ ] **Step 2: Write failing source-security tests.**

  ```ts
  test('never builds a signed-only media source', async () => {
    credentials.get.mockResolvedValue(null);
    await expect(buildPrivateMediaSource(media, credentials, apiBaseUrl)).rejects.toMatchObject({
      code: 'unauthorized',
    });
  });

  test('attaches bearer and preserves the scoped grant', async () => {
    credentials.get.mockResolvedValue('ciat_selector.secret');
    await expect(buildPrivateMediaSource(media, credentials, apiBaseUrl)).resolves.toEqual({
      uri: 'https://api.example/api/v1/sessions/s1/report-media/focus?expires=1300&grant=g',
      headers: { Authorization: 'Bearer ciat_selector.secret' },
    });
  });
  ```

  Add wrong host, HTTP production, `locked`, null URL, expiry within the 30-second refresh window, and bearer-in-URL rejection.

- [ ] **Step 3: Write failing component contract tests.**

  Mock `expo-image` and `expo-video` and assert the exact `headers` reach each native source, image `cachePolicy` is `none`, video `useCaching` is `false`, no autoplay occurs, and alt/labels are supplied. The bearer must never appear in rendered text, Jest snapshots, console calls, or TanStack Query data. The short-lived media URL may exist only in the in-memory report query/native source; it must not enter snapshots, logs, or persisted query/cache state.

- [ ] **Step 4: Run private media tests and verify the red state.**

  Run: `npm test -- privateMediaSource privateReportImage privateReportVideo --runInBand`

  Expected: module resolution fails for the three private-media modules.

- [ ] **Step 5: Implement the authenticated sources and proactive refresh.**

  Read the bearer directly through the existing `CredentialStore`, create the source in component memory, and discard it on unmount. If `expires_at <= now + 30`, refetch report-view before mounting native media. On the first native load error, refetch report-view once and rebuild from its new URL; a second failure shows a retry control and never falls back.

  ```tsx
  <Image
    source={{ uri: source.uri, headers: source.headers }}
    cachePolicy="none"
    accessibilityLabel={altText}
    accessible
    contentFit="contain"
    onError={handleAuthenticatedLoadError}
  />
  ```

  ```ts
  const videoSource: VideoSource = {
    uri: source.uri,
    headers: source.headers,
    useCaching: false,
  };
  ```

- [ ] **Step 6: Add the real development-build feasibility gate.**

  Start `privateReportMediaServer.mjs` with a LAN-reachable development origin and fresh process-local credentials. It returns the synthetic image and short MP4 only when it receives both the bearer and current grant and requires a byte-range request for video. Run `mobile/e2e/private-report-media.yaml` on one physical iOS device and one physical Android device and assert visible states `Private image loaded` and `Private video ready`. Inspect its redacted in-memory ledger and require:

  - image request has the expected `Authorization` header;
  - video metadata and range requests have the expected header;
  - no request reaches a public/signed-only endpoint;
  - the media grant query is present but redacted from access-log output;
  - neither client uses disk caching.

  If either platform fails, stop here and record the exact SDK/platform evidence. Do not proceed to Tasks 4-5.

- [ ] **Step 7: Test expiry refresh end to end.**

  Serve a report whose first media grant expires, advance the fixture clock, and assert the client refetches report-view, receives a different grant, sends the bearer again, and loads once. Assert it does not repeat the expired URL more than once.

- [ ] **Step 8: Run the private-media gate.**

  Run: `npm test -- privateMediaSource privateReportImage privateReportVideo --runInBand && npm run typecheck && npm run lint`

  Expected: all local tests pass and real-device feasibility evidence is green on both platforms.

- [ ] **Step 9: Commit private media handling.**

  ```bash
  git add mobile/package.json mobile/package-lock.json mobile/src/features/report mobile/tests/report mobile/e2e/private-report-media.yaml mobile/e2e/fixtures/private-report-media.json mobile/e2e/fixtures/private-report-image.png mobile/e2e/fixtures/private-report-video.mp4 mobile/e2e/support/privateReportMediaServer.mjs
  git commit -m "security: load native report media privately"
  ```

## Task 4: Add limited, capture-only, legacy, and failure recovery paths

**Files:**

- Create: `mobile/src/features/report/VisualUnavailableEvidence.tsx`
- Create: `mobile/src/features/report/CaptureOnlyReportScreen.tsx`
- Create: `mobile/src/features/report/LegacyReportCard.tsx`
- Create: `mobile/src/features/report/UnsupportedReportScreen.tsx`
- Create: `mobile/src/features/report/ReportErrorState.tsx`
- Create: `mobile/src/features/report/legacyReport.ts`
- Modify: `mobile/src/features/report/ReportScreen.tsx`
- Modify: `mobile/src/features/report/FocusedEvidenceCard.tsx`
- Create: `mobile/tests/report/limitedReport.test.tsx`
- Create: `mobile/tests/report/captureOnlyReport.test.tsx`
- Create: `mobile/tests/report/legacyReport.test.tsx`
- Create: `mobile/tests/report/reportRecovery.test.tsx`

**Interfaces:**

- `VisualUnavailableEvidence` renders the server observation, phase method, supporting measurement link, and `focused_media_render_failed` explanation; it never substitutes another image.
- `CaptureOnlyReportScreen({sessionId, report})` renders `CaptureGuidance` in server order and maps only `refilm`, `choose_video`, or `support` to the fixed `reportActions` adapters.
- `openLegacyReport(legacyReportUrl, apiBaseUrl) -> Promise<void>` accepts only a relative path resolved to the exact configured HTTPS API origin and calls `WebBrowser.openBrowserAsync`; it appends no query, bearer, grant, email, or session cookie.
- `ReportErrorState` maps 401 to shared sign-out, 404 to unavailable ownership-safe copy, 409 not-ready to job polling, 409 `analysis_failed` to a safe final failure with re-film/choose-video recovery, 409 unsupported to update-required, 503 to retry, and offline to a network-required explanation.

- [ ] **Step 1: Write failing limited/capture-only suppression tests.**

  ```tsx
  test('capture-only exposes recovery and no coaching surface', async () => {
    renderReportFixture('capture-only');
    expect(await screen.findByText(fixture.report_view.capture_guidance.reason_label)).toBeTruthy();
    expect(screen.queryByTestId('next-move')).toBeNull();
    expect(screen.queryByTestId('phase-breakdown')).toBeNull();
    expect(screen.queryByTestId('practice-card')).toBeNull();
    expect(screen.queryByTestId('refilm-target')).toBeNull();
    expect(screen.queryByText(/gear|shop/i)).toBeNull();
  });
  ```

  Add each capture reason code, safe-media allowlist, retry failure, choose-video, support, and no-reliable-strike cases. Assert limited visual-unavailable still shows trusted coaching text but mounts no unrelated media.

- [ ] **Step 2: Write failing legacy and error tests.**

  Assert legacy mode displays `Previous report`, explains that it opens the owned web report and may require web sign-in, resolves only the configured HTTPS origin, and passes the exact URL to WebBrowser without credentials. Reject absolute other-origin URLs, `http` in staging/production, traversal, bearer/grant query values, and malformed paths.

  Test unknown view version, unknown reason code, 401, 404, not-ready 409, `analysis_failed` 409, unavailable 503, offline, refresh success, and refresh failure. No error renders stale coaching from a prior query or the backend's internal job error.

- [ ] **Step 3: Run recovery tests and verify the red state.**

  Run: `npm test -- limitedReport captureOnlyReport legacyReport reportRecovery --runInBand`

  Expected: missing component/module failures.

- [ ] **Step 4: Implement the three report branches.**

  `ReportScreen` branches once:

  ```tsx
  if (response.mode === 'legacy') {
    return <LegacyReportCard legacyReportUrl={response.legacy_report_url} />;
  }
  if (response.report_view.outcome === 'capture_only') {
    return <CaptureOnlyReportScreen report={response.report_view} />;
  }
  return <StructuredReportScreen report={response.report_view} />;
  ```

  Do not treat capture-only as legacy and do not inspect raw job metrics to choose a branch.

- [ ] **Step 5: Implement recovery actions.**

  Re-film and choose-video call existing capture entry points with the server's action label and context. Legacy uses system browser only. Unsupported contract state shows minimum required app version/update guidance and support; it does not cast the response to a known type.

- [ ] **Step 6: Run all report-mode tests.**

  Run: `npm test -- report --runInBand && npm run typecheck && npm run lint`

  Expected: all structured, limited, capture-only, legacy, and failure cases pass.

- [ ] **Step 7: Commit recovery paths.**

  ```bash
  git add mobile/src/features/report mobile/tests/report
  git commit -m "feat: add native report recovery paths"
  ```

## Task 5: Prove accessibility, privacy clearing, and the complete native report journey

**Files:**

- Create: `mobile/tests/report/reportAccessibility.test.tsx`
- Create: `mobile/tests/report/accessibilityHarness.tsx`
- Create: `mobile/tests/report/reportPrivacy.test.ts`
- Modify: `mobile/tests/privacy/noSensitivePersistence.test.ts`
- Modify: `mobile/tests/reliability/recoveryMatrix.test.ts`
- Create: `mobile/e2e/guided-report-structured.yaml`
- Create: `mobile/e2e/guided-report-capture-only.yaml`
- Create: `mobile/e2e/guided-report-legacy.yaml`
- Create: `mobile/e2e/guided-report-expired-media.yaml`
- Create: `mobile/e2e/guided-report-accessibility.yaml`
- Modify: `mobile/e2e/coaching-loop.yaml`
- Modify: `mobile/README.md`

**Interfaces:**

- Screen-reader order is next-move title, observation, cue, focused evidence, phases, Practice, Re-film, then optional depth.
- Every status exposes text plus icon/shape; color is supplementary.
- `accessibilityOrder(tree, testIds) -> string[]` proves relative React Native tree order, and `measurePrimaryActionTargets(driver) -> Promise<MeasuredTarget[]>` records physical-device target rectangles. Unit tests never claim to measure native clipping.
- `purgeForHistoryEpoch`, `AuthStore.signOut`, history reset, and account deletion remove report queries and in-memory media sources before navigation to another account.
- Maestro fixture journeys cover structured improve, protect, limited visual-unavailable, capture-only, DTL, legacy, expired grant refresh, free, and Pro.

- [ ] **Step 1: Write the failing accessibility matrix.**

  Use RNTL accessibility queries to prove semantic headings, labels/state, source order, no `numberOfLines` truncation on report copy, no fixed-height content cards, reduced-motion props, and no horizontal phase dependency. Use the development-build harness—not Jest layout—to measure reflow, reachability, and platform target sizes at every iOS accessibility category and Android font scale 2.0.

  ```tsx
  expect(accessibilityOrder(screen.toJSON(), [
    'next-move-heading',
    'focused-evidence-heading',
    'phase-breakdown-heading',
    'practice-heading',
    'refilm-heading',
  ])).toEqual([
    'next-move-heading',
    'focused-evidence-heading',
    'phase-breakdown-heading',
    'practice-heading',
    'refilm-heading',
  ]);
  ```

- [ ] **Step 2: Write the failing privacy-clearing tests.**

  Populate a report query, mount image/video sources, then trigger 401, `history_epoch` change, sign-out, history reset, and account deletion. Assert the query key is absent, the player/source is released, no report JSON/media URL/bearer exists in cache files or SecureStore beyond the sole auth key, and a second account never sees prior text/media.

- [ ] **Step 3: Extend static sensitive-persistence scanning.**

  Scan report components, query fixtures, snapshots, console mocks, telemetry fixtures, error boundary output, crash fixtures, and generated bundles for `ciat_`, `grant=`, fixture coaching sentences, source filenames, internal report paths, checksums, and metric values. Allow fixture content only in test input files, never output snapshots/logs.

- [ ] **Step 4: Run accessibility/privacy tests and verify the red state.**

  Run: `npm test -- reportAccessibility reportPrivacy noSensitivePersistence recoveryMatrix --runInBand`

  Expected: failures identify missing semantics/clearing hooks until the components and shared cache lifecycle are connected.

- [ ] **Step 5: Fix only defects exposed by the matrix.**

  Add accessible labels/state, focus transfer on load/error, platform target padding, text reflow, reduced-motion guards, query removal, and media-player release where the failing tests point. Do not change server copy or report ordering to satisfy layout tests.

- [ ] **Step 6: Run the native report Maestro matrix.**

  Against the development backend and generated API fixtures, run:

  ```bash
  npx maestro test e2e/guided-report-structured.yaml
  npx maestro test e2e/guided-report-capture-only.yaml
  npx maestro test e2e/guided-report-legacy.yaml
  npx maestro test e2e/guided-report-expired-media.yaml
  npx maestro test e2e/guided-report-accessibility.yaml
  npx maestro test e2e/coaching-loop.yaml
  ```

  Verify one physical iPhone and one physical Android device. For the report matrix, include default text, every iOS accessibility size, Android font scale 2.0, reduced motion, DTL, capture-only, and expired-grant refetch. Run a manual VoiceOver/TalkBack traversal on the same synthetic structured and capture-only fixtures because Maestro does not prove screen-reader order; record sanitized pass/fail notes. Keep screenshots synthetic and outside Git unless Plan 6 explicitly records sanitized release evidence.

- [ ] **Step 7: Run the complete mobile gate.**

  ```bash
  npm ci
  npm run api:check
  npm run expo:doctor
  npm run lint
  npm run typecheck
  npm test -- --runInBand
  npx expo export --platform all --output-dir dist-check
  ```

  Inspect `dist-check` for credentials, media URLs, private fixture content, and source media, then remove `dist-check`; do not commit it.

- [ ] **Step 8: Commit the native verification gate.**

  ```bash
  git add mobile/tests mobile/e2e mobile/src mobile/README.md
  git commit -m "test: verify native guided reports"
  ```

## Native report plan completion gate

- [ ] Confirm `mobile/` came from the pinned Expo SDK 57 scaffold and no second app workspace was created.
- [ ] Confirm generated types and the five shared API fixtures match the committed backend OpenAPI/projection.
- [ ] Confirm the client renders server priority, statuses, drill, target, and array order without calculation or hardcoded copy.
- [ ] Confirm one canonical full practice card and one canonical full re-film target in every coaching-ready report.
- [ ] Confirm image and byte-range video requests attach bearer plus current grant on physical iOS and Android, with caching disabled and no public/signed-only fallback.
- [ ] Confirm capture-only suppresses all coaching/commerce and legacy mode neither parses HTML nor injects credentials into the browser URL.
- [ ] Confirm `report_view_v1=false` suppresses new-build entry/navigation without downgrading a structured response to legacy.
- [ ] Confirm unknown versions/reason codes fail closed with update guidance.
- [ ] Confirm Dynamic Type, Android 2.0 font scale, VoiceOver/TalkBack, reduced motion, non-color status, and touch targets pass.
- [ ] Confirm 401, sign-out, `history_epoch`, history reset, and account deletion clear report/media state before another account can render.
- [ ] Record the focused commits, local gate, and real-device private-media evidence. Do not claim TestFlight, Play testing, App Store/Google Play review, Railway activation, web cohort activation, or public release.
