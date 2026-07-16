import React, { useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  Modal,
  StyleSheet,
  Platform,
  Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { getTenants, getEmpresaAtivaId } from "../services/api";

/**
 * Menu de conta no cabeçalho (ADR-0007): mostra a empresa ativa e permite
 * TROCAR de empresa sem deslogar, além de Sair.
 *
 * - Trocar: chama onSwitch(id). O App decide — se já há sessão na empresa
 *   destino, remonta as telas com os dados dela; senão, manda pro login
 *   (já pré-selecionada naquela empresa).
 * - Sair: onLogout (com confirmação).
 */
interface Props {
  onSwitch: (id: string) => void;
  onLogout: () => void;
}

export default function ContaMenu({ onSwitch, onLogout }: Props) {
  const [aberto, setAberto] = useState(false);
  const tenants = getTenants();
  const ativoId = getEmpresaAtivaId();
  const ativo = tenants.find((t) => t.id === ativoId);

  const confirmarLogout = () => {
    setAberto(false);
    const msg = "Deseja sair da conta?";
    if (Platform.OS === "web") {
      if (window.confirm(msg)) onLogout();
    } else {
      Alert.alert("Sair", msg, [
        { text: "Cancelar", style: "cancel" },
        { text: "Sair", style: "destructive", onPress: onLogout },
      ]);
    }
  };

  return (
    <>
      <TouchableOpacity
        style={styles.headerBtn}
        onPress={() => setAberto(true)}
        accessibilityLabel="Trocar empresa"
      >
        <Ionicons name="business" size={15} color="#fff" />
        <Text style={styles.headerBtnText} numberOfLines={1}>
          {ativo?.nome ?? "Conta"}
        </Text>
        <Ionicons name="chevron-down" size={14} color="#fff" />
      </TouchableOpacity>

      <Modal
        visible={aberto}
        transparent
        animationType="fade"
        onRequestClose={() => setAberto(false)}
      >
        <TouchableOpacity
          style={styles.backdrop}
          activeOpacity={1}
          onPress={() => setAberto(false)}
        >
          <View style={styles.sheet}>
            <Text style={styles.sheetTitle}>Trocar empresa</Text>
            {tenants.map((t) => {
              const isAtivo = t.id === ativoId;
              return (
                <TouchableOpacity
                  key={t.id}
                  style={[styles.item, isAtivo && styles.itemAtivo]}
                  onPress={() => {
                    setAberto(false);
                    if (!isAtivo) onSwitch(t.id);
                  }}
                >
                  <Ionicons
                    name="business"
                    size={18}
                    color={isAtivo ? "#1a56db" : "#6b7280"}
                  />
                  <Text style={[styles.itemText, isAtivo && styles.itemTextAtivo]}>
                    {t.nome}
                  </Text>
                  {isAtivo && (
                    <Ionicons
                      name="checkmark-circle"
                      size={18}
                      color="#1a56db"
                      style={{ marginLeft: "auto" }}
                    />
                  )}
                </TouchableOpacity>
              );
            })}
            <View style={styles.sep} />
            <TouchableOpacity style={styles.item} onPress={confirmarLogout}>
              <Ionicons name="log-out-outline" size={18} color="#dc2626" />
              <Text style={[styles.itemText, { color: "#dc2626" }]}>Sair</Text>
            </TouchableOpacity>
          </View>
        </TouchableOpacity>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  headerBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginRight: 12,
    maxWidth: 170,
  },
  headerBtnText: { color: "#fff", fontWeight: "600", fontSize: 13 },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.35)",
    justifyContent: "flex-start",
    alignItems: "flex-end",
  },
  sheet: {
    backgroundColor: "#fff",
    borderRadius: 12,
    marginTop: 54,
    marginRight: 8,
    paddingVertical: 8,
    minWidth: 220,
    elevation: 8,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 12,
  },
  sheetTitle: {
    fontSize: 12,
    fontWeight: "700",
    color: "#9ca3af",
    paddingHorizontal: 16,
    paddingVertical: 6,
    textTransform: "uppercase",
  },
  item: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  itemAtivo: { backgroundColor: "#eff4ff" },
  itemText: { fontSize: 15, fontWeight: "500", color: "#374151" },
  itemTextAtivo: { color: "#1a56db", fontWeight: "700" },
  sep: { height: 1, backgroundColor: "#f3f4f6", marginVertical: 4 },
});
