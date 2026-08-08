import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { UnauthorizedError } from "@/api/client";

export default function RootLayout() {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Owned coaching data is never served from a stale cache: the
            // report route mirrors the browser's no-store posture, and a
            // history reset can invalidate everything between two launches.
            staleTime: 0,
            gcTime: 0,
            retry: (failureCount, error) => {
              // A 401 means the credential is finished — retrying cannot fix
              // it and only delays routing the golfer to reconnect.
              if (error instanceof UnauthorizedError) return false;
              return failureCount < 2;
            },
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: "#06110c" },
            headerTintColor: "#f5f2e9",
            headerTitleStyle: { fontWeight: "700" },
            contentStyle: { backgroundColor: "#eef2ef" },
          }}
        >
          <Stack.Screen name="index" options={{ title: "Today" }} />
          <Stack.Screen name="sessions" options={{ title: "History" }} />
          <Stack.Screen name="connect" options={{ title: "Connect device" }} />
        </Stack>
      </SafeAreaProvider>
    </QueryClientProvider>
  );
}
