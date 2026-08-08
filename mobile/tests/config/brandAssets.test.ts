import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

const ASSETS = path.join(__dirname, '../../assets');

function readPngSize(filePath: string): { width: number; height: number } {
  const buf = fs.readFileSync(filePath);
  expect(buf.subarray(0, 8).toString('binary')).toBe(
    '\x89PNG\r\n\x1a\n',
  );
  // IHDR: length(4) + type(4) + width(4) + height(4)
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  return { width, height };
}

function sha256(filePath: string): string {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

/** Known Expo template asset hashes we must not ship. Empty if unavailable. */
const EXPO_TEMPLATE_HASHES = new Set<string>([
  // Placeholder — if assets/images/icon.png still exists, compare against it.
]);

describe('brand assets', () => {
  const required: Array<{ name: string; width: number; height: number }> = [
    { name: 'icon.png', width: 1024, height: 1024 },
    { name: 'adaptive-icon.png', width: 1024, height: 1024 },
    { name: 'monochrome-icon.png', width: 432, height: 432 },
    { name: 'splash-icon.png', width: 1024, height: 1024 },
    { name: 'notification-icon.png', width: 96, height: 96 },
  ];

  it('ships repository-owned PNGs at required dimensions', () => {
    for (const asset of required) {
      const filePath = path.join(ASSETS, asset.name);
      expect(fs.existsSync(filePath)).toBe(true);
      const size = readPngSize(filePath);
      expect(size).toEqual({ width: asset.width, height: asset.height });
    }
  });

  it('does not match Expo template sample icons when present', () => {
    const templateIcon = path.join(ASSETS, 'images', 'icon.png');
    if (fs.existsSync(templateIcon)) {
      EXPO_TEMPLATE_HASHES.add(sha256(templateIcon));
    }
    for (const asset of required) {
      const hash = sha256(path.join(ASSETS, asset.name));
      expect(EXPO_TEMPLATE_HASHES.has(hash)).toBe(false);
    }
  });

  it('is referenced by app.config name/slug and asset paths', () => {
    process.env.EXPO_PUBLIC_APP_ENV = 'development';
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const configFactory = require('../../app.config.ts').default;
    const config = configFactory({ config: {} });
    expect(config.name).toBe('CaddieInsight');
    expect(config.slug).toBe('caddieinsight');
    expect(config.icon).toBe('./assets/icon.png');
    expect(config.web?.splash?.image).toBe('./assets/splash-icon.png');
    expect(config.android?.adaptiveIcon?.foregroundImage).toBe(
      './assets/adaptive-icon.png',
    );
    expect(config.android?.adaptiveIcon?.monochromeImage).toBe(
      './assets/monochrome-icon.png',
    );
    const plugins = JSON.stringify(config.plugins ?? []);
    expect(plugins).toContain('./assets/splash-icon.png');
    expect(plugins).toContain('./assets/notification-icon.png');
  });
});
