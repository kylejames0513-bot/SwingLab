import { TAB_ORDER } from '../../src/navigation/tabOrder';

describe('coach tab order', () => {
  it('keeps Today, Practice, Analyze, Progress, More in order', () => {
    expect(TAB_ORDER).toEqual([
      'today',
      'practice',
      'analyze',
      'progress',
      'more',
    ]);
  });
});
