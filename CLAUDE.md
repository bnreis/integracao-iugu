# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Conteúdo em **português BR** porque o usuário trabalha em pt-BR. Este arquivo cobre o que é estável: arquitetura, comandos, armadilhas. **Estado atual e próximos passos vivem em `docs/HANDOFF_OPUS46.md`** — leia esse arquivo antes de agir; ele é atualizado a cada sessão.

---

## ⚡ TL;DR

**Projeto:** Automação Iugu → NFS-e DF (padrão nacional CGNFS-e, vigente desde 2026-01-01) + boletos recorrentes mensais + MCP próprio da Iugu para Claude Desktop/Code.

**Cliente:** MEGASUPORTE SERVIÇOS DE TI LTDA (Brasília/DF, Simples Nacional ME/EPP).

**Fases:**
- **Fase 1** — webhook Iugu → empresa autorizada → decisão de emissão. ✅ Estável (validada em produção 05/06/2026).
- **Fase 2** — emissão NFS-e DF via webservice Nota Control. **Arquitetura dual** (ADR-0005): backend nacional DPS v1.01 (`_emitir_nacional`) E backend ABRASF 2.04 RPS (`_emitir_abrasf204`) coexistem em `src/nfse_df.py`, despachados por `NFSE_PADRAO` no `.env`. Os dois validam contra seus XSDs. **Habilitação em produção concluída** (chamado Nota Control 05/06/2026) — produção atual = ABRASF 2.04 (RPS série 3, `df.issnetonline.com.br/webservicenfse204`); a virada para o Padrão Nacional vira "trocar 1 variável" em 30/06/2026. ✅ **Auto-emissão LIGADA em produção** (06/06/2026): `NFSE_PADRAO=abrasf204` na VPS. Notas reais: **#408** (SINDICONDOMINIO, manual, 05/06) e **MEGATEAM** (R$1, automática pelo painel, 06/06). **E-mail automático** ao tomador (XML anexo + link de verificação) no ar, e **guardrail anti-duplicata blindado** (lock por fatura cross-process + evidência — ADR-0006, `src/nfse_guard.py`). ⚠️ **Contador de RPS é por máquina** → emitir SÓ pela VPS (emitir local diverge e dá E010). Falta (diferido): `ConsultarUrlNfse` (PDF oficial). Detalhes em `docs/fase2_nfse_df.md` + `docs/adr/ADR-0005-abrasf-2.04-rps.md` + `docs/adr/ADR-0006-guardrail-evidencia-lock-por-fatura.md`.
- **Fase 3** — Deploy na VPS Hostinger (`iugu.megasuporte.com`, IP `72.62.11.230`). ✅ Concluída — backend, painel web, HTTPS, cron e hardening de segurança no ar. ⚠️ VPS compartilhada com produção (Asterisk/PBX + Apache+PHP + MariaDB): não mexer no firewall, no fuso, nem rodar `apt upgrade`; Apache serve de proxy reverso (não instalar nginx).

**Antes de qualquer ação técnica:** confira a auto-memória (carregada automaticamente) — ela contém pendências vivas como rotação de credenciais e bloqueios cadastrais no Nota Control que podem invalidar o caminho "óbvio" sugerido pelo HANDOFF.

---

## 📖 Documentos a ler antes de mexer em código

1. **`docs/HANDOFF_OPUS46.md`** ⭐ — estado atual, último erro, próximo passo. Atualizado a cada sessão.
2. **`docs/fase2_nfse_df.md`** — detalhes técnicos da Fase 2 (operação SOAP, padrões, endpoints).
3. **`docs/scheduling.md`** — Task Scheduler para boletos recorrentes (Fase 1).
4. **`docs/deploy_vps.md`** — runbook vivo do deploy na VPS (Fase 3). Executar **um bloco por vez** no SSH, confirmando a saída antes do próximo.

