"""
Teste de regressão: emissão de NFS-e pela API Nacional (Sefin Nacional, REST).

Roda 100% OFFLINE (Sefin, assinatura e montagem via nfelib mockadas; contadores e
logs em diretório temporário) — seguro na máquina do Bruno e na VPS.

Como rodar (a partir da raiz do projeto):
    python tests/test_emissao_nacional.py

Os corpos de resposta e a DPS de exemplo são os REAIS do teste em homologação de
12/11/2026 (docs/incidente_l120_simples_nacional_2026-11.md, seção 8).
"""
from __future__ import annotations

import base64
import gzip
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("IUGU_API_TOKEN", "token-de-teste")  # Settings exige; nada é chamado

from lxml import etree  # noqa: E402

from src import nfse_df  # noqa: E402

_falhas = 0


def _check(nome: str, cond: bool) -> None:
    global _falhas
    if not cond:
        _falhas += 1
    print(f"  [{'OK  ' if cond else 'FALHOU'}] {nome}")


# Saída de _montar_xml_dps (leiaute Nota Control v1.01) — a que a Sefin recusou com E1235.
DPS_ID = "DPS530010822798774500014200001000000000900001"
DPS_NOTA_CONTROL = (
    '<DPS xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01">'
    f'<infDPS Id="{DPS_ID}"><tpAmb>2</tpAmb>'
    "<dhEmi>2026-11-12T14:17:52-03:00</dhEmi><verAplic>iugu-nfse-df-0.3</verAplic><serie>00001</serie>"
    "<nDPS>900001</nDPS><dCompet>2026-11-12</dCompet><tpEmit>1</tpEmit><cLocEmi>5300108</cLocEmi>"
    "<prest><CNPJ>27987745000142</CNPJ><IM>0781513100130</IM><xNome>MEGATEAM SERVICOS DE TI LTDA</xNome>"
    "<regTrib><opSimpNac>3</opSimpNac><regApTribSN>1</regApTribSN><regEspTrib>0</regEspTrib></regTrib></prest>"
    "<toma><CNPJ>36342291000143</CNPJ><xNome>MEGASUPORTE SERVICOS DE TI LTDA</xNome><end><endNac>"
    "<cMun>5300108</cMun><CEP>70040010</CEP></endNac><xLgr>SBN Quadra 1</xLgr><nro>S/N</nro>"
    "<xBairro>Asa Norte</xBairro></end></toma>"
    "<serv><locPrest><cLocPrestacao>5300108</cLocPrestacao></locPrest><cServ><cTribNac>010701</cTribNac>"
    "<cTribMun>1071</cTribMun><xDescServ>TESTE</xDescServ><cNBS>115013000</cNBS></cServ></serv>"
    "<valores><vServPrest><vServ>1.00</vServ></vServPrest><trib><tribMun><tribISSQN>1</tribISSQN>"
    "<tpRetISSQN>1</tpRetISSQN><pAliq>2.00</pAliq></tribMun><totTrib><pTotTribSN>7.48</pTotTribSN></totTrib>"
    "</trib></valores>"
    "<IBSCBS><finNFSe>0</finNFSe><indFinal>0</indFinal><cIndOp>000000</cIndOp><indDest>0</indDest><valores>"
    "<trib><gIBSCBS><CST>900</CST><cClassTrib>900001</cClassTrib></gIBSCBS></trib></valores></IBSCBS>"
    "</infDPS></DPS>"
)


def _no(xml: str, nome: str):
    """1º elemento (ordem de documento) com esse nome local."""
    for e in etree.fromstring(xml.encode("utf-8")).iter():
        if etree.QName(e).localname == nome:
            return e
    return None


def _filhos(xml: str, nome: str) -> list[str]:
    return [etree.QName(f).localname for f in _no(xml, nome)]


def _texto(xml: str, nome: str) -> str:
    return _no(xml, nome).text


