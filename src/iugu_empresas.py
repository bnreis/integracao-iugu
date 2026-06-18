"""
Gerenciamento de empresas usando a Iugu como fonte unica de dados.

Substitui o antigo spreadsheet.py -- a interface publica e identica
(Empresa dataclass, EmpresasRepository, funcoes utilitarias).

Dados de negocio (codigo_servico, aliquota_iss, emitir_nf, etc.) sao
armazenados como JSON no campo 'notes' do customer Iugu. Dados nativos
(name, email, cpf_cnpj, endereco) usam os campos proprios da API.

Formato do campo notes:
  {"codigo_servico": "01.07", "aliquota_iss": 2.0, "emitir_nf": true, ...}

Mapeamento de campos:
  Iugu nativo         -> Empresa
  cpf_cnpj            -> cnpj
  name                -> razao_social
  email               -> email
  zip_code/street/... -> campos de endereco

  notes (JSON)        -> Empresa
  codigo_servico      -> codigo_servico
  descricao_servico   -> descricao_servico
  aliquota_iss        -> aliquota_iss (float)
  emitir_nf           -> emitir_nf (bool)
  nf_na_criacao       -> nf_na_criacao (bool)
  descricao_boleto    -> descricao_boleto
  valor_fatura        -> valor_fatura (str BR)
  dia_criacao_fatura  -> dia_criacao_fatura (int)
  ativo               -> ativo (bool)
  observacoes         -> observacoes (str)
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .config import settings
from .iugu_client import IuguClient, IuguAPIError


# ============================================================
# Dataclass Empresa (mesma interface do antigo spreadsheet.py)
# ============================================================
@dataclass
class Empresa:
    """Representa uma empresa cadastrada no sistema (NFS-e + boleto recorrente)."""

    cnpj: str
    razao_social: str = ""
    email: str = ""
    codigo_servico: str = ""
    descricao_servico: str = ""
    aliquota_iss: float = 0.0
    emitir_nf: bool = True
    nf_na_criacao: bool = False
    iss_retido: bool = False        # tomador substituto tributário → ISSRetido=1
    inscricao_municipal: str = ""   # IM do tomador (exigida na retenção: E039/L006)
    descricao_boleto: str = ""
    valor_fatura: str = ""          # formato BR: "1850,00"
    dia_criacao_fatura: int = 0     # 1-31 (0 = desabilitado)
    observacoes: str = ""
    ativo: bool = True

    # Campos de endereco (vem do customer Iugu)
    zip_code: str = ""
    street: str = ""
    number: str = ""
    city: str = ""
    state: str = ""
    district: str = ""
    complement: str = ""

    # ID do customer na Iugu
    customer_id: str = ""

    # Colunas para compatibilidade com to_dict()
    _COLUNAS_LEGADO = [
        "cnpj", "razao_social", "email",
        "codigo_servico", "descricao_servico", "aliquota_iss",
        "emitir_nf", "nf_na_criacao", "iss_retido", "inscricao_municipal",
        "descricao_boleto", "valor_fatura", "dia_criacao_fatura",
        "observacoes", "ativo",
    ]

    def to_dict(self) -> dict:
        """Retorna dict com os campos legados (compativel com o antigo spreadsheet)."""
        return {c: getattr(self, c) for c in self._COLUNAS_LEGADO}

    def to_dict_completo(self) -> dict:
        """Retorna dict com todos os campos, incluindo endereco e customer_id."""
        d = self.to_dict()
        d.update({
            "zip_code": self.zip_code,
            "street": self.street,
            "number": self.number,
            "city": self.city,
            "state": self.state,
            "district": self.district,
            "complement": self.complement,
            "customer_id": self.customer_id,
        })
        return d

    @property
    def valor_fatura_cents(self) -> int:
        """Converte 'valor_fatura' (BR: '1850,00') para centavos (int)."""
        return parse_valor_br_to_cents(self.valor_fatura)

    def tem_boleto_recorrente(self) -> bool:
        """True se a empresa tem valor e dia configurados para cobranca automatica."""
        return self.valor_fatura_cents > 0 and 1 <= self.dia_criacao_fatura <= 31


# ============================================================
# Funcoes utilitarias (mesmas do antigo spreadsheet.py)
# ============================================================
def parse_valor_br_to_cents(valor) -> int:
    """
    Converte valor no formato BR para centavos.

    Aceita:
        "1850,00"       -> 185000
        "1.850,00"      -> 185000
        "R$ 1.850,00"   -> 185000
        1850.00 (float) -> 185000
        0, "", None     -> 0
    """
    if valor is None or valor == "":
        return 0
    if isinstance(valor, (int, float)):
        return int(round(float(valor) * 100))
    s = str(valor).strip()
    if not s:
        return 0
    s = s.replace("R$", "").replace("r$", "").replace(" ", "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        logger.warning(f"Valor invalido ao converter: {valor!r}")
        return 0


def format_cents_to_br(cents: int) -> str:
    """Formato inverso. 185000 -> '1.850,00'."""
    if cents <= 0:
        return "0,00"
    reais, centavos = divmod(cents, 100)
    reais_str = f"{reais:,}".replace(",", ".")
    return f"{reais_str},{centavos:02d}"


def normalizar_cnpj(cnpj: str) -> str:
    """Remove tudo que nao e digito e retorna apenas numeros."""
    if not cnpj:
        return ""
    return "".join(filter(str.isdigit, str(cnpj)))


# ============================================================
# Leitura/escrita do campo notes (JSON)
# ============================================================
def _parse_notes_json(notes: str | None) -> dict:
    """Tenta parsear o campo notes como JSON. Retorna dict vazio se falhar."""
    if not notes:
        return {}
    notes = notes.strip()
    if not notes.startswith("{"):
        return {}
    try:
        return json.loads(notes)
    except (json.JSONDecodeError, ValueError):
        return {}


def empresa_para_notes_json(emp: Empresa) -> str:
    """Converte campos de negocio da Empresa para JSON (campo notes da Iugu)."""
    dados = {
        "codigo_servico": str(emp.codigo_servico or ""),
        "descricao_servico": str(emp.descricao_servico or ""),
        "aliquota_iss": float(emp.aliquota_iss),
        "emitir_nf": bool(emp.emitir_nf),
        "nf_na_criacao": bool(emp.nf_na_criacao),
        "iss_retido": bool(emp.iss_retido),
        "inscricao_municipal": str(emp.inscricao_municipal or ""),
        "descricao_boleto": str(emp.descricao_boleto or ""),
        "valor_fatura": str(emp.valor_fatura or ""),
        "dia_criacao_fatura": int(emp.dia_criacao_fatura),
        "ativo": bool(emp.ativo),
        "observacoes": str(emp.observacoes or ""),
    }
    return json.dumps(dados, ensure_ascii=False)


# ============================================================
# Conversao customer Iugu -> Empresa
# ============================================================
def customer_para_empresa(customer: dict[str, Any]) -> Empresa | None:
    """
    Converte um customer da Iugu para a dataclass Empresa.
    Retorna None se o customer nao tiver CNPJ valido (14 digitos).
    Le os campos de negocio do campo notes (JSON).
    """
    cpf_cnpj = customer.get("cpf_cnpj") or ""
    cnpj = normalizar_cnpj(cpf_cnpj)
    if len(cnpj) != 14:
        return None  # So aceita CNPJ (14 digitos), ignora CPF

    # Parse do campo notes (JSON com dados de negocio)
    dados = _parse_notes_json(customer.get("notes"))

    # Aliquota ISS: float
    try:
        aliquota = float(dados.get("aliquota_iss", 0) or 0)
    except (ValueError, TypeError):
        aliquota = 0.0

    # Dia criacao fatura: int 1-31
    try:
        dia = int(float(dados.get("dia_criacao_fatura", 0) or 0))
    except (ValueError, TypeError):
        dia = 0
    if not (0 <= dia <= 31):
        dia = 0

    # Booleans
    def _bool(val, padrao=False):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "sim", "1", "yes")
        return padrao

    return Empresa(
        cnpj=cnpj,
        razao_social=customer.get("name") or "",
        email=customer.get("email") or "",
        codigo_servico=str(dados.get("codigo_servico", "")),
        descricao_servico=str(dados.get("descricao_servico", "")),
        aliquota_iss=aliquota,
        emitir_nf=_bool(dados.get("emitir_nf"), padrao=True),
        nf_na_criacao=_bool(dados.get("nf_na_criacao"), padrao=False),
        iss_retido=_bool(dados.get("iss_retido"), padrao=False),
        inscricao_municipal=str(dados.get("inscricao_municipal", "") or ""),
        descricao_boleto=str(dados.get("descricao_boleto", "")),
        valor_fatura=str(dados.get("valor_fatura", "")),
        dia_criacao_fatura=dia,
        observacoes=str(dados.get("observacoes", "")),
        ativo=_bool(dados.get("ativo"), padrao=True),
        # Endereco
        zip_code=customer.get("zip_code") or "",
        street=customer.get("street") or "",
        number=customer.get("number") or "",
        city=customer.get("city") or "",
        state=customer.get("state") or "",
        district=customer.get("district") or "",
        complement=customer.get("complement") or "",
        # ID Iugu
        customer_id=customer.get("id") or "",
    )


# ============================================================
# Repositorio (mesma interface do antigo EmpresasRepository)
# ============================================================
# Para compatibilidade com antigos imports
COLUNAS = Empresa._COLUNAS_LEGADO


# ============================================================
# Registro local de customer_ids (contorno da listagem quebrada da Iugu)
# ============================================================
# A listagem /v1/customers da Iugu passou a devolver incompleta (1 cliente), mas o
# GET por ID funciona. Mantemos um registro persistido dos customer_ids conhecidos:
# o carregar() le esse registro + as fontes da API e busca cada cliente por ID; os
# IDs resolvidos sao re-gravados (auto-cura). Semeado por scripts/seed_customer_ids.py.
# Some naturalmente quando a Iugu corrigir a listagem.
_REGISTRO_IDS_PATH = Path(settings.nfse_output_dir) / "registro_customer_ids.json"


def _ler_registro_customer_ids() -> set[str]:
    """Le o registro persistido de customer_ids. Conjunto vazio se ausente/invalido."""
    try:
        data = json.loads(_REGISTRO_IDS_PATH.read_text(encoding="utf-8-sig"))
        return {str(x) for x in data.get("customer_ids", []) if x}
    except Exception:
        return set()


def _salvar_registro_customer_ids(ids: set[str]) -> None:
    """Grava o registro de customer_ids (best-effort; nao derruba o fluxo)."""
    try:
        _REGISTRO_IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRO_IDS_PATH.write_text(
            json.dumps({"customer_ids": sorted(ids)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Falha ao salvar registro de customer_ids: {e}")


class EmpresasRepository:
    """
    Le e consulta empresas usando a Iugu como fonte de dados.

    Interface identica ao antigo EmpresasRepository (spreadsheet.py):
      - carregar(forcar=True)
      - buscar_por_cnpj(cnpj) -> Empresa | None (so ativas)
      - listar_ativas() -> list[Empresa]
      - empresas_com_boleto_recorrente() -> list[Empresa]
      - existe(cnpj) -> bool
    """

    def __init__(self):
        self._empresas: dict[str, Empresa] = {}
        self._carregada = False

    def carregar(self, forcar: bool = False) -> None:
        """Carrega todos os customers da Iugu e converte para Empresa.
        Faz GET individual de cada customer para ter acesso ao campo notes
        (list_customers nao retorna notes).
        """
        if self._carregada and not forcar:
            return

        self._empresas.clear()

        try:
            with IuguClient() as client:
                # Passo 1: coletar os IDs de TODOS os customers.
                # ⚠️ A listagem /v1/customers da Iugu pode vir INCOMPLETA (bug
                # observado: devolve só 1 cliente, mesmo com ~17 na conta e o GET
                # por ID funcionando). Por isso, além dela, coletamos os
                # customer_id das FATURAS (list_invoices lista normalmente) — assim
                # todo cliente com fatura entra, mesmo com a listagem quebrada.
                ids: set[str] = set()

                # Registro local (contorno da listagem quebrada): IDs ja conhecidos.
                ids |= _ler_registro_customer_ids()

                start = 0
                while True:
                    result = client.list_customers(limit=100, start=start)
                    items = result.get("items", [])
                    if not items:
                        break
                    for item in items:
                        cust_id = item.get("id")
                        if cust_id:
                            ids.add(cust_id)
                    total = result.get("totalItems", 0)
                    start += len(items)
                    if start >= total:
                        break

                # Enumeração por BUSCA (contorno da listagem base quebrada): a
                # listagem sem filtro devolve só 1, mas /v1/customers?query=<termo>
                # FUNCIONA. Buscamos por VOGAIS (cobrem ~todos os nomes PT-BR) em
                # paralelo e unimos os IDs — rápido (não bloqueia o worker) e sem o
                # ruído/lentidão das faturas (404 de clientes antigos). Cobertura
                # mais ampla (a-z) fica no scripts/seed_customer_ids.py.
                def _query_ids(termo: str) -> set[str]:
                    try:
                        r = client.list_customers(query=termo, limit=100)
                        return {i.get("id") for i in r.get("items", []) if i.get("id")}
                    except Exception:  # noqa: BLE001
                        return set()

                with ThreadPoolExecutor(max_workers=5) as ex:
                    for s in ex.map(_query_ids, ["a", "e", "i", "o", "u"]):
                        ids |= s

                todos_ids: list[str] = list(ids)

                # Passo 2: buscar cada customer individualmente (traz notes).
                # Em paralelo para reduzir o tempo total — o list_customers nao
                # retorna notes, entao e preciso 1 GET por cliente.
                logger.debug(f"[CARREGAR] Buscando {len(todos_ids)} customers em paralelo...")

                def _fetch(cust_id: str) -> Optional[dict]:
                    try:
                        return client.get_customer(cust_id)
                    except IuguAPIError as e:
                        # 404 é esperado: customer_id de FATURA ANTIGA cujo cliente
                        # foi recriado/excluído na Iugu — só ruído, não erro real.
                        if getattr(e, "status_code", None) == 404:
                            logger.debug(f"customer {cust_id} inexistente (404) — ID de fatura antiga")
                        else:
                            logger.warning(f"Erro ao buscar customer {cust_id}: {e.message}")
                        return None

                if todos_ids:
                    with ThreadPoolExecutor(max_workers=8) as executor:
                        for cust in executor.map(_fetch, todos_ids):
                            if not cust:
                                continue
                            emp = customer_para_empresa(cust)
                            if emp and emp.customer_id:
                                # Indexado por customer_id (chave unica): um mesmo
                                # CNPJ pode ter varios clientes/departamentos distintos.
                                self._empresas[emp.customer_id] = emp
        except IuguAPIError as e:
            logger.error(f"Erro ao carregar customers da Iugu: {e.message}")
            raise

        self._carregada = True
        # Auto-cura: persiste os IDs resolvidos no registro (uniao com o existente),
        # para a próxima carga já trazer todos por ID mesmo com a listagem quebrada.
        try:
            _salvar_registro_customer_ids(
                set(self._empresas.keys()) | _ler_registro_customer_ids()
            )
        except Exception:  # noqa: BLE001
            pass
        logger.info(f"Iugu carregada: {len(self._empresas)} empresas (CNPJ)")

    def buscar_por_cnpj(self, cnpj: str) -> Optional[Empresa]:
        """Retorna a PRIMEIRA empresa ativa com este CNPJ. None se nao houver.

        Atencao: um CNPJ pode ter varios clientes/departamentos. Para precisao,
        use buscar_por_customer_id(). Este metodo existe para compatibilidade.
        """
        self.carregar()
        alvo = normalizar_cnpj(cnpj)
        for emp in self._empresas.values():
            if emp.cnpj == alvo and emp.ativo:
                return emp
        return None

    def listar_por_cnpj(self, cnpj: str) -> list[Empresa]:
        """Retorna TODAS as empresas (departamentos) ativas com este CNPJ."""
        self.carregar()
        alvo = normalizar_cnpj(cnpj)
        return [e for e in self._empresas.values() if e.cnpj == alvo and e.ativo]

    def listar_ativas(self) -> list[Empresa]:
        """Retorna todas as empresas ativas."""
        self.carregar()
        return [e for e in self._empresas.values() if e.ativo]

    def empresas_com_boleto_recorrente(self) -> list[Empresa]:
        """Retorna empresas ativas com valor e dia configurados para cobranca."""
        self.carregar()
        return [e for e in self._empresas.values() if e.ativo and e.tem_boleto_recorrente()]

    def existe(self, cnpj: str) -> bool:
        """Verifica se um CNPJ esta cadastrado (e ativo)."""
        return self.buscar_por_cnpj(cnpj) is not None

    def buscar_por_customer_id(self, customer_id: str) -> Optional[Empresa]:
        """Busca empresa pelo customer_id (chave unica). None se nao existir.

        Retorna mesmo se inativa (para edicao/consulta direta de um departamento).

        Fallback ON-DEMAND: se o customer_id nao estiver no cache, busca o customer
        DIRETO por ID na Iugu (get_customer). Isso torna a resolucao robusta a falhas
        da listagem /v1/customers (que ja devolveu incompleta — so 1 cliente), pois o
        GET por ID funciona mesmo quando a listagem falha. Garante que a emissao via
        webhook/painel sempre resolva a empresa pelo customer_id da fatura.
        """
        self.carregar()
        emp = self._empresas.get(customer_id)
        if emp is not None:
            return emp
        if not customer_id:
            return None
        try:
            with IuguClient() as client:
                cust = client.get_customer(customer_id)
            emp = customer_para_empresa(cust)
            if emp and emp.customer_id:
                self._empresas[emp.customer_id] = emp  # cacheia p/ proximas consultas
                # Auto-cura: registra o ID p/ aparecer nas proximas cargas/lista.
                try:
                    _salvar_registro_customer_ids(
                        {emp.customer_id} | _ler_registro_customer_ids()
                    )
                except Exception:  # noqa: BLE001
                    pass
                logger.info(
                    f"[on-demand] Empresa resolvida por customer_id direto: "
                    f"{emp.razao_social} ({emp.customer_id})"
                )
                return emp
        except IuguAPIError as e:
            logger.warning(
                f"[on-demand] Falha ao buscar customer {customer_id} por ID: {e.message}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[on-demand] Erro inesperado buscando {customer_id}: {e}")
        return None


# ============================================================
# Cache compartilhado (modulo) — evita reler a Iugu a cada request
# ============================================================
_repo_cache: Optional["EmpresasRepository"] = None
_repo_cache_ts: float = 0.0
_REPO_CACHE_TTL = 300.0  # 5 minutos


def get_repo(forcar: bool = False) -> "EmpresasRepository":
    """Repositorio de empresas compartilhado e cacheado em memoria.

    Evita reler todos os customers da Iugu (1 GET por cliente) a cada
    requisicao. Expira em _REPO_CACHE_TTL segundos. As rotas de escrita
    (cadastrar/editar/excluir) chamam invalidar_cache() para refletir
    mudancas imediatamente.
    """
    global _repo_cache, _repo_cache_ts
    agora = time.monotonic()
    if _repo_cache is None or forcar or (agora - _repo_cache_ts) > _REPO_CACHE_TTL:
        repo = EmpresasRepository()
        repo.carregar(forcar=True)
        _repo_cache = repo
        _repo_cache_ts = agora
    return _repo_cache


def invalidar_cache() -> None:
    """Descarta o cache — a proxima leitura recarrega da Iugu."""
    global _repo_cache, _repo_cache_ts
    _repo_cache = None
    _repo_cache_ts = 0.0
