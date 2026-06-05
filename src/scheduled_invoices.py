"""
Módulo de geração automática de boletos recorrentes na Iugu.

Fluxo:
1. Lê as empresas cadastradas (via Iugu — custom_variables)
2. Seleciona empresas cujo `dia_criacao_fatura` == dia de hoje
   (com tratamento de fim de mês: se dia=31 e mês tem 30 dias, cai no dia 30)
3. Para cada empresa selecionada, cria um boleto na Iugu via API
4. Se a empresa tem `nf_na_criacao=True`, emite a NFS-e imediatamente
   e envia por e-mail (a custom_variable "nfse_emitida_na_criacao"="true"
   é incluída na fatura para que o webhook saiba que não precisa emitir de novo)
5. Gera um log detalhado da execução

Este módulo é invocado pelo `scripts/run_scheduled_invoices.py`,
agendado via Windows Task Scheduler (ou cron na VPS).

Regras:
- Vencimento: 10 dias após a data de criação
- Só processa empresas ATIVAS e com `valor_fatura > 0` e `dia_criacao_fatura > 0`
- Idempotência: o caller pode passar `--data-referencia` para reprocessar
- Erros individuais não interrompem o lote — cada empresa é tratada separadamente

MIGRADO: fonte de dados é agora 100% Iugu (sem planilha).
"""
from __future__ import annotations

import asyncio
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from loguru import logger

from .config import settings
from .iugu_client import IuguAPIError, IuguClient
from .iugu_empresas import Empresa, EmpresasRepository, format_cents_to_br

# Dias de vencimento após a criação (fixo segundo decisão do projeto)
DIAS_VENCIMENTO = 10


@dataclass
class ResultadoEmpresa:
    """Resultado do processamento de uma empresa."""
    cnpj: str
    razao_social: str
    sucesso: bool
    invoice_id: Optional[str] = None
    secure_url: Optional[str] = None
    digitable_line: Optional[str] = None
    valor_cents: int = 0
    erro: Optional[str] = None
    nfse_emitida: bool = False
    nfse_resultado: Optional[dict] = None
    nfse_erro: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "cnpj": self.cnpj,
            "razao_social": self.razao_social,
            "sucesso": self.sucesso,
            "invoice_id": self.invoice_id,
            "secure_url": self.secure_url,
            "digitable_line": self.digitable_line,
            "valor": format_cents_to_br(self.valor_cents),
            "erro": self.erro,
        }
        if self.nfse_emitida or self.nfse_erro:
            d["nfse_emitida"] = self.nfse_emitida
            d["nfse_erro"] = self.nfse_erro
        return d


@dataclass
class ResultadoLote:
    """Resultado agregado da execução diária."""
    data_referencia: date
    total_empresas_elegiveis: int = 0
    sucessos: list[ResultadoEmpresa] = field(default_factory=list)
    falhas: list[ResultadoEmpresa] = field(default_factory=list)
    ignoradas: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_referencia": self.data_referencia.isoformat(),
            "total_empresas_elegiveis": self.total_empresas_elegiveis,
            "total_sucessos": len(self.sucessos),
            "total_falhas": len(self.falhas),
            "total_ignoradas": len(self.ignoradas),
            "sucessos": [r.to_dict() for r in self.sucessos],
            "falhas": [r.to_dict() for r in self.falhas],
            "ignoradas": self.ignoradas,
        }


# ============================================================
# SELEÇÃO DE EMPRESAS ELEGÍVEIS
# ============================================================
def dia_efetivo(data_ref: date, dia_configurado: int) -> int:
    """
    Retorna o dia efetivo de cobrança considerando fim de mês.

    Regra: se o dia configurado for maior que o último dia do mês,
    usa o último dia disponível.
    """
    if not (1 <= dia_configurado <= 31):
        return 0
    ultimo_dia = calendar.monthrange(data_ref.year, data_ref.month)[1]
    return min(dia_configurado, ultimo_dia)


def empresas_elegiveis_hoje(
    data_ref: date, repo: Optional[EmpresasRepository] = None
) -> list[Empresa]:
    """
    Retorna empresas cujo `dia_criacao_fatura` (ajustado por fim de mês)
    bate com o dia de `data_ref`.
    """
    repo = repo or EmpresasRepository()
    repo.carregar(forcar=True)
    elegiveis = []
    for emp in repo.empresas_com_boleto_recorrente():
        dia_alvo = dia_efetivo(data_ref, emp.dia_criacao_fatura)
        if dia_alvo == data_ref.day:
            elegiveis.append(emp)
    logger.info(
        f"Dia {data_ref.day}/{data_ref.month}: {len(elegiveis)} empresa(s) elegível(eis)"
    )
    return elegiveis


