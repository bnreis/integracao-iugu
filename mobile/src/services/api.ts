/**
 * Serviço de comunicação com a API backend.
 *
 * Gerencia autenticação JWT, requisições e tratamento de erros.
 */

import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";

// Detecta se estamos no navegador (localhost) ou no celular (IP da rede)
const IS_WEB =
  typeof document !== "undefined" && typeof window !== "undefined";

// Web (painel servido pelo Apache em iugu.megasuporte.com): mesma origem → URLs relativas (/api, /auth)
// Nativo (APK): aponta direto para o backend de produção
const BASE_URL = IS_WEB ? "" : "https://iugu.megasuporte.com";

// ============================================================
// Token management — persistente e multiplataforma
//   • Nativo (APK/iOS): expo-secure-store (Keychain / EncryptedSharedPreferences)
//   • Web (painel): localStorage (SecureStore não existe no navegador)
// Mantém um cache em memória (`_token`) para que `request()` continue
// síncrono no caminho quente; a persistência é assíncrona.
// ============================================================
// ============================================================
// Multi-empresa (ADR-0007) — mesmo domínio, roteado por PREFIXO de caminho.
//   MegaSuporte -> ""          (backend :8000)
//   MegaTeam    -> "/megateam" (backend :8001, via Apache)
// Cada empresa tem seu próprio token (mesmo login nas duas). Selecionar a empresa
// = trocar o prefixo das URLs + o token ativo. Ao adicionar empresa nova, basta
// incluir aqui e subir a instância correspondente.
// ============================================================
export interface Tenant {
  id: string;
  nome: string;
  prefixo: string; // prefixo de caminho no mesmo domínio
}

// "Tenant" = nossa empresa emissora (MegaSuporte/MegaTeam). NÃO confundir com as
// "empresas" do domínio (os clientes que faturamos, em getEmpresas()).
export const TENANTS: Tenant[] = [
  { id: "megasuporte", nome: "MegaSuporte", prefixo: "" },
  { id: "megateam", nome: "MegaTeam", prefixo: "/megateam" },
];

const EMPRESA_KEY = "empresa_ativa";
let _empresaId: string = TENANTS[0].id;

export function getTenants(): Tenant[] {
  return TENANTS;
}
export function getEmpresaAtiva(): Tenant {
  return TENANTS.find((e) => e.id === _empresaId) || TENANTS[0];
}
export function getEmpresaAtivaId(): string {
  return _empresaId;
}

// Chave de token POR empresa (mantém as duas sessões em paralelo).
function tokenKey(id: string = _empresaId): string {
  return `auth_token_${id}`;
}

let _token: string | null = null;

function getToken(): string | null {
  return _token;
}

// Storage multiplataforma (web: localStorage | nativo: SecureStore).
async function _persist(key: string, val: string): Promise<void> {
  try {
    if (Platform.OS === "web") window.localStorage.setItem(key, val);
    else await SecureStore.setItemAsync(key, val);
  } catch (err) {
    console.warn(`persist(${key}): falha`, err);
  }
}
async function _read(key: string): Promise<string | null> {
  try {
    if (Platform.OS === "web") return window.localStorage.getItem(key);
    return await SecureStore.getItemAsync(key);
  } catch (err) {
    console.warn(`read(${key}): falha`, err);
    return null;
  }
}
async function _remove(key: string): Promise<void> {
  try {
    if (Platform.OS === "web") window.localStorage.removeItem(key);
    else await SecureStore.deleteItemAsync(key);
  } catch (err) {
    console.warn(`remove(${key}): falha`, err);
  }
}

// Define a empresa ativa, persiste e carrega o token dela (se já houver sessão).
// Retorna true se já existe token válido em cache para a empresa selecionada.
export async function setEmpresaAtiva(id: string): Promise<boolean> {
  if (!TENANTS.some((e) => e.id === id)) return false;
  _empresaId = id;
  await _persist(EMPRESA_KEY, id);
  _token = await _read(tokenKey(id));
  return !!_token;
}

// Persiste o token da EMPRESA ATIVA.
async function saveToken(token: string): Promise<void> {
  _token = token;
  await _persist(tokenKey(), token);
}

// Remove o token só da empresa ativa (as outras sessões permanecem).
async function clearToken(): Promise<void> {
  const k = tokenKey();
  _token = null;
  await _remove(k);
}

// Hidrata empresa ativa + token no boot (App.tsx), restaurando a sessão após reload/F5.
export async function hydrateToken(): Promise<boolean> {
  const savedEmpresa = await _read(EMPRESA_KEY);
  if (savedEmpresa && TENANTS.some((e) => e.id === savedEmpresa)) {
    _empresaId = savedEmpresa;
  }
  _token = await _read(tokenKey());
  return !!_token;
}

// ============================================================
// HTTP helpers
// ============================================================
interface ApiResponse<T = any> {
  data?: T;
  error?: string;
  status: number;
}