> ⚠️ **`README.md` está desatualizado** — ainda lista Fase 2 como "não iniciada" e menciona Flutter. **Não confie nele para estado atual**; use HANDOFF + este CLAUDE.md.

## 📚 Referências oficiais (já no projeto)

- `docs/manual_oficial_integracao/Manual_integracao_v101.pdf` — Manual oficial Nota Control v1.01 (109 páginas, 17/03/2026). **Fonte da verdade** para XML, operações, códigos de erro.
- `docs/exemplos_oficiais/GerarNfseEnvio.xml` — template oficial v1.01 com todos os campos comentados (1.077 linhas).
- `docs/exemplos_oficiais/schema_v101.xsd.xml` — XSD oficial v1.01 (5.140 linhas).
- `docs/relatorio-integracao-nfse-df.md` ⭐ — **pesquisa de contexto da Fase 2** (provedor ISSnet/Nota Control, ABRASF 2.04 vs Padrão Nacional/DPS, cronograma DF, libs e agregadores). ⚠️ **Pista crítica:** o DF **não usa "inscrição municipal" tradicional — usa o CF/DF (Cadastro Fiscal do DF)**; possível causa-raiz do bloqueio E043/IM em produção. Prazo de adequação ao Padrão Nacional **prorrogado até 30/06/2026**.

---

## 🛠 Comandos comuns

Todo o ambiente Python vive no `.venv` da raiz. PowerShell padrão; ative o venv uma vez por shell.

```powershell
cd "C:\Users\bruno.reis\.claude\Workspace\Integração Iugo"
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # se for a primeira vez ou após pull
```

### Webhook + API de gestão (FastAPI)
```powershell
# servidor local na 8000 (recarrega ao salvar)
uvicorn src.webhook_server:app --reload --host 0.0.0.0 --port 8000

# expor publicamente para a Iugu alcançar (URL muda a cada reinício)
cloudflared tunnel --url http://localhost:8000
```

### Boletos recorrentes (Fase 1)
```powershell
# rodada manual; agendado via Task Scheduler em produção (ver docs/scheduling.md)
python scripts\run_scheduled_invoices.py --saida-json logs\lote.json
```

### NFS-e DF (Fase 2)
```powershell
# valida o último DPS gerado contra XSD v1.01 (offline, rápido — bom para diagnóstico)
python scripts\validar_dps_xsd.py
python scripts\validar_dps_xsd.py --gerar          # gera DPS de exemplo e valida

# emissão manual (homologação por padrão)
python scripts\emitir_nfse_manual.py --exemplo
python scripts\emitir_nfse_manual.py <invoice_id_iugu>

# produção com R$1,00 — sempre rode --dry-run antes de enviar de verdade
python scripts\emitir_nfse_manual.py --exemplo --producao --valor 1.00 --dry-run
python scripts\emitir_nfse_manual.py --exemplo --producao --valor 1.00
```

### Conexões e credenciais
```powershell
python scripts\test_connection.py    # valida planilha, Iugu, NFS-e DF
```

### MCP Iugu (standalone, sem Claude)
```powershell
python -m mcp_iugu.server             # fica aguardando stdin (protocolo MCP)
```

### Mobile app (Expo / React Native)
```powershell
cd mobile
npm start                  # Expo Dev Tools
npm run android            # roda no Android conectado
npm run build:apk          # EAS Build — gera APK preview
```

### Testes
**Não há suíte de testes automatizada.** A pasta `tests/` existe mas está vazia, e não há `pytest.ini` / `pyproject.toml` configurados. Validação acontece via:
- `scripts/test_connection.py` (smoke test de credenciais e dependências)
- `scripts/validar_dps_xsd.py` (validação local de XML)
- `scripts/emitir_nfse_manual.py --dry-run` (gera XML sem enviar)

Se for adicionar `pytest`, alinhe com o Bruno antes — ele tem preferência por testar **por fluxo de negócio**, não por camada (ver auto-memória).

---

## 🏗 Arquitetura

