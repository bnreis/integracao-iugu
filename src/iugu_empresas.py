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
from dataclasses import dataclass
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
        "emitir_nf", "nf_na_criacao",
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
                # Passo 1: listar IDs de todos os customers
                todos_ids: list[str] = []
                start = 0
                while True:
                    result = client.list_customers(limit=100, start=start)
                    items = result.get("items", [])
                    if not items:
                        break
                    for item in items:
                        cust_id = item.get("id")
                        if cust_id:
                            todos_ids.append(cust_id)
                    total = result.get("totalItems", 0)
                    start += len(items)
                    if start >= total:
                        break

                # Passo 2: buscar cada customer individualmente (traz notes)
                logger.debug(f"[CARREGAR debug] Buscando {len(todos_ids)} customers individualmente...")
                for cust_id in todos_ids:
                    try:
                        cust = client.get_customer(cust_id)
                        notes_raw = cust.get("notes", "")
                        logger.debug(f"[CARREGAR debug] Customer {cust.get('name','?')}: notes={str(notes_raw)[:100]!r}")
                        emp = customer_para_empresa(cust)
                        if emp:
                            self._empresas[emp.cnpj] = emp
                    except IuguAPIError as e:
                        logger.warning(f"Erro ao buscar customer {cust_id}: {e.message}")
        except IuguAPIError as e:
            logger.error(f"Erro ao carregar customers da Iugu: {e.message}")
            raise

        self._carregada = True
        logger.info(f"Iugu carregada: {len(self._empresas)} empresas (CNPJ)")

    def buscar_por_cnpj(self, cnpj: str) -> Optional[Empresa]:
        """Retorna a empresa pelo CNPJ (so ativas). None se nao encontrada."""
        self.carregar()
        empresa = self._empresas.get(normalizar_cnpj(cnpj))
        if empresa and empresa.ativo:
            return empresa
        return None

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
        """Busca empresa pelo ID do customer na Iugu."""
        self.carregar()
        for emp in self._empresas.values():
            if emp.customer_id == customer_id and emp.ativo:
                return emp
        return None
