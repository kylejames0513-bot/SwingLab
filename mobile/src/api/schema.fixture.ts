/**
 * Compile-time fixture: wrong me/upload fields or missing resource_version must not typecheck.
 * Runtime smoke ensures the generated contract still exports the expected shapes.
 */
import type { components, operations } from './schema.generated';

type IdentityResponse = components['schemas']['IdentityResponse'];
type UploadComparisonMatched = components['schemas']['UploadComparisonMatched'];
type ProfileWriteBody =
  operations['mobile_resources_profile_write_api_v1_mobile_profile_put']['requestBody']['content']['application/json'];

type AssertEqual<A, B> = (<T>() => T extends A ? 1 : 2) extends <
  T,
>() => T extends B ? 1 : 2
  ? true
  : false;

type _IdentityHasResourceVersion = AssertEqual<
  IdentityResponse['resource_version'],
  1
>;
type _IdentityHasHistoryEpoch = AssertEqual<
  IdentityResponse['identity']['history_epoch'],
  number
>;
type _ProfileRequiresEpoch = AssertEqual<
  ProfileWriteBody['expected_history_epoch'],
  number
>;
type _ProfileRejectsInvented = ProfileWriteBody extends {
  invented_field: string;
}
  ? never
  : true;
type _MatchedMode = AssertEqual<UploadComparisonMatched['mode'], 'matched'>;
type _MatchedNeedsFingerprint = AssertEqual<
  UploadComparisonMatched['target_fingerprint'],
  string
>;

export const schemaContractSmoke: {
  resourceVersion: _IdentityHasResourceVersion;
  historyEpoch: _IdentityHasHistoryEpoch;
  profileEpoch: _ProfileRequiresEpoch;
  profileClosed: _ProfileRejectsInvented;
  matchedMode: _MatchedMode;
  matchedFingerprint: _MatchedNeedsFingerprint;
} = {
  resourceVersion: true,
  historyEpoch: true,
  profileEpoch: true,
  profileClosed: true,
  matchedMode: true,
  matchedFingerprint: true,
};
