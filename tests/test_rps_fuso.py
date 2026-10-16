"""
Teste de regressão: reserva de RPS por fatura + fuso America/Sao_Paulo.

Roda 100% OFFLINE (ISSnet, assinatura e e-mail mockados; contador e logs em
diretório temporário) — seguro na máquina do Bruno e na VPS.

Como rodar (a partir da raiz do projeto):
    python tests/test_rps_fuso.py

Cobre:
  1. _proximo_numero_rps(): mesma fatura reusa o RPS; fatura nova incrementa;
     liberar reserva faz a próxima incrementar; sem invoice_id sempre incrementa;
     reserva velha expira (TTL); contador no formato antigo segue válido;
     threads concorrentes nunca recebem o mesmo número.
  2. _emitir_abrasf204(): rejeição (E232) repetida NÃO queima RPS; sucesso libera a
     reserva; a chamada SOAP roda fora do event loop (asyncio.to_thread).
  3. Fuso: 22:30 de Brasília do último dia do mês (já é o mês seguinte em UTC) gera
     data de emissão, log e mês do guardrail no dia/mês de Brasília.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("IUGU_API_TOKEN", "token-de-teste")  # Settings exige; nada é chamado

from src import nfse_df, nfse_guard, tempo  # noqa: E402

_falhas = 0


def _check(nome: str, cond: bool) -> None:
    global _falhas
    if not cond:
        _falhas += 1
    print(f"  [{'OK  ' if cond else 'FALHOU'}] {nome}")


def _contador_em(tmp: Path):
    """Redireciona o contador de RPS e o lock para o diretório temporário."""
    return (
        patch.object(nfse_df.settings, "nfse_output_dir", str(tmp)),
        patch.object(nfse_df, "_COUNTER_RPS_FILE", tmp / ".contador_rps.json"),
        patch.object(nfse_df, "_COUNTER_LOCK_FILE", tmp / ".contador_dps.lock"),
    )


def teste_reserva_rps() -> None:
    print("1) _proximo_numero_rps — reserva por fatura:")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        p1, p2, p3 = _contador_em(tmp)
        with p1, p2, p3:
            (tmp / ".contador_rps.json").write_text('{"ultimo_numero": 189}', encoding="utf-8")
            _check("formato antigo (sem 'pendentes') segue válido -> 190",
                   nfse_df._proximo_numero_rps("INV_A") == "190")
            _check("mesma fatura reusa o RPS (190)", nfse_df._proximo_numero_rps("INV_A") == "190")
            _check("mesma fatura reusa de novo (190)", nfse_df._proximo_numero_rps("INV_A") == "190")
            _check("fatura NOVA incrementa (191)", nfse_df._proximo_numero_rps("INV_B") == "191")
            _check("sem invoice_id sempre incrementa (192, 193)",
                   nfse_df._proximo_numero_rps() == "192" and nfse_df._proximo_numero_rps() == "193")
            nfse_df._liberar_rps_pendente("INV_A")
            _check("após liberar, a mesma fatura pega número novo (194)",
                   nfse_df._proximo_numero_rps("INV_A") == "194")
            nfse_df._liberar_rps_pendente("INEXISTENTE")  # não pode estourar
            _check("liberar fatura inexistente é inofensivo", True)

            # TTL: reserva velha é descartada e a fatura recebe número novo.
            data = json.loads((tmp / ".contador_rps.json").read_text(encoding="utf-8"))
            data["pendentes"]["INV_B"]["ts"] = time.time() - nfse_df._RPS_PENDENTE_TTL_SEGUNDOS - 60
            (tmp / ".contador_rps.json").write_text(json.dumps(data), encoding="utf-8")
            _check("reserva expirada (TTL) não é reusada (195)",
                   nfse_df._proximo_numero_rps("INV_B") == "195")

        # Concorrência: faturas distintas em threads -> números únicos e contínuos.
        with tempfile.TemporaryDirectory() as t2:
            tmp2 = Path(t2)
            q1, q2, q3 = _contador_em(tmp2)
            with q1, q2, q3:
                obtidos: list[str] = []

                def pega(i: int) -> None:
                    obtidos.append(nfse_df._proximo_numero_rps(f"CONC_{i}"))

                ths = [threading.Thread(target=pega, args=(i,)) for i in range(8)]
                [th.start() for th in ths]
                [th.join() for th in ths]
                _check("8 threads -> 8 números distintos", len(set(obtidos)) == 8)
                _check("numeração contínua 1..8", sorted(map(int, obtidos)) == list(range(1, 9)))


def _empresa() -> MagicMock:
    e = MagicMock()
    e.razao_social = "TOMADOR TESTE"
    e.cnpj = "12345678000199"
    e.codigo_servico = "01.07"
    e.descricao_servico = "Serviços de TI"
    e.aliquota_iss = 2.0
    return e


def teste_emissao_nao_queima_rps() -> None:
    print("2) _emitir_abrasf204 — rejeição repetida não queima RPS:")
    invoice = {"id": "INV_E232", "total_cents": 58000, "total_paid_cents": 58000}
    e232 = (
        '<r><ListaMensagemRetorno><MensagemRetorno><Codigo>E232</Codigo>'
        "</MensagemRetorno></ListaMensagemRetorno></r>"
    )
    rps_usados: list[str] = []
    threads_soap: list[int] = []
    resposta = {"xml": e232}

    def fake_montar(empresa, servico, endereco_tomador, invoice, numero_rps, competencia=None):
        rps_usados.append(numero_rps)
        return "<x/>", f"rps{numero_rps}"

    def fake_soap(envelope, endpoint):
        threads_soap.append(threading.get_ident())
        return resposta["xml"], 200

    def fake_parse(xml):
        if "E232" in xml:
            return {"sucesso": False, "mensagem_erro": "[E232] erro", "mensagens": ["E232"]}
        return {"sucesso": True, "numero_nfse": "99", "codigo_verificacao": "ABC", "mensagens": []}

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        p1, p2, p3 = _contador_em(tmp)
        with p1, p2, p3, \
                patch.object(nfse_df, "ERPBRASIL_DISPONIVEL", True), \
                patch.object(nfse_df.settings, "nfse_dry_run", False), \
                patch.object(nfse_df, "_validar_configuracao", return_value=[]), \
                patch.object(nfse_df, "extrair_endereco_tomador", return_value={}), \
                patch.object(nfse_df, "_montar_xml_rps_abrasf", side_effect=fake_montar), \
                patch.object(nfse_df, "_assinar_xml", side_effect=lambda x, i: x), \
                patch.object(nfse_df, "_reposicionar_signature_dentro_de_rps", side_effect=lambda x: x), \
                patch.object(nfse_df, "_envelopar_soap_abrasf", side_effect=lambda x: x), \
                patch.object(nfse_df, "_abrasf_endpoint", return_value="https://fake"), \
                patch.object(nfse_df, "_arquivar", return_value=tmp / "x.xml"), \
                patch.object(nfse_df, "_enviar_soap_abrasf", side_effect=fake_soap), \
                patch.object(nfse_df, "_parsear_resposta_abrasf", side_effect=fake_parse), \
                patch.object(nfse_df, "_gravar_log_nfse", return_value=None):
            (tmp / ".contador_rps.json").write_text('{"ultimo_numero": 100}', encoding="utf-8")

            async def rodar():
                loop_thread = threading.get_ident()
                r = [await nfse_df._emitir_abrasf204(invoice, _empresa()) for _ in range(3)]
                resposta["xml"] = "<ok/>"
                r.append(await nfse_df._emitir_abrasf204(invoice, _empresa()))
                return loop_thread, r

            loop_thread, res = asyncio.run(rodar())

            _check("3 rejeições E232 usaram o MESMO RPS (101)", rps_usados[:3] == ["101", "101", "101"])
            _check("4ª tentativa (sucesso) também usou 101", rps_usados[3] == "101")
            _check("rejeições retornam sucesso=False", all(not r["sucesso"] for r in res[:3]))
            _check("sucesso retorna sucesso=True", res[3]["sucesso"] is True)
            cont = json.loads((tmp / ".contador_rps.json").read_text(encoding="utf-8"))
            _check("contador avançou só 1 (101), não 4", cont["ultimo_numero"] == 101)
            _check("reserva liberada após o sucesso", "INV_E232" not in (cont.get("pendentes") or {}))
            _check("SOAP rodou fora da thread do event loop",
                   all(t_ != loop_thread for t_ in threads_soap))
            nxt = nfse_df._proximo_numero_rps("INV_OUTRA")
            _check("próxima fatura recebe 102 (sem buracos)", nxt == "102")


def teste_fuso() -> None:
    print("3) Fuso America/Sao_Paulo:")
    utc = datetime(2026, 11, 1, 1, 30, tzinfo=timezone.utc)  # = 31/10/2026 22:30 em Brasília
    _check("01/11 01:30 UTC é 31/10 em Brasília",
           utc.astimezone(tempo.FUSO_BRASIL).date() == date(2026, 10, 31))
    _check("agora_br() tem offset -03:00", tempo.agora_br().utcoffset().total_seconds() == -3 * 3600)
    _check("hoje_br() == agora_br().date()", tempo.hoje_br() == tempo.agora_br().date())

    dia_br = date(2026, 10, 31)

    # a) o log de NFS-e grava data_emissao/competência no dia de Brasília
    with tempfile.TemporaryDirectory() as t:
        with patch.object(nfse_df.settings, "nfse_output_dir", t), \
                patch.object(nfse_df, "hoje_br", return_value=dia_br):
            res = MagicMock(numero_nfse="1", codigo_verificacao="X", xml_enviado_path=None,
                            xml_retorno_path=None, pdf_path=None, ambiente="producao")
            caminho = nfse_df._gravar_log_nfse(
                {"id": "INV_FUSO", "total_cents": 10000}, _empresa(), res, rps_numero="7")
            log = json.loads(Path(caminho).read_text(encoding="utf-8"))
            _check("log: data_emissao = 2026-10-31", log["data_emissao"] == "2026-10-31")
            _check("log: competencia = 2026-10", log["competencia"] == "2026-10")

    # b) guardrail: mês de 'hoje' é o de Brasília, então enxerga a nota de 31/10
    with tempfile.TemporaryDirectory() as t:
        with patch.object(nfse_guard.settings, "nfse_output_dir", t), \
                patch.object(nfse_guard, "hoje_br", return_value=dia_br):
            (Path(t) / "nfse_INV_ANT.json").write_text(json.dumps({
                "invoice_id": "INV_ANT", "cnpj": "12345678000199", "sucesso": True,
                "data_emissao": "2026-10-31", "numero_nfse": "5",
            }), encoding="utf-8")
            # fatura sem paid_at: só o "mês de hoje" pode casar com o log de outubro.
            dup = nfse_guard._verificar_nfse_duplicada(
                "INV_NOVA", "12345678000199", {"id": "INV_NOVA"})
            _check("guardrail barra 2ª nota do cliente no mês de Brasília (22:30 de 31/10)",
                   dup is not None and dup.get("fonte") == "duplicata_cliente_mes")


def main() -> int:
    teste_reserva_rps()
    teste_emissao_nao_queima_rps()
    teste_fuso()
    print()
    if _falhas:
        print(f"FALHARAM {_falhas} verificação(ões).")
        return 1
    print("Todas as verificações passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
