import React from "react";
import { View, ActivityIndicator, StyleSheet } from "react-native";

/**
 * Indicador visual do pull-to-refresh (web). Aparece no topo da lista
 * conforme o usuário puxa, e mostra o spinner enquanto recarrega.
 */
export default function PullIndicator({
  pull,
  refreshing,
}: {
  pull: number;
  refreshing: boolean;
}) {
  if (pull <= 0 && !refreshing) return null;
  const height = refreshing ? 44 : pull;
  return (
    <View style={[styles.wrap, { height }]} pointerEvents="none">
      <ActivityIndicator size="small" color="#1a56db" />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
  },
});
