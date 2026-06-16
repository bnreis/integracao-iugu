import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  Alert,
  TextInput,
  Modal,
  ScrollView,
  ActivityIndicator,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";
import {
  getFaturas,
  getFatura,
  cancelarFatura,
  emitirNfse,
  emitirNfseManual,
  reenviarNfseEmail,
  darBaixaManual,
} from "../services/api";
import { usePullToRefresh } from "../components/usePullToRefresh";
import PullIndicator from "../components/PullIndicator";

// ============================================================
// Helpers de mês
// ============================================================
const MESES = [
  "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
  "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
];

function getMesAtual(): { ano: number; mes: number } {
  const d = new Date();
  return { ano: d.getFullYear(), mes: d.getMonth() }; // 0-indexed
}

function getRange(ano: number, mes: number) {
  const from = `${ano}-${String(mes + 1).padStart(2, "0")}-01`;
  const lastDay = new Date(ano, mes + 1, 0).getDate();
  const to = `${ano}-${String(mes + 1).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`;
  return { from, to };
}

function labelMes(ano: number, mes: number): string {
  const agora = getMesAtual();
  if (ano === agora.ano && mes === agora.mes) return `${MESES[mes]} (atual)`;
  return `${MESES[mes]} ${ano}`;
}

// ============================================================
// Status helpers
// ============================================================
const STATUS_COLORS: Record<string, string> = {
  pending: "#f59e0b",
  paid: "#059669",
  externally_paid: "#0d9488",
  canceled: "#6b7280",
  expired: "#dc2626",
  refunded: "#7c3aed",
  draft: "#9ca3af",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "Pendente",
  paid: "Paga",
  externally_paid: "Paga (externa)",
  canceled: "Cancelada",
  expired: "Expirada",
  refunded: "Estornada",
  draft: "Rascunho",
};

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] || "#6b7280";
  const label = STATUS_LABELS[status] || status;
  return (
    <View style={[styles.badge, { backgroundColor: color + "20" }]}>
      <Text style={[styles.badgeText, { color }]}>{label}</Text>
    </View>
  );
}

