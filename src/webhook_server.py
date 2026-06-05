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
from .iugu_empresas import get_repo

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

# Repositório de empresas: usa o get_repo() CACHEADO de iugu_empresas (TTL 300s),
# importado acima. Evita o N+1 à Iugu (1 GET por customer) a cada webhook — antes
# havia um get_repo() local aqui que fazia carregar(forcar=True) em toda chamada.


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
# WEB-010: stages de falha RECUPERÁVEL (erro transitório upstream) → 5xx para a
# Iugu re-tentar o webhook. Demais resultados são terminais/sucesso → 200.
_STAGES_RECUPERAVEIS = {"fetch_invoice", "load_empresas", "emitir_nfse"}


def _status_http_webhook(resultado: dict[str, Any]) -> int:
    """Mapeia o resultado de processar_pagamento para o status HTTP do webhook.

    - sucesso (emitida/ignorada/pulada) → 200
    - falha terminal (status≠pago, sem CNPJ, duplicata) → 200 (não re-tentar)
    - falha recuperável (Iugu/cadastro/emissão indisponível) → 502 (Iugu re-tenta)
    """
    if resultado.get("success"):
        return 200
    if resultado.get("stage") in _STAGES_RECUPERAVEIS:
        return status.HTTP_502_BAD_GATEWAY
    return 200


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

    resultado = await processar_pagamento(invoice_id)
    # WEB-010: devolve 5xx em falha RECUPERÁVEL para a Iugu re-tentar o webhook.
    # Casos terminais (CNPJ não autorizado, emitir_nf=False, duplicata, status≠pago)
    # seguem como 200 — não faz sentido a Iugu re-tentar isso.
    return JSONResponse(resultado, status_code=_status_http_webhook(resultado))


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
def _verificar_nfse_duplicada(
    invoice_id: str, cnpj: str, invoice: dict, empresa: Any = None
) -> dict | None:
    """
    Verifica se já existe NFS-e emitida para esta fatura.
    Guardrail BASEADO EM EVIDÊNCIA: só um log de emissão REAL bem-sucedido
    (nfse_<invoice_id>.json com sucesso=True) prova que a nota existe.
    Checa duas fontes:
      1. Log local por invoice_id, com sucesso=True (nfse_emitidas/*.json)
      2. Mesmo CNPJ + mesmo mês + mesmo valor nos logs reais (sucesso=True)
         — anti-duplicata geral, usando os campos reais do log.

    `empresa` é mantido na assinatura apenas por compatibilidade com os
    chamadores; não é mais usado (a regra antiga de nf_na_criacao foi removida
    porque pulava emissão por flag, não por evidência).

    Retorna dict com detalhes se duplicata encontrada, None se ok.
    """
    from pathlib import Path
    import json as _json

    nfse_dir = Path(settings.nfse_output_dir)

    # 1. Log local por invoice_id (só emissão REAL bem-sucedida prova a nota)
    if nfse_dir.exists():
        for log_file in nfse_dir.glob("*.json"):
            try:
                data = _json.loads(log_file.read_text(encoding="utf-8"))
                if data.get("invoice_id") == invoice_id and data.get("sucesso") is True:
                    return {
                        "fonte": "log_local",
                        "detalhe": f"Arquivo: {log_file.name}",
                        "arquivo": log_file.name,
                    }
            except Exception:
                continue

    # 2. Anti-duplicata geral: mesmo CNPJ + mesmo mês + mesmo valor (campos reais)
    if nfse_dir.exists():
        valor_reais = round(
            (int(invoice.get("total_paid_cents") or invoice.get("total_cents") or 0)) / 100.0,
            2,
        )
        mes_ref = (invoice.get("paid_at") or "")[:7]  # "2026-04"

        if valor_reais > 0 and len(mes_ref) == 7:
            for log_file in nfse_dir.glob("*.json"):
                try:
                    data = _json.loads(log_file.read_text(encoding="utf-8"))
                    if (
                        data.get("sucesso") is True
                        and data.get("cnpj") == cnpj
                        and data.get("valor") == valor_reais
                        and (data.get("data_emissao") or "")[:7] == mes_ref
                    ):
                        return {
                            "fonte": "duplicata_mes_valor",
                            "detalhe": (
                                f"NFS-e já existe para CNPJ {cnpj} "
                                f"no mês {mes_ref} com valor R$ {valor_reais:.2f}"
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

    # ADR-0003 Etapa 1: resolve a empresa pelo customer_id da fatura (chave única
    # da Iugu), não por CNPJ. Um mesmo CNPJ pode ter vários customers/departamentos
    # (ex.: ALMERIA tem 3) com config fiscal distinta; buscar_por_cnpj devolveria
    # "o primeiro" e a NFS-e sairia com a alíquota/código de serviço/endereço do
    # departamento ERRADO. customer_id elimina essa ambiguidade.
    customer_id = invoice.get("customer_id")
    empresa = None
    if customer_id:
        empresa = repo.buscar_por_customer_id(customer_id)
        if not empresa:
            # Pode ser customer recém-cadastrado que ainda não entrou no cache
            # (TTL 300s — Onda 0). Faz UMA recarga forçada da Iugu e tenta de novo.
            try:
                repo = get_repo(forcar=True)
                empresa = repo.buscar_por_customer_id(customer_id)
            except Exception as e:
                logger.error(str(e))
                return {"success": False, "stage": "load_empresas", "error": str(e)}

    # Fallback compatível: fatura antiga sem customer_id no payload, OU customer_id
    # que não existe no repositório. Cai para buscar_por_cnpj (comportamento legado)
    # — pode ser ambíguo em CNPJ multi-cliente, por isso registramos um warning.
    if not empresa:
        logger.warning(
            f"ADR-0003 fallback: resolvendo fatura {invoice_id} por CNPJ {cnpj} "
            f"(customer_id={customer_id or 'ausente'} não resolvido). "
            f"Pode ser ambíguo em CNPJ com múltiplos customers."
        )
        empresa = repo.buscar_por_cnpj(cnpj)
        if not empresa:
            # Mantém a recarga forçada do cache também no caminho de fallback (Onda 0).
            try:
                repo = get_repo(forcar=True)
                empresa = repo.buscar_por_cnpj(cnpj)
            except Exception as e:
                logger.error(str(e))
                return {"success": False, "stage": "load_empresas", "error": str(e)}
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

    # 4. GUARDRAIL: verifica se já existe NFS-e para este cliente/mês/valor
    nfse_existente = _verificar_nfse_duplicada(invoice_id, cnpj, invoice, empresa)
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

        # WEB-011: rejeição da NFS-e (sucesso=False SEM exceção — ex.: erro de schema,
        # IM não liberada) NÃO pode ser rotulada como "emitida". É falha terminal
        # (re-tentar não resolve rejeição), mas precisa de registro honesto + alerta.
        if not resultado_nfse.get("sucesso"):
            mensagens = resultado_nfse.get("mensagens") or resultado_nfse.get("mensagem_erro")
            logger.error(
                f"❌ NFS-e REJEITADA para fatura {invoice_id} "
                f"(CNPJ {cnpj}, {empresa.razao_social}): {mensagens}"
            )
            return {
                "success": False,
                # stage terminal — NÃO está em _STAGES_RECUPERAVEIS → HTTP 200 (não re-tenta).
                "stage": "nfse_rejeitada",
                "invoice_id": invoice_id,
                "cnpj": cnpj,
                "empresa": empresa.razao_social,
                "acao": "nfse_rejeitada",
                "nfse": resultado_nfse,
            }

        # Sucesso: envia o e-mail e retorna como emitida.
        try:
            from datetime import date

            from .email_nfse import enviar_nfse_email

            # ResultadoEmissao.to_dict() não traz valor/data_emissao — o template
            # precisa deles. Enriquece o dict (sem mutar a chave 'nfse' do retorno)
            # com os mesmos campos que o log .json e o reenvio manual usam, para
            # que auto-envio e reenviar gerem e-mail IDÊNTICO.
            total_cents = int(
                invoice.get("total_paid_cents") or invoice.get("total_cents") or 0
            )
            dados_email = {
                **resultado_nfse,
                "valor": round(total_cents / 100.0, 2),
                "data_emissao": date.today().isoformat(),
                "razao_social": empresa.razao_social,
            }
            enviar_nfse_email(empresa, dados_email)
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
