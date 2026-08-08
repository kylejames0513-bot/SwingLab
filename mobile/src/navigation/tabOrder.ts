/** Exact coach-first destination order for navigation tests and tab layout. */
export const TAB_ORDER = [
  'today',
  'practice',
  'analyze',
  'progress',
  'more',
] as const;

export type TabName = (typeof TAB_ORDER)[number];
