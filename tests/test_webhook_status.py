"""
Teste de regressão do tratamento de falhas do webhook (WEB-010 + WEB-011).

Roda 100% OFFLINE (Iugu, NFS-e e e-mail são mockados) — seguro na máquina do
Bruno e na VPS, não toca em nenhuma API real.

Como rodar (a partir da raiz do projeto):
    python tests/test_webhook_status.py

Cobre:
  1. _status_http_webhook(): mapeamento resultado->HTTP (função pura)
       - falha recuperável (fetch_invoice/load_empresas/emitir_nfse) -> 502 (Iugu re-tenta)
       - falha terminal / sucesso -> 200 (Iugu não re-tenta)
  2. processar_pagamento(): WEB-011 — rejeição de NFS-e (sucesso=False SEM exceção)
     NÃO pode ser rotulada como "nfse_emitida"; deve virar "nfse_rejeitada" (HTTP 200).
  3. processar_pagamento(): caminho de sucesso continua "nfse_emitida".
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# Console do Windows costuma ser cp1252 — força UTF-8 para não quebrar na impressão.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Permite "from src..." rodando o arquivo direto (tests/ -> raiz do projeto)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import webhook_server  # noqa: E402
from src.webhook_server import _status_http_webhook, processar_pagamento  # noqa: E402

_falhas = 0


def _check(nome: str, cond: bool) -> None:
    global _falhas
    if not cond:
        _falhas += 1
    print(f"  [{'OK  ' if cond else 'FALHOU'}] {nome}")


def teste_status_http_webhook() -> None:
    print("1) _status_http_webhook (função pura):")
    casos = [
        ("sucesso -> 200", {"success": True, "acao": "nfse_emitida"}, 200),
        ("fetch_invoice -> 502", {"success": False, "stage": "fetch_invoice"}, 502),
        ("load_empresas -> 502", {"success": False, "stage": "load_empresas"}, 502),
        ("emitir_nfse (exceção) -> 502", {"success": False, "stage": "emitir_nfse"}, 502),
        ("nfse_rejeitada (terminal) -> 200", {"success": False, "stage": "nfse_rejeitada"}, 200),
        ("check_status -> 200", {"success": False, "stage": "check_status"}, 200),
        ("extract_cnpj -> 200", {"success": False, "stage": "extract_cnpj"}, 200),
        ("duplicata (sem stage) -> 200", {"success": False, "acao": "nfse_duplicada_bloqueada"}, 200),
    ]
    for nome, resultado, esperado in casos:
        _check(nome, _status_http_webhook(resultado) == esperado)


def _empresa_mock() -> MagicMock:
    emp = MagicMock()
    emp.emitir_nf = True
    emp.nf_na_criacao = False
    emp.razao_social = "EMPRESA TESTE LTDA"
    emp.cnpj = "12345678000199"
    emp.email = "tomador@teste.com"
    return emp


def _rodar_processar_com_emissao(resultado_emissao: dict) -> dict:
    """Roda processar_pagamento com toda a cadeia externa mockada e
    emitir_nfse devolvendo `resultado_emissao`."""
    # ADR-0003 Etapa 1: a fatura agora carrega customer_id e o webhook resolve a
    # empresa por buscar_por_customer_id (caminho primário), não mais por CNPJ.
    invoice = {"id": "INV123", "status": "paid", "payer_cpf_cnpj": "12345678000199",
               "customer_id": "cust_TESTE", "custom_variables": []}
    empresa = _empresa_mock()
    repo = MagicMock()
    repo.buscar_por_customer_id.return_value = empresa  # caminho primário
    repo.buscar_por_cnpj.return_value = empresa          # fallback (não deve ser usado aqui)

    async def fake_emitir(invoice, empresa):  # noqa: ARG001
        return resultado_emissao

    with patch.object(webhook_server, "IuguClient") as MockClient, \
            patch.object(webhook_server, "extract_cnpj_from_invoice", return_value="12345678000199"), \
            patch.object(webhook_server, "get_repo", return_value=repo), \
            patch.object(webhook_server, "_verificar_nfse_duplicada", return_value=None), \
            patch("src.nfse_df.emitir_nfse", fake_emitir), \
            patch("src.email_nfse.enviar_nfse_email", return_value=True):
        MockClient.return_value.__enter__.return_value.get_invoice.return_value = invoice
        return asyncio.run(processar_pagamento("INV123"))


def teste_web011_rejeicao() -> None:
    print("2) WEB-011 — rejeição de NFS-e não é mascarada como emitida:")
    res = _rodar_processar_com_emissao(
        {"sucesso": False, "mensagens": ["[E160] schema rejeitado"]}
    )
    _check("success == False", res.get("success") is False)
    _check("acao == 'nfse_rejeitada'", res.get("acao") == "nfse_rejeitada")
    _check("stage == 'nfse_rejeitada'", res.get("stage") == "nfse_rejeitada")
    _check("HTTP = 200 (rejeição é terminal, não re-tenta)", _status_http_webhook(res) == 200)


def teste_emissao_sucesso() -> None:
    print("3) Sucesso de emissão continua 'nfse_emitida':")
    res = _rodar_processar_com_emissao({"sucesso": True, "numero_nfse": "42"})
    _check("success == True", res.get("success") is True)
    _check("acao == 'nfse_emitida'", res.get("acao") == "nfse_emitida")
    _check("HTTP = 200", _status_http_webhook(res) == 200)


def teste_auto_envio_email_com_anexo() -> None:
    """Fluxo automático completo: emissão OK -> enviar_nfse_email é chamado com um
    dict que carrega xml_retorno_path -> o e-mail montado tem o XML EM ANEXO.

    Tudo offline: emitir_nfse e SMTP são mockados; usamos um XML de retorno real
    do disco (rps_1_retorno) só para o anexo aparecer no MIME."""
    print("4) Auto-envio do e-mail com o XML em anexo:")
    from src import email_nfse

    # XML de retorno real (emissão #408). Se não existir, valida o resto mesmo assim.
    raiz = Path(__file__).resolve().parent.parent
    xml_retorno = raiz / "nfse_emitidas" / "rps_1_retorno_20260605_170842.xml"

    resultado_emissao = {
        "sucesso": True,
        "numero_nfse": "408",
        "codigo_verificacao": "B3B17DA6A",
        "xml_retorno_path": str(xml_retorno) if xml_retorno.exists() else None,
    }

    capturado: dict = {}

    def _fake_enviar(empresa, dados, **kwargs):  # noqa: ARG001
        # Captura exatamente o que o webhook repassa ao e-mail e monta o MIME real
        # (sem SMTP) para inspecionar a estrutura/anexo.
        capturado["empresa"] = empresa
        capturado["dados"] = dados
        msg, _html, _dest = email_nfse.montar_email_nfse(empresa, dados)
        capturado["msg"] = msg
        return True

    invoice = {"id": "INV123", "status": "paid", "payer_cpf_cnpj": "12345678000199",
               "customer_id": "cust_TESTE", "custom_variables": [],
               "total_paid_cents": 315000}
    empresa = _empresa_mock()
    repo = MagicMock()
    repo.buscar_por_customer_id.return_value = empresa
    repo.buscar_por_cnpj.return_value = empresa

    async def fake_emitir(invoice, empresa):  # noqa: ARG001
        return resultado_emissao

    with patch.object(webhook_server, "IuguClient") as MockClient, \
            patch.object(webhook_server, "extract_cnpj_from_invoice", return_value="12345678000199"), \
            patch.object(webhook_server, "get_repo", return_value=repo), \
            patch.object(webhook_server, "_verificar_nfse_duplicada", return_value=None), \
            patch("src.nfse_df.emitir_nfse", fake_emitir), \
            patch("src.email_nfse.enviar_nfse_email", _fake_enviar):
        MockClient.return_value.__enter__.return_value.get_invoice.return_value = invoice
        res = asyncio.run(processar_pagamento("INV123"))

    _check("emissão automática -> 'nfse_emitida'", res.get("acao") == "nfse_emitida")
    _check("enviar_nfse_email foi chamado", "dados" in capturado)
    dados = capturado.get("dados", {})
    _check("dados do e-mail carregam xml_retorno_path", bool(dados.get("xml_retorno_path")))
    _check("dados do e-mail carregam valor (enriquecido)", dados.get("valor") == 3150.0)
    _check("dados do e-mail carregam data_emissao", bool(dados.get("data_emissao")))

    # Inspeciona o MIME: precisa ter corpo HTML E um anexo application/xml.
    msg = capturado.get("msg")
    tipos = []
    anexos_xml = []
    if msg is not None:
        for parte in msg.walk():
            tipos.append(parte.get_content_type())
            if parte.get_content_type() == "application/xml" and parte.get_filename():
                anexos_xml.append(parte.get_filename())
    _check("e-mail tem corpo text/html", "text/html" in tipos)
    _check("e-mail tem estrutura multipart/mixed", "multipart/mixed" in tipos)
    if xml_retorno.exists():
        _check("XML da NFS-e veio EM ANEXO (.xml)", len(anexos_xml) >= 1)
        print(f"       anexo(s): {anexos_xml}")
    else:
        print("       [aviso] rps_1_retorno_20260605_170842.xml ausente — anexo não verificado")


def teste_guardrail_regra1_deterministica() -> None:
    """M4 — regra 1 determinística: um nfse_<id>.json com sucesso=True no diretório
    de saída faz _verificar_nfse_duplicada detectar a duplicata pela fonte log_local.
    Roda offline: usa diretório temporário e monkeypatch de settings.nfse_output_dir."""
    print("5) Guardrail regra 1 (log determinístico por invoice_id):")
    import json
    import tempfile
    from src.config import settings

    invoice_id = "INV_DET_1"
    with tempfile.TemporaryDirectory() as tmp:
        log = {
            "invoice_id": invoice_id,
            "cnpj": "12345678000199",
            "valor": 100.0,
            "data_emissao": date.today().isoformat(),
            "sucesso": True,
        }
        (Path(tmp) / f"nfse_{invoice_id}.json").write_text(
            json.dumps(log, ensure_ascii=False), encoding="utf-8"
        )
        orig = settings.nfse_output_dir
        try:
            settings.nfse_output_dir = tmp
            res = webhook_server._verificar_nfse_duplicada(
                invoice_id, "12345678000199", {"paid_at": "2026-06-01T00:00:00"}
            )
        finally:
            settings.nfse_output_dir = orig

    _check("duplicata detectada", res is not None)
    _check("fonte == 'log_local'", (res or {}).get("fonte") == "log_local")
    _check("arquivo correto", (res or {}).get("arquivo") == f"nfse_{invoice_id}.json")


def teste_guardrail_janela_mes() -> None:
    """M2 — janela de mês: log gravado no mês CORRENTE (data_emissao=hoje) e fatura
    com paid_at no mês ANTERIOR, mesmo CNPJ e valor. A regra 2 deve casar via mês de
    hoje (mes_hoje), mesmo o invoice_id sendo diferente (não cai na regra 1)."""
    print("6) Guardrail regra 2 (janela de mês — M2):")
    import json
    import tempfile
    from datetime import timedelta
    from src.config import settings

    hoje = date.today()
    # paid_at no mês anterior (subtrai ~35 dias garante mês diferente).
    mes_anterior = (hoje.replace(day=1) - timedelta(days=1))
    paid_at = f"{mes_anterior.isoformat()}T12:00:00"

    with tempfile.TemporaryDirectory() as tmp:
        log = {
            "invoice_id": "INV_JA_EMITIDA",  # diferente da fatura nova -> regra 1 não pega
            "cnpj": "12345678000199",
            "valor": 250.0,
            "data_emissao": hoje.isoformat(),  # mês corrente
            "sucesso": True,
        }
        (Path(tmp) / "nfse_INV_JA_EMITIDA.json").write_text(
            json.dumps(log, ensure_ascii=False), encoding="utf-8"
        )
        orig = settings.nfse_output_dir
        try:
            settings.nfse_output_dir = tmp
            res = webhook_server._verificar_nfse_duplicada(
                "INV_NOVA_REPROC",
                "12345678000199",
                {"paid_at": paid_at, "total_cents": 25000},
            )
        finally:
            settings.nfse_output_dir = orig

    _check("duplicata detectada via mês de hoje", res is not None)
    _check("fonte == 'duplicata_mes_valor'", (res or {}).get("fonte") == "duplicata_mes_valor")


def teste_lock_invoice_bloqueia_emissao() -> None:
    """M1 — com o lock da fatura JÁ adquirido por 'outro processo', processar_pagamento
    deve devolver acao 'em_processamento' (e HTTP 200) sem emitir. Reaproveita a cadeia
    mockada de _rodar_processar_com_emissao, adquirindo o lockfile à mão antes."""
    print("7) Lock por invoice_id bloqueia 2ª emissão (M1):")
    import os
    import tempfile
    from src.config import settings

    invoice = {"id": "INV123", "status": "paid", "payer_cpf_cnpj": "12345678000199",
               "customer_id": "cust_TESTE", "custom_variables": []}
    empresa = _empresa_mock()
    repo = MagicMock()
    repo.buscar_por_customer_id.return_value = empresa
    repo.buscar_por_cnpj.return_value = empresa
    emitiu = {"chamado": False}

    async def fake_emitir(invoice, empresa):  # noqa: ARG001
        emitiu["chamado"] = True
        return {"sucesso": True, "numero_nfse": "999"}

    with tempfile.TemporaryDirectory() as tmp:
        orig = settings.nfse_output_dir
        # Simula outro processo: cria o lockfile recente ANTES de processar.
        lockfile = Path(tmp) / ".lock_nfse_INV123"
        fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            settings.nfse_output_dir = tmp
            with patch.object(webhook_server, "IuguClient") as MockClient, \
                    patch.object(webhook_server, "extract_cnpj_from_invoice", return_value="12345678000199"), \
                    patch.object(webhook_server, "get_repo", return_value=repo), \
                    patch("src.nfse_df.emitir_nfse", fake_emitir), \
                    patch("src.email_nfse.enviar_nfse_email", return_value=True):
                MockClient.return_value.__enter__.return_value.get_invoice.return_value = invoice
                res = asyncio.run(processar_pagamento("INV123"))
        finally:
            settings.nfse_output_dir = orig
            os.close(fd)
            try:
                lockfile.unlink()
            except OSError:
                pass

    _check("acao == 'em_processamento'", res.get("acao") == "em_processamento")
    _check("success == True", res.get("success") is True)
    _check("HTTP = 200", _status_http_webhook(res) == 200)
    _check("emitir_nfse NÃO foi chamado (lock ocupado)", emitiu["chamado"] is False)


def teste_lock_obsoleto_recupera_e_emite() -> None:
    """C3 — recuperação de lock OBSOLETO: um lockfile com mtime antigo (~now-400s,
    além do TTL=300s) E PID inexistente deve ser tratado como órfão. processar_pagamento
    RECUPERA o lock e emite normalmente (acao 'nfse_emitida')."""
    print("8) Lock OBSOLETO é recuperado e a emissão acontece (C3):")
    import os
    import tempfile
    import time
    from src.config import settings

    invoice = {"id": "INV123", "status": "paid", "payer_cpf_cnpj": "12345678000199",
               "customer_id": "cust_TESTE", "custom_variables": []}
    empresa = _empresa_mock()
    repo = MagicMock()
    repo.buscar_por_customer_id.return_value = empresa
    repo.buscar_por_cnpj.return_value = empresa
    emitiu = {"chamado": False}

    async def fake_emitir(invoice, empresa):  # noqa: ARG001
        emitiu["chamado"] = True
        return {"sucesso": True, "numero_nfse": "777"}

    # PID que (quase) certamente não existe — POSIX trata como morto; em plataformas
    # sem suporte o staleness cai na regra de idade (mtime ~400s > TTL 300s).
    pid_morto = 2147480000

    with tempfile.TemporaryDirectory() as tmp:
        orig = settings.nfse_output_dir
        lockfile = Path(tmp) / ".lock_nfse_INV123"
        # Lockfile "abandonado por um processo morto": grava PID inexistente e
        # envelhece o mtime para além do TTL.
        lockfile.write_text(str(pid_morto), encoding="utf-8")
        antigo = time.time() - 400
        os.utime(str(lockfile), (antigo, antigo))
        try:
            settings.nfse_output_dir = tmp
            with patch.object(webhook_server, "IuguClient") as MockClient, \
                    patch.object(webhook_server, "extract_cnpj_from_invoice", return_value="12345678000199"), \
                    patch.object(webhook_server, "get_repo", return_value=repo), \
                    patch.object(webhook_server, "_verificar_nfse_duplicada", return_value=None), \
                    patch("src.nfse_df.emitir_nfse", fake_emitir), \
                    patch("src.email_nfse.enviar_nfse_email", return_value=True):
                MockClient.return_value.__enter__.return_value.get_invoice.return_value = invoice
                res = asyncio.run(processar_pagamento("INV123"))
        finally:
            settings.nfse_output_dir = orig

    _check("acao == 'nfse_emitida' (lock obsoleto recuperado)", res.get("acao") == "nfse_emitida")
    _check("emitir_nfse FOI chamado", emitiu["chamado"] is True)
    _check("HTTP = 200", _status_http_webhook(res) == 200)


def teste_lock_liberado_permite_segunda_emissao() -> None:
    """C3 — liberação do lock: rodar processar_pagamento 2x em sequência (sem lock
    prévio, _verificar_nfse_duplicada mockado None) — a 2ª também emite. Prova que o
    finally liberou o lockfile (a fila não trava)."""
    print("9) Lock é liberado entre execuções — 2ª emissão também acontece (C3):")
    import tempfile
    from src.config import settings

    invoice = {"id": "INV123", "status": "paid", "payer_cpf_cnpj": "12345678000199",
               "customer_id": "cust_TESTE", "custom_variables": []}
    empresa = _empresa_mock()
    repo = MagicMock()
    repo.buscar_por_customer_id.return_value = empresa
    repo.buscar_por_cnpj.return_value = empresa
    chamadas = {"n": 0}

    async def fake_emitir(invoice, empresa):  # noqa: ARG001
        chamadas["n"] += 1
        return {"sucesso": True, "numero_nfse": str(chamadas["n"])}

    with tempfile.TemporaryDirectory() as tmp:
        orig = settings.nfse_output_dir
        try:
            settings.nfse_output_dir = tmp
            with patch.object(webhook_server, "IuguClient") as MockClient, \
                    patch.object(webhook_server, "extract_cnpj_from_invoice", return_value="12345678000199"), \
                    patch.object(webhook_server, "get_repo", return_value=repo), \
                    patch.object(webhook_server, "_verificar_nfse_duplicada", return_value=None), \
                    patch("src.nfse_df.emitir_nfse", fake_emitir), \
                    patch("src.email_nfse.enviar_nfse_email", return_value=True):
                MockClient.return_value.__enter__.return_value.get_invoice.return_value = invoice
                res1 = asyncio.run(processar_pagamento("INV123"))
                res2 = asyncio.run(processar_pagamento("INV123"))
        finally:
            settings.nfse_output_dir = orig

    _check("1ª emissão -> 'nfse_emitida'", res1.get("acao") == "nfse_emitida")
    _check("2ª emissão -> 'nfse_emitida' (lock liberado)", res2.get("acao") == "nfse_emitida")
    _check("emitir_nfse chamado 2x", chamadas["n"] == 2)


def teste_cron_sob_lock_ocupado_nao_emite() -> None:
    """C2/C3 — cron sob lock OCUPADO: com um lockfile ativo (recente, PID vivo =
    os.getpid()) para a fatura, _emitir_nfse_para_fatura NÃO deve emitir. O resultado
    fica sem nfse_emitida e o nfse_erro fala de lock."""
    print("10) Cron respeita o lock ocupado e NÃO emite (C2):")
    import os
    import tempfile
    from src.config import settings
    from src import scheduled_invoices
    from src.scheduled_invoices import ResultadoEmpresa, _emitir_nfse_para_fatura

    invoice = {"id": "INV_CRON", "status": "paid", "total_cents": 10000,
               "customer_id": "cust_TESTE", "custom_variables": []}
    emp = _empresa_mock()
    client = MagicMock()
    client.get_invoice.return_value = invoice
    resultado = ResultadoEmpresa(cnpj=emp.cnpj, razao_social=emp.razao_social,
                                 sucesso=True, invoice_id="INV_CRON")
    emitiu = {"chamado": False}

    async def fake_emitir(invoice, empresa):  # noqa: ARG001
        emitiu["chamado"] = True
        return {"sucesso": True, "numero_nfse": "555"}

    with tempfile.TemporaryDirectory() as tmp:
        orig = settings.nfse_output_dir
        # Simula o webhook (outro contexto) já emitindo: lockfile recente, PID vivo.
        lockfile = Path(tmp) / ".lock_nfse_INV_CRON"
        fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        try:
            settings.nfse_output_dir = tmp
            with patch.object(scheduled_invoices, "emitir_nfse", fake_emitir, create=True), \
                    patch("src.nfse_df.emitir_nfse", fake_emitir):
                _emitir_nfse_para_fatura(emp, resultado, client)
        finally:
            settings.nfse_output_dir = orig
            os.close(fd)
            try:
                lockfile.unlink()
            except OSError:
                pass

    _check("cron NÃO emitiu (nfse_emitida != True)", resultado.nfse_emitida is not True)
    _check("emitir_nfse NÃO foi chamado (lock ocupado)", emitiu["chamado"] is False)
    _check("nfse_erro menciona lock", "lock" in (resultado.nfse_erro or "").lower())


if __name__ == "__main__":
    teste_status_http_webhook()
    teste_web011_rejeicao()
    teste_emissao_sucesso()
    teste_auto_envio_email_com_anexo()
    teste_guardrail_regra1_deterministica()
    teste_guardrail_janela_mes()
    teste_lock_invoice_bloqueia_emissao()
    teste_lock_obsoleto_recupera_e_emite()
    teste_lock_liberado_permite_segunda_emissao()
    teste_cron_sob_lock_ocupado_nao_emite()
    print()
    if _falhas:
        print(f"[X] {_falhas} verificacao(oes) FALHARAM")
        sys.exit(1)
    print("[OK] Todos os testes passaram")
