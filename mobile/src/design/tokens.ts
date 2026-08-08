/** Semantic tokens mirroring brand intent from web_layout (not CSS copy). */
export const colors = {
  bg: '#F7F5F0',
  bgCard: '#FFFDF9',
  surfaceSoft: '#EDF3EE',
  surfaceDark: '#103C27',
  night: '#07130D',
  ink: '#17201A',
  inkSoft: '#4A544C',
  inkMuted: '#5F6B62',
  green: '#14472C',
  greenBtn: '#1A5C38',
  greenInk: '#E9F2EC',
  orange: '#A84B00',
  orangeText: '#A94708',
  orangeSoft: '#FFF3E4',
  premiumAccent: '#FFAD62',
  border: '#E3DED3',
  borderStrong: '#C9C3B8',
  controlBorder: '#7A867C',
  danger: '#8B2E2E',
  success: '#1A5C38',
} as const;

export const radii = {
  sm: 8,
  md: 14,
  lg: 22,
  xl: 32,
} as const;

export const space = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
} as const;

export const type = {
  brand: 28,
  title: 22,
  body: 16,
  caption: 13,
} as const;

export const hitSlop = {
  ios: 44,
  android: 48,
} as const;
