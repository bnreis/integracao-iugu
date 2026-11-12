# Emissão de NFS-e pela API Nacional (Sefin Nacional) — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Com `NFSE_PADRAO=nacional`, uma fatura paga da MegaTeam vira NFS-e pela API REST da Sefin Nacional (a única via aceita para Simples Nacional com competência ≥ 01/11/2026), sem duplicar nota e sem mudar o contrato de `emitir_nfse`.

**Architecture:** O backend `_emitir_nacional` de `src/nfse_df.py` mantém a montagem (`_montar_xml_dps`) e a assinatura (`_assinar_xml`) e troca só o que a Sefin exige diferente: um passo de ajuste de leiaute (`_ajustar_dps_para_sefin`), o transporte (REST + mTLS, JSON com a DPS em gzip+base64) e o parsing da resposta. A numeração de DPS reaproveita a reserva por fatura já usada no RPS, e antes de enviar o backend consulta `GET /dps/{id}` para recuperar uma nota que já tenha sido aceita numa tentativa anterior.

**Tech Stack:** Python 3, httpx (mTLS), lxml, erpbrasil.assinatura, nfelib (já instalados). Nenhuma dependência nova.

**Spec:** `docs/incidente_l120_simples_nacional_2026-11.md` (seção 8: endpoints, respostas reais e tabela dos 9 ajustes) + `scripts/teste_api_nacional_homolog.py` (prova que funcionou em 12/11/2026).

## Global Constraints

- Contrato de `emitir_nfse(invoice, empresa, competencia=None) -> dict` inalterado; só **acrescenta** a chave `chave_acesso`.
- `src/nfse_guard.py` **não é tocado**. Caminho ABRASF (`_emitir_abrasf204`) só recebe o parâmetro opcional novo dos contadores; `python tests/test_rps_fuso.py` tem que continuar 24/24.
- Nunca `verify=False`. mTLS com o A1 via `_pkcs12_to_pem_tempfiles` (PEMs temporários removidos em `finally`).
- Testes no estilo do projeto: script com `_check`, rodado por `python tests/<arquivo>.py`, 100% offline (sem pytest).
- Emissão real e homologação **só pela VPS** (`ssh studio-vps`, usuário `iugu`, instância MegaTeam). Instância MegaSuporte fica intocada.
- Commits locais por tarefa. **Push, deploy, restart e troca de `.env` só com OK explícito do Bruno**, um passo por vez.
- Endpoints (seção 8 da spec): homologação `https://sefin.producaorestrita.nfse.gov.br/SefinNacional`; produção `https://sefin.nfse.gov.br/SefinNacional`; rotas `POST /nfse`, `GET /nfse/{chave}`, `GET /dps/{idDps}`.

## Fora do escopo (decisão minha — o Bruno pulou as perguntas)

- Cancelamento pela API, PDF oficial (DANFSE) e grupo `IBSCBS` preenchido. O e-mail de nota nacional sai com o **XML + link de consulta pública** (onde o tomador baixa o PDF oficial).
- Remover o código SOAP nacional morto (`_enviar_ao_webservice`, `_envelopar_soap`, `_parsear_resposta`): limpeza depois de um mês estável.
- Decidir o destino das 5 faturas paradas (Bruno + contador). O plano só deixa o caminho pronto.

## Review Focus

1. **Timeout depois que a Sefin já aceitou a nota** → a nova tentativa tem que **recuperar** a nota, não emitir outra (Task 3, teste `recupera_sem_reenviar`).
2. **Fatura com ISS retido** (valor bruto, `tpRetISSQN=2` + `pAliq`) → leiaute ainda não provado na Sefin (Task 1 teste `retido`, Task 5 caso B).
3. **Competência retroativa anterior a 11/2026 com `NFSE_PADRAO=nacional`** → vai para o ABRASF, que ainda aceita; e competência do mês corrente não pode virar data futura (Task 1 teste `competencia`, Task 3 teste `despacho_retroativo`).
4. **Sefin responde algo que não é JSON** (HTML 403/404/5xx, queda, cert recusado) → erro legível, sem exceção de parse, reserva de número preservada (Task 2 teste `nao_json`).
5. **Tomador fora de Brasília** → `cMun` de outro município tem que ser aceito (Task 5 caso C).

---

### Task 1: Ajuste de leiaute da DPS para a Sefin + configuração

