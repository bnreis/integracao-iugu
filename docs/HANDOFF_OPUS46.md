# HANDOFF — Estado atual do projeto

> **Para o próximo assistente:** ponto de entrada da próxima sessão. Leia na íntegra antes de responder ao Bruno. Atualizado em **18/06/2026**.

**Usuário:** Bruno Reis (bruno.reis@grupontsec.com) — admin da conta Iugu da **MEGASUPORTE SERVIÇOS DE TI LTDA** (CNPJ 36.342.291/0001-43, Brasília/DF, Simples Nacional ME/EPP, ambiente "Estabelecido").

---

## 🆕 17/07/2026 — MULTI-EMPRESA no ar (MegaSuporte + MegaTeam) — ADR-0007

Segunda empresa **MegaTeam** (CNPJ 27.987.745/0001-42) rodando ao lado da MegaSuporte, no **mesmo domínio** (`iugu.megasuporte.com`), roteado por **caminho** no Apache.

**Arquitetura (ADR-0007):** duas instâncias isoladas do backend, **núcleo fiscal intocado**.
- MegaSuporte → `/opt/integracao-iugu`, systemd `iugu-webhook`, porta **8000**, rotas `/api /auth /webhook /health`.
- MegaTeam → `/opt/integracao-iugu-megateam`, systemd `iugu-webhook-megateam`, porta **8001**, rotas `/megateam/api ...`. `.env` próprio (token Iugu, A1 `certs/megateam.pfx`, IM `0781513100130`, **série RPS 3**, `NFSE_OUTPUT_DIR` próprio). Contador RPS semeado em **36** (última nota emitida) → próxima = 37. Faixa 1–50 (pedir mais em breve).
- App/painel: **seletor de empresa** (login + menu no cabeçalho `ContaMenu`); login autentica nas duas empresas e guarda os dois tokens; na web a troca recarrega a página. APK **vcode 20**.
- Backend: **`Cache-Control: no-store`** em toda resposta (`webhook_server`) — higiene anti-cache.
- Clientes replicados p/ o Iugu da MegaTeam: `scripts/importar_clientes_entre_contas.py` (17 criados).

**🔴 Incidente resolvido (mesma data):** o painel mostrava dados de UMA empresa para as duas. Causa = **rota do Apache**: `/api /auth /webhook /health` apontavam pro **8001** (MegaTeam) em vez do **8000**, corrompidas em edição manual do vhost. **Lição:** testar pelo **domínio público** (não localhost) e conferir **todas** as linhas `ProxyPass`. **Postmortem: `docs/incidente_multiempresa_apache_2026-07.md`.**

**Pendências abertas (multi-empresa):**
1. 🔴 **Backfill webhooks MegaSuporte** (~16/07 17:45 → 17/07): webhooks foram pro 8001 (rejeitados) → conferir pagos sem NFS-e e emitir (guardrail impede duplicata).
2. **Gatilho da MegaTeam** na Iugu → `https://iugu.megasuporte.com/megateam/webhook/iugu?token=<IUGU_WEBHOOK_TOKEN>`.
3. 🔴 **Excluir o token vazado** da MegaTeam na Iugu (o antigo `C69906999...`, vazou no chat — já rotacionado no `.env`, falta apagar na Iugu).
4. **Faturamento recorrente / cobrança dupla:** os 17 clientes foram importados com `dia_criacao_fatura`/`valor_fatura`. **NÃO ligar o cron da MegaTeam** até decidir quais clientes ela fatura (senão gera boleto+NFS-e em duplicidade com a MegaSuporte).
5. **Emissão de teste R$1** na MegaTeam (ponta a ponta).
6. Purge do Cloudflare + WARP (o `no-store` já impede novo cache).

---

## 🚨 14/07/2026 — TLS da ISSnet quebrou TODAS as emissões (resolvido)