# ============================================================
# GERAÇÃO DO BOLETO
# ============================================================
def _empresa_para_payer(emp: Empresa) -> dict[str, Any]:
    """
    Monta o dict 'payer' para a API da Iugu usando só os dados essenciais.

    NOTA: O endereço NÃO é enviado aqui. A Iugu NÃO faz autocomplete do
    pagador a partir do CNPJ mesmo quando existe customer cadastrado — a
    fatura volta com payer_address_* vazios. Para garantir endereço no XML
    da NFS-e, `extrair_endereco_tomador` (src/nfse_df.py) busca o customer
    pelo CNPJ via API Iugu e usa o endereço de lá. Consequência: todos os
    tomadores precisam estar cadastrados como customer na Iugu com endereço
    completo — senão a emissão falha.
    """
    payer: dict[str, Any] = {
        "cpf_cnpj": emp.cnpj,
        "name": emp.razao_social or "Cliente",
    }
    if emp.email:
        payer["email"] = emp.email
    return payer


def criar_boleto_para_empresa(
    emp: Empresa,
    data_criacao: date,
    client: Optional[IuguClient] = None,
) -> ResultadoEmpresa:
    """Cria UM boleto para a empresa. Captura erros e retorna um Resultado."""
    valor_cents = emp.valor_fatura_cents
    if valor_cents <= 0:
        return ResultadoEmpresa(
            cnpj=emp.cnpj,
            razao_social=emp.razao_social,
            sucesso=False,
            erro="valor_fatura inválido ou zero",
        )

    due_date = data_criacao + timedelta(days=DIAS_VENCIMENTO)
    descricao = emp.descricao_boleto or emp.descricao_servico or "Serviço mensal"

    metodos = [m.strip() for m in settings.fatura_metodos_pagamento.split(",") if m.strip()]

    payload = {
        "email": emp.email or "",
        "due_date": due_date,
        "items": [{
            "description": descricao,
            "quantity": 1,
            "price_cents": valor_cents,
        }],
        "payer": _empresa_para_payer(emp),
        # Vincula a fatura ao customer da empresa iterada (chave única, não-ambígua):
        # garante que invoice.customer_id chegue preenchido no webhook.
        "customer_id": emp.customer_id,
        "payable_with": metodos,
        "expires_in": settings.fatura_dias_expiracao,
        "bank_slip_extra_due": settings.fatura_boleto_extra_due,
        "fines": settings.fatura_multa_atraso_percentual > 0,
        "late_payment_fine": int(settings.fatura_multa_atraso_percentual),
        "per_day_interest": settings.fatura_juros_por_dia,
        "ignore_due_email": settings.fatura_ignorar_email_vencimento,
        "custom_variables": [
            {"name": "origem", "value": "integracao_iugu_nfse_df"},
            {"name": "tipo", "value": "boleto_recorrente"},
            {"name": "cnpj_tomador", "value": emp.cnpj},
            {"name": "data_referencia", "value": data_criacao.isoformat()},
            # Sempre "false" na criação. Só vira "true" APÓS a emissão da NFS-e
            # retornar sucesso (ver _emitir_nfse_para_fatura). Se a emissão falhar,
            # a flag fica "false" e o webhook reprocessa a nota no pagamento —
            # antes a flag era gravada "true" aqui mesmo se a emissão falhasse,
            # fazendo o webhook pular uma nota que nunca saiu (perda silenciosa).
            {"name": "nfse_emitida_na_criacao", "value": "false"},
        ],
    }

    # Cadastro legado pode não ter customer_id; nesse caso create_invoice apenas
    # não o envia (fallback por CNPJ no webhook). Logamos para diagnóstico.
    if not emp.customer_id:
        logger.debug(f"Empresa {emp.cnpj} sem customer_id — fatura criada sem vínculo de customer")

    close_client = False
    if client is None:
        client = IuguClient()
        close_client = True
    try:
        invoice = client.create_invoice(**payload)
        bank_slip = invoice.get("bank_slip") or {}
        return ResultadoEmpresa(
            cnpj=emp.cnpj,
            razao_social=emp.razao_social,
            sucesso=True,
            invoice_id=invoice.get("id"),
            secure_url=invoice.get("secure_url"),
            digitable_line=bank_slip.get("digitable_line"),
            valor_cents=valor_cents,
        )
    except IuguAPIError as exc:
        logger.error(f"Falha Iugu para {emp.cnpj}: {exc}")
        return ResultadoEmpresa(
            cnpj=emp.cnpj,
            razao_social=emp.razao_social,
            sucesso=False,
            valor_cents=valor_cents,
            erro=f"[{exc.status_code}] {exc.message}",
        )
    except Exception as exc:
        logger.exception(f"Erro inesperado para {emp.cnpj}")
        return ResultadoEmpresa(
            cnpj=emp.cnpj,
            razao_social=emp.razao_social,
            sucesso=False,
            valor_cents=valor_cents,
            erro=f"erro inesperado: {exc}",
        )
    finally:
        if close_client:
            client.close()