**Files:**
- Modify: `src/config.py` (junto dos campos `nfse_ws_url_*`, ~linha 127)
- Modify: `src/nfse_df.py` (nova função logo após `_montar_ibscbs_element`, ~linha 2262)
- Test: `tests/test_emissao_nacional.py` (criar, cabeçalho igual ao de `tests/test_rps_fuso.py` linhas 20–50)

**Interfaces:**
- Produces: `_ajustar_dps_para_sefin(xml: str, *, iss_retido: bool = False, competencia: Optional[str] = None) -> str`
- Produces (settings): `nfse_sefin_url_homologacao`, `nfse_sefin_url_producao`, `nfse_ctribmun_nacional`

- [ ] **Step 1: Teste que falha.** Fixture `DPS_NOTA_CONTROL` = saída de `_montar_xml_dps` (leiaute atual), copiada do teste de 12/11:

```python
DPS_NOTA_CONTROL = """<DPS xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01"><infDPS Id="DPS530010822798774500014200001000000000900001"><tpAmb>2</tpAmb><dhEmi>2026-11-12T14:17:52-03:00</dhEmi><verAplic>iugu-nfse-df-0.3</verAplic><serie>00001</serie><nDPS>900001</nDPS><dCompet>2026-11-12</dCompet><tpEmit>1</tpEmit><cLocEmi>5300108</cLocEmi><prest><CNPJ>27987745000142</CNPJ><IM>0781513100130</IM><xNome>MEGATEAM SERVICOS DE TI LTDA</xNome><regTrib><opSimpNac>3</opSimpNac><regApTribSN>1</regApTribSN><regEspTrib>0</regEspTrib></regTrib></prest><toma><CNPJ>36342291000143</CNPJ><xNome>MEGASUPORTE SERVICOS DE TI LTDA</xNome><end><endNac><cMun>5300108</cMun><CEP>70040010</CEP></endNac><xLgr>SBN Quadra 1</xLgr><nro>S/N</nro><xBairro>Asa Norte</xBairro></end></toma><serv><locPrest><cLocPrestacao>5300108</cLocPrestacao></locPrest><cServ><cTribNac>010701</cTribNac><cTribMun>1071</cTribMun><xDescServ>TESTE</xDescServ><cNBS>115013000</cNBS></cServ></serv><valores><vServPrest><vServ>1.00</vServ></vServPrest><trib><tribMun><tribISSQN>1</tribISSQN><tpRetISSQN>1</tpRetISSQN><pAliq>2.00</pAliq></tribMun><totTrib><pTotTribSN>7.48</pTotTribSN></totTrib></trib></valores><IBSCBS><finNFSe>0</finNFSe><indFinal>0</indFinal><cIndOp>000000</cIndOp><indDest>0</indDest><valores><trib><gIBSCBS><CST>900</CST><cClassTrib>900001</cClassTrib></gIBSCBS></trib></valores></IBSCBS></infDPS></DPS>"""

def _filhos(xml, caminho):   # nomes locais dos filhos de um nó, na ordem
    ...                      # lxml + QName(...).localname

def teste_ajuste_leiaute():
    x = nfse_df._ajustar_dps_para_sefin(DPS_NOTA_CONTROL)
    _check("infDPS na ordem aceita em 12/11", _filhos(x, "infDPS") == [
        "tpAmb", "dhEmi", "verAplic", "serie", "nDPS", "dCompet", "tpEmit",
        "finNFSe", "cLocEmi", "prest", "toma", "serv", "valores"])
    _check("prest sem IM e sem xNome", _filhos(x, "prest") == ["CNPJ", "regTrib"])
    _check("cServ: cNBS antes de xDescServ", _filhos(x, "cServ") == ["cTribNac", "cTribMun", "cNBS", "xDescServ"])
    _check("cTribMun = 001", _texto(x, "cTribMun") == "001")
    _check("sem retenção: sem pAliq", _filhos(x, "tribMun") == ["tribISSQN", "tpRetISSQN"])
    _check("Id da DPS preservado", 'Id="DPS530010822798774500014200001000000000900001"' in x)
    # retido
    r = nfse_df._ajustar_dps_para_sefin(DPS_NOTA_CONTROL, iss_retido=True)
    _check("retido: tpRetISSQN=2 e pAliq mantido",
           _texto(r, "tpRetISSQN") == "2" and _filhos(r, "tribMun") == ["tribISSQN", "tpRetISSQN", "pAliq"])
    # competencia (hoje fixado em 2026-11-12 via patch de nfse_df.hoje_br)
    _check("mês corrente não vira data futura", _texto(ajuste(competencia="2026-11"), "dCompet") == "2026-11-12")
    _check("mês passado = último dia", _texto(ajuste(competencia="2026-10"), "dCompet") == "2026-10-31")
    _check("sem competência = inalterado", _texto(x, "dCompet") == "2026-11-12")
```