```
                 webhook (invoice.status_changed)
   Iugu  ──────────────────────────────► FastAPI (src/webhook_server.py)
                                                 │
                                                 │ CNPJ → empresa autorizada?
                                                 ▼
                                         src/iugu_empresas.py
                                         (customers da Iugu + notes JSON)
                                                 │ sim
                                                 ▼
                                         src/nfse_df.py
                                         (monta DPS via nfelib v1.00,
                                          patcheia para v1.01 via lxml,
                                          assina com cert A1, envia SOAP)
                                                 │
                                                 ▼
                                         Nota Control / iss.fazenda.df.gov.br
                                                 │
                                                 ▼
                                         src/email_nfse.py
                                         (e-mail ao tomador: XML anexo + link de verificação)


   Claude Desktop/Code  ──── stdio ────►  mcp_iugu/server.py
                                          (create/list/get/cancel/refund)


   App Android (mobile/, Expo)  ── HTTP+JWT ──►  src/api_routes.py
                                                 (rotas /api/* protegidas
                                                  por src/auth.py)
```

**Fonte de dados de empresas: a Iugu (NÃO a planilha).** Confirmado por inspeção dos imports + execução real em 2026-05-30.
- `src/iugu_empresas.py` — **fonte ativa em produção**. Lê todos os customers da Iugu e monta a config de negócio (`codigo_servico`, `aliquota_iss`, `emitir_nf`, etc.) a partir do campo `notes` (JSON) de cada cliente. Importado por `webhook_server`, `scheduled_invoices`, `nfse_df`, `email_nfse` e `api_routes`. **Multi-cliente:** o repositório indexa empresas por `customer_id` (chave única), não por CNPJ — um mesmo CNPJ pode ter múltiplos customers na Iugu. Use `buscar_por_customer_id()`; `buscar_por_cnpj()`/`listar_por_cnpj()` existem por compatibilidade. ⚠️ Como a **listagem base da Iugu está quebrada** (retorna 1), o `carregar()` enumera por **busca (`query=`)** + **registro local** (`registro_customer_ids.json`) + resolução por ID on-demand — ver `docs/iugu_listagem_customers_contorno.md` e `scripts/seed_customer_ids.py`.
- `src/spreadsheet.py` — **legado**. Lê `empresas_autorizadas.xlsx`. Só sobrevive em scripts utilitários (`emitir_nfse_manual`, `import_clients_from_iugu`, `test_connection`, etc.). ⚠️ O xlsx está **desatualizado e divergente da Iugu** — não use como fonte nem confie nesses scripts para dados reais.

**Por que o "patch v1.01"?** A `nfelib` ainda emite XML no schema v1.00, mas o DF rejeita com E160 (Reforma Tributária mudou estrutura). `nfse_df.py` gera v1.00 e aplica 5 transformações via `lxml` para virar v1.01 válido — ver `_patch_xml_para_v101()`. Os 4 bugs específicos do schema estão tabulados em **Histórico de bugs** abaixo.

**Estabilidade dos módulos:**

| Módulo | Status | Quando mexer |
|---|---|---|
| `src/iugu_client.py` | Estável em produção | Só com bug reproduzível |
| `src/webhook_server.py` | Estável em produção | Só com bug reproduzível |
| `src/iugu_empresas.py` | **Fonte ativa em produção** | Só com bug reproduzível; mudar campo no `notes` exige alinhamento |
| `src/spreadsheet.py` | Legado — só scripts utilitários; xlsx desatualizado | Evitar; não usar como fonte de dados |
| `src/scheduled_invoices.py` | Estável | Só com bug reproduzível |
| `mcp_iugu/server.py` | Estável | Só com bug reproduzível |
| `src/nfse_df.py` | Estável em produção (ABRASF 2.04 auto-emissão) | Sim, se aparecer novo erro de schema |
| `src/nfse_guard.py` | **Estável em produção** (lock + guardrail anti-duplicata, ADR-0006) | Só com bug reproduzível; mexer aqui é risco de NFS-e duplicada |
| `src/email_nfse.py` | Estável em produção (auto-envio + reenviar) | Sim, sob pedido |
| `src/config.py` | Adicione campos com Field + default explícito | Sim |
| `src/api_routes.py`, `src/auth.py` | Em iteração com mobile | Sim |

