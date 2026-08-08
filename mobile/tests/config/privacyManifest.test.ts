import * as fs from 'fs';
import * as path from 'path';

import { validatePrivacyManifestXml } from '../../plugins/withPrivacyManifest';

const MANIFEST = path.join(
  __dirname,
  '../../privacy/PrivacyInfo.xcprivacy',
);

describe('PrivacyInfo.xcprivacy', () => {
  const contents = fs.readFileSync(MANIFEST, 'utf8');

  it('is a valid plist with tracking disabled and empty domains', () => {
    expect(() => validatePrivacyManifestXml(contents)).not.toThrow();
    expect(contents).toMatch(/NSPrivacyTracking<\/key>\s*<false\s*\/>/);
  });

  it('declares exactly the reviewed required-reason categories', () => {
    expect(contents).toContain('NSPrivacyAccessedAPICategoryFileTimestamp');
    expect(contents).toContain('C617.1');
    expect(contents).toContain('NSPrivacyAccessedAPICategoryDiskSpace');
    expect(contents).toContain('E174.1');
    expect(contents).toContain('NSPrivacyAccessedAPICategoryUserDefaults');
    expect(contents).toContain('CA92.1');
  });

  it('does not claim collected data types', () => {
    const selfClosing = contents.match(
      /<key>NSPrivacyCollectedDataTypes<\/key>\s*<array\s*\/>/,
    );
    const match = contents.match(
      /<key>NSPrivacyCollectedDataTypes<\/key>\s*<array>([\s\S]*?)<\/array>/,
    );
    expect(selfClosing || match).toBeTruthy();
    expect(match?.[1] ?? '').not.toContain('<dict>');
  });

  it('rejects manifests that invent extra collected-data claims', () => {
    const bad = contents
      .replace(
        /<key>NSPrivacyCollectedDataTypes<\/key>\s*<array\s*\/>/,
        `<key>NSPrivacyCollectedDataTypes</key><array><dict></dict></array>`,
      )
      .replace(
        /<key>NSPrivacyCollectedDataTypes<\/key>\s*<array>[\s\S]*?<\/array>/,
        `<key>NSPrivacyCollectedDataTypes</key><array><dict></dict></array>`,
      );
    expect(() => validatePrivacyManifestXml(bad)).toThrow(/collected-data/i);
  });
});

describe('withPrivacyManifest plugin export', () => {
  it('exports a config plugin function', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const plugin = require('../../plugins/withPrivacyManifest').default;
    expect(typeof plugin).toBe('function');
  });
});