- [ ] **Step 2:** `python tests/test_emissao_nacional.py` → FALHA com `AttributeError: ... '_ajustar_dps_para_sefin'`.
- [ ] **Step 3: Config.** Em `src/config.py`: `nfse_sefin_url_homologacao: str = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"`, `nfse_sefin_url_producao: str = "https://sefin.nfse.gov.br/SefinNacional"`, `nfse_ctribmun_nacional: str = "001"` (todos com `Field` + descrição; o `001` vem de `01.07.01.001`, alíquota 2% no DF desde 27/10/2026).
- [ ] **Step 4: Implementar `_ajustar_dps_para_sefin`** portando `ajustar()` de `scripts/teste_api_nacional_homolog.py`, com estas regras fixas: (1) `finNFSe` = `0` logo após `tpEmit`; (2) remover o grupo `IBSCBS` inteiro; (3) `cTribMun` = `settings.nfse_ctribmun_nacional`; (4) `cNBS` antes de `xDescServ`; (5) remover `prest/IM` e `prest/xNome`; (6) `iss_retido=False` → remover `pAliq`; `True` → `tpRetISSQN` = `2`, manter `pAliq`; (7) com `competencia`: `dCompet = min(_competencia_data(competencia), hoje_br().isoformat())`. Comentário de uma linha por regra citando o código de erro da spec (E1235, E0120, E0121, E0625).
- [ ] **Step 5:** `python tests/test_emissao_nacional.py` → tudo `OK`. `python tests/test_rps_fuso.py` → 24/24.
- [ ] **Step 6: Commit** — `feat(nfse): ajuste de leiaute da DPS para a Sefin Nacional`.

### Task 2: Transporte REST e leitura da resposta

**Files:**
- Modify: `src/nfse_df.py` (nova seção "SEFIN NACIONAL (REST)" antes de `_emitir_nacional`, ~linha 1420)
- Test: `tests/test_emissao_nacional.py`

**Interfaces:**
- Produces: `_sefin_base() -> str` (URL conforme `settings.nfse_ambiente`)
- Produces: `_empacotar_dps(xml_assinado: str) -> str` e `_desempacotar_xml(b64: str) -> str`
- Produces: `_sefin_request(metodo: str, caminho: str, corpo: Optional[dict] = None) -> tuple[int, dict]`
- Produces: `_parsear_resposta_sefin(status: int, corpo: dict) -> dict` com as chaves `sucesso`, `numero_nfse`, `chave_acesso`, `xml_nfse`, `mensagens`, `mensagem_erro`

- [ ] **Step 1: Testes que falham** (corpos reais da spec):

```python
def teste_transporte():
    xml = "<DPS>ç</DPS>"
    _check("ida e volta gzip+b64, com declaração XML",
           nfse_df._desempacotar_xml(nfse_df._empacotar_dps(xml)) == '<?xml version="1.0" encoding="UTF-8"?>' + xml)
    nfse_xml = '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse"><infNFSe><nNFSe>1</nNFSe></infNFSe></NFSe>'
    ok = nfse_df._parsear_resposta_sefin(201, {"chaveAcesso": "5300108" + "0" * 43,
         "nfseXmlGZipB64": _gzip_b64(nfse_xml), "alertas": None})
    _check("201 -> sucesso, número e chave", ok["sucesso"] and ok["numero_nfse"] == "1"
           and ok["chave_acesso"] == "5300108" + "0" * 43 and ok["xml_nfse"] == nfse_xml)
    rej = nfse_df._parsear_resposta_sefin(400, {"erros": [{"Codigo": "E0625", "Descricao": "Não é permitido informar alíquota", "Complemento": None}]})
    _check("400 -> erro com código", not rej["sucesso"] and "E0625" in rej["mensagem_erro"])
    um = nfse_df._parsear_resposta_sefin(404, {"erro": {"codigo": "E2404", "descricao": "Não foi gerada uma NFS-e"}})
    _check("formato 'erro' (minúsculo) também", "E2404" in um["mensagem_erro"])
    _check("201 sem XML não é sucesso", not nfse_df._parsear_resposta_sefin(201, {"chaveAcesso": "1"})["sucesso"])

def teste_nao_json():   # httpx.Client mockado devolvendo 403 text/html
    status, corpo = nfse_df._sefin_request("GET", "/nfse/x")
    _check("HTML não explode", status == 403 and corpo["erro"]["codigo"] == "HTTP403" and "<html" in corpo["erro"]["descricao"])
```

