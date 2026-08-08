import { useLocalSearchParams } from 'expo-router';

import { UploadScreen } from '@/features/analysis/UploadScreen';
import type { UploadComparison } from '@/features/analysis/uploadTypes';

export default function UploadRoute() {
  const params = useLocalSearchParams<{
    uri?: string;
    sourceName?: string;
    fileBytes?: string;
    historyEpoch?: string;
    club?: string;
    hand?: string;
    angle?: string;
    comparison?: string;
    chunkBytes?: string;
  }>();

  const comparison: UploadComparison = params.comparison
    ? (JSON.parse(params.comparison) as UploadComparison)
    : null;

  return (
    <UploadScreen
      localUri={String(params.uri ?? '')}
      sourceName={String(params.sourceName ?? 'swing.mp4')}
      fileBytes={Number(params.fileBytes ?? '0')}
      historyEpoch={Number(params.historyEpoch ?? '0')}
      club={
        (params.club as
          | 'driver'
          | 'fairway-wood'
          | 'hybrid'
          | 'iron'
          | 'wedge') ?? 'iron'
      }
      hand={(params.hand as 'left' | 'right') ?? 'right'}
      angle={(params.angle as 'face-on' | 'dtl') ?? 'face-on'}
      comparison={comparison}
      chunkBytes={Number(params.chunkBytes ?? String(4 * 1024 * 1024))}
    />
  );
}
