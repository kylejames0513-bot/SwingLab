import { Tabs, router } from 'expo-router';
import { Pressable, Text, View, type GestureResponderEvent } from 'react-native';

import { colors } from '@/design/tokens';
import { TAB_ORDER } from '@/navigation/tabOrder';

function AnalyzeTabButton(props: {
  onPress?: (event: GestureResponderEvent) => void;
  accessibilityState?: { selected?: boolean };
}) {
  return (
    <Pressable
      onPress={props.onPress}
      accessibilityRole="button"
      accessibilityLabel="Analyze"
      accessibilityState={props.accessibilityState}
      style={{
        flex: 1,
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: 48,
      }}
    >
      <View
        style={{
          minWidth: 56,
          minHeight: 56,
          borderRadius: 28,
          backgroundColor: colors.greenBtn,
          alignItems: 'center',
          justifyContent: 'center',
          marginBottom: 8,
        }}
      >
        <Text style={{ color: colors.greenInk, fontWeight: '700', fontSize: 22 }}>
          +
        </Text>
      </View>
      <Text style={{ color: colors.ink, fontSize: 12, fontWeight: '600' }}>
        Analyze
      </Text>
    </Pressable>
  );
}

const TITLES: Record<(typeof TAB_ORDER)[number], string> = {
  today: 'Today',
  practice: 'Practice',
  analyze: 'Analyze',
  progress: 'Progress',
  more: 'More',
};

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.greenBtn,
        tabBarInactiveTintColor: colors.inkMuted,
        tabBarStyle: {
          minHeight: 64,
          paddingBottom: 8,
          backgroundColor: colors.bgCard,
          borderTopColor: colors.border,
        },
      }}
    >
      <Tabs.Screen name="today" options={{ title: TITLES.today }} />
      <Tabs.Screen name="practice" options={{ title: TITLES.practice }} />
      <Tabs.Screen
        name="analyze"
        options={{
          title: TITLES.analyze,
          tabBarButton: (props) => (
            <AnalyzeTabButton
              accessibilityState={props.accessibilityState}
              onPress={(event) => {
                props.onPress?.(event);
                router.push('/capture');
              }}
            />
          ),
        }}
      />
      <Tabs.Screen name="progress" options={{ title: TITLES.progress }} />
      <Tabs.Screen name="more" options={{ title: TITLES.more }} />
    </Tabs>
  );
}