- [ ] **Step 2:** rodar → FALHA (funções inexistentes).
- [ ] **Step 3: Implementar.** `_sefin_request`: `httpx.Client(cert=(cert, key), timeout=60)` com `verify` padrão (foi o que funcionou em 12/11), PEMs removidos em `finally`; resposta não-JSON vira `{"erro": {"codigo": f"HTTP{status}", "descricao": texto[:300]}}`; erro de rede **propaga** (quem chama trata). `_parsear_resposta_sefin`: sucesso só com `status == 201` **e** `chaveAcesso` **e** `nfseXmlGZipB64`; `numero_nfse` = texto de `nNFSe` no XML; erros formatados como `"<Codigo> — <Descricao> (<Complemento>)"`.
- [ ] **Step 4:** rodar → tudo `OK`.
- [ ] **Step 5: Commit** — `feat(nfse): transporte REST da Sefin Nacional`.

### Task 3: `_emitir_nacional` sobre REST, com reserva de número e recuperação

**Files:**
- Modify: `src/nfse_df.py` — `ResultadoEmissao` (132–160), dispatcher (176–199), contadores (457–536), `_emitir_nacional` (1423–1613), `_arquivar` (2622), `_gravar_log_nfse` (2680–2705)
- Test: `tests/test_emissao_nacional.py`

**Interfaces:**
- Consumes: tudo das Tasks 1 e 2.
- Produces: `ResultadoEmissao.chave_acesso: Optional[str] = None` (em `to_dict()` e no log `nfse_<invoice_id>.json`, chave `chave_acesso`); `codigo_verificacao` recebe a mesma chave.
- Produces: parâmetro `arquivo: Optional[Path] = None` em `_ler_contador_rps`, `_gravar_contador_rps`, `_proximo_numero_rps`, `_liberar_rps_pendente` (`None` → `_COUNTER_RPS_FILE`, lido na hora da chamada para os `patch` dos testes continuarem valendo).
- Produces: `_arquivar(xml_str, prefix, suffix, ext: str = "xml")`.

- [ ] **Step 1: Testes que falham** (diretório temporário; `_montar_xml_dps` devolve `(DPS_NOTA_CONTROL, id)`, `_assinar_xml` devolve a entrada, `_sefin_request` é um fake que registra as chamadas):

```python
def teste_emissao():
    # a) caminho feliz
    r = emitir(fake=[(404, ERRO_E2404), (201, RESP_201)])
    _check("201 -> sucesso com número e chave", r["sucesso"] and r["numero_nfse"] == "1" and r["chave_acesso"] == CHAVE)
    _check("codigo_verificacao = chave", r["codigo_verificacao"] == CHAVE)
    _check("ordem: GET /dps/<id> e depois POST /nfse", chamadas == [("GET", "/dps/" + DPS_ID), ("POST", "/nfse")])
    _check("log nfse_<fatura>.json tem chave_acesso", json_log["chave_acesso"] == CHAVE and json_log["padrao"] == "nacional")
    _check("XML da NFS-e arquivado e apontado", Path(r["xml_retorno_path"]).read_text(encoding="utf-8") == NFSE_XML)
    _check("reserva liberada", "INV_A" not in contador_dps().get("pendentes", {}))
    _check("contador de RPS intocado", not (tmp / ".contador_rps.json").exists())
    # b) rejeição não queima número
    r1 = emitir(fake=[(404, ERRO_E2404), (400, ERRO_E0625)]); r2 = emitir(fake=[(404, ERRO_E2404), (400, ERRO_E0625)])
    _check("rejeição -> erro com código", not r1["sucesso"] and "E0625" in r1["mensagem_erro"])
    _check("segunda tentativa reusa o mesmo nDPS", ids_enviados[0] == ids_enviados[1])
    # c) recupera_sem_reenviar: a Sefin já tem a nota desta DPS
    r = emitir(fake=[(200, {"chaveAcesso": CHAVE}), (200, {"chaveAcesso": CHAVE, "nfseXmlGZipB64": B64})])
    _check("recupera a nota e NÃO faz POST", r["sucesso"] and all(m != "POST" for m, _ in chamadas))
    # d) timeout
    r = emitir(fake=[(404, ERRO_E2404), httpx.ReadTimeout("x")])
    _check("timeout -> falha sem exceção, reserva mantida", not r["sucesso"] and "INV_A" in contador_dps()["pendentes"])
    # e) despacho_retroativo
    _check("competência 2026-10 com padrao=nacional vai pro ABRASF", backend_chamado(competencia="2026-10") == "abrasf204")
    _check("competência 2026-11 fica no nacional", backend_chamado(competencia="2026-11") == "nacional")
    # f) não bloqueia o event loop
    _check("POST roda via asyncio.to_thread", to_thread_chamado)
    # g) dry-run
    _check("dry-run: monta e assina, zero chamadas à Sefin", dry["sucesso"] and chamadas == [])
```

