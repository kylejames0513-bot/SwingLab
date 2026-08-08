import * as ImagePicker from 'expo-image-picker';
import { Linking } from 'react-native';

import { suffixFromUri } from './mediaPreflight';
import type { CapturedMedia } from './types';

export async function openSystemSettings(): Promise<void> {
  await Linking.openSettings();
}

export async function pickLibraryVideo(): Promise<
  | { status: 'canceled' }
  | { status: 'denied' }
  | { status: 'picked'; media: Omit<CapturedMedia, 'uri'> & { uri: string } }
> {
  const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) {
    return { status: 'denied' };
  }

  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['videos'],
    allowsEditing: false,
    videoExportPreset: ImagePicker.VideoExportPreset.Passthrough,
  });

  if (result.canceled || !result.assets[0]) {
    return { status: 'canceled' };
  }

  const asset = result.assets[0];
  const suffix = suffixFromUri(asset.uri);
  if (!suffix) {
    throw new Error('unsupported_suffix');
  }

  return {
    status: 'picked',
    media: {
      uri: asset.uri,
      sizeBytes: asset.fileSize ?? 0,
      durationSeconds: asset.duration ? asset.duration / 1000 : 0,
      suffix,
      mimeType: asset.mimeType ?? 'video/mp4',
      source: 'library',
      audioExpected: true,
    },
  };
}
