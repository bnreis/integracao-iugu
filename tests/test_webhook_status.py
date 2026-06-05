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


if __name__ == "__main__":
    teste_status_http_webhook()
    teste_web011_rejeicao()
    teste_emissao_sucesso()
    teste_auto_envio_email_com_anexo()
    print()
    if _falhas:
        print(f"[X] {_falhas} verificacao(oes) FALHARAM")
        sys.exit(1)
    print("[OK] Todos os testes passaram")
