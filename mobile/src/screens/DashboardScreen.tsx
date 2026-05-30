import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  TouchableOpacity,
  Platform,
  ActivityIndicator,
  Alert,
  Dimensions,
  useWindowDimensions,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { getDashboard, emitirNfse, cancelarFatura } from "../services/api";

// ============================================================
// Helpers
// ============================================================
/** Converte data ISO ou "YYYY-MM-DD ..." para "dd/mm/yyyy" */
function formatDateBR(raw?: string | null): string {
  if (!raw) return "—";
  // Tenta parsear — aceita ISO, "YYYY-MM-DD HH:MM", etc.
  const d = new Date(raw);
  if (isNaN(d.getTime())) {
    // fallback: tenta extrair manualmente "YYYY-MM-DD"
    const m = raw.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (m) return `${m[3]}/${m[2]}/${m[1]}`;
    return raw;
  }
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
}

// ============================================================
// Types
// ============================================================
interface DashboardData {
  data_referencia: string;
  mes_referencia: string;
  hoje: {
    criadas: number;
    valor_criado: string;
    pagas: number;
    valor_pago: string;
    nfse_emitidas: number;
    nfse_erros: number;
  };
  mes: {
    criadas: number;
    valor_criado: string;
    valor_criado_cents: number;
    pagas: number;
    valor_pago: string;
    valor_pago_cents: number;
    taxa_conversao: number;
    nfse_emitidas: number;
    nfse_erros: number;
  };
  pendencias: {
    faturas_pendentes: number;
    valor_pendente: string;
    faturas_vencidas: number;
    valor_vencido: string;
    nfse_pendentes: number;
    top_vencidas: Array<{
      invoice_id: string;
      payer_name: string;
      total: string;
      due_date: string;
    }>;
    top_nfse_pendentes: Array<{
      invoice_id: string;
      payer_name: string;
      total: string;
      paid_at: string;
    }>;
  };
  empresas_ativas: number;
  ambiente_nfse: string;
  dry_run: boolean;
}

// ============================================================
// KPI Card
// ============================================================
function KpiCard({
  icon,
  label,
  value,
  sublabel,
  color,
  small,
}: {
  icon: string;
  label: string;
  value: string | number;
  sublabel?: string;
  color: string;
  small?: boolean;
}) {
  return (
    <View style={[styles.kpiCard, { borderLeftColor: color }, small && styles.kpiCardSmall]}>
      <View style={styles.kpiHeader}>
        <Ionicons name={icon as any} size={small ? 16 : 20} color={color} />
        <Text style={[styles.kpiLabel, small && { fontSize: 11 }]}>{label}</Text>
      </View>
      <Text style={[styles.kpiValue, { color }, small && { fontSize: 20 }]}>{value}</Text>
      {sublabel && <Text style={styles.kpiSub}>{sublabel}</Text>}
    </View>
  );
}

