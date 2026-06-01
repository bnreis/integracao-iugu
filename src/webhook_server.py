"""
Servidor FastAPI — webhooks Iugu + API de gestão para app Android.

Funcionalidades:
  1. Webhooks: recebe gatilhos da Iugu (pagamento → NFS-e)
  2. API de gestão: dashboard, faturas, NFS-e, empresas (protegida por JWT)

Endpoints de webhook (sem auth):
  GET  /health           → healthcheck
  POST /webhook/iugu     → recebe os gatilhos da Iugu
  GET  /empresas         → lista empresas autorizadas (debug)
  POST /processar/{id}   → reprocessa manualmente uma fatura

Endpoints de gestão (requerem JWT):
  POST /auth/login       → autenticação
  GET  /api/dashboard    → resumo do dia
  GET  /api/faturas      → listar faturas
  ...                    → ver src/api_routes.py para lista completa

Para rodar localmente:
    uvicorn src.webhook_server:app --reload --host 0.0.0.0 --port 8000

Para expor publicamente (webhooks + app):
    cloudflared tunnel --url http://localhost:8000
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from .auth import usuario_autenticado
from .config import settings
from .iugu_client import IuguAPIError, IuguClient, extract_cnpj_from_invoice
from .iugu_empresas import EmpresasRepository

app = FastAPI(
    title="Integração Iugu + NFS-e DF",
    description="Webhooks Iugu + API de gestão para app Android",
    version="0.3.0",
    # Documentação interativa desligada em produção (não expor o schema da API).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# --- CORS (restrito ao domínio do painel; configurável via CORS_ORIGINS no .env) ---
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Registra rotas da API de gestão ---
from .api_routes import api_router, auth_router

app.include_router(auth_router)
app.include_router(api_router)

# Repositório de empresas (singleton — agora lê da Iugu)
def get_repo() -> EmpresasRepository:
    repo = EmpresasRepository()
    repo.carregar(forcar=True)
    return repo


# ============================================================
# Endpoints de infra
# ============================================================
@app.get("/health")
async def health():
    """Healthcheck simples."""
    return {
        "status": "ok",
        "service": "iugu-nfse-df",
        "ambiente_nfse": settings.nfse_ambiente,
    }


@app.get("/empresas", dependencies=[Depends(usuario_autenticado)])
async def listar_empresas():
    """Lista as empresas autorizadas (útil para debug). Requer JWT."""
    try:
        repo = get_repo()
        empresas = [e.to_dict() for e in repo.listar_ativas()]
        # Oculta dados sensíveis no retorno
        for e in empresas:
            e["cnpj"] = f"***{e['cnpj'][-4:]}" if e.get("cnpj") else ""
        return {"total": len(empresas), "empresas": empresas}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ============================================================
# Webhook principal
# ============================================================
@app.post("/webhook/iugu")
async def receber_webhook_iugu(request: Request):
    """
    Endpoint principal que recebe os gatilhos da Iugu.

    A Iugu envia os dados como application/x-www-form-urlencoded,
    com o evento no campo "event" e os dados em "data[...]".

    Documentação: https://dev.iugu.com/docs/gatilhos
    """
    # Valida token se configurado (enviado pela Iugu como query param ou header)
    if settings.iugu_webhook_token:
        token_recebido = (
            request.headers.get("X-Iugu-Token")
            or request.query_params.get("token")
            or ""
        )
        # Comparação em tempo constante (evita timing attack na validação do token).
        if not secrets.compare_digest(token_recebido, settings.iugu_webhook_token):
            logger.warning(f"Webhook rejeitado — token inválido: {token_recebido[:10]}...")
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Token de webhook inválido",
            )

    # Parse do body (form-urlencoded)
    form = await request.form()
    data = dict(form)
    event = data.get("event", "")
    invoice_id = data.get("data[id]") or data.get("data%5Bid%5D")
    invoice_status = data.get("data[status]")

    logger.info(f"Webhook recebido: event={event} invoice_id={invoice_id} status={invoice_status}")

    # Só processamos fatura paga
    if event != "invoice.status_changed" or invoice_status != "paid":
        logger.debug(f"Evento ignorado: {event} / status {invoice_status}")
        return JSONResponse({"ignored": True, "reason": "not a paid invoice event"})

    if not invoice_id:
        raise HTTPException(400, "invoice_id ausente no payload")

    # Processa em background (resposta rápida para a Iugu não dar timeout)
    resultado = await processar_pagamento(invoice_id)
    return JSONResponse(resultado)


@app.post("/processar/{invoice_id}", dependencies=[Depends(usuario_autenticado)])
async def processar_manualmente(invoice_id: str):
    """
    Reprocessa manualmente uma fatura. Requer JWT.
    Útil para testes e reprocessamento quando o webhook falhou.
    """
    return await processar_pagamento(invoice_id)


# ============================================================
# Guardrail contra NFS-e duplicada
# ============================================================
def _verificar_nfse_duplicada(invoice_id: str, cnpj: str, invoice: dict) -> dict | None:
    """
    Verifica se já existe NFS-e emitida para esta fatura.
    Checa múltiplas fontes:
      1. Log local por invoice_id (nfse_emitidas/*.json)
      2. Arquivo DPS por invoice_id (nfse_emitidas/dps_*)
      3. Empresa com nf_na_criacao=True
      4. Custom variable 'nfse_emitida_na_criacao' na fatura da Iugu
      5. Mesmo CNPJ + mesmo mês + mesmo valor nos logs locais (anti-duplicata geral)

    Retorna dict com detalhes se duplicata encontrada, None se ok.
    """
    from pathlib import Path
    import json as _json

    nfse_dir = Path(settings.nfse_output_dir)

    # 1. Log local por invoice_id
    if nfse_dir.exists():
        for log_file in nfse_dir.glob("*.json"):
            try:
                data = _json.loads(log_file.read_text(encoding="utf-8"))
                if data.get("invoice_id") == invoice_id:
                    return {
                        "fonte": "log_local",
                        "detalhe": f"Arquivo: {log_file.name}",
                        "arquivo": log_file.name,
                    }
            except Exception:
                continue

        # 2. Arquivo DPS
        for dps_file in nfse_dir.glob("dps_*"):
            if invoice_id[:8] in dps_file.name:
                return {
                    "fonte": "arquivo_dps",
                    "detalhe": f"Arquivo: {dps_file.name}",
                    "arquivo": dps_file.name,
                }

    # 3. Empresa com nf_na_criacao=True → NFS-e emitida junto com o boleto
    try:
        repo = get_repo()
        empresa = repo.buscar_por_cnpj(cnpj)
        if empresa and empresa.nf_na_criacao and empresa.emitir_nf:
            return {
                "fonte": "nf_na_criacao",
                "detalhe": f"{empresa.razao_social} emite NF-e na criação da fatura",
            }
    except Exception:
        pass

    # 4. Custom variable na fatura da Iugu
    for var in (invoice.get("custom_variables") or []):
        if isinstance(var, dict) and var.get("name") == "nfse_emitida_na_criacao":
            if var.get("value") == "true":
                return {
                    "fonte": "custom_variable_iugu",
                    "detalhe": "Fatura marcada com nfse_emitida_na_criacao=true na Iugu",
                }

    # 5. Anti-duplicata geral: mesmo CNPJ + mesmo mês + mesmo valor
    if nfse_dir.exists():
        valor_fatura = invoice.get("total_cents") or invoice.get("total_paid_cents")
        paid_at = invoice.get("paid_at") or ""
        mes_ref = paid_at[:7] if len(paid_at) >= 7 else ""  # "2026-04"

        if valor_fatura and mes_ref:
            for log_file in nfse_dir.glob("*.json"):
                try:
                    data = _json.loads(log_file.read_text(encoding="utf-8"))
                    if (
                        data.get("cnpj") == cnpj
                        and data.get("valor_cents") == valor_fatura
                        and (data.get("competencia", "") or "").startswith(mes_ref)
                    ):
                        return {
                            "fonte": "duplicata_mes_valor",
                            "detalhe": (
                                f"NFS-e já existe para CNPJ {cnpj} "
                                f"no mês {mes_ref} com valor {valor_fatura} cents"
                            ),
                            "arquivo": log_file.name,
                        }
                except Exception:
                    continue

    return None


# ============================================================
# Lógica central
# ============================================================
async def processar_pagamento(invoice_id: str) -> dict[str, Any]:
    """
    Fluxo central: busca a fatura, valida o CNPJ contra o cadastro Iugu
    e aciona a emissão de NFS-e se autorizado.
    """
    # 1. Busca detalhes da fatura na Iugu
    try:
        with IuguClient() as client:
            invoice = client.get_invoice(invoice_id)
    except IuguAPIError as e:
        logger.error(f"Erro ao buscar fatura {invoice_id}: {e}")
        return {"success": False, "stage": "fetch_invoice", "error": str(e)}

    if invoice.get("status") != "paid":
        logger.info(
            f"Fatura {invoice_id} não está paga (status={invoice.get('status')}) — ignorando"
        )
        return {
            "success": False,
            "stage": "check_status",
            "reason": f"status={invoice.get('status')}",
        }

    # 2. Extrai CNPJ
    cnpj = extract_cnpj_from_invoice(invoice)
    if not cnpj:
        logger.warning(f"Fatura {invoice_id} sem CNPJ identificável")
        return {"success": False, "stage": "extract_cnpj", "reason": "CNPJ não encontrado"}

    logger.info(f"Fatura {invoice_id} paga — CNPJ do pagador: {cnpj}")

    # 3. Verifica no cadastro (agora via Iugu)
    try:
        repo = get_repo()
    except Exception as e:
        logger.error(str(e))
        return {"success": False, "stage": "load_empresas", "error": str(e)}

    empresa = repo.buscar_por_cnpj(cnpj)
    if not empresa:
        logger.info(
            f"CNPJ {cnpj} não está cadastrado como empresa autorizada — pulando emissão"
        )
        return {
            "success": True,
            "invoice_id": invoice_id,
            "cnpj": cnpj,
            "acao": "ignorado",
            "motivo": "CNPJ não autorizado para emissão automática",
        }

    # 3.1. Respeita a flag emitir_nf
    if not empresa.emitir_nf:
        logger.info(
            f"CNPJ {cnpj} ({empresa.razao_social}) cadastrado mas com emitir_nf=False — pulando"
        )
        return {
            "success": True,
            "invoice_id": invoice_id,
            "cnpj": cnpj,
            "empresa": empresa.razao_social,
            "acao": "ignorado",
            "motivo": "emitir_nf=False",
        }

    # 3.2. Verifica se a NFS-e já foi emitida na criação da fatura (nf_na_criacao)
    nfse_ja_emitida = False
    if empresa.nf_na_criacao:
        nfse_ja_emitida = True
    custom_vars = invoice.get("custom_variables") or []
    for var in custom_vars:
        if isinstance(var, dict) and var.get("name") == "nfse_emitida_na_criacao":
            if var.get("value") == "true":
                nfse_ja_emitida = True
                break

    if nfse_ja_emitida:
        logger.info(
            f"📄 CNPJ {cnpj} ({empresa.razao_social}) — NFS-e já foi emitida na criação "
            f"da fatura (nf_na_criacao=True). Pulando emissão no pagamento."
        )
        return {
            "success": True,
            "invoice_id": invoice_id,
            "cnpj": cnpj,
            "empresa": empresa.razao_social,
            "acao": "nfse_pulada",
            "motivo": "NFS-e já emitida na criação da fatura (nf_na_criacao=True)",
        }

    # 4. GUARDRAIL: verifica se já existe NFS-e para este cliente/mês/valor
    nfse_existente = _verificar_nfse_duplicada(invoice_id, cnpj, invoice)
    if nfse_existente:
        logger.warning(
            f"⚠️ GUARDRAIL: NFS-e já existe para fatura {invoice_id} "
            f"(CNPJ {cnpj}, {empresa.razao_social}). Fonte: {nfse_existente.get('fonte', '?')}"
        )
        return {
            "success": False,
            "invoice_id": invoice_id,
            "cnpj": cnpj,
            "empresa": empresa.razao_social,
            "acao": "nfse_duplicada_bloqueada",
            "error": f"NFS-e já emitida para esta fatura. {nfse_existente.get('detalhe', '')}",
            "nfse_existente": nfse_existente,
        }

    # 5. Aciona emissão da NFS-e
    logger.info(
        f"CNPJ {cnpj} autorizado — emitindo NFS-e para {empresa.razao_social}"
    )
    try:
        from .nfse_df import emitir_nfse

        resultado_nfse = await emitir_nfse(invoice=invoice, empresa=empresa)

        if resultado_nfse.get("sucesso"):
            try:
                from .email_nfse import enviar_nfse_email
                enviar_nfse_email(empresa, resultado_nfse)
            except ImportError:
                logger.warning("Módulo email_nfse não disponível — NFS-e não enviada por e-mail")
            except Exception as email_exc:
                logger.error(f"Falha ao enviar NFS-e por e-mail: {email_exc}")

        return {
            "success": True,
            "invoice_id": invoice_id,
            "cnpj": cnpj,
            "empresa": empresa.razao_social,
            "acao": "nfse_emitida",
            "nfse": resultado_nfse,
        }
    except Exception as exc:
        logger.exception(f"Falha ao emitir NFS-e para fatura {invoice_id}")
        return {
            "success": False,
            "stage": "emitir_nfse",
            "invoice_id": invoice_id,
            "cnpj": cnpj,
            "error": str(exc),
        }


# ============================================================
# Execução direta
# ============================================================
def main():
    """Inicia o servidor usando uvicorn."""
    import uvicorn

    uvicorn.run(
        "src.webhook_server:app",
        host=settings.webhook_host,
        port=settings.webhook_port,
        log_level=settings.webhook_log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
