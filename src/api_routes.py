"""
Endpoints da API de gestao -- consumidos pelo app Android.

Rotas:
  POST /auth/login              -> autenticacao (retorna JWT)
  GET  /api/dashboard           -> resumo do dia
  GET  /api/faturas             -> listar faturas (com filtros)
  GET  /api/faturas/{id}        -> detalhes de uma fatura
  POST /api/faturas             -> criar fatura manual
  POST /api/faturas/{id}/cancel -> cancelar fatura pendente
  POST /api/faturas/{id}/baixa-manual -> baixa manual (externally_pay) + auto-emite NFS-e
  GET  /api/nfse                -> listar NFS-e emitidas
  POST /api/nfse/{invoice_id}/emitir   -> emitir/gerar NFS-e
  POST /api/nfse/{invoice_id}/reenviar -> reenviar e-mail da NFS-e
  POST /api/empresas            -> cadastrar nova empresa
  GET  /api/empresas            -> listar empresas
  GET  /api/empresas/{cnpj}     -> detalhes de uma empresa
  PUT  /api/empresas/{cnpj}     -> editar empresa
  DELETE /api/empresas/{cnpj}   -> excluir empresa

Todas as rotas /api/* exigem autenticacao JWT (header Authorization: Bearer <token>).

MIGRADO: fonte de dados e agora 100% Iugu (sem planilha).
Dados de negocio ficam como JSON no campo notes do customer.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field as PydField

from .auth import (
    LoginRequest,
    LoginResponse,
    UsuarioAutenticado,
    client_ip,
    login,
    usuario_autenticado,
)
from .config import settings
from .iugu_client import IuguAPIError, IuguClient
from .iugu_empresas import (
    COLUNAS,
    Empresa,
    EmpresasRepository,
    empresa_para_notes_json,
    format_cents_to_br,
    get_repo,
    invalidar_cache,
    normalizar_cnpj,
)

# ============================================================
# Routers
# ============================================================
auth_router = APIRouter(prefix="/auth", tags=["Autenticacao"])
api_router = APIRouter(
    prefix="/api",
    tags=["Gestao"],
    dependencies=[Depends(usuario_autenticado)],
)


# ============================================================
# VALIDACAO DE PATH PARAMS
# ============================================================
# Formato do ID de fatura da Iugu: alfanumerico + hifen, ~32 chars (hex do UUID
# sem tracos, ou variantes). Restringimos a [A-Za-z0-9-]{16,64} para rejeitar
# logo na borda qualquer valor com "/", "..", espaco ou caractere de controle
# antes que ele alcance a API da Iugu ou o nome de um lockfile (path traversal).
_RE_INVOICE_ID = re.compile(r"^[A-Za-z0-9-]{16,64}$")


def _validar_invoice_id(invoice_id: str) -> str:
    """Valida o formato do invoice_id recebido como path param.

    Rejeita com 422 qualquer ID fora do padrao da Iugu. Defesa de borda: impede
    que valores maliciosos (path traversal, injecao) cheguem ao client da Iugu ou
    componham caminhos de arquivo. Retorna o proprio ID para uso encadeado.
    """
    if not isinstance(invoice_id, str) or not _RE_INVOICE_ID.match(invoice_id):
        raise HTTPException(422, "invoice_id inválido")
    return invoice_id


# ============================================================
# AUTH
# ============================================================
@auth_router.post("/login", response_model=LoginResponse)
async def endpoint_login(credenciais: LoginRequest, request: Request):
    """Autentica e retorna um token JWT (rate-limited por IP)."""
    return login(credenciais, ip=client_ip(request))


# ============================================================
# DASHBOARD
# ============================================================
def _primeiro_e_ultimo_dia_mes(data_ref: date):
    """Retorna o primeiro e ultimo dia do mes da data_ref."""
    primeiro = data_ref.replace(day=1)
    if data_ref.month == 12:
        ultimo = data_ref.replace(year=data_ref.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        ultimo = data_ref.replace(month=data_ref.month + 1, day=1) - timedelta(days=1)
    return primeiro, ultimo


def _contar_nfse_periodo(nfse_dir: Path, data_inicio: date, data_fim: date):
    """Conta NFS-e emitidas e erros num periodo a partir dos LOGS reais.

    Conta apenas emissões reais — arquivos `nfse_<invoice_id>.json` (gravados só
    em emissão bem-sucedida), pela `data_emissao`. NÃO varre `dps_*`/`rps_*`, que
    incluem dry-runs e artefatos de teste e inflavam o número (achado A-09).
    """
    emitidas = 0
    erros = 0
    if not nfse_dir.exists():
        return emitidas, erros
    for f in nfse_dir.glob("nfse_*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        de = str(data.get("data_emissao") or "")[:10]  # aceita "YYYY-MM-DD" ou ISO
        try:
            d = date.fromisoformat(de) if de else None
        except ValueError:
            d = None
        if d is None or not (data_inicio <= d <= data_fim):
            continue
        if data.get("sucesso"):
            emitidas += 1
        else:
            erros += 1
    return emitidas, erros


def _empresa_emite_nf(repo: Any, fatura: dict) -> bool:
    """True se a empresa da fatura está marcada para emitir NF-e (emitir_nf=True).

    Resolve a empresa por customer_id (canônico, ADR-0003) com fallback por CNPJ.
    Sem repo, ou empresa não encontrada → False. Empresa que NÃO emite não deve
    aparecer como pendente nem com badge "s/ NF-e" (vale p/ emissão manual e no
    pagamento). Lookups são em cache (get_repo) — baratos por fatura.
    """
    if repo is None:
        return False
    emp = None
    cid = fatura.get("customer_id")
    if cid:
        emp = repo.buscar_por_customer_id(cid)
    if emp is None:
        from .iugu_client import extract_cnpj_from_invoice
        cnpj = extract_cnpj_from_invoice(fatura)
        if cnpj:
            emp = repo.buscar_por_cnpj(cnpj)
    return bool(emp and emp.emitir_nf)


@api_router.get("/dashboard")
async def dashboard(
    data: Optional[str] = Query(None, description="Data no formato YYYY-MM-DD (default: hoje)"),
):
    """
    Dashboard gerencial: visao do dia + do mes + pendencias acionaveis.
    Data de corte: so considera faturas criadas a partir de 01/03/2026.
    """
    data_ref = date.fromisoformat(data) if data else date.today()
    primeiro_mes, ultimo_mes = _primeiro_e_ultimo_dia_mes(data_ref)
    tz = "-03:00"

    DATA_CORTE = date(2026, 3, 1)
    data_corte_iso = f"{DATA_CORTE}T00:00:00{tz}"

    try:
        with IuguClient() as client:
            criadas_hoje = client.list_invoices(
                created_at_from=f"{data_ref}T00:00:00{tz}",
                created_at_to=f"{data_ref}T23:59:59{tz}",
                limit=100,
            )
            pagas_hoje = client.list_invoices(
                paid_at_from=f"{data_ref}T00:00:00{tz}",
                paid_at_to=f"{data_ref}T23:59:59{tz}",
                limit=100,
            )

            inicio_mes = max(primeiro_mes, DATA_CORTE)
            criadas_mes = client.list_invoices(
                created_at_from=f"{inicio_mes}T00:00:00{tz}",
                created_at_to=f"{ultimo_mes}T23:59:59{tz}",
                limit=100,
            )
            pagas_mes = client.list_invoices(
                paid_at_from=f"{inicio_mes}T00:00:00{tz}",
                paid_at_to=f"{ultimo_mes}T23:59:59{tz}",
                limit=100,
            )

            pendentes = client.list_invoices(
                status="pending",
                created_at_from=data_corte_iso,
                limit=100,
            )
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro ao consultar Iugu: {e.message}")

    # "Faturado"/"criadas" desconsidera CANCELADAS (boleto cancelado nao e
    # faturamento real, e nao deve entrar na taxa de conversao).
    # Nota: nao ha checagem explicita de =="paid" no dashboard — as "pagas" vem de
    # filtros por intervalo de paid_at repassados crus a Iugu (que NAO conhece o
    # valor "externally_paid" como filtro), entao baixas manuais ja entram nas
    # pagas via paid_at sem ajuste aqui. O unico filtro de status local e
    # "!= canceled", que ja trata externally_paid corretamente como nao-cancelada.
    def _nao_cancelada(f):
        return f.get("status") != "canceled"

    items_criadas_hoje = [f for f in criadas_hoje.get("items", []) if _nao_cancelada(f)]
    items_pagas_hoje = pagas_hoje.get("items", [])
    items_criadas_mes = [f for f in criadas_mes.get("items", []) if _nao_cancelada(f)]
    items_pagas_mes = pagas_mes.get("items", [])
    items_pendentes = pendentes.get("items", [])

    # VENCIDA = fatura pendente cuja DATA DE VENCIMENTO REAL (da fatura gerada, não
    # do cadastro) JÁ PASSOU. Regra: due_date < hoje — vencimento no PRÓPRIO dia
    # ainda está no prazo (não é pendência). Calculado por fatura, então reflete
    # corretamente faturas reemitidas com nova data de vencimento.
    def _esta_vencida(f: dict) -> bool:
        dd = str(f.get("due_date") or "")[:10]
        try:
            return date.fromisoformat(dd) < data_ref
        except ValueError:
            return False

    items_vencidas = [f for f in items_pendentes if _esta_vencida(f)]

    def soma_cents(items, campo="total_cents"):
        return sum(int(f.get(campo) or f.get("total_cents") or 0) for f in items)

    total_criado_hoje = soma_cents(items_criadas_hoje)
    total_pago_hoje = soma_cents(items_pagas_hoje, "total_paid_cents")
    total_criado_mes = soma_cents(items_criadas_mes)
    total_pago_mes = soma_cents(items_pagas_mes, "total_paid_cents")
    total_pendente = soma_cents(items_pendentes)
    total_vencido = soma_cents(items_vencidas)

    # Quantidade de criadas = sem canceladas (usa a lista ja filtrada)
    qtd_criadas_mes = len(items_criadas_mes)
    qtd_pagas_mes = pagas_mes.get("totalItems", len(items_pagas_mes))
    taxa_conversao = round((qtd_pagas_mes / qtd_criadas_mes * 100), 1) if qtd_criadas_mes > 0 else 0.0

    nfse_dir = Path(settings.nfse_output_dir)
    nfse_hoje, nfse_erros_hoje = _contar_nfse_periodo(nfse_dir, max(data_ref, DATA_CORTE), data_ref)
    nfse_mes, nfse_erros_mes = _contar_nfse_periodo(nfse_dir, max(primeiro_mes, DATA_CORTE), ultimo_mes)

    # NFS-e pendentes
    nfse_pendentes = 0
    nfse_pendentes_list = []
    mapa_nfse = _carregar_mapa_nfse()
    nfse_arquivos = set()
    if nfse_dir.exists():
        for f in nfse_dir.glob("dps_*"):
            nfse_arquivos.add(f.name)

    # Repo das empresas (via Iugu, cacheado) — resolvido aqui para checar emitir_nf.
    try:
        repo = get_repo()
    except Exception:
        repo = None

    for fatura in items_pagas_mes:
        fatura_id = fatura.get("id", "")
        # Evidencia confiavel = log de emissao real (nfse_<invoice_id>.json) por
        # invoice_id. A custom_variable nfse_emitida_na_criacao foi REMOVIDA da
        # deteccao: era gravada na criacao do boleto mesmo sem emissao real,
        # gerando falso-positivo em faturas antigas.
        tem_nfse = fatura_id in mapa_nfse or any(fatura_id in nome for nome in nfse_arquivos)
        # Só é "pendente de NF-e" se a empresa REALMENTE emite NF-e. Empresa com
        # emitir_nf=False (ex.: MEGATEAM) nunca deve aparecer como pendente.
        if not tem_nfse and _empresa_emite_nf(repo, fatura):
            nfse_pendentes += 1
            if len(nfse_pendentes_list) < 5:
                nfse_pendentes_list.append({
                    "invoice_id": fatura_id,
                    "payer_name": fatura.get("payer_name") or fatura.get("email") or "---",
                    "total": fatura.get("total"),
                    "paid_at": fatura.get("paid_at"),
                })

    # Empresas ativas (reaproveita o repo já resolvido acima)
    try:
        total_empresas = len(repo.listar_ativas()) if repo else 0
    except Exception:
        total_empresas = 0

    top_vencidas = []
    for f in sorted(items_vencidas, key=lambda x: x.get("due_date", ""))[:5]:
        top_vencidas.append({
            "invoice_id": f.get("id"),
            "payer_name": f.get("payer_name") or f.get("email") or "---",
            "total": f.get("total"),
            "due_date": f.get("due_date"),
        })

    return {
        "data_referencia": data_ref.isoformat(),
        "mes_referencia": f"{primeiro_mes.isoformat()} a {ultimo_mes.isoformat()}",
        "hoje": {
            "criadas": len(items_criadas_hoje),
            "valor_criado": format_cents_to_br(total_criado_hoje),
            "pagas": len(items_pagas_hoje),
            "valor_pago": format_cents_to_br(total_pago_hoje),
            "nfse_emitidas": nfse_hoje,
            "nfse_erros": nfse_erros_hoje,
        },
        "mes": {
            "criadas": qtd_criadas_mes,
            "valor_criado": format_cents_to_br(total_criado_mes),
            "valor_criado_cents": total_criado_mes,
            "pagas": qtd_pagas_mes,
            "valor_pago": format_cents_to_br(total_pago_mes),
            "valor_pago_cents": total_pago_mes,
            "taxa_conversao": taxa_conversao,
            "nfse_emitidas": nfse_mes,
            "nfse_erros": nfse_erros_mes,
        },
        "pendencias": {
            "faturas_pendentes": pendentes.get("totalItems", len(items_pendentes)),
            "valor_pendente": format_cents_to_br(total_pendente),
            "faturas_vencidas": len(items_vencidas),
            "valor_vencido": format_cents_to_br(total_vencido),
            "nfse_pendentes": nfse_pendentes,
            "top_vencidas": top_vencidas,
            "top_nfse_pendentes": nfse_pendentes_list,
        },
        "empresas_ativas": total_empresas,
        "ambiente_nfse": settings.nfse_ambiente,
        "dry_run": settings.nfse_dry_run,
    }


# ============================================================
# FATURAS
# ============================================================
class CriarFaturaRequest(BaseModel):
    cnpj: str = PydField(..., description="CNPJ da empresa (so numeros ou formatado)")
    valor_cents: int = PydField(..., gt=0, description="Valor em centavos")
    descricao: str = PydField("Servico mensal", description="Descricao do item")
    dias_vencimento: int = PydField(10, ge=1, le=90, description="Dias ate o vencimento")
    observacoes: Optional[str] = PydField(
        None, description="Observacoes que aparecem na fatura para o cliente (opcional)"
    )


@api_router.get("/faturas")
async def listar_faturas(
    status_filter: Optional[str] = Query(None, alias="status", description="pending, paid, canceled, expired"),
    limite: int = Query(20, ge=1, le=100),
    pagina: int = Query(0, ge=0, description="Offset para paginacao"),
    busca: Optional[str] = Query(None, description="Busca textual"),
    created_from: Optional[str] = Query(None, description="Data inicio (YYYY-MM-DD)"),
    created_to: Optional[str] = Query(None, description="Data fim (YYYY-MM-DD)"),
):
    """Lista faturas da Iugu com filtros."""
    # Valida as datas (YYYY-MM-DD) e converte para o formato ISO com timezone que
    # a Iugu espera (mesmo padrão usado em dashboard()). Datas inválidas viravam
    # um 502 opaco vindo da Iugu; agora retornam 422 com mensagem clara.
    tz = "-03:00"

    def _validar_data(valor: Optional[str], campo: str, fim_do_dia: bool) -> Optional[str]:
        if not valor:
            return None
        try:
            d = date.fromisoformat(valor.strip())
        except ValueError:
            raise HTTPException(
                422, f"{campo} invalido: '{valor}'. Use o formato YYYY-MM-DD."
            )
        sufixo = "T23:59:59" if fim_do_dia else "T00:00:00"
        return f"{d.isoformat()}{sufixo}{tz}"

    created_from_iso = _validar_data(created_from, "created_from", fim_do_dia=False)
    created_to_iso = _validar_data(created_to, "created_to", fim_do_dia=True)

    try:
        with IuguClient() as client:
            result = client.list_invoices(
                status=status_filter,
                limit=limite,
                start=pagina * limite,
                query=busca,
                created_at_from=created_from_iso,
                created_at_to=created_to_iso,
            )
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro Iugu: {e.message}")

    items = result.get("items", [])
    mapa_nfse = _carregar_mapa_nfse()
    logger.debug(f"[NFS-e check] Logs locais: {len(mapa_nfse)}")

    # Repo cacheado para checar emitir_nf por fatura (badge "N/A" vs "s/ NF-e").
    try:
        repo = get_repo()
    except Exception:
        repo = None

    def _check_nfse(fatura: dict) -> bool:
        # Detecta NFS-e por EVIDENCIA da propria fatura, nunca pelo flag
        # nf_na_criacao da empresa: o flag diz que a empresa DEVERIA emitir na
        # criacao, mas nao prova que esta fatura especifica tem nota. Confiar
        # no flag marcava ate faturas pendentes/sem nota como "emitida".
        fatura_id = fatura.get("id", "")
        # Evidencia confiavel = log de emissao real por invoice_id
        # (nfse_<invoice_id>.json), gravado SO em emissao bem-sucedida. A
        # custom_variable nfse_emitida_na_criacao foi REMOVIDA da deteccao: era
        # setada na criacao mesmo sem emissao real (falso-positivo em antigas).
        return fatura_id in mapa_nfse

    return {
        "total": result.get("totalItems", len(items)),
        "pagina": pagina,
        "limite": limite,
        "faturas": [
            {
                "id": f.get("id"),
                "email": f.get("email"),
                "status": f.get("status"),
                "total": f.get("total"),
                "total_cents": f.get("total_cents"),
                "due_date": f.get("due_date"),
                "paid_at": f.get("paid_at"),
                "secure_url": f.get("secure_url"),
                "payer_name": f.get("payer_name"),
                "payer_cpf_cnpj": f.get("payer_cpf_cnpj"),
                "created_at": f.get("created_at_iso"),
                "nfse_emitida": _check_nfse(f),
                "empresa_emite_nf": _empresa_emite_nf(repo, f),
            }
            for f in items
        ],
    }


@api_router.get("/faturas/{invoice_id}")
async def detalhe_fatura(invoice_id: str):
    """Detalhes completos de uma fatura."""
    _validar_invoice_id(invoice_id)
    try:
        with IuguClient() as client:
            invoice = client.get_invoice(invoice_id)
    except IuguAPIError as e:
        if e.status_code == 404:
            raise HTTPException(404, "Fatura nao encontrada")
        raise HTTPException(502, f"Erro Iugu: {e.message}")

    # Detecta NFS-e SO por evidencia de emissao real: log local por invoice_id
    # (nfse_<invoice_id>.json). A custom_variable nfse_emitida_na_criacao foi
    # REMOVIDA da deteccao (era gravada na criacao mesmo sem emissao real,
    # gerando falso-positivo), e nunca usamos o flag nf_na_criacao da empresa.
    nfse_info = _buscar_nfse_da_fatura(invoice_id)
    nfse_emitida = nfse_info is not None

    # Flag emitir_nf da empresa (para o app não exibir "s/ NF-e"/"Gerar" quando a
    # empresa não emite). Resiliente: se falhar, assume True (não esconde ações).
    try:
        empresa_emite_nf = _empresa_emite_nf(get_repo(), invoice)
    except Exception:
        empresa_emite_nf = True

    bank_slip = invoice.get("bank_slip") or {}
    pix = invoice.get("pix") or {}

    return {
        "id": invoice.get("id"),
        "status": invoice.get("status"),
        "email": invoice.get("email"),
        "total": invoice.get("total"),
        "total_cents": invoice.get("total_cents"),
        "total_paid_cents": invoice.get("total_paid_cents"),
        "due_date": invoice.get("due_date"),
        "paid_at": invoice.get("paid_at"),
        "created_at": invoice.get("created_at_iso"),
        "secure_url": invoice.get("secure_url"),
        "payer_name": invoice.get("payer_name"),
        "payer_cpf_cnpj": invoice.get("payer_cpf_cnpj"),
        "items": invoice.get("items", []),
        "boleto_linha_digitavel": bank_slip.get("digitable_line"),
        "pix_qrcode": pix.get("qrcode_text"),
        "custom_variables": invoice.get("custom_variables", []),
        "logs": [
            {
                "description": l.get("description"),
                "notes": l.get("notes"),
                "created_at": l.get("created_at"),
            }
            for l in (invoice.get("logs") or [])
        ],
        "nfse": nfse_info,
        "nfse_emitida": nfse_emitida,
        "empresa_emite_nf": empresa_emite_nf,
    }


@api_router.post("/faturas")
async def criar_fatura(req: CriarFaturaRequest):
    """Cria uma fatura manual para uma empresa cadastrada."""
    repo = get_repo()
    cnpj_limpo = normalizar_cnpj(req.cnpj)
    empresa = repo.buscar_por_cnpj(cnpj_limpo)

    if not empresa:
        raise HTTPException(404, f"CNPJ {req.cnpj} nao encontrado no cadastro")

    due_date = date.today() + timedelta(days=req.dias_vencimento)
    metodos = [m.strip() for m in settings.fatura_metodos_pagamento.split(",") if m.strip()]

    payload = {
        "email": empresa.email or "",
        "due_date": due_date,
        "items": [{
            "description": req.descricao,
            "quantity": 1,
            "price_cents": req.valor_cents,
        }],
        # Repassa o customer_id da empresa resolvida para que invoice.customer_id
        # chegue preenchido no webhook (se ausente, create_invoice não o envia).
        "customer_id": empresa.customer_id,
        "payer": {
            "cpf_cnpj": empresa.cnpj,
            "name": empresa.razao_social or "Cliente",
        },
        "payable_with": metodos,
        "expires_in": settings.fatura_dias_expiracao,
        "bank_slip_extra_due": settings.fatura_boleto_extra_due,
        "fines": settings.fatura_multa_atraso_percentual > 0,
        "late_payment_fine": int(settings.fatura_multa_atraso_percentual),
        "per_day_interest": settings.fatura_juros_por_dia,
        "custom_variables": [
            {"name": "origem", "value": "app_mobile"},
            {"name": "cnpj_tomador", "value": empresa.cnpj},
        ],
    }

    # Observações: campo nativo da Iugu que aparece na fatura para o cliente.
    if req.observacoes and req.observacoes.strip():
        payload["observations"] = req.observacoes.strip()

    try:
        with IuguClient() as client:
            invoice = client.create_invoice(**payload)
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro Iugu: [{e.status_code}] {e.message}")

    bank_slip = invoice.get("bank_slip") or {}
    pix = invoice.get("pix") or {}

    return {
        "sucesso": True,
        "id": invoice.get("id"),
        "status": invoice.get("status"),
        "secure_url": invoice.get("secure_url"),
        "boleto_linha_digitavel": bank_slip.get("digitable_line"),
        "pix_qrcode": pix.get("qrcode_text"),
        "valor": format_cents_to_br(req.valor_cents),
        "vencimento": due_date.isoformat(),
        "empresa": empresa.razao_social,
    }


@api_router.post("/faturas/{invoice_id}/cancel")
async def cancelar_fatura(invoice_id: str):
    """Cancela uma fatura pendente."""
    _validar_invoice_id(invoice_id)
    try:
        with IuguClient() as client:
            result = client.cancel_invoice(invoice_id)
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro Iugu: {e.message}")

    status_final = result.get("status")
    sucesso = status_final == "canceled"
    if not sucesso:
        logger.warning(
            f"Cancelamento da fatura {invoice_id} nao refletiu: status Iugu = {status_final!r}"
        )
    return {
        "sucesso": sucesso,
        "id": invoice_id,
        "status": status_final,
        "mensagem": (
            "Fatura cancelada"
            if sucesso
            else f"A Iugu nao cancelou (status atual: {status_final}). "
            "Boletos ja registrados no banco nao podem ser cancelados pela API."
        ),
    }


class BaixaManualRequest(BaseModel):
    # Literal restringe a forma de pagamento aos 3 valores aceitos; qualquer
    # outro valor faz o FastAPI rejeitar com 422 antes de chegar na rota.
    forma_pagamento: Literal["Pix na conta", "Dinheiro", "Outros"] = PydField(
        ..., description="Forma de pagamento da baixa manual"
    )


@api_router.post("/faturas/{invoice_id}/baixa-manual")
async def baixa_manual_fatura(invoice_id: str, req: BaixaManualRequest):
    """Baixa manual: considera a fatura paga externamente (externally_pay) e, no
    mesmo fluxo, tenta auto-emitir a NFS-e e enviar o e-mail (igual ao pagamento).

    A fatura passa ao status "externally_paid" (sem tarifa Iugu). A emissão segue
    protegida pelo guardrail/lock de processar_pagamento (evita duplicata).
    """
    _validar_invoice_id(invoice_id)
    forma_pagamento = req.forma_pagamento

    # F1: valida o ESTADO da fatura ANTES de dar baixa. Só faz sentido baixar
    # manualmente uma fatura pendente ou expirada. Faturas já pagas (paid /
    # externally_paid), canceladas, estornadas (refunded) ou em chargeback NÃO
    # podem ser "baixadas" — fazê-lo dispararia uma emissão indevida de NFS-e.
    try:
        with IuguClient() as client:
            invoice = client.get_invoice(invoice_id)
    except IuguAPIError as e:
        if e.status_code == 404:
            raise HTTPException(404, "Fatura nao encontrada")
        raise HTTPException(502, f"Erro ao buscar fatura: {e.message}")

    status_atual = invoice.get("status")
    if status_atual not in ("pending", "expired"):
        raise HTTPException(
            409,
            "Só é possível dar baixa manual em fatura pendente ou expirada "
            f"(status atual: {status_atual}).",
        )

    # a) ID interno do pagamento externo: uuid hex tem 32 chars (cabe no limite).
    #    A descrição é a própria forma escolhida (<=50 chars, garantido no client).
    external_payment_id = uuid.uuid4().hex

    # b) Registra a baixa na Iugu. Se falhar, NÃO seguimos para a emissão.
    try:
        with IuguClient() as client:
            invoice = client.externally_pay(
                invoice_id, external_payment_id, forma_pagamento
            )
    except IuguAPIError as e:
        # F3: não vazar o detalhe cru da Iugu na resposta. Logamos o erro completo
        # (status + mensagem) para diagnóstico, mas devolvemos mensagem genérica.
        logger.error(
            f"Falha no externally_pay da fatura {invoice_id}: "
            f"[{e.status_code}] {e.message}"
        )
        raise HTTPException(
            502, "Não foi possível registrar a baixa manual na Iugu. Tente novamente."
        )

    # F1: confere se a baixa refletiu (espelha o cuidado de cancelar_fatura). Se a
    # Iugu devolver um status e ele não for externally_paid, alerta (pode não ter
    # refletido), mas seguimos — a auto-emissão ainda é protegida pelo guardrail.
    status_iugu = invoice.get("status") or "externally_paid"
    if invoice.get("status") and invoice.get("status") != "externally_paid":
        logger.warning(
            f"Baixa manual da fatura {invoice_id} pode não ter refletido: "
            f"status Iugu = {status_iugu!r}"
        )

    # c) Auto-emissão: processar_pagamento re-busca a fatura (agora externally_paid),
    #    passa no gate e emite + envia o e-mail. Guardrail/lock protegem contra duplicata.
    from .webhook_server import processar_pagamento
    resultado = await processar_pagamento(invoice_id)

    # d) Monta a resposta do contrato. A BAIXA deu certo (sucesso=True), mas a NFS-e
    #    pode ter sido rejeitada/duplicada/ignorada — F4: propagar isso ao contrato
    #    em vez de fingir sucesso silencioso.
    acao = resultado.get("acao", "-")
    nfse_emitida = bool(acao == "nfse_emitida")

    error = None
    if not nfse_emitida:
        # Junta as mensagens de rejeição do provedor, se houver, senão usa o error
        # genérico que processar_pagamento já devolve.
        nfse_dict = resultado.get("nfse") or {}
        mensagens = nfse_dict.get("mensagens") if isinstance(nfse_dict, dict) else None
        if mensagens:
            error = "; ".join(str(m) for m in mensagens) if isinstance(mensagens, (list, tuple)) else str(mensagens)
        else:
            error = resultado.get("error")

    # Mensagem clara por caso de NFS-e.
    if nfse_emitida:
        numero = (resultado.get("nfse") or {}).get("numero_nfse") or "?"
        mensagem = (
            f"Baixa manual registrada ({forma_pagamento}). NFS-e nº {numero} emitida."
        )
    elif acao == "nfse_rejeitada":
        mensagem = (
            f"Baixa manual registrada ({forma_pagamento}), mas a NFS-e foi "
            f"REJEITADA: {error or 'motivo não informado'}."
        )
    elif acao == "nfse_duplicada_bloqueada":
        mensagem = (
            f"Baixa manual registrada ({forma_pagamento}). NFS-e NÃO emitida: "
            f"já existe nota para esta fatura ({error or 'duplicata'})."
        )
    elif acao == "em_processamento":
        mensagem = (
            f"Baixa manual registrada ({forma_pagamento}). NFS-e em processamento "
            "por outra execução (não emitida agora)."
        )
    elif acao == "ignorado":
        mensagem = (
            f"Baixa manual registrada ({forma_pagamento}). NFS-e não emitida: "
            f"{resultado.get('motivo') or 'emissão não aplicável a esta empresa'}."
        )
    else:
        mensagem = (
            f"Baixa manual registrada ({forma_pagamento}). NFS-e: {acao}"
            + (f" ({error})" if error else "")
        )

    return {
        "sucesso": True,
        "status": status_iugu,
        "nfse_emitida": nfse_emitida,
        "nfse": resultado,
        "mensagem": mensagem,
        "error": error,
    }


# ============================================================
# NFS-e
# ============================================================
@api_router.get("/nfse")
async def listar_nfse(
    limite: int = Query(20, ge=1, le=100),
):
    """Lista as ultimas NFS-e emitidas (baseado nos XMLs arquivados)."""
    nfse_dir = Path(settings.nfse_output_dir)
    if not nfse_dir.exists():
        return {"total": 0, "nfse": []}

    arquivos = sorted(nfse_dir.glob("dps_*"), reverse=True)[:limite]
    nfse_list = []
    for arq in arquivos:
        nome = arq.stem
        retorno_path = nfse_dir / arq.name.replace("dps_", "retorno_")
        info = {
            "arquivo_dps": arq.name,
            "data_emissao": _extrair_data_do_nome(nome),
            "tem_retorno": retorno_path.exists(),
        }

        log_path = nfse_dir / (arq.stem + ".json")
        if log_path.exists():
            try:
                meta = json.loads(log_path.read_text(encoding="utf-8"))
                info.update({
                    "numero_nfse": meta.get("numero_nfse"),
                    "cnpj_tomador": meta.get("cnpj"),
                    "razao_social": meta.get("razao_social"),
                    "valor": meta.get("valor"),
                    "sucesso": meta.get("sucesso", False),
                })
            except Exception:
                pass

        nfse_list.append(info)

    return {"total": len(nfse_list), "nfse": nfse_list}


@api_router.post("/nfse/{invoice_id}/emitir")
async def emitir_nfse_endpoint(invoice_id: str):
    """Emite (ou gera em dry-run) a NFS-e para uma fatura."""
    _validar_invoice_id(invoice_id)
    from .webhook_server import processar_pagamento
    resultado = await processar_pagamento(invoice_id)
    return resultado


@api_router.post("/nfse/{invoice_id}/reenviar")
async def reenviar_nfse_email(invoice_id: str):
    """Reenvia o e-mail da fatura.

    - Se houver NFS-e emitida -> reenvia a NFS-e (com anexos) via SMTP.
    - Caso contrario (cobranca/boleto) -> usa o envio nativo da Iugu
      (POST /v1/invoices/{id}/send_email).
    """
    _validar_invoice_id(invoice_id)
    try:
        with IuguClient() as client:
            invoice = client.get_invoice(invoice_id)
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro ao buscar fatura: {e.message}")

    nfse_info = _buscar_nfse_da_fatura(invoice_id)

    # Caso 1 -- fatura com NFS-e: reenvia a NFS-e (com anexos) pelo nosso SMTP.
    if nfse_info:
        from .iugu_client import extract_cnpj_from_invoice
        cnpj = extract_cnpj_from_invoice(invoice)
        empresa = get_repo().buscar_por_cnpj(cnpj) if cnpj else None
        if not empresa:
            raise HTTPException(404, f"CNPJ {cnpj} nao encontrado no cadastro")
        try:
            from .email_nfse import enviar_nfse_email
            sucesso = enviar_nfse_email(empresa, nfse_info)
        except ImportError:
            raise HTTPException(500, "Modulo de e-mail nao disponivel")
        if sucesso:
            return {"sucesso": True, "mensagem": f"NF-e reenviada para {empresa.email}"}
        return {"sucesso": False, "mensagem": "Falha ao enviar a NF-e por e-mail -- verifique os logs"}

    # Caso 2 -- sem NFS-e: reenvia a cobranca/boleto pelo envio nativo da Iugu.
    try:
        with IuguClient() as client:
            client.send_invoice_email(invoice_id)
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro ao reenviar cobranca: {e.message}")
    return {
        "sucesso": True,
        "mensagem": f"Cobranca reenviada para {invoice.get('email') or 'o cliente'}",
    }


# ============================================================
# EMPRESAS -- agora 100% via Iugu (notes JSON, sem custom_variables)
# ============================================================
class CriarEmpresaRequest(BaseModel):
    cnpj: str = PydField(..., description="CNPJ da empresa (so numeros ou formatado)")
    razao_social: str = PydField(..., description="Razao social da empresa")
    email: str = PydField(..., description="E-mail de contato/cobranca")
    codigo_servico: str = PydField("01.07", description="Codigo do servico (LC 116)")
    descricao_servico: str = PydField("", description="Descricao do servico prestado")
    aliquota_iss: float = PydField(2.0, description="Aliquota ISS (%)")
    emitir_nf: bool = PydField(True, description="Emitir NF-e automaticamente")
    nf_na_criacao: bool = PydField(False, description="Emitir NF-e junto com o boleto")
    descricao_boleto: str = PydField("", description="Descricao no boleto")
    valor_fatura: str = PydField("", description="Valor da fatura em formato BR (ex: 1850,00)")
    dia_criacao_fatura: int = PydField(0, ge=0, le=31, description="Dia do mes para cobranca recorrente (0=sem)")
    observacoes: str = PydField("", description="Observacoes internas")
    ativo: bool = PydField(True, description="Empresa ativa")
    # Campos de endereco
    zip_code: str = PydField("", description="CEP")
    street: str = PydField("", description="Rua/Logradouro")
    number: str = PydField("", description="Numero")
    city: str = PydField("", description="Cidade")
    state: str = PydField("", description="UF (2 letras)")
    district: str = PydField("", description="Bairro")
    complement: str = PydField("", description="Complemento")


@api_router.post("/empresas")
async def cadastrar_empresa(req: CriarEmpresaRequest):
    """Cadastra uma nova empresa: cria customer na Iugu com dados de negocio no campo notes (JSON)."""
    cnpj_limpo = normalizar_cnpj(req.cnpj)
    if len(cnpj_limpo) != 14:
        raise HTTPException(422, "CNPJ invalido -- deve conter 14 digitos")

    # Verifica se CNPJ ja existe na Iugu
    customer_existente = _buscar_customer_iugu_por_cnpj(cnpj_limpo)
    if customer_existente:
        raise HTTPException(409, f"CNPJ {cnpj_limpo} ja cadastrado (empresa: {customer_existente.get('name', '?')})")

    # Monta Empresa temporaria para gerar o JSON do notes
    emp_temp = Empresa(
        cnpj=cnpj_limpo,
        razao_social=req.razao_social,
        email=req.email,
        codigo_servico=req.codigo_servico,
        descricao_servico=req.descricao_servico,
        aliquota_iss=req.aliquota_iss,
        emitir_nf=req.emitir_nf,
        nf_na_criacao=req.nf_na_criacao,
        descricao_boleto=req.descricao_boleto,
        valor_fatura=req.valor_fatura,
        dia_criacao_fatura=req.dia_criacao_fatura,
        observacoes=req.observacoes,
        ativo=req.ativo,
    )
    notes_json = empresa_para_notes_json(emp_temp)

    try:
        with IuguClient() as client:
            customer = client.create_customer(
                email=req.email,
                name=req.razao_social,
                cpf_cnpj=cnpj_limpo,
                notes=notes_json,
                zip_code=req.zip_code or None,
                street=req.street or None,
                number=req.number or None,
                city=req.city or None,
                state=req.state or None,
                district=req.district or None,
                complement=req.complement or None,
            )
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro ao criar customer na Iugu: [{e.status_code}] {e.message}")

    customer_id = customer.get("id", "")
    logger.info(f"Empresa cadastrada: {req.razao_social} (CNPJ {cnpj_limpo}) -- customer Iugu: {customer_id}")

    invalidar_cache()  # nova empresa aparece imediatamente nas leituras

    return {
        "sucesso": True,
        "customer_id": customer_id,
        "cnpj": cnpj_limpo,
        "razao_social": req.razao_social,
        "email": req.email,
    }


@api_router.get("/empresas")
async def listar_empresas(
    apenas_ativas: bool = Query(True, description="Filtrar apenas empresas ativas"),
):
    """Lista empresas cadastradas (via Iugu)."""
    repo = get_repo()

    empresas = repo.listar_ativas() if apenas_ativas else list(repo._empresas.values())

    return {
        "total": len(empresas),
        "empresas": [
            {
                "cnpj": e.cnpj,
                "razao_social": e.razao_social,
                "email": e.email,
                "codigo_servico": e.codigo_servico,
                "descricao_servico": e.descricao_servico,
                "aliquota_iss": e.aliquota_iss,
                "emitir_nf": e.emitir_nf,
                "nf_na_criacao": e.nf_na_criacao,
                "descricao_boleto": e.descricao_boleto,
                "valor_fatura": e.valor_fatura,
                "dia_criacao_fatura": e.dia_criacao_fatura,
                "ativo": e.ativo,
                "observacoes": e.observacoes,
                # Campos de endereco
                "zip_code": e.zip_code,
                "street": e.street,
                "number": e.number,
                "city": e.city,
                "state": e.state,
                "district": e.district,
                "complement": e.complement,
                # ID Iugu
                "customer_id": e.customer_id,
            }
            for e in empresas
        ],
    }


@api_router.get("/empresas/{cnpj}")
async def detalhe_empresa(cnpj: str):
    """Detalhes de uma empresa pelo CNPJ."""
    repo = get_repo()
    cnpj_limpo = normalizar_cnpj(cnpj)
    empresa = repo.buscar_por_cnpj(cnpj_limpo)
    if not empresa:
        raise HTTPException(404, f"Empresa com CNPJ {cnpj} nao encontrada")

    return empresa.to_dict_completo()


class EditarEmpresaRequest(BaseModel):
    razao_social: Optional[str] = None
    email: Optional[str] = None
    codigo_servico: Optional[str] = None
    descricao_servico: Optional[str] = None
    aliquota_iss: Optional[float] = None
    emitir_nf: Optional[bool] = None
    nf_na_criacao: Optional[bool] = None
    descricao_boleto: Optional[str] = None
    valor_fatura: Optional[str] = None
    dia_criacao_fatura: Optional[int] = None
    ativo: Optional[bool] = None
    observacoes: Optional[str] = None
    # Campos de endereco
    zip_code: Optional[str] = None
    street: Optional[str] = None
    number: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    complement: Optional[str] = None


@api_router.put("/empresas/{cnpj}")
async def editar_empresa(cnpj: str, req: EditarEmpresaRequest):
    """Edita campos de uma empresa diretamente na Iugu (dados de negocio via notes JSON)."""
    cnpj_limpo = normalizar_cnpj(cnpj)

    # Busca o customer na Iugu
    customer = _buscar_customer_iugu_por_cnpj(cnpj_limpo)
    if not customer:
        raise HTTPException(404, f"CNPJ {cnpj} nao encontrado na Iugu")

    cust_id = customer["id"]
    dados = req.model_dump(exclude_none=True)

    if not dados:
        return {"sucesso": True, "mensagem": "Nenhum campo para atualizar"}

    # Separa campos nativos da Iugu vs campos de negocio (notes JSON)
    campos_nativos_iugu = {}
    if "razao_social" in dados:
        campos_nativos_iugu["name"] = dados.pop("razao_social")
    if "email" in dados:
        campos_nativos_iugu["email"] = dados.pop("email")
    # Endereco
    for campo_endereco in ["zip_code", "street", "number", "city", "state", "district", "complement"]:
        if campo_endereco in dados:
            campos_nativos_iugu[campo_endereco] = dados.pop(campo_endereco)

    # Campos de negocio: ler notes JSON existente, fazer merge, regravar
    campos_negocio = [
        "codigo_servico", "descricao_servico", "aliquota_iss",
        "emitir_nf", "nf_na_criacao", "descricao_boleto",
        "valor_fatura", "dia_criacao_fatura", "ativo", "observacoes",
    ]
    tem_campo_negocio = any(c in dados for c in campos_negocio)

    if tem_campo_negocio:
        # Le o notes atual do customer
        from .iugu_empresas import _parse_notes_json
        notes_raw = customer.get("notes")
        logger.debug(f"[PUT debug] notes RAW do customer: {notes_raw!r}")
        notes_atual = _parse_notes_json(notes_raw)
        logger.debug(f"[PUT debug] notes parseado: {notes_atual}")

        # Aplica as alteracoes
        for campo in campos_negocio:
            if campo in dados:
                logger.debug(f"[PUT debug] Atualizando {campo}: {notes_atual.get(campo)!r} -> {dados[campo]!r}")
                notes_atual[campo] = dados[campo]

        # Serializa de volta
        notes_json = json.dumps(notes_atual, ensure_ascii=False)
        campos_nativos_iugu["notes"] = notes_json
        logger.info(f"[PUT debug] notes FINAL a enviar: {notes_json}")

    logger.debug(f"[PUT debug] Payload chaves para Iugu: {list(campos_nativos_iugu.keys())}")
    try:
        with IuguClient() as client:
            result = client.update_customer(cust_id, **campos_nativos_iugu)
            logger.info(f"[PUT debug] Resposta Iugu: notes salvo = {str(result.get('notes', 'N/A'))[:200]}")
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro ao atualizar customer Iugu: {e.message}")

    campos_atualizados = list(req.model_dump(exclude_none=True).keys())
    logger.info(f"Empresa {cnpj} atualizada na Iugu: {campos_atualizados}")

    invalidar_cache()  # mudancas aparecem imediatamente nas leituras

    return {
        "sucesso": True,
        "cnpj": cnpj_limpo,
        "campos_atualizados": campos_atualizados,
    }


@api_router.delete("/empresas/{cnpj}")
async def excluir_empresa(cnpj: str):
    """Exclui empresa: remove customer da Iugu."""
    cnpj_limpo = normalizar_cnpj(cnpj)

    customer = _buscar_customer_iugu_por_cnpj(cnpj_limpo)
    if not customer:
        raise HTTPException(404, f"Empresa com CNPJ {cnpj} nao encontrada na Iugu")

    razao_social = customer.get("name", "?")
    cust_id = customer["id"]

    try:
        with IuguClient() as client:
            client.delete_customer(cust_id)
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro ao excluir customer Iugu: {e.message}")

    logger.info(f"Empresa excluida: {razao_social} (CNPJ {cnpj_limpo})")

    invalidar_cache()  # remocao reflete imediatamente nas leituras

    return {
        "sucesso": True,
        "cnpj": cnpj_limpo,
        "razao_social": razao_social,
    }


# ============================================================
# HELPERS
# ============================================================
def _buscar_customer_iugu_por_cnpj(cnpj_limpo: str, completo: bool = True) -> Optional[dict]:
    """Busca um customer na Iugu pelo CNPJ.
    Se completo=True (padrao), faz GET individual para trazer todos os campos (inclusive notes).
    list_customers nao retorna o campo notes, entao sem completo=True os dados de negocio se perdem.
    """
    try:
        with IuguClient() as client:
            result = client.list_customers(query=cnpj_limpo)
            items = result.get("items", [])
            for item in items:
                cpf_cnpj_iugu = "".join(filter(str.isdigit, str(item.get("cpf_cnpj", ""))))
                if cpf_cnpj_iugu == cnpj_limpo:
                    if completo and item.get("id"):
                        # Busca completa para ter o campo notes
                        return client.get_customer(item["id"])
                    return item
    except IuguAPIError as e:
        logger.warning(f"Erro ao buscar customer Iugu por CNPJ {cnpj_limpo}: {e.message}")
    return None


def _buscar_nfse_da_fatura(invoice_id: str) -> Optional[dict]:
    """Busca informacoes de NFS-e associada a uma fatura."""
    nfse_dir = Path(settings.nfse_output_dir)
    if not nfse_dir.exists():
        return None

    for log_file in nfse_dir.glob("*.json"):
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
            if data.get("invoice_id") == invoice_id:
                return data
        except Exception:
            continue

    for xml_file in nfse_dir.glob(f"*{invoice_id[:8]}*"):
        return {
            "arquivo": xml_file.name,
            "invoice_id": invoice_id,
            "sucesso": True,
        }

    return None


def _carregar_mapa_nfse() -> dict[str, dict]:
    """Carrega mapa invoice_id -> info de NFS-e a partir dos logs."""
    mapa: dict[str, dict] = {}
    nfse_dir = Path(settings.nfse_output_dir)
    if not nfse_dir.exists():
        return mapa

    for log_file in nfse_dir.glob("*.json"):
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
            inv_id = data.get("invoice_id")
            if inv_id:
                mapa[inv_id] = data
        except Exception:
            continue

    return mapa


def _extrair_data_do_nome(nome: str) -> Optional[str]:
    """Extrai data de nomes como 'dps_20_20260420_143025' -> '2026-04-20'."""
    partes = nome.split("_")
    for parte in partes:
        if len(parte) == 8 and parte.isdigit():
            try:
                return f"{parte[:4]}-{parte[4:6]}-{parte[6:]}"
            except Exception:
                pass
    return None
