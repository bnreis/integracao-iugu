# Prova em HOMOLOGACAO (producao restrita) da emissao pela API Nacional com o CODIGO REAL
# (nfse_df.emitir_nfse). NAO toca producao: forca homologacao e um diretorio de saida proprio
# so neste processo. Resultados em docs/incidente_l120_simples_nacional_2026-11.md.
#
# Rodar na VPS, numa COPIA do codigo (o servico em producao nao e tocado), a partir do repo local:
#   ssh studio-vps 'cd /tmp/homolog_nacional/src && sudo -u iugu env SAIDA=/tmp/homolog_nacional/saida \
#       CASO=A /opt/integracao-iugu-megateam/.venv/bin/python -' < scripts/teste_api_nacional_homolog.py
#
# SAIDA: UM diretorio so, mantido entre os casos (contador de DPS, reservas e indices ficam la).
# CASO (um ou varios, separados por virgula):
#   A normal R$1,00, codigo de servico como esta no cadastro real ("01.07"), tomador com e-mail
#   B ISS retido (valor bruto R$2,00 em custom_variables)
#   C tomador de Sao Paulo/SP, R$3,00
#   D recuperacao: estado de "timeout depois do aceite" do caso A -> mesma chave, sem POST
#   E reenvio direto da DPS ja aceita do caso A -> o que a Sefin responde a DPS repetida
#   F contador de DPS atrasado: 1a tentativa recusa sem enviar, 2a usa o numero seguinte (R$4,00)
import asyncio
import json
import os
import sys
import time
from pathlib import Path

SAIDA = Path(os.environ["SAIDA"])
SAIDA.mkdir(parents=True, exist_ok=True)
os.environ.update(NFSE_PADRAO="nacional", NFSE_AMBIENTE="homologacao",
                  NFSE_OUTPUT_DIR=str(SAIDA), NFSE_DRY_RUN="false")

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="WARNING")

from src import nfse_df as n  # noqa: E402
from src.config import settings  # noqa: E402

# Travas: se qualquer uma falhar, nada e enviado.
assert settings.nfse_ambiente == "homologacao" and settings.nfse_padrao == "nacional"
assert "producaorestrita" in n._sefin_base(), n._sefin_base()
assert Path(n._COUNTER_FILE).parent == SAIDA and Path(settings.nfse_output_dir) == SAIDA

CONTADOR = Path(n._COUNTER_FILE)
ESTADO = SAIDA / "estado.json"
if not CONTADOR.exists():  # numero alto, inedito na serie de homologacao (900001 ja foi usado)
    CONTADOR.write_text(json.dumps({"ultimo_numero": int(os.environ.get("NDPS_INICIAL", "900100"))}))

_orig = n._sefin_request


def _espia(metodo, caminho, corpo=None):
    status, resp = _orig(metodo, caminho, corpo)
    print(f"   >> {metodo} {caminho[:70]} -> HTTP {status} | campos: {sorted(resp.keys())}")
    return status, resp


n._sefin_request = _espia


def _estado() -> dict:
    return json.loads(ESTADO.read_text()) if ESTADO.exists() else {}


def emitir(caso: str, cents: int, *, cidade="Brasília", uf="DF", cep="70040010",
           retido=False, extras=None) -> dict:
    empresa = n.Empresa(
        cnpj="36342291000143", razao_social="MEGASUPORTE SERVICOS DE TI LTDA",
        email="homologacao@example.com", codigo_servico=os.environ.get("CODIGO_SERVICO", "01.07"),
        descricao_servico="TESTE DE INTEGRACAO - SEM VALOR FISCAL", aliquota_iss=2.0,
        iss_retido=retido, inscricao_municipal=os.environ.get("IM_TOMADOR", ""),
    )
    invoice = {"id": f"HOMOLOG_{caso}", "total_cents": cents,
               "payer_name": empresa.razao_social, "payer_cpf_cnpj": empresa.cnpj,
               "payer_address_street": "SBN Quadra 1", "payer_address_number": "S/N",
               "payer_address_district": "Asa Norte", "payer_address_city": cidade,
               "payer_address_state": uf, "payer_address_zip_code": cep, **(extras or {})}
    r = asyncio.run(n.emitir_nfse(invoice, empresa))
    print(f"[{caso}] sucesso={r['sucesso']} ambiente={r.get('ambiente')} numero={r.get('numero_nfse')} "
          f"chave={r.get('chave_acesso')}")
    if not r["sucesso"]:
        print(f"[{caso}] ERRO: {r.get('mensagem_erro')}")
    else:
        log = json.loads((SAIDA / f"nfse_HOMOLOG_{caso}.json").read_text(encoding="utf-8"))
        est = _estado()
        est[caso] = {"numero_dps": str(log.get("rps_numero")), "chave": r["chave_acesso"]}
        ESTADO.write_text(json.dumps(est, indent=1))
    return r


for caso in os.environ.get("CASO", "A").upper().split(","):
    print(f"===== CASO {caso} =====")
    if caso == "A":
        emitir("A", 100)
    elif caso == "B":
        emitir("B", 196, retido=True, extras={"custom_variables": [{"name": "valor_bruto_nfse", "value": "200"}]})
    elif caso == "C":
        emitir("C", 300, cidade="São Paulo", uf="SP", cep="01310100")
    elif caso == "D":
        a = _estado()["A"]
        (SAIDA / "nfse_HOMOLOG_A.json").unlink(missing_ok=True)
        cont = json.loads(CONTADOR.read_text())
        cont.setdefault("pendentes", {})["HOMOLOG_A"] = {"numero": int(a["numero_dps"]), "ts": time.time()}
        CONTADOR.write_text(json.dumps(cont))
        r = emitir("A", 100)
        print("[D] mesma chave do caso A:", r.get("chave_acesso") == a["chave"])
    elif caso == "E":
        a = _estado()["A"]
        assinada = sorted(SAIDA.glob(f"dps_{a['numero_dps']}_dps_assinada_*.xml"))[-1].read_text(encoding="utf-8")
        status, resp = n._sefin_request("POST", "/nfse", {"dpsXmlGZipB64": n._empacotar_dps(assinada)})
        resp.pop("nfseXmlGZipB64", None)
        print(f"[E] HTTP {status}: {json.dumps(resp, ensure_ascii=False)[:900]}")
    elif caso == "F":
        cont = json.loads(CONTADOR.read_text())
        cont["ultimo_numero"] -= 1
        CONTADOR.write_text(json.dumps(cont))
        r1 = emitir("F", 400)
        print("[F] 1a tentativa recusou sem enviar:", not r1["sucesso"])
        emitir("F", 400)
    else:
        print("caso desconhecido:", caso)
print("evidencias em", SAIDA)
