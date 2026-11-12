# Teste isolado da API Nacional (producao restrita = homologacao). NAO usa contadores, NAO
# arquiva em nfse_emitidas, NAO toca producao. Resultado em docs/incidente_l120_simples_nacional_2026-11.md.
# Rodar na VPS (o A1 e o .env ficam la), a partir da raiz do repo local:
#   ssh studio-vps 'cd /opt/integracao-iugu-megateam && sudo -u iugu env SEM_IBSCBS=1 NDPS=900002 .venv/bin/python -' < scripts/teste_api_nacional_homolog.py
# NDPS precisa ser inedito (900001 ja virou a NFS-e de teste n. 1 em 12/11/2026).
import os, gzip, base64, json, tempfile, httpx
from pathlib import Path
from lxml import etree
from src.config import settings
from src.spreadsheet import Empresa
from src import nfse_df as n

NS = "http://www.sped.fazenda.gov.br/nfse"
Q = lambda t: f"{{{NS}}}{t}"


def _set(k, v):
    try:
        setattr(settings, k, v)
    except Exception:
        object.__setattr__(settings, k, v)


def ajustar(xml: str) -> str:
    """Adapta a DPS (leiaute Nota Control v1.01) ao leiaute que a Sefin Nacional pede."""
    root = etree.fromstring(xml.encode("utf-8"))
    inf = root.find(Q("infDPS"))
    ibs = inf.find(Q("IBSCBS"))
    tp_emit = inf.find(Q("tpEmit"))
    # AJUSTES (editados a cada rodada conforme o erro de schema devolvido)
    fin = ibs.find(Q("finNFSe"))
    ibs.remove(fin)
    fin.tail = None
    tp_emit.addnext(fin)
    # cTribMun nacional = 3 digitos; DF administra 01.07.01.001 (aliq 2%, desde 27/10/2026)
    # segundo adn.../parametrizacao/5300108/01.07.01.001/<data>/aliquota. "1071" e do leiaute ISSnet.
    for el in root.iter(Q("cTribMun")):
        el.text = os.environ.get("CTRIBMUN", "001")
    # cNBS: depois de xDescServ nada e aceito -> tentar antes
    nbs = next(root.iter(Q("cNBS")))
    desc = next(root.iter(Q("xDescServ")))
    nbs.getparent().remove(nbs)
    desc.addprevious(nbs)
    # IBSCBS/valores/trib: CST e cClassTrib direto em <trib>, sem o wrapper gIBSCBS
    g = next(root.iter(Q("gIBSCBS")))
    for filho in list(g):
        g.addprevious(filho)
    g.getparent().remove(g)
    # cIndOp "000000" (placeholder nosso) nao existe na calculadora IBS/CBS.
    # Simples Nacional: testar sem o grupo IBSCBS (SEM_IBSCBS=1)
    if os.environ.get("SEM_IBSCBS"):
        inf.remove(ibs)
    # E0120: DF nao tem dados complementares no CNC -> IM do prestador nao pode ir
    prest = inf.find(Q("prest"))
    prest.remove(prest.find(Q("IM")))
    # E0121: emitente = prestador -> xNome do prestador nao pode ir
    prest.remove(prest.find(Q("xNome")))
    # E0625: Simples Nacional (opSimpNac=3) sem retencao -> pAliq nao pode ir
    for el in root.iter(Q("pAliq")):
        el.getparent().remove(el)
    return etree.tostring(root, encoding="unicode")


out = Path(tempfile.mkdtemp(prefix="teste_api_nacional_"))
_set("nfse_ambiente", "homologacao")  # tpAmb=2
_set("nfse_output_dir", str(out))  # defesa: nada cai em nfse_emitidas
assert settings.nfse_ambiente == "homologacao"

empresa = Empresa(cnpj="36342291000143", razao_social="MEGASUPORTE SERVICOS DE TI LTDA",
                  email="", codigo_servico=settings.nfse_codigo_servico_padrao,
                  descricao_servico="TESTE DE INTEGRACAO - SEM VALOR FISCAL",
                  aliquota_iss=settings.nfse_aliquota_iss_padrao)
invoice = {"id": "TESTE_API_NACIONAL", "total_cents": 100,
           "payer_name": empresa.razao_social, "payer_cpf_cnpj": empresa.cnpj,
           "payer_address_street": "SBN Quadra 1", "payer_address_number": "S/N",
           "payer_address_district": "Asa Norte", "payer_address_city": "Brasília",
           "payer_address_state": "DF", "payer_address_zip_code": "70040010"}
servico = n.DadosServico(codigo_servico=empresa.codigo_servico, descricao=empresa.descricao_servico,
                         valor_cents=100, aliquota_iss=empresa.aliquota_iss)
xml, dps_id = n._montar_xml_dps(empresa=empresa, servico=servico,
                                endereco_tomador=n.extrair_endereco_tomador(invoice),
                                invoice=invoice, numero_dps=os.environ.get("NDPS", "900001"))
xml = ajustar(xml)
assinado = n._assinar_xml(xml, dps_id)
if not assinado.lstrip().startswith("<?xml"):
    assinado = '<?xml version="1.0" encoding="UTF-8"?>' + assinado
(out / "dps_assinada.xml").write_text(assinado, encoding="utf-8")
if os.environ.get("VER_XML"):
    print(xml)

payload = {"dpsXmlGZipB64": base64.b64encode(gzip.compress(assinado.encode("utf-8"))).decode()}
cert, key = n._pkcs12_to_pem_tempfiles(Path(settings.nfse_certificado_path), settings.nfse_certificado_senha)
try:
    with httpx.Client(cert=(str(cert), str(key)), timeout=60) as c:
        r = c.post("https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse", json=payload)
finally:
    os.remove(cert)
    os.remove(key)
(out / "resposta.txt").write_text(f"{r.status_code}\n{r.text}", encoding="utf-8")
print("HTTP", r.status_code)
try:
    j = r.json()
    b64 = j.pop("nfseXmlGZipB64", None)
    print(json.dumps(j, ensure_ascii=False, indent=1)[:4000])
    if b64:
        nfse = gzip.decompress(base64.b64decode(b64)).decode("utf-8")
        (out / "nfse_retorno.xml").write_text(nfse, encoding="utf-8")
        print("NFS-e de teste recebida, bytes:", len(nfse))
        print(nfse[:2500])
except Exception:
    print(r.text[:3000])
print("evidencias em", out)
