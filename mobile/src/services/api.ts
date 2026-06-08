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
const TOKEN_KEY = "auth_token";

let _token: string | null = null;

function getToken(): string | null {
  return _token;
}

// Persiste o token no storage seguro da plataforma. Assíncrono.
async function saveToken(token: string): Promise<void> {
  _token = token;
  try {
    if (Platform.OS === "web") {
      window.localStorage.setItem(TOKEN_KEY, token);
    } else {
      await SecureStore.setItemAsync(TOKEN_KEY, token);
    }
  } catch (err) {
    // Se a persistência falhar, segue só com o cache em memória (não derruba o login).
    console.warn("saveToken: falha ao persistir token", err);
  }
}

// Remove o token da memória e do storage. Assíncrono.
async function clearToken(): Promise<void> {
  _token = null;
  try {
    if (Platform.OS === "web") {
      window.localStorage.removeItem(TOKEN_KEY);
    } else {
      await SecureStore.deleteItemAsync(TOKEN_KEY);
    }
  } catch (err) {
    // Limpeza best-effort: o cache em memória já foi zerado acima.
    console.warn("clearToken: falha ao remover token do storage", err);
  }
}

// Hidrata o cache em memória a partir do storage persistido.
// Chamado no boot do app (App.tsx) para restaurar a sessão após reload/F5.
export async function hydrateToken(): Promise<boolean> {
  try {
    if (Platform.OS === "web") {
      _token = window.localStorage.getItem(TOKEN_KEY);
    } else {
      _token = await SecureStore.getItemAsync(TOKEN_KEY);
    }
  } catch (err) {
    console.warn("hydrateToken: falha ao ler token do storage", err);
    _token = null;
  }
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
    const response = await fetch(`${BASE_URL}${path}`, {
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