**Sintoma:** toda emissão falhava com `[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain` ao chamar `df.issnetonline.com.br`. **Não era cadastro/cliente** — era o **certificado TLS do servidor da ISSnet** (renovado ~30/06). A cadeia GoDaddy nova (`GoDaddy TLS Root CA - R1` + raiz legada `Go Daddy Class 2`) não é ancorada nem pelo `certifi` (2026.05) nem pelo bundle do SO → ambos dão `verify code 19`.

**Correção (commit `77ef90c`):** `verify` das chamadas httpx à ISSnet virou configurável — `_verify_ssl()` em `src/nfse_df.py` usa `settings.nfse_ca_bundle_path` (env `NFSE_CA_BUNDLE_PATH`) quando setado, senão certifi. **Nunca `verify=False`** (endpoint fiscal). Na VPS: bundle `certifi + cadeia GoDaddy da ISSnet` em `/opt/integracao-iugu/certs/issnet_ca_bundle.pem`, apontado no `.env`.

**📄 Postmortem completo (sintoma, diagnóstico, correção, validação, prevenção):** `docs/incidente_tls_issnet_2026-07.md`.

**⚠️ Vai repetir quando a ISSnet renovar o cert.** Para refazer o bundle (na VPS):
```bash
CB=$(sudo -u iugu /opt/integracao-iugu/.venv/bin/python -m certifi)
echo | openssl s_client -connect df.issnetonline.com.br:443 -servername df.issnetonline.com.br -showcerts 2>/dev/null \
 | awk '/-----BEGIN CERTIFICATE-----/{f=1} f{print} /-----END CERTIFICATE-----/{f=0}' > /tmp/issnet_chain.pem
cat "$CB" /tmp/issnet_chain.pem | sudo tee /opt/integracao-iugu/certs/issnet_ca_bundle.pem >/dev/null
sudo chown iugu:iugu /opt/integracao-iugu/certs/issnet_ca_bundle.pem
# valide: openssl ... -CAfile /opt/integracao-iugu/certs/issnet_ca_bundle.pem  → verify return code: 0 (ok)
sudo systemctl restart iugu-webhook
```

**🔴 Pendência:** faturas pagas de ~30/06 a 14/07 que deveriam ter gerado NFS-e **não geraram** (ex.: KEMMI PHARMA visto no log). Auditar pagas sem NFS-e no período e emitir manualmente (respeitando o guardrail 1/mês).

---

## 🆕 Novidades desta sessão (18/06/2026)

App mobile/web (Expo) em produção (APK + painel web). Mudanças deployadas:
- **Baixa manual de fatura** (pagamento externo via Iugu `externally_pay`) + auto-emissão de NFS-e + e-mail. Status `externally_paid` é tratado como pago no fluxo de emissão.
- **Emissão MANUAL de NFS-e em fatura ainda NÃO paga** (botão "Gerar NFS-e (manual)" na fatura pendente → `processar_pagamento(forcar_emissao=True)`; mantém lock+guardrail).
- **ISS retido na fonte** (substituto tributário, ex.: FIPECQ): flag `iss_retido` + **Inscrição Municipal do tomador** por empresa → RPS sai com `IssRetido=1` + `ResponsavelRetencao=1` + IM do tomador. Resolve L060/E280/E039/L006. (Confirmado pela NFS-e real de abril.)
- **Dashboard:** "NFS-e pendente" só p/ empresa com `emitir_nf=True`; "Ações necessárias" por **vencimento real** da fatura (`due_date < hoje`), não por vencimento futuro; badge **"NF-e: N/A"** p/ quem não emite.
- **Mobile UX:** menu inferior acima da barra do Android (safe-area), **swipe** entre abas, **um único** spinner de loading, popups em linguagem de usuário.
- 🔴 **Contorno da listagem de clientes da Iugu quebrada** (bug deles — `GET /v1/customers` retorna 1; por ID e `?query=` funcionam). Emissão resolve por `customer_id` on-demand; lista enumera por **busca (vogais)** + **registro local** (`nfse_emitidas/registro_customer_ids.json`) + `scripts/seed_customer_ids.py` (a-z). Voltou a carregar os 17. **Ver `docs/iugu_listagem_customers_contorno.md`.**
- 🛡️ **Guardrail "1 NFS-e por cliente por mês"** (auditoria 18/06): a Regra 2 do `nfse_guard` passou de **CNPJ+valor+mês** para **CNPJ+mês** (sem valor) + normalização de CNPJ. Fecha o furo de **2 faturas do mesmo cliente no mês com valores diferentes** e o de **cancelada+recriada com ISS retido** (log=bruto × fatura=líquido). Vale p/ automático **e** manual (todos passam por `_verificar_nfse_duplicada`: webhook, `/emitir`, `/emitir-manual`, `/baixa-manual`, cron). Mensagens de bloqueio no app ficaram amigáveis. Validador: `scripts/validar_guardrail_nfse.py` (9 cenários, todos verdes). **Trade-off:** 2ª nota legítima no mesmo mês é barrada (desejado). **Ver ADR-0006.**
- 🆕 **Marcar NF-e como já emitida** (commit `3062b82`): cenário **fatura cancelada+recriada** depois de já ter emitido a nota. A fatura nova (outro `invoice_id`) aparecia como pendente e **reemitiria** ao ser paga. `registrar_nfse_emitida_manual` (`src/nfse_df.py`) grava o índice `nfse_<id>.json` (`sucesso=true`, `marcada_manualmente=true`) **sem emitir/enviar** → painel mostra "emitida" + guardrail (Regra 1) **bloqueia** a reemissão. Endpoint `POST /api/nfse/{id}/marcar-emitida` (idempotente) + botão no detalhe da fatura. **Ver `docs/adr/ADR-0006...` (Complemento 18/06).** App em **versionCode 15** (APK regenerado).

