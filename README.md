# Integração Iugu → NFS-e DF

Sistema que, ao receber notificação de pagamento de uma fatura na [Iugu](https://iugu.com), verifica se o CNPJ do pagador está em uma planilha de empresas autorizadas e, se estiver, emite automaticamente uma Nota Fiscal de Serviço Eletrônica (NFS-e) no portal do ISS do Distrito Federal.

O projeto também inclui um **servidor MCP (Model Context Protocol)** que permite gerenciar boletos da Iugu diretamente pelo Claude Desktop / Cowork / Claude Code.

---

## Visão geral da arquitetura

```
┌────────────┐   webhook   ┌─────────────────────┐   busca    ┌────────────┐
│   Iugu     │ ──────────► │  FastAPI Webhook    │ ─────────► │  Planilha  │
│ (gatilho   │             │   webhook_server.py │            │   Excel    │
│ invoice    │             │                     │            └────────────┘
│ paid)      │             │                     │                 │
└────────────┘             │                     │                 │ CNPJ
                           │                     │                 │ autorizado?
                           │                     │                 ▼
                           │                     │   ┌─────────────────────┐
                           │                     │──►│   NFS-e DF          │
                           │                     │   │  (padrão nacional)  │
                           │                     │   │   nfse_df.py        │
                           │                     │   └─────────────────────┘
                           └─────────────────────┘           │
                                                             ▼
                                                  ┌──────────────────────┐
                                                  │ iss.fazenda.df.gov.br│
                                                  │     webservice       │
                                                  └──────────────────────┘

                    ┌──────────────────────────────────┐
                    │  MCP Iugu (mcp_iugu/server.py)   │  ◄───── Claude
                    │  create/list/get/cancel boletos  │         Desktop/Cowork/Code
                    └──────────────────────────────────┘
```

---

## Estrutura do projeto

```
Integração Iugo/
├── README.md                     ← este arquivo
├── requirements.txt              ← dependências Python
├── .env.example                  ← modelo de configuração
├── .gitignore
├── empresas_autorizadas.xlsx     ← planilha de CNPJs autorizados (já criada)
│
├── src/
│   ├── config.py                 ← carrega .env via Pydantic Settings
│   ├── iugu_client.py            ← cliente HTTP da API Iugu
│   ├── spreadsheet.py            ← leitura/escrita da planilha Excel
│   ├── webhook_server.py         ← FastAPI para receber webhooks
│   └── nfse_df.py                ← emissão NFS-e (esqueleto FASE 2)
│
├── mcp_iugu/
│   └── server.py                 ← servidor MCP para Claude
│
├── scripts/
│   ├── create_spreadsheet.py     ← gera planilha modelo
│   └── test_connection.py        ← valida credenciais e conexões
│
├── certs/                        ← coloque seu .pfx aqui (não vai pro git)
└── tests/                        ← (para testes futuros)
```

---

## Instalação passo a passo

### 1. Requisitos

- Python 3.10 ou superior
- Conta ativa na [Iugu](https://iugu.com) com Live Token da API
- Inscrição Municipal ativa no DF
- Certificado Digital A1 (.pfx) válido
- Usuário e senha do portal [iss.fazenda.df.gov.br](https://iss.fazenda.df.gov.br)

### 2. Criar e ativar o ambiente virtual

**Windows (PowerShell):**
```powershell
cd "caminho\para\Integração Iugo"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS / WSL:**
```bash
cd "caminho/para/Integração Iugo"
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 4. Configurar variáveis de ambiente

Copie o arquivo de exemplo e preencha com suas credenciais:

```bash
cp .env.example .env
```

Edite o `.env` com:
- Seu **Live Token** da Iugu (obtido em `app.iugu.com` → Administração → Contas → API Tokens)
- Dados do prestador (CNPJ, Inscrição Municipal, Razão Social)
- Caminho e senha do certificado digital

### 5. Preencher a planilha de empresas autorizadas

A planilha `empresas_autorizadas.xlsx` já está criada com 2 linhas de exemplo. Abra no Excel/LibreOffice e:

- Mantenha a primeira aba chamada **"Empresas Autorizadas"**
- **Não altere** os nomes nem a ordem das colunas
- Para cada empresa, preencha: CNPJ, razão social, e-mail, endereço completo, código de serviço, alíquota ISS
- Use `ativo = False` para desabilitar sem apagar a linha

Se quiser regenerar a planilha do zero (sem exemplos):
```bash
python scripts/create_spreadsheet.py --sem-exemplos
```

### 6. Validar tudo

```bash
python scripts/test_connection.py
```

Deve retornar ✅ para Planilha e Iugu. NFS-e DF pode retornar ⚠️ até você preencher o certificado e inscrição municipal.

---

## Uso — Servidor de Webhook

### Rodar localmente

```bash
python -m src.webhook_server
```

Servidor sobe em `http://localhost:8000`.

### Expor publicamente para testes (ngrok)

A Iugu precisa alcançar seu servidor pela internet. Durante o desenvolvimento, use [ngrok](https://ngrok.com):

```bash
ngrok http 8000
```

Copie a URL HTTPS que o ngrok gerar (ex: `https://abc123.ngrok-free.app`).

### Configurar o webhook na Iugu

1. Acesse [app.iugu.com](https://app.iugu.com) → **Administração** → **Gatilhos (Webhooks)**
2. Crie um novo gatilho:
   - **Evento**: `invoice.status_changed`
   - **URL**: `https://abc123.ngrok-free.app/webhook/iugu?token=SEU_IUGU_WEBHOOK_TOKEN`
3. Salve.

### Testar manualmente

Com o servidor rodando, você pode simular o processamento de uma fatura específica:

```bash
curl -X POST http://localhost:8000/processar/ID_DA_FATURA
```

### Endpoints disponíveis

| Método | Rota                    | Descrição                                  |
|--------|-------------------------|---------------------------------------------|
| GET    | `/health`               | Healthcheck                                 |
| POST   | `/webhook/iugu`         | Recebe gatilhos da Iugu                     |
| GET    | `/empresas`             | Lista empresas autorizadas (debug)          |
| POST   | `/processar/{id}`       | Reprocessa manualmente uma fatura paga      |

---

## Uso — MCP da Iugu (no Claude)

O servidor MCP expõe 5 ferramentas ao Claude: `create_boleto`, `list_boletos`, `get_boleto`, `cancel_boleto` e `refund_boleto`.

### Instalação no Claude Desktop

Edite o arquivo de configuração do Claude Desktop:

- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Adicione:

```json
{
  "mcpServers": {
    "iugu": {
      "command": "python",
      "args": ["-m", "mcp_iugu.server"],
      "cwd": "C:\\caminho\\absoluto\\para\\Integração Iugo",
      "env": {
        "IUGU_API_TOKEN": "seu_live_token_aqui"
      }
    }
  }
}
```

Reinicie o Claude Desktop. Se tudo estiver certo, você verá "iugu" na lista de servidores MCP conectados e poderá pedir coisas como:

> "Liste os 10 últimos boletos pagos"
> "Crie um boleto para Empresa XYZ com vencimento em 15/05 no valor de R$ 500"
> "Cancele o boleto com ID abc-123"

### Instalação no Claude Code

Rode uma vez:

```bash
claude mcp add iugu -- python -m mcp_iugu.server
```

Certifique-se de que o `.env` está no diretório onde o Claude Code é executado, ou defina a variável `IUGU_API_TOKEN`.

### Testar o MCP localmente (sem Claude)

```bash
python -m mcp_iugu.server
```

O servidor fica aguardando mensagens no stdin (protocolo MCP).

---

## Roadmap — o que já está pronto vs. o que falta

### ✅ Fase 1 — Concluída
- [x] Estrutura do projeto
- [x] Cliente Iugu (create/list/get/cancel/refund)
- [x] Planilha Excel de empresas autorizadas (com aba de instruções)
- [x] Servidor FastAPI com endpoint de webhook
- [x] MCP da Iugu para Claude Desktop/Cowork/Code
- [x] Script de validação de credenciais

### ⏳ Fase 2 — NFS-e DF (padrão nacional CGNFS-e)
- [ ] Baixar XSD e manual oficial em [iss.fazenda.df.gov.br/online](https://iss.fazenda.df.gov.br/online) (aba Downloads)
- [ ] Implementar `_montar_xml_dps()` seguindo o XSD
- [ ] Implementar `_assinar_xml()` com `signxml` + certificado A1
- [ ] Implementar `_enviar_ao_webservice()` (SOAP ou REST)
- [ ] Parsear resposta (número NFS-e + código verificação)
- [ ] Gerar PDF (DANFSE) e arquivar XML
- [ ] Homologar no ambiente de testes do DF
- [ ] Solicitar manual de homologação em suporte@notaeletronica.com.br

> ⚠️ **Atenção sobre a Reforma Tributária**: desde 01/01/2026 o DF opera no padrão nacional (CGNFS-e), substituindo o ABRASF. Certifique-se de baixar o manual atualizado e usar o XSD do padrão nacional.

### 🚀 Fase 3 — Migração para VPS Hostinger
- [ ] Provisionar VPS
- [ ] Configurar serviço systemd para rodar o webhook
- [ ] Configurar nginx como reverse proxy com HTTPS (Let's Encrypt)
- [ ] Apontar a URL de webhook oficial na Iugu para o domínio da VPS

### 🚀 Fase 4 — App mobile (opcional, futuro)
- [ ] Backend: expor API REST (FastAPI) para o app mobile
- [ ] Autenticação JWT
- [ ] App em **Flutter** (Android + Wear OS para Galaxy Watch)
- [ ] Telas: dashboard de faturas, criar boleto rápido, notificações push

---

## Segurança

- O `.env` **nunca** deve ser commitado — já está no `.gitignore`
- Certificado `.pfx` fica em `/certs` — também no `.gitignore`
- Use sempre **HTTPS** para a URL do webhook (ngrok já faz isso; na VPS, use Let's Encrypt)
- O `IUGU_WEBHOOK_TOKEN` é uma camada extra de proteção — a Iugu o enviará na URL e validamos antes de processar
- Ao migrar para VPS, considere usar certificado A3 (com dispositivo físico) ou um HSM

---

## Links úteis

- [Documentação API Iugu](https://dev.iugu.com/reference)
- [Gatilhos (Webhooks) Iugu](https://dev.iugu.com/docs/gatilhos)
- [Portal ISS DF](https://iss.fazenda.df.gov.br/)
- [Portal Nacional NFS-e (padrão CGNFS-e)](https://www.gov.br/nfse/pt-br)
- [Lista de Serviços DF / LC 116](https://www.fazenda.df.gov.br)
- [SDK MCP Python](https://github.com/modelcontextprotocol/python-sdk)
- [FastAPI](https://fastapi.tiangolo.com/)

---

## Suporte e dúvidas

- Suporte Iugu: [suporte.iugu.com](https://suporte.iugu.com)
- Homologação NFS-e DF: `suporte@notaeletronica.com.br`
- Reforma tributária / NFS-e DF: acompanhe [economia.df.gov.br](https://www.economia.df.gov.br)
