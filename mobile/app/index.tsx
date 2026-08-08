import { useQuery } from "@tanstack/react-query";
import { Link, Redirect } from "expo-router";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { UnauthorizedError, api } from "@/api/client";

export default function TodayScreen() {
  const today = useQuery({ queryKey: ["today"], queryFn: api.today });

  // The client clears the credential before raising this, so the connect
  // screen is the whole recovery — there is nothing to retry.
  if (today.error instanceof UnauthorizedError) {
    return <Redirect href="/connect" />;
  }

  if (today.isPending) {
    return (
      <View style={[styles.screen, styles.centered]}>
        <ActivityIndicator color="#0f3d28" />
      </View>
    );
  }

  if (today.error) {
    return (
      <View style={[styles.screen, styles.centered]}>
        <Text style={styles.error}>{today.error.message}</Text>
      </View>
    );
  }

  const view = today.data;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.hero}>
        <Text style={styles.eyebrow}>
          {view.membership?.is_pro ? "PRO MEMBER" : "TODAY"}
        </Text>
        <Text style={styles.title}>{view.headline ?? "Today"}</Text>
      </View>

      <View style={styles.tiles}>
        <Tile label="Preferred club" value={view.preferred_club ?? "Not set"} />
        <Tile
          label="Practice block"
          value={
            view.practice_minutes ? `${view.practice_minutes} minutes` : "Not set"
          }
        />
        <Tile
          label="Plan"
          value={view.membership?.is_pro ? "Pro member" : "Free plan"}
        />
      </View>

      <Link href="/sessions" style={styles.link}>
        View swing history
      </Link>
    </ScrollView>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.tile}>
      <Text style={styles.tileLabel}>{label.toUpperCase()}</Text>
      <Text style={styles.tileValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#eef2ef" },
  centered: { alignItems: "center", justifyContent: "center" },
  content: { padding: 18, gap: 16 },
  hero: { backgroundColor: "#06110c", borderRadius: 22, padding: 22, gap: 8 },
  eyebrow: {
    color: "#ffad62",
    fontSize: 11,
    fontWeight: "600",
    letterSpacing: 1.6,
  },
  title: { color: "#ffffff", fontSize: 40, fontWeight: "800", letterSpacing: -1.4 },
  // Three readings stay a row, as on the web app — stacking three short facts
  // to full width is mostly dead space.
  tiles: { flexDirection: "row", gap: 9 },
  tile: {
    flex: 1,
    minWidth: 0,
    padding: 13,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d4ddd6",
    backgroundColor: "#f8fbf9",
    gap: 4,
  },
  tileLabel: { color: "#5a655e", fontSize: 9.5, fontWeight: "600", letterSpacing: 1 },
  tileValue: { color: "#0f3d28", fontSize: 15, fontWeight: "800" },
  link: { color: "#1a5c38", fontSize: 16, fontWeight: "700" },
  error: { color: "#8f4509", fontSize: 16, textAlign: "center", padding: 24 },
});