// ============================================================
// Alert Card (pendências)
// ============================================================
function AlertCard({
  icon,
  label,
  count,
  valor,
  color,
  items,
  itemLabel,
}: {
  icon: string;
  label: string;
  count: number;
  valor: string;
  color: string;
  items?: Array<{
    line1: string;
    line2: string;
    actionLabel?: string;
    actionIcon?: string;
    actionColor?: string;
    onAction?: () => void;
  }>;
  itemLabel?: string;
}) {
  if (count === 0) return null;
  return (
    <View style={[styles.alertCard, { borderLeftColor: color }]}>
      <View style={styles.alertHeader}>
        <Ionicons name={icon as any} size={20} color={color} />
        <View style={{ flex: 1, marginLeft: 10 }}>
          <Text style={[styles.alertTitle, { color }]}>{count} {label}</Text>
          <Text style={styles.alertValor}>Total: R$ {valor}</Text>
        </View>
      </View>
      {items && items.length > 0 && (
        <View style={styles.alertItems}>
          {itemLabel && <Text style={styles.alertItemLabel}>{itemLabel}</Text>}
          {items.map((item, i) => (
            <View key={i} style={styles.alertItemRow}>
              <View style={styles.alertItemInfo}>
                <Text style={styles.alertItemName} numberOfLines={1}>{item.line1}</Text>
                <Text style={styles.alertItemDetail}>{item.line2}</Text>
              </View>
              {item.onAction && (
                <TouchableOpacity
                  style={[styles.alertActionBtn, { backgroundColor: item.actionColor || color }]}
                  onPress={item.onAction}
                >
                  {item.actionIcon && (
                    <Ionicons name={item.actionIcon as any} size={13} color="#fff" />
                  )}
                  <Text style={styles.alertActionText}>{item.actionLabel || "Ação"}</Text>
                </TouchableOpacity>
              )}
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

// ============================================================
// Progress Bar
// ============================================================
function ProgressBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <View style={styles.progressContainer}>
      <View style={styles.progressHeader}>
        <Text style={styles.progressLabel}>{label}</Text>
        <Text style={[styles.progressPct, { color }]}>{pct.toFixed(0)}%</Text>
      </View>
      <View style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>
    </View>
  );
}

// ============================================================
// Responsive helpers
// ============================================================
/** Retorna true se a tela é "pequena" (smartphone típico ≤ 420px) */
function useIsSmallScreen() {
  const { width } = useWindowDimensions();
  return width < 420;
}

// ============================================================
// Main Component
// ============================================================
export default function DashboardScreen() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError("");
    const res = await getDashboard();
    if (res.data) {
      setData(res.data);
    } else {
      setError(res.error || "Erro ao carregar dashboard");
    }
    setLoading(false);
  }, []);

  // ── Helper de confirmação (funciona tanto na web quanto no mobile) ──
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

  // ── Ações ──
  const handleEmitirNfse = (invoiceId: string, payerName: string) => {
    confirmar(
      "Emitir NFS-e",
      `Gerar nota fiscal para "${payerName}"?\n\nFatura: ${invoiceId}`,
      async () => {
        setActionLoading(invoiceId);
        const res = await emitirNfse(invoiceId);
        setActionLoading(null);
        if (res.data?.success) {
          alertMsg("Sucesso", `NFS-e: ${res.data.acao || "processada"}`);
          fetchData();
        } else {
          alertMsg("Erro", res.data?.error || res.error || "Falha ao emitir NFS-e");
        }
      },
    );
  };

  const handleCancelarFatura = (invoiceId: string, payerName: string) => {
    confirmar(
      "Cancelar fatura",
      `Cancelar fatura vencida de "${payerName}"?\n\nID: ${invoiceId}`,
      async () => {
        setActionLoading(invoiceId);
        const res = await cancelarFatura(invoiceId);
        setActionLoading(null);
        if (res.data) {
          alertMsg("Sucesso", "Fatura cancelada");
          fetchData();
        } else {
          alertMsg("Erro", res.error || "Falha ao cancelar");
        }
      },
    );
  };

  const isSmall = useIsSmallScreen();

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const hoje = new Date().toLocaleDateString("pt-BR", {
    weekday: "long",
    day: "2-digit",
    month: "long",
    year: "numeric",
  });

  if (loading && !data) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#1a56db" />
        <Text style={styles.loadingText}>Carregando dashboard...</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.container}
      refreshControl={
        <RefreshControl refreshing={loading} onRefresh={fetchData} />
      }
    >
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.greeting}>Dashboard</Text>
        <Text style={styles.date}>{hoje}</Text>
      </View>

      {/* Erro */}
      {error ? (
        <View style={styles.errorBox}>
          <Ionicons name="warning" size={20} color="#dc2626" />
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity onPress={fetchData}>
            <Text style={styles.retryText}>Tentar novamente</Text>
          </TouchableOpacity>
        </View>
      ) : null}

      {/* Banners */}
      {data?.dry_run && (
        <View style={styles.dryRunBanner}>
          <Ionicons name="flask" size={16} color="#92400e" />
          <Text style={styles.dryRunText}>
            NFS-e em modo DRY-RUN (teste) — Ambiente: {data.ambiente_nfse}
          </Text>
        </View>
      )}

      {data && (
        <>
          {/* ── SEÇÃO: HOJE ── */}
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Hoje</Text>
          </View>

          <View style={[styles.kpiGrid, isSmall && styles.kpiGrid2col]}>
            <View style={isSmall ? styles.kpiHalf : styles.kpiFlex}>
              <KpiCard
                icon="document-text"
                label="Criadas"
                value={data.hoje.criadas}
                sublabel={`R$ ${data.hoje.valor_criado}`}
                color="#1a56db"
                small
              />
            </View>
            <View style={isSmall ? styles.kpiHalf : styles.kpiFlex}>
              <KpiCard
                icon="checkmark-circle"
                label="Pagas"
                value={data.hoje.pagas}
                sublabel={`R$ ${data.hoje.valor_pago}`}
                color="#059669"
                small
              />
            </View>
            <View style={isSmall ? styles.kpiHalf : styles.kpiFlex}>
              <KpiCard
                icon="receipt"
                label="NFS-e"
                value={data.hoje.nfse_emitidas}
                sublabel={data.hoje.nfse_erros > 0 ? `${data.hoje.nfse_erros} erro(s)` : "Sem erros"}
                color={data.hoje.nfse_erros > 0 ? "#dc2626" : "#7c3aed"}
                small
              />
            </View>
            <View style={isSmall ? styles.kpiHalf : styles.kpiFlex}>
              <KpiCard
                icon="business"
                label="Empresas"
                value={data.empresas_ativas}
                color="#0891b2"
                small
              />
            </View>
          </View>

          {/* ── SEÇÃO: MÊS ── */}
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Resumo do mês</Text>
          </View>

          <View style={[styles.kpiGrid, isSmall && styles.kpiGridMesSmall]}>
            <View style={isSmall ? styles.kpiMesItemSmall : styles.kpiFlex}>
              <KpiCard
                icon="trending-up"
                label="Faturado"
                value={`R$ ${data.mes.valor_criado}`}
                sublabel={`${data.mes.criadas} fatura(s)`}
                color="#1a56db"
              />
            </View>
            <View style={isSmall ? styles.kpiMesItemSmall : styles.kpiFlex}>
              <KpiCard
                icon="wallet"
                label="Recebido"
                value={`R$ ${data.mes.valor_pago}`}
                sublabel={`${data.mes.pagas} pagamento(s)`}
                color="#059669"
              />
            </View>
            <View style={isSmall ? { width: "100%" } : styles.kpiFlex}>
              <KpiCard
                icon="cash"
                label="A receber"
                value={`R$ ${data.pendencias.valor_pendente}`}
                sublabel={`${data.pendencias.faturas_pendentes} pendente(s)`}
                color={data.pendencias.faturas_vencidas > 0 ? "#dc2626" : "#f59e0b"}
              />
            </View>
          </View>

          {/* Barra de conversão */}
          <View style={styles.progressCard}>
            <ProgressBar
              label="Taxa de conversão (criadas → pagas)"
              value={data.mes.taxa_conversao}
              max={100}
              color={data.mes.taxa_conversao >= 80 ? "#059669" : data.mes.taxa_conversao >= 50 ? "#f59e0b" : "#dc2626"}
            />
            <View style={styles.progressStats}>
              <Text style={styles.progressStatText}>
                NFS-e emitidas: {data.mes.nfse_emitidas}
              </Text>
              {data.mes.nfse_erros > 0 && (
                <Text style={[styles.progressStatText, { color: "#dc2626" }]}>
                  Erros: {data.mes.nfse_erros}
                </Text>
              )}
            </View>
          </View>

          {/* ── SEÇÃO: PENDÊNCIAS ── */}
          {(data.pendencias.faturas_vencidas > 0 ||
            data.pendencias.faturas_pendentes > 0 ||
            data.pendencias.nfse_pendentes > 0) && (
            <>
              <View style={styles.sectionHeader}>
                <Ionicons name="alert-circle" size={18} color="#dc2626" />
                <Text style={[styles.sectionTitle, { color: "#dc2626", marginLeft: 6 }]}>
                  Ações necessárias
                </Text>
              </View>

              {/* Faturas vencidas */}
              <AlertCard
                icon="time"
                label="fatura(s) vencida(s)"
                count={data.pendencias.faturas_vencidas}
                valor={data.pendencias.valor_vencido}
                color="#dc2626"
                itemLabel="Mais antigas:"
                items={data.pendencias.top_vencidas.map((v) => ({
                  line1: v.payer_name,
                  line2: `R$ ${v.total} — Venc: ${formatDateBR(v.due_date)}`,
                  actionLabel: actionLoading === v.invoice_id ? "..." : "Cancelar",
                  actionIcon: "close-circle",
                  actionColor: "#dc2626",
                  onAction: () => handleCancelarFatura(v.invoice_id, v.payer_name),
                }))}
              />

              {/* NFS-e pendentes */}
              <AlertCard
                icon="document-attach"
                label="NFS-e pendente(s)"
                count={data.pendencias.nfse_pendentes}
                valor={data.pendencias.top_nfse_pendentes
                  .reduce((acc, x) => acc, "—")}
                color="#f59e0b"
                itemLabel="Pagamentos sem NFS-e:"
                items={data.pendencias.top_nfse_pendentes.map((n) => ({
                  line1: n.payer_name,
                  line2: `R$ ${n.total} — Pago: ${formatDateBR(n.paid_at)}`,
                  actionLabel: actionLoading === n.invoice_id ? "..." : "Emitir NFS-e",
                  actionIcon: "receipt",
                  actionColor: "#7c3aed",
                  onAction: () => handleEmitirNfse(n.invoice_id, n.payer_name),
                }))}
              />

            </>
          )}

          {/* ── Tudo OK ── */}
          {data.pendencias.faturas_vencidas === 0 &&
           data.pendencias.nfse_pendentes === 0 && (
            <View style={styles.allGoodBanner}>
              <Ionicons name="checkmark-circle" size={20} color="#059669" />
              <Text style={styles.allGoodText}>
                Tudo em dia — sem pendências!
              </Text>
            </View>
          )}

          {/* Espaço final */}
          <View style={{ height: 24 }} />
        </>
      )}
    </ScrollView>
  );
}