def teste_ajuste_leiaute() -> None:
    print("1) _ajustar_dps_para_sefin — leiaute aceito pela Sefin em 12/11/2026:")
    x = nfse_df._ajustar_dps_para_sefin(DPS_NOTA_CONTROL)
    _check("infDPS na ordem aceita (finNFSe após tpEmit, sem IBSCBS)", _filhos(x, "infDPS") == [
        "tpAmb", "dhEmi", "verAplic", "serie", "nDPS", "dCompet", "tpEmit",
        "finNFSe", "cLocEmi", "prest", "toma", "serv", "valores"])
    _check("finNFSe = 0", _texto(x, "finNFSe") == "0")
    _check("prest sem IM e sem xNome", _filhos(x, "prest") == ["CNPJ", "regTrib"])
    _check("nome do tomador preservado", _filhos(x, "toma") == ["CNPJ", "xNome", "end"])
    _check("cServ: cNBS antes de xDescServ",
           _filhos(x, "cServ") == ["cTribNac", "cTribMun", "cNBS", "xDescServ"])
    _check("cTribMun = 001", _texto(x, "cTribMun") == "001")
    _check("sem retenção: sem pAliq", _filhos(x, "tribMun") == ["tribISSQN", "tpRetISSQN"])
    _check("Id da DPS preservado", f'Id="{DPS_ID}"' in x)
    _check("sem competência: dCompet inalterado", _texto(x, "dCompet") == "2026-11-12")

    r = nfse_df._ajustar_dps_para_sefin(DPS_NOTA_CONTROL, iss_retido=True)
    _check("retido: tpRetISSQN=2 e pAliq mantido",
           _texto(r, "tpRetISSQN") == "2"
           and _filhos(r, "tribMun") == ["tribISSQN", "tpRetISSQN", "pAliq"]
           and _texto(r, "pAliq") == "2.00")

    with patch.object(nfse_df, "hoje_br", return_value=date(2026, 11, 12)):
        atual = nfse_df._ajustar_dps_para_sefin(DPS_NOTA_CONTROL, competencia="2026-11")
        passado = nfse_df._ajustar_dps_para_sefin(DPS_NOTA_CONTROL, competencia="2026-10")
    _check("competência do mês corrente não vira data futura", _texto(atual, "dCompet") == "2026-11-12")
    _check("competência de mês passado = último dia do mês", _texto(passado, "dCompet") == "2026-10-31")


CHAVE = "53001082227987745000142000000000000126112332975926"
NFSE_XML = (
    '<?xml version="1.0" encoding="utf-8"?><NFSe versao="1.01" xmlns="http://www.sped.fazenda.gov.br/nfse">'
    f'<infNFSe Id="NFS{CHAVE}"><xLocEmi>Brasília</xLocEmi><nNFSe>1</nNFSe><cStat>100</cStat></infNFSe></NFSe>'
)


def _gzip_b64(texto: str) -> str:
    return base64.b64encode(gzip.compress(texto.encode("utf-8"))).decode()


RESP_201 = {"tipoAmbiente": 2, "versaoAplicativo": "SefinNacional_1.6.0", "idDps": "NFS" + CHAVE,
            "chaveAcesso": CHAVE, "nfseXmlGZipB64": _gzip_b64(NFSE_XML), "alertas": None}
ERRO_E0625 = {"tipoAmbiente": 2, "idDPS": DPS_ID, "erros": [{
    "Codigo": "E0625", "Descricao": "Não é permitido informar alíquota quando não há indicação de retenção do ISSQN",
    "Complemento": None}]}
ERRO_E2404 = {"tipoAmbiente": 0, "erro": {
    "codigo": "E2404", "descricao": "Não foi gerada uma NFS-e com o identificador de DPS informado"}}