- [ ] **Step 2:** rodar → FALHA.
- [ ] **Step 3: Contadores e `_arquivar`** — acrescentar os parâmetros opcionais (sem mudar comportamento padrão). Rodar `python tests/test_rps_fuso.py` → 24/24.
- [ ] **Step 4: Dispatcher** — constante `_COMPETENCIA_INICIO_NACIONAL = "2026-11"`; vai para `_emitir_abrasf204` quando `padrao == "abrasf204"` **ou** quando `competencia` foi informada e `competencia[:7] < _COMPETENCIA_INICIO_NACIONAL` (`None` segue no nacional; a ISSnet só recusa competência ≥ 01/11/2026; o DF só existe na Sefin desde 27/10/2026).
- [ ] **Step 5: Reescrever `_emitir_nacional`** mantendo validação, `DadosServico`, log inicial e o formato de retorno. Sequência: número = `_proximo_numero_rps(invoice.get("id"), arquivo=_COUNTER_FILE)` → `_montar_xml_dps` → `_ajustar_dps_para_sefin(xml, iss_retido=getattr(empresa, "iss_retido", False), competencia=competencia)` → `_assinar_xml` → arquivar `dps_<n>_dps_assinada` → se `nfse_dry_run`: retornar sucesso `DRY-RUN-DPS-<n>` aqui → `GET /dps/{dps_id}`: se 200, `GET /nfse/{chave}` e tratar como sucesso (log `[NFS-e Nacional] Nota já existente recuperada`; os dois GET responderam 200 em 12/11, mas o campo `nfseXmlGZipB64` no corpo do `GET /nfse` ainda não foi conferido — o caso D da Task 5 confirma; se vier sem XML, é sucesso com `xml_retorno_path=None`) → senão `POST /nfse` com `{"dpsXmlGZipB64": _empacotar_dps(...)}` → em falha arquivar o JSON (`ext="json"`) e retornar `mensagem_erro` → em sucesso arquivar o XML da NFS-e (`dps_<n>_retorno`), preencher `numero_nfse`, `codigo_verificacao = chave_acesso = chave`, `_gravar_log_nfse(..., rps_numero=<n>)`, `_liberar_rps_pendente(invoice.get("id"), arquivo=_COUNTER_FILE)`. Todas as chamadas à Sefin via `await asyncio.to_thread(...)` dentro de um `try/except` que devolve `sucesso=False` (nunca levanta).
- [ ] **Step 6:** `python tests/test_emissao_nacional.py` e `python tests/test_rps_fuso.py` → tudo `OK`; `python scripts/validar_guardrail_nfse.py` → 9/9.
- [ ] **Step 7: Commit** — `feat(nfse): emissão nacional via API da Sefin com reserva de DPS e recuperação`.

### Task 4: E-mail da nota nacional

**Files:**
- Modify: `src/email_nfse.py` — constante junto de `_PORTAL_DANFSE` (linha 53), `_montar_html` (240–316), `montar_email_nfse` (334–410)
- Test: `tests/test_emissao_nacional.py`

