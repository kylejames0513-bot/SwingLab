import { isAllowedGearHost } from '../../src/features/more/gear';
import {
  MAX_PRIVACY_EXPORT_ZIP_BYTES,
} from '../../src/features/more/privacy';
import { EXPORT_DOWNLOAD_BANNED_APIS } from '../../src/features/more/exportDownloader';
import { minTouchSize } from '../../src/platform/a11y';

describe('more / privacy contracts', () => {
  it('allows only matching HTTPS gear hosts', () => {
    expect(
      isAllowedGearHost('https://shop.example.com/path', 'https://shop.example.com'),
    ).toBe(true);
    expect(
      isAllowedGearHost('http://shop.example.com', 'https://shop.example.com'),
    ).toBe(false);
    expect(
      isAllowedGearHost('https://evil.example.com', 'https://shop.example.com'),
    ).toBe(false);
  });

  it('keeps the privacy export ZIP max aligned to OpenAPI', () => {
    expect(MAX_PRIVACY_EXPORT_ZIP_BYTES).toBe(1_100_000_000);
  });

  it('documents banned export download APIs', () => {
    expect(EXPORT_DOWNLOAD_BANNED_APIS).toEqual(
      expect.arrayContaining(['File.downloadFileAsync', 'FormData', 'arrayBuffer']),
    );
  });
});

describe('a11y touch targets', () => {
  it('uses 44pt iOS and 48dp Android minima', () => {
    expect(minTouchSize('ios')).toBe(44);
    expect(minTouchSize('android')).toBe(48);
  });
});
