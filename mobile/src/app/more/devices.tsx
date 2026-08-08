import { useCallback, useEffect, useState } from 'react';
import { View } from 'react-native';

import { listDevices, revokeDevice, type DeviceSummary } from '@/features/more/devices';
import { Button, ScrollScreen, Text } from '@/ui/primitives';
import { space } from '@/design/tokens';

export default function DevicesRoute() {
  const [devices, setDevices] = useState<DeviceSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const next = await listDevices();
      setDevices(next);
      setError(null);
    } catch {
      setError('Could not load devices.');
      setDevices([]);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await listDevices();
        if (!cancelled) {
          setDevices(next);
        }
      } catch {
        if (!cancelled) {
          setError('Could not load devices.');
          setDevices([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ScrollScreen accessibilityLabel="Devices">
      <Text size="brand" weight="700">
        Devices
      </Text>
      {error ? <Text tone="danger">{error}</Text> : null}
      <View style={{ gap: space.md, marginTop: space.md }}>
        {(devices ?? []).map((device) => (
          <View key={device.selector}>
            <Text weight="600">
              {device.label}
              {device.is_current ? ' (this device)' : ''}
            </Text>
            {!device.is_current ? (
              <Button
                label="Revoke"
                variant="danger"
                onPress={() => {
                  void revokeDevice(device.selector).then(reload);
                }}
              />
            ) : null}
          </View>
        ))}
      </View>
    </ScrollScreen>
  );
}