async function request<T = any>(
  method: string,
  path: string,
  body?: any,
  requireAuth: boolean = true
): Promise<ApiResponse<T>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (requireAuth) {
    const token = getToken();
    if (!token) {
      return { error: "Não autenticado", status: 401 };
    }
    headers["Authorization"] = `Bearer ${token}`;
  }

  try {
    // Roteia por empresa: mesmo domínio, prefixo de caminho (ex.: "/megateam").
    const prefixo = getEmpresaAtiva().prefixo;
    const response = await fetch(`${BASE_URL}${prefixo}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store", // sempre busca dados frescos (sem cache do navegador/PWA)
    });

    const data = await response.json().catch(() => null);

    if (response.status === 401) {
      await clearToken();
      return { error: "Sessão expirada — faça login novamente", status: 401 };
    }

    if (!response.ok) {
      return {
        error: data?.detail || data?.error || `Erro ${response.status}`,
        status: response.status,
      };
    }

    return { data, status: response.status };
  } catch (err: any) {
    return {
      error: `Falha de conexão: ${err.message || "Servidor indisponível"}`,
      status: 0,
    };
  }
}

// ============================================================
// AUTH
// ============================================================
export async function login(
  usuario: string,
  senha: string
): Promise<{ success: boolean; error?: string }> {
  const res = await request(
    "POST",
    "/auth/login",
    { usuario, senha },
    false
  );
  if (res.data?.access_token) {
    await saveToken(res.data.access_token);
    return { success: true };
  }
  return { success: false, error: res.error || "Falha no login" };
}

export async function logout(): Promise<void> {
  await clearToken();
}

// ============================================================
// DASHBOARD
// ============================================================
export async function getDashboard(data?: string) {
  const query = data ? `?data=${data}` : "";
  return request("GET", `/api/dashboard${query}`);
}

// ============================================================
// FATURAS
// ============================================================
export async function getFaturas(params?: {
  status?: string;
  limite?: number;
  pagina?: number;
  busca?: string;
  created_from?: string;
  created_to?: string;
}) {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.limite) searchParams.set("limite", String(params.limite));
  if (params?.pagina) searchParams.set("pagina", String(params.pagina));
  if (params?.busca) searchParams.set("busca", params.busca);
  if (params?.created_from) searchParams.set("created_from", params.created_from);
  if (params?.created_to) searchParams.set("created_to", params.created_to);
  const query = searchParams.toString();
  return request("GET", `/api/faturas${query ? `?${query}` : ""}`);
}

export async function getFatura(id: string) {
  return request("GET", `/api/faturas/${id}`);
}

export async function criarFatura(dados: {
  cnpj: string;
  valor_cents: number;
  descricao: string;
  dias_vencimento?: number;
  observacoes?: string;
}) {
  return request("POST", "/api/faturas", dados);
}

export async function cancelarFatura(id: string) {
  return request("POST", `/api/faturas/${id}/cancel`);
}

export async function darBaixaManual(invoiceId: string, formaPagamento: string) {
  return request("POST", `/api/faturas/${invoiceId}/baixa-manual`, {
    forma_pagamento: formaPagamento,
  });
}

// ============================================================
// NFS-e
// ============================================================
export async function getNfseList(limite?: number) {
  const query = limite ? `?limite=${limite}` : "";
  return request("GET", `/api/nfse${query}`);
}

export async function emitirNfse(invoiceId: string) {
  return request("POST", `/api/nfse/${invoiceId}/emitir`);
}

// Emissão MANUAL: gera a NFS-e mesmo com a fatura ainda NÃO paga (decisão do operador).
export async function emitirNfseManual(invoiceId: string) {
  return request("POST", `/api/nfse/${invoiceId}/emitir-manual`);
}

// Marca a fatura como JÁ tendo NF-e emitida (sem emitir agora). Usado quando a nota
// foi emitida por uma fatura anterior (cancelada+recriada): bloqueia a reemissão.
export async function marcarNfseEmitida(
  invoiceId: string,
  numeroNfse?: string
) {
  return request("POST", `/api/nfse/${invoiceId}/marcar-emitida`, {
    numero_nfse: numeroNfse || "",
  });
}

export async function reenviarNfseEmail(invoiceId: string) {
  return request("POST", `/api/nfse/${invoiceId}/reenviar`);
}

// ============================================================
// EMPRESAS
// ============================================================
export async function getEmpresas(apenasAtivas: boolean = true) {
  return request("GET", `/api/empresas?apenas_ativas=${apenasAtivas}`);
}

export async function getEmpresa(cnpj: string) {
  return request("GET", `/api/empresas/${cnpj}`);
}

export async function editarEmpresa(cnpj: string, dados: Record<string, any>) {
  return request("PUT", `/api/empresas/${cnpj}`, dados);
}

export async function cadastrarEmpresa(dados: {
  cnpj: string;
  razao_social: string;
  email: string;
  codigo_servico?: string;
  descricao_servico?: string;
  aliquota_iss?: number;
  emitir_nf?: boolean;
  nf_na_criacao?: boolean;
  descricao_boleto?: string;
  valor_fatura?: string;
  dia_criacao_fatura?: number;
  observacoes?: string;
}) {
  return request("POST", "/api/empresas", dados);
}

export async function excluirEmpresa(cnpj: string) {
  return request("DELETE", `/api/empresas/${cnpj}`);
}

export { BASE_URL };
