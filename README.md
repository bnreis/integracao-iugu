# Integração Iugu → NFS-e DF

Automação fiscal que, ao receber o webhook de **fatura paga** da [Iugu](https://iugu.com),
verifica se o cliente está autorizado e **emite automaticamente a NFS-e** no Distrito
Federal, **envia o XML por e-mail** ao tomador e mantém a **cobrança recorrente** (boletos
mensais). Inclui **painel web + app mobile** de gestão e um **servidor MCP** da Iugu para o
Claude.

> **Cliente de referência:** MEGASUPORTE SERVIÇOS DE TI LTDA (Brasília/DF, Simples Nacional
> ME/EPP). Em produção desde 06/2026 — 1ª NFS-e real emitida pela integração em 05/06/2026.

---

## ⚡ TL;DR (estado atual)

| Capacidade | Status |
|---|---|
| Webhook Iugu → identificação de cliente autorizado | ✅ produção |
| Emissão NFS-e DF (automática no pagamento) | ✅ produção (ABRASF 2.04) |
| E-mail automático ao tomador (XML anexo + link de verificação) | ✅ produção |
| Boletos recorrentes mensais (cron) | ✅ produção |
| Painel web + app Android (Expo) | ✅ produção / iteração |
| MCP da Iugu para Claude | ✅ |
| Guardrail anti-duplicata (evidência + lock por fatura) | ✅ |

**Arquitetura de emissão dual** (ver `docs/adr/ADR-0005`): dois backends coexistem em
`src/nfse_df.py` e são escolhidos por **uma variável** (`NFSE_PADRAO`):
- `abrasf204` — **RPS série 3 / ABRASF 2.04** → produção HOJE no DF.
- `nacional` — **DPS v1.01 / Padrão Nacional** → vira obrigatório em **30/06/2026** (troca de 1 variável).

---

## 🏗 Arquitetura

```
                webhook (invoice.status_changed = paid)
  Iugu ───────────────────────────────────►  FastAPI (src/webhook_server.py)
                                                    │
                                  customer_id → empresa autorizada? (src/iugu_empresas.py)
                                                    │ sim + emitir_nf=True
                                                    ▼
                                      src/nfse_guard.py  ── lock por invoice_id +
                                                            guardrail anti-duplicata
                                                    │ livre
                                                    ▼
                                      src/nfse_df.py  ── emitir_nfse() despacha por NFSE_PADRAO
                                          ├── _emitir_abrasf204  (RPS série 3, produção)
                                          └── _emitir_nacional   (DPS v1.01, pós-30/06)
                                                    │  assina (A1) + SOAP
                                                    ▼
                                      ISSnet DF (df.issnetonline.com.br/webservicenfse204)
                                                    │ NFS-e nº + código
                                                    ▼
                                      src/email_nfse.py  ── e-mail ao tomador
                                                            (XML anexo + link de verificação)

  Cron (src/scheduled_invoices.py)  ── boletos recorrentes; emite-na-criação para
                                        empresas nf_na_criacao=True (mesmo lock+guardrail)

  Claude Desktop/Code  ── stdio ──►  mcp_iugu/server.py  (create/list/get/cancel/refund)

  App Android (mobile/, Expo)  ── HTTP+JWT ──►  src/api_routes.py (rotas /api/*, src/auth.py)
```

**Fonte de dados das empresas = a Iugu (não planilha).** `src/iugu_empresas.py` lê os
*customers* da Iugu e monta a config fiscal de cada um a partir do campo `notes` (JSON):
`codigo_servico`, `aliquota_iss`, `emitir_nf`, `nf_na_criacao`, etc. A indexação é por
**`customer_id`** (chave única) — um mesmo CNPJ pode ter vários customers/departamentos.
A planilha `empresas_autorizadas.xlsx` é **legada** (só uns scripts utilitários ainda a leem).

---

## 🧱 Stack

- **Backend:** Python 3.13, FastAPI + Uvicorn (1 worker), `pydantic-settings` (`.env`), `httpx`, `loguru`.
- **NFS-e:** `lxml` (montagem/patches de XML), `erpbrasil.assinatura` (assinatura XMLDSig com certificado **A1 .pfx**), SOAP com mTLS.
- **E-mail:** `smtplib` + MIME (`multipart/mixed` → `related` com logo CID + anexo XML).
- **Mobile:** React Native + Expo (pasta `mobile/`), EAS Build.
- **Infra:** VPS Hostinger (Ubuntu), `systemd`, Apache (proxy reverso) + Let's Encrypt, `cron`.

---

## 📂 Estrutura

```
src/
├── config.py             pydantic-settings carrega .env
├── iugu_client.py        cliente HTTP da Iugu
├── webhook_server.py     FastAPI: webhooks + monta routers + fluxo de pagamento
├── api_routes.py         endpoints /api/* (gestão p/ painel e mobile, JWT)
├── auth.py               login + dependency JWT
├── iugu_empresas.py      ★ FONTE ATIVA — empresas vêm da Iugu (notes JSON), por customer_id
├── nfse_guard.py         ★ lock por invoice (cross-process) + guardrail anti-duplicata
├── nfse_df.py            emissão NFS-e DF — dispatcher dual (ABRASF 2.04 / DPS v1.01)
├── email_nfse.py         e-mail ao tomador (template HTML + logo + XML anexo)
├── scheduled_invoices.py boletos recorrentes mensais (cron)
└── spreadsheet.py        LEGADO (xlsx) — só scripts utilitários

mcp_iugu/server.py        servidor MCP da Iugu para Claude
mobile/                   app React Native + Expo (Login, Dashboard, Faturas, Empresas)
scripts/                  utilitários CLI (validação, emissão manual, preview de e-mail, migração)
tests/test_webhook_status.py   testes offline do fluxo do webhook (guardrail/lock/e-mail)
docs/                     documentação, ADRs, manuais oficiais, XSD/WSDL/exemplos
assets/logo_megasuporte.png    logo embutida no e-mail (necessária na VPS p/ o CID)
certs/*.pfx               certificado A1 (NÃO commitar)
nfse_emitidas/            XMLs enviados/recebidos + logs nfse_<invoice_id>.json (gitignored)
.env                      configurações + credenciais (NÃO commitar)
```

---

## 🚀 Como replicar (do zero)

### 1. Pré-requisitos
- Python 3.11+ (projeto roda em 3.13)
- Conta Iugu com **Live API Token**
- **Habilitação fiscal no DF** junto ao provedor (ISSnet/Nota Control) + **CF/DF** (a "inscrição municipal" do DF) ativo para emissão em produção
- **Certificado Digital A1** (`.pfx`) válido
- (Produção) VPS Linux com domínio + HTTPS

### 2. Ambiente
```bash
python -m venv .venv
# Windows:  .\.venv\Scripts\Activate.ps1
# Linux:    source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configurar `.env`
Copie `.env.example` → `.env` e preencha (ver tabela abaixo). Coloque o `.pfx` em `certs/`.

### 4. Cadastrar empresas **na Iugu** (não na planilha)
No campo `notes` (JSON) de cada *customer* da Iugu, defina a config fiscal. Mínimo:
```json
{"emitir_nf": true, "codigo_servico": "01.07", "aliquota_iss": 2.0, "nf_na_criacao": false}
```
- `emitir_nf: true` → essa empresa emite NFS-e automaticamente quando a fatura é paga.
- `nf_na_criacao: true` → emite junto com a criação do boleto (cron), não no pagamento.

### 5. Validar conexões
```bash
python scripts/test_connection.py
```

### 6. Rodar o webhook localmente
```bash
uvicorn src.webhook_server:app --reload --host 0.0.0.0 --port 8000
# expor p/ a Iugu alcançar (URL muda a cada reinício):
cloudflared tunnel --url http://localhost:8000
```
Na Iugu: **Gatilhos** → evento `invoice.status_changed` → URL
`https://SEU_HOST/webhook/iugu?token=SEU_IUGU_WEBHOOK_TOKEN`.

---

## ⚙️ Variáveis de ambiente principais (`.env`)

| Variável | Para quê |
|---|---|
| `IUGU_API_TOKEN` | Live token da API Iugu |
| `IUGU_WEBHOOK_TOKEN` | token validado na URL do webhook |
| `NFSE_AMBIENTE` | `producao` ou `homologacao` |
| `NFSE_PADRAO` | **`abrasf204`** (hoje) ou `nacional` (pós-30/06/2026) |
| `NFSE_CERT_PATH` / `NFSE_CERT_SENHA` | caminho e senha do `.pfx` A1 |
| `NFSE_INSCRICAO_MUNICIPAL` | CF/DF do prestador (ex.: `0796481500161`) |
| `NFSE_CNPJ_PRESTADOR` | CNPJ do prestador |
| `NFSE_SERIE_RPS` | série do RPS (DF = `3`) |
| `NFSE_CNAE` / `NFSE_MUNICIPIO_INCIDENCIA` | exigidos pelo ISSnet DF (`6209100` / `5300108`) |
| `NFSE_CODIGO_TRIB_MUNICIPAL` / `NFSE_ALIQUOTA_ISS_PADRAO` | tributação (ex.: `1071` / `2.0`) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USAR_TLS` | servidor de e-mail |
| `SMTP_USUARIO` / `SMTP_SENHA` | credenciais SMTP |
| `SMTP_REMETENTE_EMAIL` / `SMTP_REMETENTE_NOME` | remetente (ex.: `financeiro@megasuporte.com`) |
| `API_JWT_SECRET` | segredo do JWT do painel/app |

> ⚠️ **Nunca** commite `.env` nem `certs/*.pfx` (já estão no `.gitignore`).

---

## 🔁 Fluxo de emissão automática (resumo)

1. Iugu envia `invoice.status_changed`; o webhook confirma `status == paid`.
2. Resolve a empresa por `customer_id`; respeita a flag `emitir_nf`.
3. **Lock por `invoice_id`** (cross-process) + **guardrail anti-duplicata** (`src/nfse_guard.py`):
   só um log de emissão **real** (`nfse_<invoice_id>.json` com `sucesso=true`) prova que a
   nota existe. Evita 2ª nota em reentrega da Iugu ou corrida webhook×cron.
4. `emitir_nfse()` despacha por `NFSE_PADRAO`, assina com o A1 e envia SOAP ao ISSnet.
5. Em sucesso: grava o log, devolve número + código e **dispara o e-mail** ao tomador
   (XML anexo + link oficial de verificação de autenticidade do DF).

**Status HTTP do webhook (WEB-010/011):** falha recuperável → **502** (Iugu re-tenta);
sucesso, rejeição fiscal ou duplicata → **200** (não re-tenta).

---

## 🛠 Comandos comuns

```bash
# Webhook + API
uvicorn src.webhook_server:app --reload --host 0.0.0.0 --port 8000

# NFS-e DF — validar XML offline
python scripts/validar_rps_xsd.py            # ABRASF 2.04
python scripts/validar_dps_xsd.py            # Padrão Nacional

# Emissão manual (homologação por padrão; --producao --valor 1.00 --dry-run p/ teste)
python scripts/emitir_nfse_manual.py <invoice_id>

# Pré-visualizar o e-mail da NFS-e (sem enviar)
python scripts/preview_email_nfse.py

# Enviar e-mail de TESTE para um destinatário arbitrário (usa o template real)
python scripts/enviar_email_nfse_teste.py --para voce@exemplo.com --xml nfse_emitidas/rps_X_retorno_*.xml

# Boletos recorrentes (cron em produção)
python scripts/run_scheduled_invoices.py --saida-json logs/lote.json

# Testes (offline; não tocam APIs reais)
python tests/test_webhook_status.py

# MCP Iugu (standalone)
python -m mcp_iugu.server
```

### Mobile (Expo)
```bash
cd mobile
npm start                 # Expo Dev Tools
npm run android           # roda no Android conectado
npm run build:apk         # EAS Build (APK preview)
```

---

## ☁️ Deploy (VPS)

Modelo: **edições locais → `git push` → na VPS `git pull` + `systemctl restart`**.
Runbook detalhado em `docs/deploy_vps.md`. Resumo:

```bash
cd /opt/integracao-iugu
sudo -u iugu git pull
sudo systemctl restart iugu-webhook    # recarrega .env (NFSE_PADRAO etc.)
systemctl status iugu-webhook --no-pager
```

- Serviço `systemd`: `uvicorn src.webhook_server:app --host 127.0.0.1 --port 8000`.
- **Apache** faz o proxy reverso + HTTPS (Let's Encrypt). VPS **compartilhada** com produção
  (Asterisk/PBX + Apache/PHP + MariaDB): **não** mexer no firewall, fuso, `apt upgrade`, nem instalar nginx.
- **Emissões devem sair apenas pela VPS.** O contador de RPS (`.contador_rps.json`) é por
  máquina — emitir também pela máquina local faz os contadores divergirem e o ISSnet
  rejeita com **E010** (RPS já informado).

---

## 🔒 Segurança

- `compare_digest` no login + rate-limit; **JWT** obrigatório nas rotas sensíveis; **CORS** restrito; docs do FastAPI desativados em produção; HSTS/X-Frame no Apache.
- Detalhes e pendências: `docs/pentest_2026-06.md`, `docs/ressalvas_pentest_2026-06.md`, e a seção de pendências do `docs/HANDOFF_OPUS46.md` (rotação de credenciais).

---

## 📚 Documentação

| Arquivo | Conteúdo |
|---|---|
| `CLAUDE.md` | Guia estável (arquitetura, comandos, armadilhas) para assistentes/devs |
| `docs/HANDOFF_OPUS46.md` | **Estado atual + próximos passos** (atualizado a cada sessão) |
| `docs/fase2_nfse_df.md` | Detalhes técnicos da emissão NFS-e DF |
| `docs/adr/` | Decisões de arquitetura (ADR-0001..0006) |
| `docs/deploy_vps.md` | Runbook da infra na VPS |
| `docs/runbook_primeira_emissao_abrasf.md` | Passo a passo da emissão ABRASF |
| `docs/relatorio-integracao-nfse-df.md` | Pesquisa de contexto (ABRASF × Padrão Nacional, CF/DF) |

---

## 🔗 Links úteis

- [API Iugu](https://dev.iugu.com/reference) · [Gatilhos/Webhooks](https://dev.iugu.com/docs/gatilhos)
- [Verificação de autenticidade NFS-e DF](https://iss.fazenda.df.gov.br/online/NotaDigital/VerificaAutenticidade.aspx)
- [ABRASF — NFS-e 2.04](https://abrasf.org.br/biblioteca/arquivos-publicos/nfs-e/versao-2-04)
- [Portal Nacional NFS-e (CGNFS-e)](https://www.gov.br/nfse/pt-br) · [FastAPI](https://fastapi.tiangolo.com/) · [SDK MCP Python](https://github.com/modelcontextprotocol/python-sdk)
