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

import asyncio
import base64
import gzip
import json
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

import httpx  # noqa: E402
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


def _nfse_xml(cnpj_tomador: str = "36342291000143", valor: str = "1.00") -> str:
    """NFS-e como a Sefin devolve: dados da nota + a DPS de origem embutida."""
    return (
        '<?xml version="1.0" encoding="utf-8"?><NFSe versao="1.01" xmlns="http://www.sped.fazenda.gov.br/nfse">'
        f'<infNFSe Id="NFS{CHAVE}"><xLocEmi>Brasília</xLocEmi><nNFSe>1</nNFSe><cStat>100</cStat>'
        f'<DPS versao="1.01"><infDPS Id="{DPS_ID}"><toma><CNPJ>{cnpj_tomador}</CNPJ></toma>'
        f"<valores><vServPrest><vServ>{valor}</vServ></vServPrest></valores></infDPS></DPS>"
        "</infNFSe></NFSe>"
    )


NFSE_XML = _nfse_xml()


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


class _Cenario:
    """Monta o ambiente offline de _emitir_nacional num diretório temporário."""

    def __init__(self, tmp: Path, respostas: list, dry_run: bool = False):
        self.tmp, self.respostas = tmp, list(respostas)
        self.chamadas: list[tuple[str, str]] = []
        self.ids_montados: list[str] = []
        self.corpos_post: list[dict] = []
        self.to_thread: list = []
        self.dry_run = dry_run

    def _montar(self, *, empresa, servico, endereco_tomador, invoice, numero_dps):
        dps_id = "DPS530010822798774500014200001" + numero_dps.zfill(15)
        self.ids_montados.append(dps_id)
        return DPS_NOTA_CONTROL.replace(DPS_ID, dps_id), dps_id

    def _sefin(self, metodo, caminho, corpo=None):
        self.chamadas.append((metodo, caminho))
        if corpo is not None:
            self.corpos_post.append(corpo)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def emitir(self, invoice_id: str = "INV_A", competencia=None) -> dict:
        real_to_thread = asyncio.to_thread

        async def espia(fn, *a, **k):
            self.to_thread.append(fn)
            return await real_to_thread(fn, *a, **k)

        empresa = nfse_df.Empresa(cnpj="36342291000143", razao_social="MEGASUPORTE SERVICOS DE TI LTDA",
                                  email="", codigo_servico="010701", descricao_servico="TESTE", aliquota_iss=2.0)
        invoice = {"id": invoice_id, "total_cents": 100, "payer_address_city": "Brasília",
                   "payer_address_state": "DF", "payer_address_zip_code": "70040010"}
        s, n, t = nfse_df.settings, nfse_df, self.tmp
        with patch.object(s, "nfse_output_dir", str(t)), patch.object(s, "nfse_padrao", "nacional"), \
                patch.object(s, "nfse_ambiente", "homologacao"), patch.object(s, "nfse_dry_run", self.dry_run), \
                patch.object(n, "_COUNTER_FILE", t / ".contador_dps.json"), \
                patch.object(n, "_COUNTER_RPS_FILE", t / ".contador_rps.json"), \
                patch.object(n, "_COUNTER_LOCK_FILE", t / ".contador_dps.lock"), \
                patch.object(n, "NFELIB_DISPONIVEL", True), patch.object(n, "ERPBRASIL_DISPONIVEL", True), \
                patch.object(n, "_validar_configuracao", return_value=[]), \
                patch.object(n, "_montar_xml_dps", side_effect=self._montar), \
                patch.object(n, "_assinar_xml", side_effect=lambda xml, dps_id: xml), \
                patch.object(n, "_sefin_request", side_effect=self._sefin), \
                patch.object(n.asyncio, "to_thread", espia):
            return asyncio.run(n.emitir_nfse(invoice, empresa, competencia=competencia))

    def pendentes_dps(self) -> dict:
        f = self.tmp / ".contador_dps.json"
        return json.loads(f.read_text(encoding="utf-8")).get("pendentes", {}) if f.exists() else {}