"Estável em produção" significa: o código já roda no fluxo real; mudanças sem causa específica costumam quebrar o fluxo do Bruno. Antes de refatorar, pergunte.

---

## 🛡 Ambiente Windows + armadilhas

- **SO:** Windows 11 + PowerShell. Path do projeto: `C:\Users\bruno.reis\.claude\Workspace\Integração Iugo`.
- **Use `curl.exe`, não `curl`** — `curl` em PowerShell é alias de `Invoke-WebRequest`, que não aceita `-u`.
- **Sandbox do Cowork não alcança `api.iugu.com` nem `notacontrol.com.br`** (proxy bloqueia). Qualquer script que toque API real deve rodar na máquina do Bruno, não no sandbox.
- **`erpbrasil.assinatura.Certificado` espera path (string), não bytes.** Se passar bytes, ele tenta decodificar como base64 e falha silenciosamente.
- **Cloudflared quick tunnel gera URL nova a cada reinício** — gatilho da Iugu fica órfão. Em produção (Fase 3 / VPS) não precisa: nginx + Let's Encrypt resolve.
- **Certificado A1:** `certs/*.pfx`, válido até 2027-03-05.
- ⚠️ **TLS do servidor da ISSnet (não confundir com o A1):** a ISSnet (`df.issnetonline.com.br`) usa cert **GoDaddy** que renova periodicamente; quando a cadeia nova não é ancorada pelo `certifi`/bundle do SO, TODA emissão falha com `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`. Conserto: `verify` é configurável via `NFSE_CA_BUNDLE_PATH` (`.env`) → aponta pra um bundle `certifi + cadeia GoDaddy` (`certs/issnet_ca_bundle.pem`). **Nunca usar `verify=False`.** Procedimento de refazer o bundle está no HANDOFF (seção 14/07/2026). Aconteceu em ~30/06/2026.
- ⚠️ **Listagem de clientes da Iugu quebrada (bug deles, desde ~18/06/2026):** `GET /v1/customers` SEM filtro retorna só 1, mas `GET /v1/customers/{id}` e `?query=<termo>` funcionam. Contorno no código: `carregar()` enumera por **busca (vogais)** + registro local (`nfse_emitidas/registro_customer_ids.json`) + busca por ID on-demand; `scripts/seed_customer_ids.py` semeia a lista completa (busca a-z). Se a lista de empresas vier incompleta, rode o seed + restart. Detalhes em `docs/iugu_listagem_customers_contorno.md`.
- 🗓️ **CNPJ alfanumérico (Receita/Iugu, a partir de jul/2026):** validação de CNPJ hoje exige 14 **dígitos** — precisará aceitar letras (tratar como string). Adaptação pendente antes de julho/2026.

---

## 🤝 Estilo de colaboração com o Bruno

- Responder em **português BR**, passo a passo, sem jargão desnecessário.
- **Sempre usar `AskUserQuestion`** antes de iniciar tarefa multi-passo. Ele decide rápido e bem.
- Confunde tokens/IDs ocasionalmente (já colou `invoice_id` onde precisava de token) — confirmar tipo de dado quando o valor parece ambíguo.
- Tem costume de colar credenciais no chat — avisar educadamente quando acontecer e tratar como pendência de rotação.
- Gosta de saber o **porquê** de cada passo, não só "faça X".
- Preferência de teste: por **fluxo de negócio** (fatura → pago → webhook → NFS-e → email), não por camada técnica isolada.

---

## 🔒 Hardening de segurança (Fase 8)