**Pendências abertas:**
1. 🗓️ **CNPJ alfanumérico (jul/2026):** adaptar validação de CNPJ (hoje exige 14 dígitos) p/ aceitar letras (string). É a próxima "adaptação" a tratar.
2. 📩 **Abrir chamado na Iugu** sobre a listagem `/v1/customers` retornar 1 (contorno já segura a operação).
3. 🔴 **Rotação de credenciais** (seção 8): webhook token, senha A1 (`mega10`), senha SMTP.
4. ⚠️ **Emitir/gerar só pela VPS** (contador de RPS por máquina) + pedir mais RPS série 3 antes de esgotar 1–50.

### 🧭 Estratégia futura — mudança de classificação tributária (se necessário)
Avaliado em 18/06: trocar a **classificação fiscal** da NFS-e (Descrição, Atividade Municipal/item LC116, Tributação Nacional, NBS) é **simples tecnicamente** (são config), mas **sensível fiscalmente** (validar com a contabilidade + cadastro do prestador no DF). Mapa do que controlamos hoje:
- **Descrição dos serviços** → `descricao_servico` (cadastro da empresa, no `notes`) ou `nfse_descricao_servico_padrao` (`.env`). Já editável.
- **Item LC116** → `codigo_servico` (cadastro, ex.: `01.07`). **CodigoTributacaoMunicipio** → `settings.nfse_codigo_trib_municipal` (`.env`, hoje **1071** — GLOBAL p/ todas as empresas).
- **Tributação Nacional (cTribNac)** e **NBS**: ⚠️ **NÃO enviamos no XML ABRASF 2.04** (produção) — o ISSnet/DF **deriva** do código municipal. Só aparecem no XML no backend **nacional/DPS** (`_normalizar_cTribNac` + `cNBS`), p/ pós-30/06.
- **Plano se precisar variar por cliente:** tornar `codigo_servico` + `codigo_trib_municipal` (+ descrição) **por empresa** (campos no `notes`, igual já feito com `aliquota_iss`/`iss_retido`/`inscricao_municipal`) — mudança **pequena**, padrão já estabelecido. Surfacing no app: novos campos na seção de cobrança do cadastro. **Antes:** confirmar com o contador a combinação item+código municipal+alíquota correta e a habilitação do prestador no DF.

---

## 1. Projeto em uma linha