// ============================================================
// Componente principal
// ============================================================
export default function FaturasScreen() {
  const [faturas, setFaturas] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [filtro, setFiltro] = useState<string | undefined>(undefined);
  const [busca, setBusca] = useState("");

  // Mês selecionado
  const agora = getMesAtual();
  const [ano, setAno] = useState(agora.ano);
  const [mes, setMes] = useState(agora.mes);

  // Busca com debounce
  const [buscaDebounced, setBuscaDebounced] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onChangeBusca = (text: string) => {
    setBusca(text);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setBuscaDebounced(text.trim());
    }, 500);
  };

  // Modal detalhe
  const [detalhe, setDetalhe] = useState<any>(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  // Seletor de baixa manual (modal aninhado)
  const [baixaSelectVisible, setBaixaSelectVisible] = useState(false);
  const [baixaInvoiceId, setBaixaInvoiceId] = useState<string | null>(null);

  // Helper de confirmação (funciona na web e no mobile)
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

  const fetchFaturas = useCallback(async () => {
    setLoading(true);
    const range = getRange(ano, mes);
    const res = await getFaturas({
      status: filtro,
      limite: 100,
      busca: buscaDebounced || undefined,
      created_from: range.from,
      created_to: range.to,
    });
    if (res.data) {
      setFaturas(res.data.faturas || []);
      setTotal(res.data.total || 0);
    }
    setLoading(false);
  }, [filtro, buscaDebounced, ano, mes]);

  // Recarrega ao focar a tela (ex: voltar de outra aba) e quando os filtros mudam.
  useFocusEffect(
    useCallback(() => {
      fetchFaturas();
    }, [fetchFaturas])
  );

  // Pull-to-refresh customizado (web). No APK nativo, o RefreshControl abaixo
  // continua cuidando do gesto.
  const scrollTopRef = useRef(0);
  const { wrapperRef, pull } = usePullToRefresh(
    () => scrollTopRef.current,
    fetchFaturas
  );

  // Data de corte: março/2026
  const CORTE_ANO = 2026;
  const CORTE_MES = 2; // 0-indexed (2 = março)

  // Navegação de mês
  const mesAnterior = () => {
    // Não volta antes de março/2026
    if (ano === CORTE_ANO && mes <= CORTE_MES) return;
    if (mes === 0) {
      setMes(11);
      setAno(ano - 1);
    } else {
      setMes(mes - 1);
    }
  };

  const mesSeguinte = () => {
    const agora = getMesAtual();
    // Não avança além do mês atual
    if (ano === agora.ano && mes >= agora.mes) return;
    if (mes === 11) {
      setMes(0);
      setAno(ano + 1);
    } else {
      setMes(mes + 1);
    }
  };

  const isMesAtual = ano === agora.ano && mes === agora.mes;
  const isMesCorte = ano === CORTE_ANO && mes === CORTE_MES;

  // Ações
  const abrirDetalhe = async (id: string, nfseEmitidaFromList?: boolean) => {
    setActionLoading(true);
    setModalVisible(true);
    const res = await getFatura(id);
    if (res.data) {
      // Usa nfse_emitida do detalhe (3 fontes), com fallback da lista
      if (res.data.nfse_emitida === undefined && nfseEmitidaFromList !== undefined) {
        res.data.nfse_emitida = nfseEmitidaFromList;
      }
      setDetalhe(res.data);
    }
    setActionLoading(false);
  };

  const handleCancelar = async (id: string) => {
    confirmar(
      "Cancelar fatura",
      "Tem certeza? Essa ação não pode ser desfeita.",
      async () => {
        const res = await cancelarFatura(id);
        if (res.data?.sucesso) {
          alertMsg("Sucesso", "Fatura cancelada com sucesso.");
          setModalVisible(false);
          fetchFaturas();
        } else {
          alertMsg("Erro", "Não foi possível cancelar a fatura. Tente novamente.");
        }
      },
    );
  };

  const handleEmitirNfse = (id: string) => {
    confirmar(
      "Emitir NFS-e",
      "Confirma a emissão da nota fiscal para esta fatura?",
      async () => {
        setActionLoading(true);
        const res = await emitirNfse(id);
        setActionLoading(false);
        if (res.data?.success) {
          alertMsg("Sucesso", "Nota Fiscal emitida e enviada com sucesso!");
        } else {
          alertMsg("Erro", "Não foi possível emitir a Nota Fiscal para esta fatura.");
        }
      },
    );
  };

  // Emissão MANUAL: gera a NFS-e mesmo com a fatura ainda NÃO paga.
  const handleEmitirNfseManual = (id: string) => {
    confirmar(
      "Gerar NFS-e (manual)",
      "Esta fatura ainda NÃO foi paga. Deseja emitir a Nota Fiscal mesmo assim?",
      async () => {
        setActionLoading(true);
        const res = await emitirNfseManual(id);
        setActionLoading(false);
        if (res.data?.success) {
          alertMsg("Sucesso", "Nota Fiscal emitida e enviada com sucesso!");
          setModalVisible(false);
          setDetalhe(null);
          fetchFaturas();
        } else {
          alertMsg("Erro", "Não foi possível emitir a Nota Fiscal para esta fatura.");
        }
      },
    );
  };

  const handleReenviarEmail = (id: string, isNfse: boolean = false) => {
    const titulo = isNfse ? "Reenviar NF-e" : "Reenviar e-mail";
    const mensagem = isNfse
      ? "Confirma o reenvio da NF-e por e-mail para o cliente?"
      : "Confirma o reenvio do e-mail de cobrança para o cliente?";
    confirmar(
      titulo,
      mensagem,
      async () => {
        setActionLoading(true);
        const res = await reenviarNfseEmail(id);
        setActionLoading(false);
        if (res.data?.sucesso) {
          alertMsg(
            "Sucesso",
            isNfse
              ? "Nota Fiscal reenviada por e-mail com sucesso."
              : "E-mail de cobrança reenviado com sucesso."
          );
        } else {
          alertMsg("Erro", res.error || "Não foi possível reenviar o e-mail. Tente novamente.");
        }
      },
    );
  };

  const abrirBaixaManual = (id: string) => {
    setBaixaInvoiceId(id);
    setBaixaSelectVisible(true);
  };

  const handleBaixaManual = async (forma: string) => {
    if (!baixaInvoiceId) return;
    setBaixaSelectVisible(false);
    setActionLoading(true);
    const res = await darBaixaManual(baixaInvoiceId, forma);
    setActionLoading(false);
    if (res.data?.sucesso) {
      if (res.data?.nfse_emitida === true) {
        alertMsg(
          "Sucesso",
          "Fatura baixada com sucesso! A Nota Fiscal foi emitida e enviada automaticamente."
        );
      } else {
        alertMsg(
          "Sucesso",
          "Fatura baixada com sucesso! A Nota Fiscal não foi emitida para este cliente."
        );
      }
      setBaixaInvoiceId(null);
      setModalVisible(false);
      setDetalhe(null);
      fetchFaturas();
    } else {
      alertMsg("Erro", res.data?.error || res.error || "Falha na baixa manual");
    }
  };

  const FORMAS_PAGAMENTO = ["Pix na conta", "Dinheiro", "Outros"];

  const filtros = [
    { label: "Todas", value: undefined },
    { label: "Pendentes", value: "pending" },
    { label: "Pagas", value: "paid" },
    { label: "Canceladas", value: "canceled" },
  ];

  const renderFatura = ({ item }: { item: any }) => (
    <TouchableOpacity
      style={styles.faturaCard}
      onPress={() => abrirDetalhe(item.id, item.nfse_emitida)}
    >
      <View style={styles.faturaHeader}>
        <Text style={styles.faturaName} numberOfLines={1}>
          {item.payer_name || item.email || "—"}
        </Text>
        <View style={styles.faturaHeaderBadges}>
          <StatusBadge status={item.status} />
          {item.nfse_emitida ? (
            <View style={styles.nfseBadge}>
              <Ionicons name="checkmark-circle" size={12} color="#059669" />
              <Text style={styles.nfseBadgeText}>NF-e</Text>
            </View>
          ) : item.empresa_emite_nf === false ? (
            // Empresa não emite NF-e → badge neutro (não é pendência).
            <View style={styles.nfseNaBadge}>
              <Ionicons name="remove-circle" size={12} color="#9ca3af" />
              <Text style={styles.nfseNaText}>NF-e: N/A</Text>
            </View>
          ) : (
            <View style={styles.nfsePendenteBadge}>
              <Ionicons name="alert-circle" size={12} color="#f59e0b" />
              <Text style={styles.nfsePendenteText}>s/ NF-e</Text>
            </View>
          )}
        </View>
      </View>
      <View style={styles.faturaBody}>
        <Text style={styles.faturaValor}>{item.total || "—"}</Text>
        <Text style={styles.faturaDate}>Venc: {item.due_date || "—"}</Text>
      </View>
      {item.payer_cpf_cnpj && (
        <Text style={styles.faturaCnpj}>{item.payer_cpf_cnpj}</Text>
      )}
    </TouchableOpacity>
  );

  return (
    <View style={styles.container}>
      {/* Seletor de mês */}
      <View style={styles.monthSelector}>
        <TouchableOpacity
          onPress={mesAnterior}
          style={[styles.monthArrow, isMesCorte && { opacity: 0.3 }]}
          disabled={isMesCorte}
        >
          <Ionicons name="chevron-back" size={22} color="#1a56db" />
        </TouchableOpacity>
        <View style={styles.monthCenter}>
          <Text style={styles.monthLabel}>{labelMes(ano, mes)}</Text>
          <Text style={styles.monthCount}>
            {loading ? "..." : `${total} fatura${total !== 1 ? "s" : ""}`}
          </Text>
        </View>
        <TouchableOpacity
          onPress={mesSeguinte}
          style={[styles.monthArrow, isMesAtual && { opacity: 0.3 }]}
          disabled={isMesAtual}
        >
          <Ionicons name="chevron-forward" size={22} color="#1a56db" />
        </TouchableOpacity>
      </View>

      {/* Barra de busca */}
      <View style={styles.searchBar}>
        <Ionicons name="search" size={18} color="#9ca3af" />
        <TextInput
          style={styles.searchInput}
          placeholder="Buscar por nome, CNPJ..."
          value={busca}
          onChangeText={onChangeBusca}
          returnKeyType="search"
        />
        {busca.length > 0 && (
          <TouchableOpacity onPress={() => { setBusca(""); setBuscaDebounced(""); }}>
            <Ionicons name="close-circle" size={20} color="#9ca3af" />
          </TouchableOpacity>
        )}
      </View>

      {/* Filtros de status */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.filtrosBar}
        contentContainerStyle={styles.filtrosContent}
      >
        {filtros.map((f) => (
          <TouchableOpacity
            key={f.label}
            style={[
              styles.filtroChip,
              filtro === f.value && styles.filtroChipAtivo,
            ]}
            onPress={() => setFiltro(f.value)}
          >
            <Text
              style={[
                styles.filtroText,
                filtro === f.value && styles.filtroTextAtivo,
              ]}
            >
              {f.label}
            </Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* Lista */}
      <View ref={wrapperRef} style={{ flex: 1 }}>
        {/* PullIndicator é o pull-to-refresh do WEB. No nativo (APK) quem cuida é
            o RefreshControl — renderizar os dois juntos mostrava 2 spinners.
            Só no web; e só com dados (no load inicial fica o spinner central). */}
        {Platform.OS === "web" && (
          <PullIndicator pull={pull} refreshing={loading && faturas.length > 0} />
        )}
        <FlatList
          data={faturas}
          keyExtractor={(item) => item.id}
          renderItem={renderFatura}
          onScroll={(e) => {
            scrollTopRef.current = e.nativeEvent.contentOffset.y;
          }}
          scrollEventThrottle={16}
          refreshControl={
            // No web o pull-to-refresh é o PullIndicator custom (acima); no nativo
            // é o RefreshControl. refreshing só com dados: no LOAD INICIAL (lista
            // vazia) quem aparece é o ActivityIndicator central — senão dava 2 spinners.
            Platform.OS === "web" ? undefined : (
              <RefreshControl
                refreshing={loading && faturas.length > 0}
                onRefresh={fetchFaturas}
              />
            )
          }
          contentContainerStyle={styles.lista}
          ListEmptyComponent={
            loading ? (
              <ActivityIndicator size="large" color="#1a56db" style={{ marginTop: 60 }} />
            ) : (
              <Text style={styles.emptyText}>Nenhuma fatura neste mês</Text>
            )
          }
        />
      </View>

      {/* Modal de detalhes */}
      <Modal visible={modalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <TouchableOpacity
              style={styles.modalClose}
              onPress={() => {
                setModalVisible(false);
                setDetalhe(null);
              }}
            >
              <Ionicons name="close" size={24} color="#6b7280" />
            </TouchableOpacity>

            {actionLoading && !detalhe ? (
              <ActivityIndicator size="large" color="#1a56db" />
            ) : detalhe ? (
              <ScrollView showsVerticalScrollIndicator={false}>
                <Text style={styles.modalTitle}>Fatura</Text>
                <StatusBadge status={detalhe.status} />

                <View style={styles.detailRow}>
                  <Text style={styles.detailLabel}>ID</Text>
                  <Text style={styles.detailValue} selectable>
                    {detalhe.id}
                  </Text>
                </View>
                <View style={styles.detailRow}>
                  <Text style={styles.detailLabel}>Pagador</Text>
                  <Text style={styles.detailValue}>
                    {detalhe.payer_name || "—"}
                  </Text>
                </View>
                <View style={styles.detailRow}>
                  <Text style={styles.detailLabel}>CNPJ</Text>
                  <Text style={styles.detailValue}>
                    {detalhe.payer_cpf_cnpj || "—"}
                  </Text>
                </View>
                <View style={styles.detailRow}>
                  <Text style={styles.detailLabel}>Valor</Text>
                  <Text style={styles.detailValueBold}>
                    {detalhe.total || "—"}
                  </Text>
                </View>
                <View style={styles.detailRow}>
                  <Text style={styles.detailLabel}>Vencimento</Text>
                  <Text style={styles.detailValue}>
                    {detalhe.due_date || "—"}
                  </Text>
                </View>
                {detalhe.paid_at && (
                  <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>Pago em</Text>
                    <Text style={styles.detailValue}>{detalhe.paid_at}</Text>
                  </View>
                )}

                {/* NFS-e info */}
                {detalhe.nfse && (
                  <View style={styles.nfseBox}>
                    <Ionicons name="receipt" size={16} color="#7c3aed" />
                    <Text style={styles.nfseText}>
                      NFS-e: {detalhe.nfse.numero_nfse || "Emitida"}
                    </Text>
                  </View>
                )}

                {/* Ações */}
                <View style={styles.actions}>
                  {/* NF-e já emitida → Reenviar (independente do status) */}
                  {detalhe.nfse_emitida && (
                    <TouchableOpacity
                      style={[styles.actionBtn, { backgroundColor: "#0891b2" }]}
                      onPress={() => handleReenviarEmail(detalhe.id, true)}
                      disabled={actionLoading}
                    >
                      <Ionicons name="mail" size={16} color="#fff" />
                      <Text style={styles.actionText}>Reenviar NF-e</Text>
                    </TouchableOpacity>
                  )}
                  {/* Paga (Iugu ou baixa externa) sem NF-e → Gerar.
                      Só se a empresa emite NF-e (empresa_emite_nf !== false). */}
                  {(detalhe.status === "paid" ||
                    detalhe.status === "externally_paid") &&
                    !detalhe.nfse_emitida &&
                    detalhe.empresa_emite_nf !== false && (
                    <TouchableOpacity
                      style={[styles.actionBtn, { backgroundColor: "#7c3aed" }]}
                      onPress={() => handleEmitirNfse(detalhe.id)}
                      disabled={actionLoading}
                    >
                      <Ionicons name="receipt" size={16} color="#fff" />
                      <Text style={styles.actionText}>Gerar NFS-e</Text>
                    </TouchableOpacity>
                  )}
                  {/* Pendente → Baixa manual + Gerar NFS-e (manual) + Reenviar + Cancelar */}
                  {detalhe.status === "pending" && (
                    <>
                      <TouchableOpacity
                        style={[styles.actionBtn, { backgroundColor: "#059669" }]}
                        onPress={() => abrirBaixaManual(detalhe.id)}
                        disabled={actionLoading}
                      >
                        <Ionicons name="cash" size={16} color="#fff" />
                        <Text style={styles.actionText}>Dar baixa manual</Text>
                      </TouchableOpacity>
                      {/* Emissão MANUAL da NFS-e mesmo sem pagamento (empresa que emite). */}
                      {detalhe.empresa_emite_nf !== false && !detalhe.nfse_emitida && (
                        <TouchableOpacity
                          style={[styles.actionBtn, { backgroundColor: "#7c3aed" }]}
                          onPress={() => handleEmitirNfseManual(detalhe.id)}
                          disabled={actionLoading}
                        >
                          <Ionicons name="receipt" size={16} color="#fff" />
                          <Text style={styles.actionText}>Gerar NFS-e (manual)</Text>
                        </TouchableOpacity>
                      )}
                      <TouchableOpacity
                        style={[styles.actionBtn, { backgroundColor: "#0891b2" }]}
                        onPress={() => handleReenviarEmail(detalhe.id)}
                        disabled={actionLoading}
                      >
                        <Ionicons name="mail" size={16} color="#fff" />
                        <Text style={styles.actionText}>Reenviar E-mail</Text>
                      </TouchableOpacity>
                      <TouchableOpacity
                        style={[styles.actionBtn, { backgroundColor: "#dc2626" }]}
                        onPress={() => handleCancelar(detalhe.id)}
                        disabled={actionLoading}
                      >
                        <Ionicons name="close-circle" size={16} color="#fff" />
                        <Text style={styles.actionText}>Cancelar</Text>
                      </TouchableOpacity>
                    </>
                  )}
                </View>

                {actionLoading && (
                  <ActivityIndicator
                    style={{ marginTop: 16 }}
                    color="#1a56db"
                  />
                )}

                {/* Histórico da fatura (logs da Iugu) */}
                {Array.isArray(detalhe.logs) && detalhe.logs.length > 0 && (
                  <View style={{ marginTop: 22, borderTopWidth: 1, borderTopColor: "#f3f4f6", paddingTop: 14 }}>
                    <Text style={{ fontSize: 16, fontWeight: "700", color: "#111827", marginBottom: 12 }}>
                      Histórico
                    </Text>
                    {detalhe.logs.map((log: any, i: number) => (
                      <View key={i} style={{ flexDirection: "row", marginBottom: 12 }}>
                        <View
                          style={{
                            width: 8,
                            height: 8,
                            borderRadius: 4,
                            backgroundColor: "#1a56db",
                            marginTop: 5,
                            marginRight: 10,
                          }}
                        />
                        <View style={{ flex: 1 }}>
                          <Text style={{ fontSize: 14, color: "#111827" }}>
                            {log.description || "—"}
                          </Text>
                          {log.created_at ? (
                            <Text style={{ fontSize: 12, color: "#9ca3af", marginTop: 2 }}>
                              {log.created_at}
                            </Text>
                          ) : null}
                        </View>
                      </View>
                    ))}
                  </View>
                )}
              </ScrollView>
            ) : null}
          </View>
        </View>
      </Modal>

      {/* Modal aninhado: seletor de forma de pagamento (baixa manual) */}
      <Modal visible={baixaSelectVisible} animationType="fade" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.baixaSheet}>
            <Text style={styles.baixaTitle}>Dar baixa manual</Text>
            <Text style={styles.baixaSubtitle}>
              Selecione a forma de pagamento recebida
            </Text>
            {FORMAS_PAGAMENTO.map((forma) => (
              <TouchableOpacity
                key={forma}
                style={[styles.actionBtn, { backgroundColor: "#059669" }]}
                onPress={() => handleBaixaManual(forma)}
                disabled={actionLoading}
              >
                <Ionicons name="cash" size={16} color="#fff" />
                <Text style={styles.actionText}>{forma}</Text>
              </TouchableOpacity>
            ))}
            <TouchableOpacity
              style={[styles.actionBtn, { backgroundColor: "#6b7280" }]}
              onPress={() => {
                setBaixaSelectVisible(false);
                setBaixaInvoiceId(null);
              }}
              disabled={actionLoading}
            >
              <Ionicons name="close" size={16} color="#fff" />
              <Text style={styles.actionText}>Cancelar</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// ============================================================
// Styles
// ============================================================
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f3f4f6" },
  // Seletor de mês
  monthSelector: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: "#fff",
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#e5e7eb",
    flexShrink: 0,
  },
  monthArrow: { padding: 6 },
  monthCenter: { alignItems: "center" },
  monthLabel: { fontSize: 16, fontWeight: "700", color: "#111827" },
  monthCount: { fontSize: 11, color: "#6b7280", marginTop: 2 },
  // Busca
  searchBar: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#fff",
    margin: 12,
    marginBottom: 0,
    borderRadius: 10,
    paddingHorizontal: 12,
    elevation: 1,
    flexShrink: 0,
  },
  searchInput: { flex: 1, padding: 12, fontSize: 15 },
  // Filtros
  filtrosBar: { paddingHorizontal: 12, paddingVertical: 10, flexGrow: 0, flexShrink: 0 },
  filtrosContent: { alignItems: "center" },
  filtroChip: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: "#fff",
    marginRight: 8,
    elevation: 1,
  },
  filtroChipAtivo: { backgroundColor: "#1a56db" },
  filtroText: { color: "#374151", fontSize: 13, fontWeight: "500" },
  filtroTextAtivo: { color: "#fff" },
  // Lista
  lista: { padding: 12 },
  faturaCard: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
    elevation: 2,
  },
  faturaHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
  },
  faturaHeaderBadges: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    flexShrink: 0,
    marginLeft: 6,
  },
  nfseBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    backgroundColor: "#ecfdf5",
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 8,
  },
  nfseBadgeText: {
    fontSize: 10,
    fontWeight: "700",
    color: "#059669",
  },
  nfsePendenteBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    backgroundColor: "#fffbeb",
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 8,
  },
  nfsePendenteText: {
    fontSize: 10,
    fontWeight: "700",
    color: "#f59e0b",
  },
  nfseNaBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    backgroundColor: "#f3f4f6",
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 8,
  },
  nfseNaText: {
    fontSize: 10,
    fontWeight: "700",
    color: "#9ca3af",
  },
  faturaName: {
    fontSize: 14,
    fontWeight: "600",
    color: "#111827",
    flex: 1,
    marginRight: 6,
  },
  faturaBody: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 6,
  },
  faturaValor: { fontSize: 16, fontWeight: "bold", color: "#111827" },
  faturaDate: { fontSize: 12, color: "#6b7280" },
  faturaCnpj: { fontSize: 11, color: "#9ca3af", marginTop: 3 },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  badgeText: { fontSize: 12, fontWeight: "600" },
  emptyText: {
    textAlign: "center",
    color: "#9ca3af",
    marginTop: 40,
    fontSize: 15,
  },
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
    minHeight: 280,
  },
  modalClose: { alignSelf: "flex-end", padding: 4 },
  modalTitle: {
    fontSize: 22,
    fontWeight: "bold",
    color: "#111827",
    marginBottom: 12,
  },
  detailRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#f3f4f6",
  },
  detailLabel: { fontSize: 14, color: "#6b7280" },
  detailValue: {
    fontSize: 14,
    color: "#111827",
    maxWidth: "60%",
    textAlign: "right",
  },
  detailValueBold: { fontSize: 16, fontWeight: "bold", color: "#111827" },
  nfseBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "#f5f3ff",
    padding: 12,
    borderRadius: 8,
    marginTop: 16,
  },
  nfseText: { color: "#7c3aed", fontWeight: "500" },
  actions: { flexDirection: "column", gap: 10, marginTop: 20 },
  // Sheet do seletor de baixa manual
  baixaSheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    gap: 10,
  },
  baixaTitle: {
    fontSize: 20,
    fontWeight: "bold",
    color: "#111827",
  },
  baixaSubtitle: {
    fontSize: 14,
    color: "#6b7280",
    marginBottom: 6,
  },
  actionBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderRadius: 10,
  },
  actionText: { color: "#fff", fontWeight: "600", fontSize: 15 },
});
