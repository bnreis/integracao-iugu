"""
Guardrail anti-duplicata + lock cross-process por fatura (NFS-e DF).

Módulo NEUTRO (sem dependência de webhook_server nem scheduled_invoices) para que
TANTO o webhook (uvicorn, async) QUANTO o cron de boletos (outro processo)
compartilhem a MESMA serialização e a MESMA checagem de duplicata. Emitir NFS-e
gera documento fiscal (difícil de cancelar) — duplicar é o pior cenário.

Conteúdo:
  - _LOCK_INVOICE_TTL_SEGUNDOS : TTL do lockfile obsoleto.
  - _lock_invoice(invoice_id)  : context manager, lockfile atômico O_CREAT|O_EXCL.
  - _verificar_nfse_duplicada(...) : guardrail BASEADO EM EVIDÊNCIA (log .json real).

⚠️ Para NÃO criar import circular, este módulo NÃO pode importar webhook_server
nem scheduled_invoices.
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from .config import settings


# ============================================================
# Lock por invoice_id (cross-process) — M1
# ============================================================
# TTL do lockfile: se um lock for mais antigo que isto E o processo que o criou não
# estiver mais vivo, assumimos que o dono morreu sem liberar (crash) e o
# consideramos OBSOLETO.
# (C3) 300s — a seção crítica pode levar SOAP ~60s + assinatura + SMTP + IO; um TTL
# curto (120s) corria o risco de reclamar o lock de um processo LENTO porém VIVO,
# gerando uma 2ª nota. Com 300s + checagem de PID vivo, só reclamamos lock realmente
# órfão.
_LOCK_INVOICE_TTL_SEGUNDOS = 300


def _pid_vivo(pid: int) -> bool:
    """Indica se o processo `pid` ainda está vivo (best-effort, cross-platform).

    Política CONSERVADORA: na dúvida, retorna True (trata como vivo) — nunca
    reclamamos um lock por engano só porque não conseguimos confirmar a morte do
    dono. Reclamar lock de processo VIVO levaria a NFS-e duplicada (pior cenário).

    - POSIX: os.kill(pid, 0) → vivo; ProcessLookupError/ESRCH → morto;
      PermissionError/EPERM → vivo (existe, mas sem permissão de sinalizar).
    - Plataforma sem os.kill (ex.: alguns Windows) ou qualquer outra exceção →
      CONSERVADOR: retorna True (vivo).
    """
    if pid <= 0:
        # PID inválido/desconhecido → não dá para confirmar morte; cai só na regra
        # de idade (caller decide). Aqui sinalizamos "não vivo" para que a idade
        # mande, mas só é chamado quando há PID lido com sucesso.
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Processo existe (de outro usuário) — está vivo.
        return True
    except Exception:
        # AttributeError (sem os.kill), OSError inesperado, plataforma sem suporte:
        # conservador → trata como vivo (NUNCA reclama por engano).
        return True


def _ler_pid_lockfile(lockfile: Path) -> int | None:
    """Lê o PID gravado no lockfile. Arquivo vazio/corrompido → None (sem-PID)."""
    try:
        conteudo = lockfile.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not conteudo:
        return None
    try:
        return int(conteudo)
    except ValueError:
        return None


@contextmanager
def _lock_invoice(invoice_id: str):
    """Lock cross-process por fatura, baseado em lockfile atômico.

    Serializa o trecho verificar->emitir->gravar_log para a MESMA invoice, fechando
    a corrida entre processos distintos: webhook (uvicorn) re-entregue pela Iugu
    (retry de 502) e o CRON de boletos, que roda em OUTRO processo e poderia tocar a
    mesma fatura quase ao mesmo tempo. Duplicar NFS-e (documento fiscal) é o pior caso.

    Implementação: os.open(O_CREAT|O_EXCL|O_WRONLY) cria o arquivo de forma atômica —
    funciona em Windows (local) e Linux (VPS). O_EXCL é a FONTE DA VERDADE da posse.
    Ao adquirir, gravamos o PID do processo no lockfile.

    Detecção de OBSOLETO (FileExistsError): consideramos o lock órfão se
    (idade > TTL) OU (o PID gravado NÃO está mais vivo). Isso evita ficar travado
    por um crash, mas sem reclamar o lock de um processo lento porém VIVO (C3).

    Faz `yield True` quando ADQUIRIU o lock e `yield False` quando está OCUPADO (não
    obsoleto). Libera no finally apenas se foi quem adquiriu (fecha o fd e remove o
    lockfile, ignorando OSError).
    """
    # Defesa em profundidade: NÃO interpolar o invoice_id cru no nome do arquivo.
    # Mesmo que a borda (API) já valide o formato, sanitizamos aqui para garantir
    # que apenas [A-Za-z0-9_-] componha o nome — assim um invoice_id com "../" ou
    # "/" nunca escapa do diretório nfse_output_dir nem cria um lockfile fora dele.
    seguro = re.sub(r"[^A-Za-z0-9_-]", "_", invoice_id)
    lockfile = Path(settings.nfse_output_dir) / f".lock_nfse_{seguro}"
    fd = None
    adquiriu = False
    try:
        # Garante o diretório (mesmo dir do índice .json) antes de tentar o lock.
        try:
            lockfile.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        for tentativa in range(2):  # no máximo 1 retry, após limpar lock obsoleto
            try:
                fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                adquiriu = True
                # Grava o PID para a checagem de staleness por processo vivo (C3).
                # Best-effort: se falhar a escrita, a regra de idade ainda protege.
                try:
                    os.write(fd, str(os.getpid()).encode("ascii"))
                except OSError:
                    pass
                break
            except FileExistsError:
                # Lock existe: vivo (ocupado) ou órfão (processo morto/antigo)?
                try:
                    idade = time.time() - lockfile.stat().st_mtime
                except OSError:
                    # Sumiu entre o open e o stat — tenta adquirir de novo no loop.
                    continue

                pid = _ler_pid_lockfile(lockfile)
                # Obsoleto se passou do TTL OU se o dono (PID) não está mais vivo.
                # PID None (arquivo vazio/corrompido) → cai só na regra de idade.
                dono_morto = pid is not None and not _pid_vivo(pid)
                obsoleto = (idade > _LOCK_INVOICE_TTL_SEGUNDOS) or dono_morto

                if obsoleto:
                    logger.warning(
                        f"[lock] Lock obsoleto para fatura {invoice_id} "
                        f"(idade {idade:.0f}s, TTL {_LOCK_INVOICE_TTL_SEGUNDOS}s, "
                        f"pid={pid}, dono_morto={dono_morto}) — "
                        f"removendo e tentando novamente"
                    )
                    try:
                        lockfile.unlink()
                    except OSError:
                        pass
                    continue  # tenta adquirir de novo
                # Lock recente e dono vivo → outro processo está emitindo esta fatura.
                break
        yield adquiriu
    finally:
        if adquiriu:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                lockfile.unlink()
            except OSError:
                pass


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

    # 1. (M4) Defesa primária DETERMINÍSTICA: o log tem nome fixo
    # nfse_<invoice_id>.json. Abrir direto é mais barato e robusto que varrer glob
    # (não depende de o campo invoice_id estar correto dentro de cada arquivo).
    nome_det = f"nfse_{invoice_id}.json"
    log_det = nfse_dir / nome_det
    if log_det.exists():
        try:
            data = _json.loads(log_det.read_text(encoding="utf-8"))
            if data.get("sucesso") is True:
                return {
                    "fonte": "log_local",
                    "detalhe": f"Arquivo: {nome_det}",
                    "arquivo": nome_det,
                }
        except Exception:
            # Arquivo corrompido/parcial: ignora a regra 1 e segue para a regra 2.
            pass

    # 2. Anti-duplicata geral: mesmo CNPJ + mesmo valor + mesmo MÊS (campos reais).
    # (M2) O log grava data_emissao = date.today(), mas a fatura traz paid_at. Num
    # reprocessamento na virada do mês esses dois meses divergem — então casamos o
    # mês do log com QUALQUER um dos dois: o mês do pagamento OU o mês de hoje.
    if nfse_dir.exists():
        valor_reais = round(
            (int(invoice.get("total_paid_cents") or invoice.get("total_cents") or 0)) / 100.0,
            2,
        )
        mes_pagamento = (invoice.get("paid_at") or "")[:7]  # "2026-04"
        mes_hoje = date.today().isoformat()[:7]
        meses_validos = {m for m in (mes_pagamento, mes_hoje) if len(m) >= 7}

        if valor_reais > 0 and meses_validos:
            for log_file in nfse_dir.glob("*.json"):
                try:
                    data = _json.loads(log_file.read_text(encoding="utf-8"))
                    mes_log = (data.get("data_emissao") or "")[:7]
                    if (
                        data.get("sucesso") is True
                        and data.get("cnpj") == cnpj
                        and data.get("valor") == valor_reais
                        and mes_log in meses_validos
                    ):
                        return {
                            "fonte": "duplicata_mes_valor",
                            "detalhe": (
                                f"NFS-e já existe para CNPJ {cnpj} "
                                f"no mês {mes_log} com valor R$ {valor_reais:.2f}"
                            ),
                            "arquivo": log_file.name,
                        }
                except Exception:
                    continue

    return None
