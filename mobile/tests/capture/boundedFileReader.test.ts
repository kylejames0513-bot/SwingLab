import {
  readBoundedChunk,
  refuseWholeFileByteApi,
  resetFileAdapter,
  setFileAdapter,
} from '../../src/platform/files';

describe('bounded file reader', () => {
  afterEach(() => {
    resetFileAdapter();
  });

  it('reads at most one chunk and always closes the handle', async () => {
    const closes: number[] = [];
    const payload = Uint8Array.from({ length: 100 }, (_, i) => i);
    setFileAdapter({
      async open() {
        return {
          offset: 0,
          async readBytes(chunkLength) {
            return payload.slice(0, chunkLength);
          },
          async close() {
            closes.push(1);
          },
        };
      },
      async exists() {
        return true;
      },
      async size() {
        return payload.byteLength;
      },
    });

    const chunk = await readBoundedChunk('file:///tmp/big.mp4', 0, 64);
    expect(chunk.byteLength).toBe(64);
    expect(closes).toEqual([1]);
  });

  it('closes the handle when readBytes fails', async () => {
    const closes: number[] = [];
    setFileAdapter({
      async open() {
        return {
          offset: 0,
          async readBytes() {
            throw new Error('io');
          },
          async close() {
            closes.push(1);
          },
        };
      },
      async exists() {
        return true;
      },
      async size() {
        return 10;
      },
    });
    await expect(readBoundedChunk('file:///x', 0, 8)).rejects.toThrow(/io/);
    expect(closes).toEqual([1]);
  });

  it('refuses whole-file byte APIs for media paths', () => {
    expect(() => refuseWholeFileByteApi('bytes')).toThrow(/readBytes/);
    expect(() => refuseWholeFileByteApi('slice')).toThrow(/readBytes/);
  });
});
