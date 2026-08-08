import { schemaContractSmoke } from '../../src/api/schema.fixture';

describe('generated OpenAPI schema fixture', () => {
  it('keeps closed Identity/upload/profile contract smoke markers', () => {
    expect(schemaContractSmoke.resourceVersion).toBe(true);
    expect(schemaContractSmoke.historyEpoch).toBe(true);
    expect(schemaContractSmoke.profileEpoch).toBe(true);
    expect(schemaContractSmoke.profileClosed).toBe(true);
    expect(schemaContractSmoke.matchedMode).toBe(true);
    expect(schemaContractSmoke.matchedFingerprint).toBe(true);
  });
});