**Interfaces:**
- Consumes: `dados["chave_acesso"]` (Task 3). Ausente/vazio → e-mail **idêntico ao de hoje**.
- Produces: `_CONSULTA_NACIONAL = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave="`; `_montar_html(..., chave_acesso: Optional[str] = None)`.

- [ ] **Step 1: Teste que falha:**

```python
def teste_email():
    msg, html, _ = email_nfse.montar_email_nfse(empresa, {**dados, "chave_acesso": CHAVE, "xml_retorno_path": str(xml_nacional)})
    _check("link de consulta com a chave", email_nfse._CONSULTA_NACIONAL + CHAVE in html)
    _check("não manda pro portal da ISSnet", "VerificaAutenticidade" not in html)
    _check("rótulo 'Chave de acesso'", "Chave de acesso" in html and "Código de verificação" not in html)
    _check("não promete PDF", "PDF da NFS-e" not in html)
    _check("só o XML anexo, byte a byte", anexos(msg) == [("application/xml", NFSE_XML.encode())])
    _, html_abrasf, _ = email_nfse.montar_email_nfse(empresa, dados)      # sem chave
    _check("nota ABRASF segue igual", "VerificaAutenticidade" in html_abrasf and "Código de verificação" in html_abrasf)
```

- [ ] **Step 2:** rodar → FALHA.
- [ ] **Step 3: Implementar.** Com `chave_acesso`: linha da tabela vira "Chave de acesso"; parágrafo de autenticidade aponta para `_CONSULTA_NACIONAL + chave` e diz que ali se baixa o PDF oficial; linha "Anexos" cita só o XML; **não** chamar `_anexar_pdf_danfse` (o gerador local só entende ABRASF e sairia em branco). O anexo XML já funciona: `_extrair_nfse_do_retorno` devolve o arquivo inteiro quando não acha `CompNfse`/`Nfse` — não mexer.
- [ ] **Step 4:** rodar → tudo `OK`.
- [ ] **Step 5: Commit** — `feat(email): e-mail da NFS-e nacional com link de consulta pública`.

### Task 5: Prova em homologação com o código real (VPS)

**Files:**
- Modify: `scripts/teste_api_nacional_homolog.py` — apagar o `ajustar()` local e a chamada httpx; passar a chamar `nfse_df.emitir_nfse(invoice, empresa)`.

**Interfaces:**
- Consumes: código das Tasks 1–3 já na VPS. O script força, **só no processo dele**, `NFSE_PADRAO=nacional`, `NFSE_AMBIENTE=homologacao` e `NFSE_OUTPUT_DIR=$SAIDA` (um diretório só, mantido entre os casos) por variável de ambiente antes de importar `src` (o serviço em produção segue em `abrasf204`).

- [ ] **Step 0 (somente leitura, antes do pull):** conferir que o `.env` da MegaTeam tem `NFSE_PADRAO=abrasf204` **explícito** (o default do `config.py` é `nacional`, que agora emite de verdade).
- [ ] **Step 1 (precisa de OK do Bruno):** push + `git pull` **só na MegaTeam**, **sem** mexer no `.env`; restart; conferir pelo domínio público autenticado que o painel responde e `NFSE_PADRAO` continua `abrasf204`. Rodar na VPS `python tests/test_emissao_nacional.py` e `python tests/test_rps_fuso.py`.
- [ ] **Step 2 (somente leitura): levantamento das empresas reais.** Para cada empresa ativa (`get_repo()`): `codigo_servico` do cadastro → `_normalizar_cTribNac`; cidade/UF do cadastro na Iugu → `_codigo_ibge_por_cidade(..., default="")`. Listar quem **não** dá `010701` e quem está fora do DF em cidade não mapeada. A prova da seção 8 só cobriu `010701`: se o cadastro real traz `01.07` (→ `010700`), isso tem que ser testado no caso A.
- [ ] **Step 3:** adaptar o script. **Um único** diretório de saída para todos os casos (env `SAIDA`, criado uma vez e mantido), contador de DPS semeado num número alto ainda não usado na série de homologação, e **valor diferente por caso** (senão um caso "passa" recuperando a nota do outro). Casos (env `CASO`):
  - **A** normal, R$ 1,00, com a configuração **de uma empresa real** (o `codigo_servico` como está no cadastro) e tomador **com e-mail**;
  - **B** `iss_retido=True` com `custom_variables valor_bruto_nfse`, R$ 2,00;
  - **C** tomador de São Paulo/SP (CEP `01310100`), R$ 3,00;
  - **D** recuperação: apagar o `nfse_<id>.json` de A **e recolocar a reserva de A** em `.contador_dps.json` (é o estado real depois de um timeout), repetir A;
  - **E** reenviar direto (`_sefin_request("POST", "/nfse", ...)`) a DPS já aceita de A, para registrar o que a Sefin responde a uma DPS repetida;
  - **F** contador atrasado: voltar `ultimo_numero` em 1 e emitir uma fatura nova, R$ 4,00; repetir.