Já aplicado no backend — não regredir sem motivo:
- **Login** (`src/auth.py`): credenciais comparadas com `secrets.compare_digest` (tempo constante) + rate limit no endpoint de login.
- **CORS restrito** a origens conhecidas (não usar `allow_origins=["*"]`).
- **JWT obrigatório** em rotas sensíveis, incluindo `/empresas` e `/processar/{id}`.
- **Docs do FastAPI desativados** (`/docs`, `/redoc`, OpenAPI) em produção.

## 🐛 Histórico de bugs do schema v1.01 (referência)

Os 4 bugs que travaram a Fase 2 com `E160`. Já corrigidos em `src/nfse_df.py`; deixar aqui para futuros assistentes diagnosticarem regressões mais rápido.

| # | Sintoma | Causa | Correção |
|---|---|---|---|
| 1 | E160 | `<opConsumServ>` em `<locPrest>` — removido no XSD v1.01 | Removido do construtor nfelib + remoção via lxml em `_patch_xml_para_v101()` |
| 2 | E160 | `<totTrib>` com 2 filhos (`indTotTrib` + `pTotTribSN`) — é `<choice>` exclusivo | Mantém apenas `pTotTribSN` (Simples Nacional) |
| 3 | E160 | `<nDPS>` com zeros à esquerda — pattern XSD `[1-9][0-9]{0,14}` | `_proximo_numero_dps()` retorna `str(int)` sem padding |
| 4 | E160 | `<verAplic>` com 27 chars — maxLength=20 | `"iugu-nfse-df-0.3"` (16 chars) |

Outras correções relacionadas: grupo `<IBSCBS>` injetado via lxml (era ausente), `versao` 1.00→1.01, Id da DPS com 45 chars no formato correto, `cTribNac` normalizado de `"01.07"` → `"010700"`, `cTribMun` e `cNBS` adicionados, ordem dos filhos de `<tribMun>` corrigida, `tpImunidade` removido quando `tribISSQN != 2`. Detalhe completo no HANDOFF.

---

## 🗂 Estrutura do código

```
src/
├── config.py             pydantic-settings carrega .env
├── iugu_client.py        cliente HTTP Iugu
├── webhook_server.py     FastAPI: webhooks + monta routers
├── api_routes.py         endpoints /api/* (gestão para mobile, JWT)
├── auth.py               login + dependency JWT
├── iugu_empresas.py      ★ FONTE ATIVA — empresas vêm da Iugu (notes JSON)
├── spreadsheet.py        legado (xlsx desatualizado) — só scripts utilitários
├── scheduled_invoices.py boletos recorrentes mensais (usa nfse_guard no cron)
├── nfse_guard.py         ★ lock por invoice (cross-process) + guardrail anti-duplicata (ADR-0006)
├── nfse_df.py            emissão NFS-e DF — dispatcher dual ABRASF 2.04 / DPS v1.01 + assinatura + SOAP
└── email_nfse.py         e-mail ao tomador (template HTML + logo CID + XML anexo)

mcp_iugu/server.py        MCP para Claude Desktop/Code

mobile/                   React Native + Expo
├── App.tsx
├── app.json, eas.json    config Expo + EAS Build
├── package.json          (npm start | npm run android | npm run build:apk)
└── src/
    ├── screens/          Login, Dashboard, Faturas, Empresas, CadastrarEmpresa
    ├── navigation/       AppNavigator (stack navigation)
    └── services/         API client para o backend

scripts/                  Utilitários CLI (ver "Comandos comuns" acima)
                          + migração (migrate_*.py), auditoria, import
                          + seed_customer_ids.py (semeia o registro de clientes — contorno listagem Iugu)
docs/                     Documentação + manual oficial + XSD/exemplos
certs/*.pfx               Certificado A1 (não commitar)
nfse_emitidas/            XMLs enviados/recebidos + logs nfse_*.json + registro_customer_ids.json
empresas_autorizadas.xlsx Planilha LEGADA (desatualizada; fonte real é a Iugu)
.env                      Configurações + credenciais (não commitar)
```