def teste_emissao() -> None:
    print("4) _emitir_nacional — emissão pela Sefin:")
    with tempfile.TemporaryDirectory() as t:
        c = _Cenario(Path(t), [(404, ERRO_E2404), (201, RESP_201)])
        r = c.emitir()
        dps_id = c.ids_montados[0]
        _check("201 -> sucesso com número e chave",
               r["sucesso"] and r["numero_nfse"] == "1" and r["chave_acesso"] == CHAVE)
        _check("codigo_verificacao = chave de acesso", r["codigo_verificacao"] == CHAVE)
        _check("ordem: GET /dps/<id> e depois POST /nfse", c.chamadas == [("GET", "/dps/" + dps_id), ("POST", "/nfse")])
        enviado = nfse_df._desempacotar_xml(c.corpos_post[0]["dpsXmlGZipB64"])
        _check("o que foi enviado já está no leiaute da Sefin (sem IM, com finNFSe)",
               "<IM>" not in enviado and "finNFSe" in enviado and dps_id in enviado)
        log = json.loads((Path(t) / "nfse_INV_A.json").read_text(encoding="utf-8"))
        _check("log nfse_<fatura>.json tem chave_acesso e padrao nacional",
               log["chave_acesso"] == CHAVE and log["padrao"] == "nacional" and log["numero_nfse"] == "1")
        _check("XML da NFS-e arquivado e apontado, byte a byte",
               Path(r["xml_retorno_path"]).read_text(encoding="utf-8") == NFSE_XML)
        _check("reserva da DPS liberada", "INV_A" not in c.pendentes_dps())
        _check("contador de RPS (ABRASF) intocado", not (Path(t) / ".contador_rps.json").exists())
        _check("chamadas à Sefin rodam via asyncio.to_thread", len(c.to_thread) == 2)

    with tempfile.TemporaryDirectory() as t:
        c = _Cenario(Path(t), [(404, ERRO_E2404), (400, ERRO_E0625), (404, ERRO_E2404), (400, ERRO_E0625)])
        r1, r2 = c.emitir(), c.emitir()
        _check("rejeição -> falha com o código da Sefin", not r1["sucesso"] and "E0625" in r1["mensagem_erro"])
        _check("rejeição não queima número: 2ª tentativa reusa o mesmo nDPS",
               len(c.ids_montados) == 2 and c.ids_montados[0] == c.ids_montados[1])
        _check("rejeição não grava índice de nota", not (Path(t) / "nfse_INV_A.json").exists())
        c.respostas = [(404, ERRO_E2404), (400, ERRO_E0625)]
        c.emitir(invoice_id="INV_B")
        _check("outra fatura recebe outro número", c.ids_montados[2] != c.ids_montados[0])

    with tempfile.TemporaryDirectory() as t:
        c = _Cenario(Path(t), [(200, {"chaveAcesso": CHAVE}),
                               (200, {"chaveAcesso": CHAVE, "nfseXmlGZipB64": _gzip_b64(NFSE_XML)})])
        r = c.emitir()
        _check("recupera_sem_reenviar: nota já aceita é recuperada, sem POST",
               r["sucesso"] and r["chave_acesso"] == CHAVE and r["numero_nfse"] == "1"
               and c.chamadas == [("GET", "/dps/" + c.ids_montados[0]), ("GET", "/nfse/" + CHAVE)])
        _check("recuperação grava o índice e libera a reserva",
               (Path(t) / "nfse_INV_A.json").exists() and "INV_A" not in c.pendentes_dps())

    with tempfile.TemporaryDirectory() as t:
        outra = _gzip_b64(_nfse_xml(cnpj_tomador="11222333000181"))
        c = _Cenario(Path(t), [(200, {"chaveAcesso": CHAVE}), (200, {"chaveAcesso": CHAVE, "nfseXmlGZipB64": outra})])
        r = c.emitir()
        _check("nota existente de OUTRO tomador não é adotada (número de DPS reaproveitado)",
               not r["sucesso"] and CHAVE in r["mensagem_erro"] and not (Path(t) / "nfse_INV_A.json").exists()
               and all(m != "POST" for m, _ in c.chamadas))

    with tempfile.TemporaryDirectory() as t:
        c = _Cenario(Path(t), [(404, ERRO_E2404), httpx.ReadTimeout("tempo esgotado")])
        r = c.emitir()
        _check("timeout -> falha sem exceção, reserva mantida",
               not r["sucesso"] and "Sefin" in r["mensagem_erro"] and "INV_A" in c.pendentes_dps())

    with tempfile.TemporaryDirectory() as t:
        c = _Cenario(Path(t), [], dry_run=True)
        r = c.emitir()
        _check("dry-run: monta e assina, zero chamadas à Sefin",
               r["sucesso"] and r["numero_nfse"].startswith("DRY-RUN-DPS-") and c.chamadas == []
               and not (Path(t) / "nfse_INV_A.json").exists())


def teste_despacho() -> None:
    print("5) emitir_nfse — despacho por padrão e competência:")

    def backend(padrao: str, competencia):
        usados: list[str] = []

        async def abrasf(*a, **k):
            usados.append("abrasf204")
            return {"sucesso": False}

        async def nacional(*a, **k):
            usados.append("nacional")
            return {"sucesso": False}

        with patch.object(nfse_df.settings, "nfse_padrao", padrao), \
                patch.object(nfse_df, "_emitir_abrasf204", abrasf), patch.object(nfse_df, "_emitir_nacional", nacional):
            asyncio.run(nfse_df.emitir_nfse({"id": "X"}, None, competencia=competencia))
        return usados[0]

    _check("despacho_retroativo: 2026-10 com padrao=nacional vai pro ABRASF", backend("nacional", "2026-10") == "abrasf204")
    _check("competência 2026-11 fica no nacional", backend("nacional", "2026-11") == "nacional")
    _check("sem competência fica no nacional", backend("nacional", None) == "nacional")
    _check("padrao=abrasf204 segue no ABRASF", backend("abrasf204", None) == "abrasf204")


if __name__ == "__main__":
    teste_ajuste_leiaute()
    teste_transporte()
    teste_nao_json()
    teste_emissao()
    teste_despacho()
    print()
    if _falhas:
        print(f"{_falhas} verificação(ões) FALHARAM.")
        sys.exit(1)
    print("Todas as verificações passaram.")