- [ ] **Step 4:** rodar. Esperado: A/B/C → `sucesso=True`, `ambiente=homologacao`, número e chave de 50 dígitos; **D** → `sucesso=True` com a **mesma chave** de A, log "Nota já existente recuperada" e **nenhum** POST (isso também prova que `GET /nfse/{chave}` devolve `nfseXmlGZipB64`); **E** → rejeição, com o código anotado; **F** → 1ª tentativa falha com "contador de DPS desta máquina está atrasado" sem POST, 2ª emite com o número seguinte.
- [ ] **Step 5:** se algum caso for rejeitado (ou o Step 2 achar `cTribNac` diferente de `010701`, ou D mostrar que `GET /nfse` não traz o XML), a correção entra **com teste que falha antes** em `tests/test_emissao_nacional.py`, e repete-se o Step 4. Registrar cada erro na tabela da seção 8 da spec. Mudança em `_ajustar_dps_para_sefin` ou na recuperação pede uma revisão curta do delta antes da Task 6.
- [ ] **Step 6:** conferir que nada de produção mudou: `.contador_rps.json` igual, sem `.contador_dps.json` nem arquivos `dps_*` em `nfse_emitidas/`.
- [ ] **Step 7: Commit** — `test(nfse): prova em homologação pela API Nacional com o código real`.

### Task 6: Virada em produção + documentação

**Files:**
- Create: `docs/adr/ADR-0008-emissao-api-nacional-sefin.md` (+ linha em `docs/adr/README.md`)
- Modify: `CLAUDE.md` (TL;DR Fase 2, diagrama, tabela de módulos), `docs/adr/ADR-0005-abrasf-2.04-rps.md` (nota: a virada é pela API da Sefin, não pelo SOAP `wsnfsenacional`), `docs/incidente_l120_simples_nacional_2026-11.md` (status resolvido + seção de correção/validação), `docs/BASE_CONHECIMENTO_INCIDENTES.md`, `docs/HANDOFF_OPUS46.md`

- [ ] **Step 1 (somente leitura, da VPS):** `GET https://sefin.nfse.gov.br/SefinNacional/nfse/<chave inexistente>` com o A1 → esperado JSON `E2401`; `GET https://adn.nfse.gov.br/parametrizacao/5300108/01.07.01.001/<hoje>/aliquota` (TLS ≤ 1.2) → esperado alíquota `2.00`. Se algum falhar, **parar** e registrar no incidente.
- [ ] **Step 2 (OK explícito do Bruno; fatura escolhida por ele):** no `.env` da MegaTeam trocar `NFSE_PADRAO=nacional`, restart, e emitir **uma** fatura pendente pelo painel. É nota fiscal de verdade.
- [ ] **Step 3: Conferir a primeira nota** — número e chave no painel; nota visível no portal nacional; link `_CONSULTA_NACIONAL + chave` abre a nota (se a URL estiver errada, corrigir a constante + teste antes de seguir); e-mail chegou com o XML. Só depois liberar as demais faturas.
- [ ] **Step 4: Plano de volta** — qualquer problema: `NFSE_PADRAO=abrasf204` + restart (volta ao estado de hoje; o ABRASF continua recusando novembro com L120, então o contorno é o portal nacional).
- [ ] **Step 5: Documentar** — ADR-0008 (decisão, endpoints, 9 ajustes, recuperação por `GET /dps`, procedimento para "contador de DPS fora de sincronia", fora do escopo), demais arquivos acima; memória `l120-api-nacional` atualizada.
- [ ] **Step 6: Commit** — `docs: ADR-0008 e fechamento do incidente L120 (emissão pela API Nacional)`.