Automação que, ao receber webhook de fatura paga da Iugu, emite NFS-e automática no DF, gera boletos recorrentes mensais e tem app/painel de gestão. Hospedado em VPS própria da Hostinger (`iugu.megasuporte.com`, IP `72.62.11.230`).

## 2. Estado atual resumido (06/06/2026)

| Fase | Status | Observação |
|------|--------|------------|
| **Fase 1 — Webhook + cobrança + cadastro Iugu** | ✅ **Validada em produção** | Fluxo "fatura → pago → webhook → atualização" rodando no ar |
| **Fase 2 — Emissão NFS-e DF** | ✅ **AUTOMÁTICA EM PRODUÇÃO** | Auto-emissão ligada na VPS (`NFSE_PADRAO=abrasf204`). 2 NFS-e reais: **#408** (SINDICONDOMINIO, manual, 05/06) e **MEGATEAM** (R$1, auto pelo painel, 06/06). E-mail automático com XML anexo no ar. Guardrail anti-duplicata blindado |
| **Fase 3 — Deploy VPS** | ✅ **Concluída** | Backend (commit `909ac61`), painel web, HTTPS, cron e hardening rodando |
| **Hardening de segurança (OWASP/pentest)** | ✅ Aplicado | CORS restrito, JWT, rate-limit, HSTS, compare_digest, /docs off etc. Detalhes em `docs/pentest_2026-06.md` e `docs/ressalvas_pentest_2026-06.md` |
| **Arquitetura interna (roadmap)** | 🟡 **Onda 0 + ADR-0003 Etapa 1 deployados** | ADR-0001 (SQLite), ADR-0002 (idempotência), ADR-0003 Etapa 2 e ADR-0004 propostos em `docs/adr/` |

## 3. Última conquista — EMISSÃO AUTOMÁTICA no ar + e-mail + guardrail blindado (06/06/2026)

✅ **A emissão automática está ligada e provada em produção.** Em 06/06/2026, a fatura de
teste da **MEGATEAM (R$ 1,00)** foi emitida **automaticamente pelo painel/webhook** via
ABRASF 2.04 (RPS 2) na VPS, com **e-mail automático** ao tomador (XML anexo + link de
verificação). Antes disso, em 05/06, a **#408** (SINDICONDOMINIO, R$ 3.150, código
**B3B17DA6A**) provou o fluxo manual. Os dois confirmam o caminho end-to-end
RPS→assinatura A1→SOAP→ISSnet DF→NFS-e oficial.

### O que entrou nesta sessão (commits na `main`, deployados na VPS — `909ac61`)
1. **E-mail automático da NFS-e** (`src/email_nfse.py`): template HTML com logo (CID), tabela
   (IM/número/código/data/valor), **link oficial de verificação** do DF e XML anexo.
   Estrutura MIME `multipart/mixed` (corrige sumiço do corpo no Gmail quando há anexo).
   Disparado tanto no auto-envio (webhook) quanto no "Reenviar NF-e" do painel — template idêntico.
   Remetente: `financeiro@megasuporte.com`. Logo em `assets/logo_megasuporte.png` (precisa existir na VPS).
2. **Guardrail anti-duplicata baseado em EVIDÊNCIA** (removidos gatilhos por flag
   `nf_na_criacao`/custom_variable, que davam falso-positivo): só o log real
   `nfse_<invoice_id>.json` com `sucesso=true` prova a nota. Corrigida a regra de anti-duplicata
   por CNPJ+mês+valor que estava **morta** (comparava campos inexistentes).
3. **`src/nfse_guard.py` (novo)** — módulo neutro com **lock por `invoice_id` (cross-process)**
   + guardrail, compartilhado por webhook **e** cron. Fecha a janela de NFS-e duplicada em
   reentrega da Iugu e em corrida webhook×cron (achado ALTO do appsec). TTL 300s + checagem de
   PID vivo (conservador); fallback de índice mínimo se a gravação do log falhar.
4. **Testes** (`tests/test_webhook_status.py`): 10/10, incluindo lock ocupado/obsoleto/liberado,
   cron sob lock e e-mail com anexo. Tudo offline.

