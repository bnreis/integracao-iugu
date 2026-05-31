"""
Endpoints da API de gestao -- consumidos pelo app Android.

Rotas:
  POST /auth/login              -> autenticacao (retorna JWT)
  GET  /api/dashboard           -> resumo do dia
  GET  /api/faturas             -> listar faturas (com filtros)
  GET  /api/faturas/{id}        -> detalhes de uma fatura
  POST /api/faturas             -> criar fatura manual
  POST /api/faturas/{id}/cancel -> cancelar fatura pendente
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
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field as PydField

from .auth import LoginRequest, LoginResponse, UsuarioAutenticado, login, usuario_autenticado
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
# AUTH
# ============================================================
@auth_router.post("/login", response_model=LoginResponse)
async def endpoint_login(request: LoginRequest):
    """Autentica e retorna um token JWT."""
    return login(request)


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
    """Conta NFS-e emitidas e erros num periodo, varrendo a pasta de output."""
    emitidas = 0
    erros = 0
    if not nfse_dir.exists():
        return emitidas, erros
    for f in nfse_dir.iterdir():
        if not f.is_file():
            continue
        nome = f.name
        for part in nome.split("_"):
            if len(part) == 8 and part.isdigit():
                try:
                    file_date = date(int(part[:4]), int(part[4:6]), int(part[6:8]))
                    if data_inicio <= file_date <= data_fim:
                        if nome.startswith("dps_"):
                            emitidas += 1
                        elif nome.startswith("retorno_"):
                            try:
                                conteudo = f.read_text(encoding="utf-8", errors="ignore")
                                if "erro" in conteudo.lower() or "rejeic" in conteudo.lower():
                                    erros += 1
                            except Exception:
                                pass
                except ValueError:
                    pass
                break
    return emitidas, erros


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

            ontem = data_ref - timedelta(days=1)
            vencidas = client.list_invoices(
                status="pending",
                due_date_to=f"{ontem}",
                created_at_from=data_corte_iso,
                limit=100,
            )
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro ao consultar Iugu: {e.message}")

    items_criadas_hoje = criadas_hoje.get("items", [])
    items_pagas_hoje = pagas_hoje.get("items", [])
    items_criadas_mes = criadas_mes.get("items", [])
    items_pagas_mes = pagas_mes.get("items", [])
    items_pendentes = pendentes.get("items", [])
    items_vencidas = vencidas.get("items", [])

    def soma_cents(items, campo="total_cents"):
        return sum(int(f.get(campo) or f.get("total_cents") or 0) for f in items)

    total_criado_hoje = soma_cents(items_criadas_hoje)
    total_pago_hoje = soma_cents(items_pagas_hoje, "total_paid_cents")
    total_criado_mes = soma_cents(items_criadas_mes)
    total_pago_mes = soma_cents(items_pagas_mes, "total_paid_cents")
    total_pendente = soma_cents(items_pendentes)
    total_vencido = soma_cents(items_vencidas)

    qtd_criadas_mes = criadas_mes.get("totalItems", len(items_criadas_mes))
    qtd_pagas_mes = pagas_mes.get("totalItems", len(items_pagas_mes))
    taxa_conversao = round((qtd_pagas_mes / qtd_criadas_mes * 100), 1) if qtd_criadas_mes > 0 else 0.0

    nfse_dir = Path(settings.nfse_output_dir)
    nfse_hoje, nfse_erros_hoje = _contar_nfse_periodo(nfse_dir, max(data_ref, DATA_CORTE), data_ref)
    nfse_mes, nfse_erros_mes = _contar_nfse_periodo(nfse_dir, max(primeiro_mes, DATA_CORTE), ultimo_mes)

    # NFS-e pendentes
    nfse_pendentes = 0
    nfse_pendentes_list = []
    mapa_nfse = _carregar_mapa_nfse()
    cnpjs_nf_na_criacao = _carregar_cnpjs_nf_na_criacao()
    nfse_arquivos = set()
    if nfse_dir.exists():
        for f in nfse_dir.glob("dps_*"):
            nfse_arquivos.add(f.name)
    for fatura in items_pagas_mes:
        fatura_id = fatura.get("id", "")
        tem_nfse = fatura_id in mapa_nfse or any(fatura_id in nome for nome in nfse_arquivos)
        if not tem_nfse:
            cnpj_raw = (fatura.get("payer_cpf_cnpj") or "").replace(".", "").replace("/", "").replace("-", "").strip()
            if cnpj_raw and cnpj_raw in cnpjs_nf_na_criacao:
                tem_nfse = True
        if not tem_nfse:
            nfse_pendentes += 1
            if len(nfse_pendentes_list) < 5:
                nfse_pendentes_list.append({
                    "invoice_id": fatura_id,
                    "payer_name": fatura.get("payer_name") or fatura.get("email") or "---",
                    "total": fatura.get("total"),
                    "paid_at": fatura.get("paid_at"),
                })

    # Empresas ativas (agora via Iugu)
    try:
        repo = get_repo()
        total_empresas = len(repo.listar_ativas())
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
            "faturas_vencidas": vencidas.get("totalItems", len(items_vencidas)),
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
    try:
        with IuguClient() as client:
            result = client.list_invoices(
                status=status_filter,
                limit=limite,
                start=pagina * limite,
                query=busca,
                created_at_from=created_from,
                created_at_to=created_to,
            )
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro Iugu: {e.message}")

    items = result.get("items", [])
    mapa_nfse = _carregar_mapa_nfse()
    cnpjs_nf_na_criacao = _carregar_cnpjs_nf_na_criacao()
    logger.debug(f"[NFS-e check] Logs locais: {len(mapa_nfse)} | CNPJs nf_na_criacao: {cnpjs_nf_na_criacao}")

    def _check_nfse(fatura: dict) -> bool:
        fatura_id = fatura.get("id", "")
        if fatura_id in mapa_nfse:
            return True
        cnpj_raw = (fatura.get("payer_cpf_cnpj") or "").replace(".", "").replace("/", "").replace("-", "").strip()
        if cnpj_raw and cnpj_raw in cnpjs_nf_na_criacao:
            return True
        for var in (fatura.get("custom_variables") or []):
            if isinstance(var, dict) and var.get("name") == "nfse_emitida_na_criacao":
                if var.get("value") == "true":
                    return True
        return False

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
            }
            for f in items
        ],
    }


@api_router.get("/faturas/{invoice_id}")
async def detalhe_fatura(invoice_id: str):
    """Detalhes completos de uma fatura."""
    try:
        with IuguClient() as client:
            invoice = client.get_invoice(invoice_id)
    except IuguAPIError as e:
        if e.status_code == 404:
            raise HTTPException(404, "Fatura nao encontrada")
        raise HTTPException(502, f"Erro Iugu: {e.message}")

    nfse_info = _buscar_nfse_da_fatura(invoice_id)

    nfse_emitida = nfse_info is not None
    if not nfse_emitida:
        cnpj_raw = (invoice.get("payer_cpf_cnpj") or "").replace(".", "").replace("/", "").replace("-", "").strip()
        cnpjs_nf_na_criacao = _carregar_cnpjs_nf_na_criacao()
        if cnpj_raw and cnpj_raw in cnpjs_nf_na_criacao:
            nfse_emitida = True
    if not nfse_emitida:
        for var in (invoice.get("custom_variables") or []):
            if isinstance(var, dict) and var.get("name") == "nfse_emitida_na_criacao":
                if var.get("value") == "true":
                    nfse_emitida = True
                    break

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
        "nfse": nfse_info,
        "nfse_emitida": nfse_emitida,
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
    try:
        with IuguClient() as client:
            result = client.cancel_invoice(invoice_id)
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro Iugu: {e.message}")
    return {"sucesso": True, "id": invoice_id, "status": result.get("status")}


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
    from .webhook_server import processar_pagamento
    resultado = await processar_pagamento(invoice_id)
    return resultado


@api_router.post("/nfse/{invoice_id}/reenviar")
async def reenviar_nfse_email(invoice_id: str):
    """Reenvia o e-mail da NFS-e para uma fatura ja processada."""
    try:
        with IuguClient() as client:
            invoice = client.get_invoice(invoice_id)
    except IuguAPIError as e:
        raise HTTPException(502, f"Erro ao buscar fatura: {e.message}")

    from .iugu_client import extract_cnpj_from_invoice
    cnpj = extract_cnpj_from_invoice(invoice)
    if not cnpj:
        raise HTTPException(400, "CNPJ nao identificavel na fatura")

    repo = get_repo()
    empresa = repo.buscar_por_cnpj(cnpj)
    if not empresa:
        raise HTTPException(404, f"CNPJ {cnpj} nao encontrado no cadastro")

    nfse_info = _buscar_nfse_da_fatura(invoice_id)
    if not nfse_info:
        raise HTTPException(404, "Nenhuma NFS-e encontrada para esta fatura")

    try:
        from .email_nfse import enviar_nfse_email
        sucesso = enviar_nfse_email(empresa, nfse_info)
        if sucesso:
            return {"sucesso": True, "mensagem": f"E-mail reenviado para {empresa.email}"}
        else:
            return {"sucesso": False, "mensagem": "Falha ao enviar e-mail -- verifique os logs"}
    except ImportError:
        raise HTTPException(500, "Modulo de e-mail nao disponivel")


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


def _carregar_cnpjs_nf_na_criacao() -> set[str]:
    """Retorna set de CNPJs com nf_na_criacao=True."""
    try:
        repo = get_repo()
        return {
            e.cnpj
            for e in repo._empresas.values()
            if e.nf_na_criacao and e.ativo and e.emitir_nf
        }
    except Exception as exc:
        logger.warning(f"Erro ao carregar empresas para nf_na_criacao: {exc}")
        return set()


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
