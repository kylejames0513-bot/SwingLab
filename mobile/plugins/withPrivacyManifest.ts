import {
  ConfigPlugin,
  IOSConfig,
  withDangerousMod,
  withXcodeProject,
} from '@expo/config-plugins';
import * as fs from 'fs';
import * as path from 'path';

const MANIFEST_RELATIVE = path.join('privacy', 'PrivacyInfo.xcprivacy');
const REQUIRED_REASONS: Record<string, string> = {
  NSPrivacyAccessedAPICategoryFileTimestamp: 'C617.1',
  NSPrivacyAccessedAPICategoryDiskSpace: 'E174.1',
  NSPrivacyAccessedAPICategoryUserDefaults: 'CA92.1',
};

export function validatePrivacyManifestXml(contents: string): void {
  if (!contents.includes('<plist') || !contents.includes('</plist>')) {
    throw new Error('PrivacyInfo.xcprivacy is not a valid plist document.');
  }
  if (
    !contents.includes('<key>NSPrivacyTracking</key>') ||
    !/<key>NSPrivacyTracking<\/key>\s*<false\s*\/>/.test(contents)
  ) {
    throw new Error('NSPrivacyTracking must be false.');
  }
  if (!contents.includes('<key>NSPrivacyTrackingDomains</key>')) {
    throw new Error('NSPrivacyTrackingDomains must be declared.');
  }
  // Empty tracking domains array (no string entries between the domains key and next top-level key).
  const domainsMatch = contents.match(
    /<key>NSPrivacyTrackingDomains<\/key>\s*<array>([\s\S]*?)<\/array>/,
  );
  if (!domainsMatch || domainsMatch[1]?.includes('<string>')) {
    throw new Error('NSPrivacyTrackingDomains must be empty.');
  }

  for (const [category, reason] of Object.entries(REQUIRED_REASONS)) {
    if (!contents.includes(category)) {
      throw new Error(`Missing privacy API category ${category}.`);
    }
    if (!contents.includes(reason)) {
      throw new Error(`Missing required reason ${reason} for ${category}.`);
    }
  }

  // Reject undeclared collected-data claims: only an empty array is allowed.
  const collectedMatch = contents.match(
    /<key>NSPrivacyCollectedDataTypes<\/key>\s*<array>([\s\S]*?)<\/array>/,
  );
  if (!collectedMatch) {
    throw new Error('NSPrivacyCollectedDataTypes must be declared.');
  }
  if (collectedMatch[1]?.includes('<dict>')) {
    throw new Error(
      'NSPrivacyCollectedDataTypes must be empty; no undeclared collected-data claims.',
    );
  }
}

/**
 * Validates the repository-owned PrivacyInfo.xcprivacy and copies it into the
 * generated iOS app target root bundle under CNG. Idempotent.
 */
const withPrivacyManifest: ConfigPlugin = (config) => {
  config = withDangerousMod(config, [
    'ios',
    async (cfg) => {
      const projectRoot = cfg.modRequest.projectRoot;
      const source = path.join(projectRoot, MANIFEST_RELATIVE);
      if (!fs.existsSync(source)) {
        throw new Error(`Missing repository privacy manifest at ${source}`);
      }
      const contents = fs.readFileSync(source, 'utf8');
      validatePrivacyManifestXml(contents);

      const iosRoot = path.join(projectRoot, 'ios');
      const projectName =
        cfg.modRequest.projectName ??
        IOSConfig.XcodeUtils.getProjectName(projectRoot);
      const targetDir = path.join(iosRoot, projectName);
      fs.mkdirSync(targetDir, { recursive: true });
      const dest = path.join(targetDir, 'PrivacyInfo.xcprivacy');
      fs.writeFileSync(dest, contents, 'utf8');
      return cfg;
    },
  ]);

  config = withXcodeProject(config, (cfg) => {
    const project = cfg.modResults;
    const projectName =
      cfg.modRequest.projectName ??
      IOSConfig.XcodeUtils.getProjectName(cfg.modRequest.projectRoot);
    const filePath = `${projectName}/PrivacyInfo.xcprivacy`;
    if (!project.hasFile(filePath)) {
      // Best-effort add; DangerousMod already writes the file for root-bundle use.
      try {
        IOSConfig.XcodeUtils.addResourceFileToGroup({
          filepath: filePath,
          groupName: projectName,
          project,
          isBuildFile: true,
        });
      } catch {
        // Prebuild without a full xcodeproj still has the file on disk.
      }
    }
    return cfg;
  });

  return config;
};

export default withPrivacyManifest;