> Revisão: cada mudança passou por **squad (web-backend + code-reviewer + appsec)**. O appsec
> só aprovou após o cron entrar no mesmo lock+guardrail.

Como chegamos aqui: a MEGASUPORTE foi **habilitada em produção** pelo Nota Control (chamado resolvido 05/06/2026). Dois esclarecimentos mudaram o rumo técnico:

1. **Produção do DF hoje é ABRASF 2.04, não Padrão Nacional.** Endpoint oficial: `https://df.issnetonline.com.br/webservicenfse204/nfse.asmx`. Documento = RPS série 3.
2. **O Padrão Nacional (DPS v1.01) que já tínhamos pronto** só vira obrigatório em **30/06/2026** (prazo prorrogado). Por isso vai bem em XSD mas dava 404 no endpoint `wsnfsenacional` — não estava liberado lá.

Para cobrir os dois mundos, foi criado o **ADR-0005**: arquitetura dual (dispatcher por `NFSE_PADRAO` no `.env`). A virada de 30/06 vira "trocar 1 variável", não reescrita.

### Backends de emissão atuais (em `src/nfse_df.py`)

- **`_emitir_nacional`** (DPS v1.01) — pronto, XSD válido, E160 resolvido (5 patches lxml). Caminho do Padrão Nacional para depois de 30/06.
- **`_emitir_abrasf204`** (RPS série 3) — pronto estruturalmente, XSD válido **com e sem assinatura** (`scripts/validar_rps_xsd.py`/`--com-assinatura`). Caminho de produção HOJE.
- **`emitir_nfse(invoice, empresa)`** — dispatcher fino lê `settings.nfse_padrao` (`"nacional"` ou `"abrasf204"`).

### Dados fiscais confirmados (não pedir de novo)

- **CF/DF / IM:** `0796481500161` — bate com a ficha cadastral oficial.
- **Código de tributação municipal:** `1071` + alíquota **2%** — **confirmado pela contabilidade em 05/06/2026** (a ficha lista 1071=5% genérico, mas o contador autorizou 1071+2% para este prestador).
- `cTribNac=010701`, `cTribMun=1071`, `cNBS=115013000`, IBSCBS CST `900`/cClassTrib `900001`, tributos SN `7,48%`.

### Validado no 1º envio real (confirmado pelo ISSnet)

