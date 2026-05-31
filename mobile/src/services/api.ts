/**
 * Serviço de comunicação com a API backend.
 *
 * Gerencia autenticação JWT, requisições e tratamento de erros.
 */

// Detecta se estamos no navegador (localhost) ou no celular (IP da rede)
const IS_WEB =
  typeof document !== "undefined" && typeof window !== "undefined";

// Web (painel servido pelo Apache em iugu.megasuporte.com): mesma origem → URLs relativas (/api, /auth)
// Nativo (APK): aponta direto para o backend de produção
const BASE_URL = IS_WEB ? "" : "https://iugu.megasuporte.com";

// ============================================================
// Token management — memória simples (funciona em todas plataformas)
// No celular, futuramente pode usar SecureStore após build nativo
// ============================================================
let _token: string | null = null;

function getToken(): string | null {
  return _token;
}

function saveToken(token: string): void {
  _token = token;
}

function clearToken(): void {
  _token = null;
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
      clearToken();
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
    saveToken(res.data.access_token);
    return { success: true };
  }
  return { success: false, error: res.error || "Falha no login" };
}

export async function logout(): Promise<void> {
  clearToken();
}

export function isAuthenticated(): boolean {
  return !!getToken();
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
