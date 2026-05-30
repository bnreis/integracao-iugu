import React, { useCallback, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  Modal,
  ScrollView,
  Alert,
  ActivityIndicator,
  TextInput,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";
import { getEmpresas, criarFatura, emitirNfse, excluirEmpresa } from "../services/api";

export default function EmpresasScreen({ navigation }: any) {
  const [empresas, setEmpresas] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [busca, setBusca] = useState("");
  const [selecionada, setSelecionada] = useState<any>(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [saving, setSaving] = useState(false);

  const confirmar = (titulo: string, mensagem: string, onConfirm: () => void) => {
    if (Platform.OS === "web") {
      if (window.confirm(`${titulo}\n\n${mensagem}`)) {
        onConfirm();
      }
    } else {
      Alert.alert(titulo, mensagem, [
        { text: "Cancelar", style: "cancel" },
        { text: "Confirmar", onPress: onConfirm },
      ]);
    }
  };

  const alertMsg = (titulo: string, mensagem: string) => {
    if (Platform.OS === "web") {
      window.alert(`${titulo}\n\n${mensagem}`);
    } else {
      Alert.alert(titulo, mensagem);
    }
  };

  const fetchEmpresas = useCallback(async () => {
    setLoading(true);
    const res = await getEmpresas(false);
    if (res.data) {
      const lista = Array.isArray(res.data)
        ? res.data
        : res.data.empresas || [];
      setEmpresas(lista);
    }
    setLoading(false);
  }, []);

  const empresasFiltradas = useMemo(() => {
    if (!busca.trim()) return empresas;
    const termo = busca.toLowerCase().trim();
    return empresas.filter((e) => {
      const nome = (e.razao_social || "").toLowerCase();
      const cnpj = (e.cnpj || "").replace(/\D/g, "");
      const email = (e.email || "").toLowerCase();
      return nome.includes(termo) || cnpj.includes(termo) || email.includes(termo);
    });
  }, [empresas, busca]);

  // Sempre recarrega ao ganhar foco (voltou de editar, salvar, etc.)
  useFocusEffect(
    useCallback(() => {
      fetchEmpresas();
    }, [])
  );

  const abrirDetalhe = (emp: any) => {
    setSelecionada({ ...emp });
    setModalVisible(true);
  };

  const handleGerarFatura = async () => {
    if (!selecionada) return;
    if (!selecionada.valor_fatura || Number(selecionada.valor_fatura) <= 0) {
      alertMsg("Atencao", "Esta empresa nao tem valor de fatura definido.");
      return;
    }
    confirmar(
      "Gerar fatura",
      `Criar fatura de R$ ${selecionada.valor_fatura} para ${selecionada.razao_social}?`,
      async () => {
        setSaving(true);
        const valorCents = Math.round(Number(selecionada.valor_fatura) * 100);
        const res = await criarFatura({
          cnpj: selecionada.cnpj,
          valor_cents: valorCents,
          descricao: selecionada.descricao_boleto || selecionada.descricao_servico || "Servico de TI",
        });
        setSaving(false);
        if (res.data?.invoice_id) {
          alertMsg("Sucesso", `Fatura criada!\nID: ${res.data.invoice_id}`);
        } else {
          alertMsg("Erro", res.error || res.data?.error || "Falha ao criar fatura");
        }
      },
    );
  };

  const handleGerarNfse = async () => {
    if (!selecionada) return;
    confirmar(
      "Gerar NFS-e",
      `Emitir nota fiscal para ${selecionada.razao_social}?\n\nIsso buscara a ultima fatura paga desta empresa e gerara a NFS-e.`,
      async () => {
        setSaving(true);
        const { getFaturas } = await import("../services/api");
        const faturas = await getFaturas({
          status: "paid",
          busca: selecionada.cnpj,
          limite: 1,
        });
        const ultimaFatura = faturas.data?.faturas?.[0];
        if (!ultimaFatura) {
          setSaving(false);
          alertMsg("Atencao", "Nenhuma fatura paga encontrada para esta empresa.");
          return;
        }
        const res = await emitirNfse(ultimaFatura.id);
        setSaving(false);
        if (res.data?.success) {
          alertMsg("Sucesso", `NFS-e: ${res.data.acao || "processada"}\nFatura: ${ultimaFatura.id}`);
        } else {
          alertMsg("Erro", res.data?.error || res.error || "Falha ao emitir NFS-e");
        }
      },
    );
  };

  const handleEditar = (emp: any) => {
    navigation.navigate("CadastrarEmpresa", { empresa: emp, onSaved: fetchEmpresas });
  };

  const handleExcluir = (emp: any) => {
    confirmar(
      "Excluir empresa",
      `Tem certeza que deseja excluir ${emp.razao_social}? Esta acao ira remover a empresa permanentemente.`,
      async () => {
        setSaving(true);
        const res = await excluirEmpresa(emp.cnpj);
        setSaving(false);
        if (res.data?.sucesso) {
          alertMsg("Empresa excluida", `${emp.razao_social} foi excluida.`);
          fetchEmpresas();
        } else {
          alertMsg("Erro", res.error || "Falha ao excluir empresa.");
        }
      },
    );
  };

  const renderEmpresa = ({ item }: { item: any }) => (
    <TouchableOpacity
      style={[styles.card, !item.ativo && styles.cardInativa]}
      onPress={() => abrirDetalhe(item)}
    >
      <View style={styles.cardHeader}>
        <View style={styles.cardHeaderLeft}>
          <Text style={styles.cardNome} numberOfLines={1}>
            {item.razao_social}
          </Text>
          <Text style={styles.cardCnpj}>{formatCnpj(item.cnpj)}</Text>
        </View>
        <View style={styles.cardHeaderRight}>
          {item.valor_fatura ? (
            <Text style={styles.cardValor}>R$ {String(item.valor_fatura).replace(/R\$\s*/g, "")}</Text>
          ) : null}
          <View style={styles.cardActions}>
            {!item.ativo && (
              <View style={styles.inativaBadge}>
                <Text style={styles.inativaText}>Inativa</Text>
              </View>
            )}
            <TouchableOpacity
              onPress={() => handleEditar(item)}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Ionicons name="create-outline" size={20} color="#1a56db" />
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => handleExcluir(item)}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Ionicons name="trash-outline" size={20} color="#dc2626" />
            </TouchableOpacity>
          </View>
        </View>
      </View>

      {/* Linha de info: Criacao da fatura + Emitir NF-e + NF-e na criacao */}
      <View style={styles.cardInfoLine}>
        <View style={styles.infoItem}>
          <Ionicons name="calendar-outline" size={13} color="#6b7280" />
          <Text style={styles.infoLabel}>Fatura: </Text>
          <Text style={styles.infoValue}>
            {item.dia_criacao_fatura > 0
              ? `dia ${item.dia_criacao_fatura}`
              : "sem recorrencia"}
          </Text>
        </View>

        <View style={styles.infoSeparator} />

        <View style={styles.infoItem}>
          <Ionicons
            name={item.emitir_nf ? "checkmark-circle" : "close-circle"}
            size={13}
            color={item.emitir_nf ? "#166534" : "#9ca3af"}
          />
          <Text style={styles.infoLabel}>NF-e: </Text>
          <Text style={[styles.infoValue, { color: item.emitir_nf ? "#166534" : "#9ca3af" }]}>
            {item.emitir_nf ? "Sim" : "Nao"}
          </Text>
        </View>

        <View style={styles.infoSeparator} />

        <View style={styles.infoItem}>
          <Ionicons
            name={item.nf_na_criacao ? "checkmark-circle" : "close-circle"}
            size={13}
            color={item.nf_na_criacao ? "#3730a3" : "#9ca3af"}
          />
          <Text style={styles.infoLabel}>Na criacao: </Text>
          <Text style={[styles.infoValue, { color: item.nf_na_criacao ? "#3730a3" : "#9ca3af" }]}>
            {item.nf_na_criacao ? "Sim" : "Nao"}
          </Text>
        </View>
      </View>
    </TouchableOpacity>
  );

  return (
    <View style={styles.container}>
      {/* Barra de busca */}
      <View style={styles.searchBar}>
        <Ionicons name="search" size={18} color="#9ca3af" />
        <TextInput
          style={styles.searchInput}
          placeholder="Buscar por nome, CNPJ, e-mail..."
          value={busca}
          onChangeText={setBusca}
          returnKeyType="search"
        />
        {busca.length > 0 && (
          <TouchableOpacity onPress={() => setBusca("")}>
            <Ionicons name="close-circle" size={20} color="#9ca3af" />
          </TouchableOpacity>
        )}
      </View>

      <FlatList
        data={empresasFiltradas}
        keyExtractor={(item) => item.cnpj}
        renderItem={renderEmpresa}
        refreshControl={
          <RefreshControl refreshing={loading} onRefresh={fetchEmpresas} />
        }
        contentContainerStyle={styles.lista}
        ListEmptyComponent={
          loading ? (
            <ActivityIndicator size="large" color="#1a56db" style={{ marginTop: 60 }} />
          ) : (
            <Text style={styles.emptyText}>
              {busca.trim() ? "Nenhuma empresa encontrada" : "Nenhuma empresa cadastrada"}
            </Text>
          )
        }
      />

      {/* Modal detalhes */}
      <Modal visible={modalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <TouchableOpacity
              style={styles.modalClose}
              onPress={() => setModalVisible(false)}
            >
              <Ionicons name="close" size={24} color="#6b7280" />
            </TouchableOpacity>

            {selecionada && (
              <ScrollView>
                <Text style={styles.modalTitle} numberOfLines={2}>
                  {selecionada.razao_social}
                </Text>
                <Text style={styles.modalCnpj}>
                  {formatCnpj(selecionada.cnpj)}
                </Text>

                <DetailRow label="E-mail" value={selecionada.email || "---"} />
                <DetailRow
                  label="Valor fatura"
                  value={
                    selecionada.valor_fatura
                      ? `R$ ${String(selecionada.valor_fatura).replace(/R\$\s*/g, "")}`
                      : "---"
                  }
                />
                <DetailRow
                  label="Criacao da fatura"
                  value={
                    selecionada.dia_criacao_fatura > 0
                      ? `Dia ${selecionada.dia_criacao_fatura}`
                      : "Sem recorrencia"
                  }
                />
                <DetailRow
                  label="Observacoes"
                  value={selecionada.observacoes || "---"}
                />

                {/* Acoes */}
                <View style={styles.actionsSection}>
                  <Text style={styles.toggleSectionTitle}>Acoes</Text>
                  <View style={styles.actionsRow}>
                    <TouchableOpacity
                      style={[styles.actionBtn, { backgroundColor: "#1a56db" }]}
                      onPress={handleGerarFatura}
                      disabled={saving}
                    >
                      <Ionicons name="document-text" size={18} color="#fff" />
                      <Text style={styles.actionBtnText}>Gerar Fatura</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[styles.actionBtn, { backgroundColor: "#7c3aed" }]}
                      onPress={handleGerarNfse}
                      disabled={saving}
                    >
                      <Ionicons name="receipt" size={18} color="#fff" />
                      <Text style={styles.actionBtnText}>Gerar NFS-e</Text>
                    </TouchableOpacity>
                  </View>
                </View>

                {saving && (
                  <ActivityIndicator
                    style={{ marginTop: 12 }}
                    color="#1a56db"
                  />
                )}
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>
    </View>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.detailRow}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text style={styles.detailValue}>{value}</Text>
    </View>
  );
}

function formatCnpj(cnpj: string): string {
  const d = cnpj.replace(/\D/g, "");
  if (d.length !== 14) return cnpj;
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f3f4f6" },
  searchBar: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#fff",
    margin: 12,
    marginBottom: 0,
    borderRadius: 10,
    paddingHorizontal: 12,
    elevation: 1,
  },
  searchInput: { flex: 1, padding: 10, fontSize: 14 },
  lista: { padding: 12 },
  card: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    elevation: 2,
  },
  cardInativa: { opacity: 0.5 },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
  },
  cardHeaderLeft: {
    flex: 1,
    marginRight: 10,
  },
  cardHeaderRight: {
    alignItems: "flex-end",
    gap: 4,
  },
  cardNome: { fontSize: 14, fontWeight: "600", color: "#111827" },
  cardActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  cardCnpj: { fontSize: 11, color: "#9ca3af", marginTop: 1 },
  cardValor: {
    fontSize: 14,
    fontWeight: "bold",
    color: "#111827",
  },
  // Linha de info unificada (fatura + NF-e + NF na criacao)
  cardInfoLine: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: 4,
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#f3f4f6",
  },
  infoItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
  },
  infoLabel: {
    fontSize: 11,
    color: "#6b7280",
  },
  infoValue: {
    fontSize: 11,
    fontWeight: "600",
    color: "#374151",
  },
  infoSeparator: {
    width: 1,
    height: 12,
    backgroundColor: "#e5e7eb",
    marginHorizontal: 4,
  },
  inativaBadge: {
    backgroundColor: "#f3f4f6",
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 6,
  },
  inativaText: { fontSize: 10, color: "#6b7280", fontWeight: "600" },
  emptyText: { textAlign: "center", color: "#9ca3af", marginTop: 40, fontSize: 14 },
  // Modal
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.4)",
    justifyContent: "flex-end",
  },
  modalContent: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    maxHeight: "90%",
  },
  modalClose: { alignSelf: "flex-end", padding: 4 },
  modalTitle: { fontSize: 18, fontWeight: "bold", color: "#111827" },
  modalCnpj: { fontSize: 13, color: "#6b7280", marginBottom: 14 },
  detailRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#f3f4f6",
  },
  detailLabel: { fontSize: 13, color: "#6b7280", flexShrink: 0 },
  detailValue: {
    fontSize: 13,
    color: "#111827",
    maxWidth: "60%",
    textAlign: "right",
    flexShrink: 1,
  },
  actionsSection: { marginTop: 16 },
  actionsRow: {
    flexDirection: "column",
    gap: 10,
    marginTop: 4,
  },
  actionBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 14,
    borderRadius: 10,
  },
  actionBtnText: {
    color: "#fff",
    fontSize: 15,
    fontWeight: "600",
  },
});