// ============================================================
// Styles
// ============================================================
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f3f4f6" },
  loadingContainer: { flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: "#f3f4f6" },
  loadingText: { marginTop: 12, color: "#6b7280", fontSize: 15 },

  header: { padding: 16, paddingTop: 10 },
  greeting: { fontSize: 22, fontWeight: "bold", color: "#111827" },
  date: { fontSize: 13, color: "#6b7280", marginTop: 3 },

  // Sections
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 8,
  },
  sectionTitle: { fontSize: 16, fontWeight: "700", color: "#374151" },

  // KPI Grid — mobile-first (2 colunas em smartphone, row em desktop)
  kpiGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    paddingHorizontal: 12,
    gap: 10,
  },
  kpiGrid2col: {
    // No mobile: 2 colunas (cada item ~48%)
  },
  kpiGridMesSmall: {
    // Mês: 2 + 1 no mobile
  },
  kpiFlex: {
    flex: 1,
    minWidth: 120,
  },
  kpiHalf: {
    width: "47%",
  },
  kpiMesItemSmall: {
    width: "47%",
  },
  kpiCard: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 14,
    borderLeftWidth: 4,
    elevation: 2,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.08,
    shadowRadius: 4,
  },
  kpiCardSmall: {
    padding: 10,
  },
  kpiHeader: { flexDirection: "row", alignItems: "center", gap: 5, marginBottom: 4 },
  kpiLabel: { fontSize: 11, color: "#6b7280" },
  kpiValue: { fontSize: 22, fontWeight: "bold" },
  kpiSub: { fontSize: 10, color: "#9ca3af", marginTop: 2 },

  // Progress
  progressCard: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 16,
    marginHorizontal: 12,
    marginTop: 10,
    elevation: 2,
  },
  progressContainer: { marginBottom: 8 },
  progressHeader: { flexDirection: "row", justifyContent: "space-between", marginBottom: 6 },
  progressLabel: { fontSize: 13, color: "#374151", fontWeight: "500" },
  progressPct: { fontSize: 14, fontWeight: "bold" },
  progressTrack: {
    height: 10,
    backgroundColor: "#e5e7eb",
    borderRadius: 5,
    overflow: "hidden",
  },
  progressFill: { height: "100%", borderRadius: 5 },
  progressStats: { flexDirection: "row", justifyContent: "space-between", marginTop: 8 },
  progressStatText: { fontSize: 12, color: "#6b7280" },

  // Alert Cards
  alertCard: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 16,
    marginHorizontal: 12,
    marginTop: 10,
    borderLeftWidth: 4,
    elevation: 2,
  },
  alertHeader: { flexDirection: "row", alignItems: "center" },
  alertTitle: { fontSize: 15, fontWeight: "700" },
  alertValor: { fontSize: 13, color: "#6b7280", marginTop: 2 },
  alertItems: { marginTop: 12, borderTopWidth: 1, borderTopColor: "#f3f4f6", paddingTop: 10 },
  alertItemLabel: { fontSize: 11, color: "#9ca3af", marginBottom: 6, fontWeight: "600", textTransform: "uppercase" },
  alertItemRow: {
    flexDirection: "column",
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#f9fafb",
    gap: 6,
  },
  alertItemInfo: {
    flex: 1,
  },
  alertItemName: { fontSize: 13, color: "#374151" },
  alertItemDetail: { fontSize: 12, color: "#6b7280", marginTop: 2 },
  alertActionBtn: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
  },
  alertActionText: {
    color: "#fff",
    fontSize: 12,
    fontWeight: "600",
  },

  // All good
  allGoodBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#ecfdf5",
    margin: 12,
    marginTop: 16,
    padding: 16,
    borderRadius: 10,
  },
  allGoodText: { color: "#059669", fontSize: 14, fontWeight: "600" },

  // Banners
  dryRunBanner: {
    marginHorizontal: 12,
    padding: 12,
    backgroundColor: "#fef3c7",
    borderRadius: 8,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  dryRunText: { color: "#92400e", fontSize: 13 },

  // Error
  errorBox: {
    margin: 16,
    padding: 16,
    backgroundColor: "#fef2f2",
    borderRadius: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  errorText: { color: "#dc2626", flex: 1 },
  retryText: { color: "#1a56db", fontWeight: "600" },
});
