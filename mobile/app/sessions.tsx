import { useQuery } from "@tanstack/react-query";
import { Redirect } from "expo-router";
import { ActivityIndicator, FlatList, StyleSheet, Text, View } from "react-native";

import { UnauthorizedError, api } from "@/api/client";
import type { SessionSummary } from "@/api/types";

export default function SessionsScreen() {
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });

  if (sessions.error instanceof UnauthorizedError) {
    return <Redirect href="/connect" />;
  }

  if (sessions.isPending) {
    return (
      <View style={[styles.screen, styles.centered]}>
        <ActivityIndicator color="#0f3d28" />
      </View>
    );
  }

  if (sessions.error) {
    return (
      <View style={[styles.screen, styles.centered]}>
        <Text style={styles.error}>{sessions.error.message}</Text>
      </View>
    );
  }

  const rows = sessions.data.sessions ?? [];

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      data={rows}
      keyExtractor={(row) => row.id}
      ListEmptyComponent={
        <Text style={styles.empty}>
          No sessions yet. Film one swing to create a coaching result you can
          return to after practice.
        </Text>
      }
      renderItem={({ item }) => <SessionRow session={item} />}
    />
  );
}

function SessionRow({ session }: { session: SessionSummary }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowTitle}>
        {session.club ? session.club : "Swing"}
        {session.angle ? ` · ${session.angle}` : ""}
      </Text>
      {/* State comes from the server. The client never infers readiness from
          whether a report URL happens to be present. */}
      <Text style={styles.rowState}>{session.state}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#eef2ef" },
  centered: { alignItems: "center", justifyContent: "center" },
  content: { padding: 18, gap: 10 },
  row: {
    padding: 14,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d4ddd6",
    backgroundColor: "#ffffff",
    gap: 4,
  },
  rowTitle: { color: "#0f3d28", fontSize: 16, fontWeight: "700" },
  rowState: { color: "#5a655e", fontSize: 12, letterSpacing: 0.8 },
  empty: { color: "#445049", fontSize: 15, padding: 18, lineHeight: 22 },
  error: { color: "#8f4509", fontSize: 16, textAlign: "center", padding: 24 },
});