def teste_transporte() -> None:
    print("2) Sefin — empacotamento e leitura da resposta:")
    xml = "<DPS>ç</DPS>"
    pacote = nfse_df._empacotar_dps(xml)
    _check("ida e volta gzip+b64, com declaração XML",
           nfse_df._desempacotar_xml(pacote) == '<?xml version="1.0" encoding="UTF-8"?>' + xml)
    _check("declaração XML não é duplicada",
           nfse_df._desempacotar_xml(nfse_df._empacotar_dps('<?xml version="1.0" encoding="UTF-8"?>' + xml)).count("<?xml") == 1)

    ok = nfse_df._parsear_resposta_sefin(201, RESP_201)
    _check("201 -> sucesso, número e chave",
           ok["sucesso"] and ok["numero_nfse"] == "1" and ok["chave_acesso"] == CHAVE and ok["xml_nfse"] == NFSE_XML)
    rej = nfse_df._parsear_resposta_sefin(400, ERRO_E0625)
    _check("400 -> erro com código e descrição",
           not rej["sucesso"] and "E0625" in rej["mensagem_erro"] and "alíquota" in rej["mensagem_erro"])
    compl = nfse_df._parsear_resposta_sefin(400, {"erros": [
        {"Codigo": "E1235", "Descricao": "Falha no esquema XML do DF-e.", "Complemento": "cLocEmi inválido"}]})
    _check("complemento do erro aparece", "cLocEmi inválido" in compl["mensagem_erro"])
    um = nfse_df._parsear_resposta_sefin(404, ERRO_E2404)
    _check("formato 'erro' (minúsculo) também é lido", not um["sucesso"] and "E2404" in um["mensagem_erro"])
    _check("201 sem XML não é sucesso", not nfse_df._parsear_resposta_sefin(201, {"chaveAcesso": CHAVE})["sucesso"])
    _check("200 com XML não é emissão (só 201)", not nfse_df._parsear_resposta_sefin(200, RESP_201)["sucesso"])
    vazio = nfse_df._parsear_resposta_sefin(500, {})
    _check("corpo vazio -> erro citando o HTTP", not vazio["sucesso"] and "500" in vazio["mensagem_erro"])


class _RespFake:
    def __init__(self, status: int, texto: str):
        self.status_code, self.text = status, texto

    def json(self):
        import json as _json
        return _json.loads(self.text)


class _ClientFake:
    """Substitui httpx.Client: registra a chamada e devolve a resposta programada."""
    chamadas: list = []
    resposta = None

    def __init__(self, **kw):
        _ClientFake.kw = kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, metodo, url, json=None):
        _ClientFake.chamadas.append((metodo, url, json))
        return _ClientFake.resposta


def teste_nao_json() -> None:
    print("3) _sefin_request — mTLS e respostas que não são JSON:")
    with tempfile.TemporaryDirectory() as t:
        cert, key = Path(t) / "c.pem", Path(t) / "k.pem"
        cert.write_text("c"), key.write_text("k")
        _ClientFake.chamadas = []
        with patch.object(nfse_df, "_pkcs12_to_pem_tempfiles", return_value=(cert, key)), \
                patch.object(nfse_df.httpx, "Client", _ClientFake), \
                patch.object(nfse_df.settings, "nfse_ambiente", "homologacao"):
            _ClientFake.resposta = _RespFake(403, "<html><body>403 - Forbidden</body></html>")
            status, corpo = nfse_df._sefin_request("GET", "/nfse/x")
            _check("HTML 403 não explode e vira erro legível",
                   status == 403 and corpo["erro"]["codigo"] == "HTTP403" and "<html" in corpo["erro"]["descricao"])
            _check("URL = base de homologação + caminho", _ClientFake.chamadas[-1][1]
                   == "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse/x")
            _check("usa o A1 (mTLS) e não desliga a verificação TLS",
                   _ClientFake.kw.get("cert") == (str(cert), str(key)) and _ClientFake.kw.get("verify", True) is not False)
            _check("PEMs temporários removidos", not cert.exists() and not key.exists())

            cert.write_text("c"), key.write_text("k")
            _ClientFake.resposta = _RespFake(201, '{"chaveAcesso": "1"}')
            status, corpo = nfse_df._sefin_request("POST", "/nfse", {"dpsXmlGZipB64": "abc"})
            _check("JSON devolvido como dict e corpo enviado como JSON",
                   (status, corpo) == (201, {"chaveAcesso": "1"})
                   and _ClientFake.chamadas[-1][2] == {"dpsXmlGZipB64": "abc"})
        with patch.object(nfse_df.settings, "nfse_ambiente", "producao"):
            _check("produção aponta para sefin.nfse.gov.br",
                   nfse_df._sefin_base() == "https://sefin.nfse.gov.br/SefinNacional")


if __name__ == "__main__":
    teste_ajuste_leiaute()
    teste_transporte()
    teste_nao_json()
    print()
    if _falhas:
        print(f"{_falhas} verificação(ões) FALHARAM.")
        sys.exit(1)
    print("Todas as verificações passaram.")