- Namespace, SOAPAction, XML aninhado (não CDATA), `versaoDados=2.04` — **aceitos**.
- **CNAE** `6209100` (`NFSE_CNAE`) e **MunicipioIncidencia** `5300108` (`NFSE_MUNICIPIO_INCIDENCIA`) — adicionados após os erros E311/L001; **aceitos**.
- Item `01.07` + código municipal `1071` + alíquota `2%` — confirmados (batem com a NFS-e #402 do portal).
- **RPS série 3:** faixa autorizada **1–50** (liberada 13/01/2023). 1ª nota usou RPS **1**; `.contador_rps.json` em 1 (próximo = 2). ⚠️ **Solicitar mais RPS no portal antes de esgotar os 50.**

## 4. Próximos passos

1. ⚠️ **CONTADOR DE RPS É POR MÁQUINA.** `.contador_rps.json` da VPS ≠ da máquina local. Agora que
   o automático roda na VPS, **emitir SOMENTE pela VPS** — emitir também localmente faz os
   contadores divergirem e o ISSnet rejeita com **E010** ("RPS já informado"). Usados até agora:
   RPS 1 (#408, local) e RPS 2 (MEGATEAM, VPS).
2. **Solicitar mais RPS série 3** no portal do ISSnet antes de esgotar a faixa **1–50**.
3. **Confirmar quem emite automático:** revisar quais empresas têm `emitir_nf=True` na Iugu
   (essas emitem sozinhas no pagamento) e quais têm `nf_na_criacao=True` (emitem no cron).
4. **`ConsultarUrlNfse`** (PDF oficial) — ainda **diferido** (dava E160 nas tentativas; falta um
   exemplo real do ISSnet/ACBr para acertar o namespace). Hoje o e-mail vai com **XML anexo +
   link de verificação** — quando resolvido, anexar também o PDF oficial. Decisão de produto:
   **não geramos PDF próprio** (reportlab removido). 💡 **Alternativa promissora** (ver
   `docs/analise_portal_nacional_nfse.md`): quando no Padrão Nacional, puxar o **DANFSe oficial
   pela API do ADN nacional** (`nfse.gov.br`) — resolve o PDF sem depender da extensão do ISSnet.
   ⚠️ `nfe.fazenda.gov.br` é NF-e de **mercadorias** — NÃO serve para nossa NFS-e (documento errado).
5. **Rotação de credenciais** (seção 8) — agora inclui a **senha SMTP** que entrou no `.env` da VPS.
6. (Robustez) ler `.contador_rps.json`/`.contador_dps.json` com `utf-8-sig` (evita o tropeço do BOM).
7. Runbook da emissão manual: `docs/runbook_primeira_emissao_abrasf.md`.

## 5. Documentos a ler antes de mexer em código (ordem)

1. `CLAUDE.md` (este projeto) — visão estável.
2. **`docs/fase2_nfse_df.md`** — status técnico atualizado da Fase 2.
3. **`docs/adr/README.md` + `docs/adr/ADR-0005-abrasf-2.04-rps.md`** — decisão da arquitetura dual.
4. `docs/deploy_vps.md` — runbook vivo da infra (Fase 3 + hardening).
5. `docs/pentest_2026-06.md` + `docs/ressalvas_pentest_2026-06.md` — segurança.
6. `docs/relatorio-integracao-nfse-df.md` — pesquisa de contexto (ABRASF vs Padrão Nacional, libs, agregadores).

> `README.md` foi **reescrito em 06/06/2026** — agora é o guia canônico de replicação (arquitetura atual, fonte=Iugu, emissão dual, auto-emissão, e-mail, guardrail, deploy). Pode confiar.

## 6. Referências oficiais já no projeto

### Padrão Nacional (DPS v1.01)
- `docs/manual_oficial_integracao/Manual_integracao_v101.pdf` (109 páginas, 17/03/2026)
- `docs/exemplos_oficiais/GerarNfseEnvio.xml` (template comentado, 1.077 linhas)
- `docs/exemplos_oficiais/schema_v101.xsd.xml` (XSD oficial, 5.140 linhas)

### ABRASF 2.04 (RPS)
- `docs/exemplos_oficiais/abrasf204/schema/schema nfse v2-04.xsd` — XSD oficial ABRASF
- `docs/exemplos_oficiais/abrasf204/wsdl/nfse.wsdl` — WSDL oficial (operações, namespace, SOAPAction)
- `docs/exemplos_oficiais/abrasf204/manual_2.04.pdf` — Manual ABRASF 2.04
- Há também o PDF "Códigos de Hash NFS-e 2.04" em `C:\Users\bruno.reis\Downloads\` (escaneado — entra em jogo no momento da assinatura/validação)

## 7. Histórico de bugs do schema (referência para o backend nacional)

Mantido para diagnóstico se algum desses ressurgir num upgrade futuro da `nfelib`. **Já corrigidos** em `src/nfse_df.py`.

| # | Sintoma | Causa | Correção |
|---|---|---|---|
| 1 | E160 | `<opConsumServ>` em `<locPrest>` (removido no XSD v1.01) | Removido via patch lxml |
| 2 | E160 | `<totTrib>` com 2 filhos (choice exclusivo) | Mantém só `pTotTribSN` |
| 3 | E160 | `<nDPS>` com zeros à esquerda (pattern `[1-9][0-9]{0,14}`) | `_proximo_numero_dps()` retorna `str(int)` sem padding |
| 4 | E160 | `<verAplic>` > 20 chars | Encurtado para "iugu-nfse-df-0.3" |
| 5 | E160 | Grupo `<IBSCBS>` ausente | Injetado via lxml |
| — | E043 | "IM não cadastrada" em homologação Padrão Nacional | Resolvido pela habilitação cadastral de 05/06/2026 |
| — | E160 ABRASF | `<Signature>` em posição inválida (filha de `GerarNfseEnvio` em vez de `Rps`) | `_reposicionar_signature_dentro_de_rps` aplicado após `_assinar_xml` no fluxo ABRASF |

## 8. Pendências de segurança (avisar o Bruno no início)

1. 🔴 **Rotacionar credenciais expostas no chat ao longo do projeto:**
   - `IUGU_API_TOKEN` (já rotacionado uma vez; revisar)
   - `IUGU_WEBHOOK_TOKEN` (re-vazou num log paste — precisa re-rotação)
   - **Senha do certificado A1** (`mega10` — fraca)
   - Senha SMTP
2. 🟡 Confirmar `API_JWT_SECRET` forte e fixo no `.env` da VPS.
3. 🟡 Reduzir validade do JWT de 72h para 15–60min + refresh (ressalva da revisão de segurança).
4. 🟡 Adicionar **CSP** no vhost Apache (já temos HSTS/X-Frame/etc.) — mitiga o JWT em `localStorage` do painel.
5. 🟡 AMI/SIP do Asterisk expostos em `0.0.0.0` na mesma VPS — fora do escopo deste projeto, mas é o maior risco *da máquina*.

## 9. Estilo de colaboração com o Bruno

- Português BR, passo a passo, sem jargão desnecessário.
- Windows 11 + PowerShell (usar `curl.exe`, não `curl`).
- Forte em Python; às vezes confunde tokens/IDs (confirmar tipo quando ambíguo).
- Tem costume de colar credenciais no chat — avisar educadamente quando acontecer.
- **Preferência de teste:** por fluxo de negócio (fatura → pago → webhook → NFS-e → email), não por camada isolada.
- **SEMPRE usar `AskUserQuestion`** antes de iniciar tarefa multi-passo.

## 10. Decisões de produto já tomadas (evite re-perguntar)

- **Operação SOAP (ambos os backends):** síncrona, 1 documento → 1 resposta (`GerarNfse`).
- **Assinatura:** XMLDSig enveloped, C14N, RSA-SHA1, SHA1; certificado A1 SERPRORFB (válido até 05/03/2027).
- **Empresas (multi-cliente):** fonte é a **Iugu** (campo `notes` JSON em cada customer). Indexação por `customer_id` (não CNPJ — um CNPJ pode ter múltiplos customers/"departamentos"). ADR-0003 Etapa 1 deployada; Etapa 2 (rotas/app por `customer_id`) pendente.
- **Persistência:** ainda sem banco — estado em arquivos `nfse_emitidas/*.xml`. ADR-0001 (SQLite) proposto.
- **Webhook:** processa síncrono; falha recuperável → 502 (Iugu re-tenta), falha terminal/sucesso → 200 (WEB-010). Rejeição não é mais mascarada como sucesso (WEB-011).

## 11. Checklist de primeira ação na próxima sessão

1. ✅ Alertar pendências de credenciais (seção 8) — inclui **senha SMTP**.
2. ✅ Confirmar estado real lendo `CLAUDE.md` + `docs/fase2_nfse_df.md` + `docs/adr/README.md`.
3. ⚠️ Lembrar a regra de ouro do **contador de RPS por máquina**: emitir **só pela VPS** (seção 4).
4. ✅ Para retomar o roadmap arquitetural: começar pelo **ADR-0001 (SQLite)** — é a fundação dos ADRs 0002/0004.

---

**Resumo:** Fases 1, 2 e 3 **no ar e em produção**. A emissão de NFS-e DF agora é
**automática** no pagamento (ABRASF 2.04), com **e-mail automático** ao tomador e
**guardrail anti-duplicata blindado** (lock por fatura + evidência, ADR-0006). Pendências
principais: contador de RPS único na VPS, faixa de RPS 1–50, `ConsultarUrlNfse` (PDF oficial,
diferido) e rotação de credenciais. Roadmap arquitetural em `docs/adr/`.