# ============================================================
# EMISSÃO DE NFS-e NA CRIAÇÃO DA FATURA
# ============================================================
def _emitir_nfse_para_fatura(
    emp: Empresa,
    resultado: ResultadoEmpresa,
    client: IuguClient,
) -> None:
    """Emite NFS-e imediatamente para empresas com nf_na_criacao=True."""
    if not resultado.invoice_id:
        resultado.nfse_erro = "invoice_id ausente — não é possível emitir NFS-e"
        return

    try:
        invoice = client.get_invoice(resultado.invoice_id)
    except IuguAPIError as exc:
        resultado.nfse_erro = f"Falha ao buscar fatura para NFS-e: {exc}"
        logger.error(f"NFS-e {emp.cnpj}: falha ao buscar fatura {resultado.invoice_id}: {exc}")
        return

    try:
        from .nfse_df import emitir_nfse
        nfse_result = asyncio.run(emitir_nfse(invoice=invoice, empresa=emp))
        if nfse_result.get("sucesso"):
            resultado.nfse_emitida = True
            resultado.nfse_resultado = nfse_result
            logger.info(
                f"📄 NFS-e emitida na criação: {emp.razao_social} — "
                f"Nº {nfse_result.get('numero_nfse', '?')}"
            )
            # Só agora (emissão OK) marca a fatura como já tendo NFS-e na criação,
            # para o webhook não reemitir no pagamento. Se isso falhar, o pior caso
            # é o webhook tentar reemitir e ser barrado pelo guardrail anti-duplicata.
            try:
                client.update_invoice(
                    resultado.invoice_id,
                    custom_variables=[
                        {"name": "nfse_emitida_na_criacao", "value": "true"}
                    ],
                )
            except Exception as exc:
                logger.warning(
                    f"NFS-e {emp.cnpj}: emitida, mas falhou ao marcar "
                    f"nfse_emitida_na_criacao=true na fatura {resultado.invoice_id}: {exc}"
                )
            _enviar_nfse_por_email(emp, nfse_result)
        else:
            resultado.nfse_erro = nfse_result.get("mensagem_erro", "erro desconhecido")
            logger.error(
                f"❌ NFS-e falhou na criação para {emp.razao_social}: "
                f"{resultado.nfse_erro}"
            )
    except Exception as exc:
        resultado.nfse_erro = f"erro inesperado: {exc}"
        logger.exception(f"NFS-e {emp.cnpj}: erro inesperado ao emitir")


def _enviar_nfse_por_email(emp: Empresa, nfse_result: dict) -> None:
    """Envia a NFS-e por e-mail para o cliente."""
    try:
        from .email_nfse import enviar_nfse_email
        enviar_nfse_email(emp, nfse_result)
    except ImportError:
        logger.warning(
            f"Módulo email_nfse não disponível — NFS-e de {emp.razao_social} "
            f"emitida mas NÃO enviada por e-mail"
        )
    except Exception as exc:
        logger.error(f"Falha ao enviar NFS-e por e-mail para {emp.razao_social}: {exc}")


# ============================================================
# EXECUÇÃO DO LOTE DIÁRIO
# ============================================================
def executar_dia(
    data_ref: Optional[date] = None,
    dry_run: bool = False,
) -> ResultadoLote:
    """
    Executa o lote diário de criação de boletos.

    Args:
        data_ref: data de referência (default: hoje)
        dry_run: se True, lista o que seria feito mas não chama a API
    """
    data_ref = data_ref or date.today()
    lote = ResultadoLote(data_referencia=data_ref)

    elegiveis = empresas_elegiveis_hoje(data_ref)
    lote.total_empresas_elegiveis = len(elegiveis)

    if not elegiveis:
        logger.info(f"Nenhuma empresa para cobrar em {data_ref}. Fim.")
        return lote

    if dry_run:
        metodos = [m.strip() for m in settings.fatura_metodos_pagamento.split(",") if m.strip()]
        logger.info("[DRY-RUN] As seguintes empresas seriam cobradas:")
        logger.info(
            f"  Configurações: pagamento={metodos}, "
            f"expira_em={settings.fatura_dias_expiracao}d, "
            f"multa={settings.fatura_multa_atraso_percentual}%, "
            f"juros_dia={'sim' if settings.fatura_juros_por_dia else 'não'}"
        )
        for emp in elegiveis:
            nf_flag = " + NFS-e na criação" if (emp.nf_na_criacao and emp.emitir_nf) else ""
            logger.info(
                f"  • {emp.razao_social} ({emp.cnpj}) — "
                f"R$ {format_cents_to_br(emp.valor_fatura_cents)} — "
                f"venc. {data_ref + timedelta(days=DIAS_VENCIMENTO)}{nf_flag}"
            )
            lote.ignoradas.append(f"{emp.cnpj} (dry-run)")
        return lote

    with IuguClient() as client:
        for emp in elegiveis:
            resultado = criar_boleto_para_empresa(emp, data_ref, client=client)
            if resultado.sucesso:
                lote.sucessos.append(resultado)
                logger.info(
                    f"✅ Boleto criado: {emp.razao_social} "
                    f"({resultado.invoice_id}) R$ {format_cents_to_br(resultado.valor_cents)}"
                )

                if emp.nf_na_criacao and emp.emitir_nf:
                    logger.info(
                        f"📄 nf_na_criacao=True para {emp.razao_social} — emitindo NFS-e..."
                    )
                    _emitir_nfse_para_fatura(emp, resultado, client)
            else:
                lote.falhas.append(resultado)
                logger.error(
                    f"❌ Falhou: {emp.razao_social} ({emp.cnpj}) — {resultado.erro}"
                )

    return lote
