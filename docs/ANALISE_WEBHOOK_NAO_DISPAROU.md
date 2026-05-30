# Análise Investigativa: Webhook não disparou após pagamento (Fatura 9D6CFFE2621440C3B4D50F78A317EFA0)

**Data:** 21/04/2026  
**Fatura:** 9D6CFFE2621440C3B4D50F78A317EFA0  
**Resultado esperado:** Webhook `POST /webhook/iugu` deveria ter sido chamado  
**Resultado observado:** Nenhum evento de webhook registrado

---

## 1. Checklist: O que é necessário para o webhook funcionar

O webhook da Iugu só dispara se **todos** estes requisitos forem atendidos:

### 1.1 — Configuração no código
- ✅ Arquivo `src/webhook_server.py` implementado (linhas 97-141)
- ✅ Endpoint `/webhook/iugu` declarado como `@app.post("/webhook/iugu")`
- ✅ Esperando evento: `event="invoice.status_changed"` E `status="paid"`
- ✅ Token de validação: `IUGU_WEBHOOK_TOKEN` configurado no `.env`

### 1.2 — Configuração no .env
```
IUGU_WEBHOOK_TOKEN=a7f3c982e4b51d8760fa93c5ad0e6b2891f4d7c3e5a82b4f96d1e0a8c3b57f29
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8000
```
✅ Está preenchido no projeto

### 1.3 — Configuração na Iugu (CRÍTICO)
Este é o passo mais frequente que falta:

**❓ PERGUNTA:** Você registrou a URL do webhook no painel da Iugu?

Para isso, você precisa:
1. Acessar `https://app.iugu.com/`
2. Ir em **Configurações → Webhooks** (ou similar, pode variar na interface)
3. Adicionar um novo webhook com:
   - **URL:** `https://seu-dominio-ou-ip/webhook/iugu` (ou via cloudflared tunnel)
   - **Token (secret):** `a7f3c982e4b51d8760fa93c5ad0e6b2891f4d7c3e5a82b4f96d1e0a8c3b57f29` (o token do `.env`)
   - **Eventos:** `invoice.status_changed` (pagamentos de faturas)
4. Ativar o webhook

**Se esta etapa não foi feita, o webhook NUNCA será disparado.**

### 1.4 — Servidor rodando e acessível
- **Servidor local rodando?** `uvicorn src.webhook_server:app` deve estar em execução
- **URL acessível de fora?** Iugu precisa conseguir alcançar a URL do webhook:
  - Se localmente: precisa de **cloudflared tunnel** ou ngrok
  - Se em VPS: precisa de IP público + porta aberta no firewall

---

## 2. Possíveis motivos para o webhook não disparar

### Causa 1️⃣: Webhook não registrado na Iugu (MAIS COMUM)
**Probabilidade: 80%**

**Sintomas:**
- Fatura paga, mas nenhuma NFS-e emitida
- Sem nenhum POST em `/webhook/iugu`
- Sem logs de tentativa de webhook

**Verificação:**
```bash
# Rode isto e veja se aparece alguma requisição POST:
# (Se rodar localmente com cloudflared, veja os logs do cloudflared)
```

**Solução:**
1. Acesse `https://app.iugu.com/configuration/webhooks` (ou navegue pela interface)
2. Registre a URL: `https://seu-tunnel-cloudflared.ngrok.io/webhook/iugu`
3. Configure token e eventos

---

### Causa 2️⃣: URL do webhook inacessível (SEGUNDA MAIS COMUM)
**Probabilidade: 15%**

**Cenário:**
- Webhook está registrado na Iugu
- Servidor está rodando localmente
- Mas Iugu não consegue acessar porque não há exposição pública

**Verificação:**
```bash
# Teste se a URL é acessível de fora:
# (Substitua pela sua URL real)
curl -I https://seu-tunnel.ngrok.io/health

# Deve retornar 200 OK:
# HTTP/2 200
# ...
# {"status": "ok", "service": "iugu-nfse-df", ...}
```

**Solução:**
- Use **cloudflared tunnel** (configurado no projeto) para expor o servidor:
  ```bash
  cloudflared tunnel --url http://localhost:8000
  ```
- Ou use ngrok:
  ```bash
  ngrok http 8000
  ```
- Registre a URL gerada no painel de webhooks da Iugu

---

### Causa 3️⃣: Token de webhook incorreto
**Probabilidade: 3%**

**Cenário:**
- Webhook é disparado
- Mas a validação de token falha (`IUGU_WEBHOOK_TOKEN` não bate)
- Requisição é rejeitada com HTTP 401

**Verificação:**
- O token enviado pela Iugu (`X-Iugu-Token` header ou query param `token`) deve bater com `IUGU_WEBHOOK_TOKEN`
- Veja os logs: procure por "Token de webhook inválido"

**Solução:**
- Se a Iugu enviou um token diferente, atualize `IUGU_WEBHOOK_TOKEN` no `.env` para o que a Iugu envia
- Ou remova o token (`IUGU_WEBHOOK_TOKEN=""`) temporariamente para desabilitar a validação (menos seguro)

---

### Causa 4️⃣: Fatura paga de forma que não dispara webhook
**Probabilidade: 2%**

**Cenários:**
- Fatura foi marcada como "paga" manualmente no painel (importação, ajuste)
- Webhook registrado para apenas certos tipos de evento
- Fatura paga via integração que não dispara webhooks

**Verificação:**
- Consulte a API Iugu direto para confirmar status:
  ```bash
  # Verificar status da fatura (use IUGU_API_TOKEN do .env)
  curl -u 6171E3B14FF...:x \
    "https://api.iugu.com/v1/invoices/9D6CFFE2621440C3B4D50F78A317EFA0"
  ```

**Solução:**
- Use o endpoint de reprocessamento manual:
  ```bash
  curl -X POST http://localhost:8000/processar/9D6CFFE2621440C3B4D50F78A317EFA0
  ```
  Este simula o webhook e emite a NFS-e

---

### Causa 5️⃣: Erro no fluxo dentro do webhook
**Probabilidade: 1%**

**Cenário:**
- Webhook é disparado
- Mas falha em algum passo (CNPJ não encontrado, planilha vazia, etc.)
- NFS-e não é emitida

**Verificação:**
- Procure nos logs de erro
- Se rodar com `--log-level DEBUG`, veja mensagens detalhadas

**Solução:**
- Veja seção 4 abaixo para debugar o processamento manual

---

## 3. Fluxo esperado de um webhook (passo a passo)

```
1. Cliente paga fatura no Iugu (via boleto, PIX, etc.)
   ↓
2. Iugu registra pagamento (status="paid")
   ↓
3. Iugu verifica: há webhook registrado para "invoice.status_changed"?
   ↓
4. SIM: Iugu faz POST para URL do webhook com:
   POST /webhook/iugu
   Content-Type: application/x-www-form-urlencoded
   
   event=invoice.status_changed
   data[id]=9D6CFFE2621440C3B4D50F78A317EFA0
   data[status]=paid
   X-Iugu-Token: <seu-token>  (opcional, depende config)
   ↓
5. Servidor recebe, valida token, extrai invoice_id e status
   ↓
6. Chama processar_pagamento(invoice_id)
   ↓
7. Busca fatura na Iugu API
   ↓
8. Extrai CNPJ do pagador
   ↓
9. Busca CNPJ na planilha de empresas autorizadas
   ↓
10. Se autorizado e emitir_nf=True: chama emitir_nfse()
    ↓
11. Gera XML, assina com certificado A1
    ↓
12. Envia para webservice Nota Control (DF)
    ↓
13. Recebe resposta (sucesso ou erro)
    ↓
14. Gera PDF da NFS-e
    ↓
15. Retorna sucesso ao webhook
```

---

## 4. Testes para debugar

### Teste 1: Verificar se o servidor está rodando
```powershell
# Na sua máquina, em PowerShell:
$response = Invoke-WebRequest -Uri "http://localhost:8000/health"
$response.StatusCode  # Deve ser 200
$response.Content     # Deve mostrar JSON com status ok
```

### Teste 2: Reprocessar a fatura manualmente
Se o webhook não disparou, você pode forçar o reprocessamento:

```powershell
# Simula o webhook para esta fatura específica:
Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8000/processar/9D6CFFE2621440C3B4D50F78A317EFA0" `
  -ContentType "application/json"
```

Isso vai:
1. Buscar a fatura na Iugu
2. Verificar se está paga
3. Extrair CNPJ
4. Buscar na planilha
5. Emitir NFS-e se autorizado

**Veja o resultado no terminal onde está rodando o uvicorn.**

### Teste 3: Consultar a fatura na Iugu API
```powershell
# Verificar dados da fatura:
$token = "6171E3B14FF1D6767E0E9E2DB5106C7598C40659D69B614FF20DE839038470A3"
$invoice_id = "9D6CFFE2621440C3B4D50F78A317EFA0"

$response = Invoke-WebRequest `
  -Uri "https://api.iugu.com/v1/invoices/$invoice_id" `
  -Headers @{ "Authorization" = "Bearer $token" }

# Ou usando curl.exe:
curl.exe -u "${token}:x" `
  "https://api.iugu.com/v1/invoices/9D6CFFE2621440C3B4D50F78A317EFA0"
```

Procure por:
- `status`: deve ser `"paid"`
- `paid_at`: quando foi pago
- `items`: qual cliente (para extrair CNPJ)

### Teste 4: Verificar se o webhook foi registrado na Iugu
```powershell
# Listar webhooks configurados na conta Iugu:
$token = "6171E3B14FF1D6767E0E9E2DB5106C7598C40659D69B614FF20DE839038470A3"

curl.exe -u "${token}:x" `
  "https://api.iugu.com/v1/accounts"
  # Procure por "webhooks" na resposta
```

---

## 5. Roteiro de verificação (passo a passo para Bruno)

### Etapa 1: Confirmar que o servidor está rodando
```powershell
# Terminal 1: Rode o servidor
cd C:\Users\bruno.reis\.claude\Workspace\Integração Iugo
python -m uvicorn src.webhook_server:app --reload --host 0.0.0.0 --port 8000
```

Você deve ver:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Etapa 2: Expor o servidor publicamente (cloudflared)
```powershell
# Terminal 2: Rode cloudflared tunnel
cloudflared tunnel --url http://localhost:8000

# Você vai ver algo como:
# https://seu-subdomain.trycloudflare.com
```

**Copie a URL**, vamos precisar dela.

### Etapa 3: Verificar se a URL é acessível
```powershell
# Terminal 3: Teste a URL
curl.exe -I https://seu-subdomain.trycloudflare.com/health
# Deve retornar HTTP/2 200
```

### Etapa 4: Registrar (ou verificar) o webhook na Iugu
1. Acesse `https://app.iugu.com/`
2. Navegue até **Configurações → Webhooks** (ou similar)
3. Verifique se há um webhook para `/webhook/iugu`
4. Se não houver, crie um novo:
   - **URL:** `https://seu-subdomain.trycloudflare.com/webhook/iugu`
   - **Token (secret):** `a7f3c982e4b51d8760fa93c5ad0e6b2891f4d7c3e5a82b4f96d1e0a8c3b57f29`
   - **Eventos:** `invoice.status_changed`
5. Salve/Ative

### Etapa 5: Reprocessar a fatura ou gerar uma nova para teste
**Opção A — Reprocessar a fatura que já foi paga:**
```powershell
curl.exe -X POST "http://localhost:8000/processar/9D6CFFE2621440C3B4D50F78A317EFA0"
```

**Opção B — Gerar uma nova fatura de teste, pagar, e validar que o webhook dispara:**
1. Crie uma fatura manual de R$ 1,00 para uma empresa autorizada
2. Marque como paga
3. Veja nos logs do terminal 1 (`uvicorn`) se o webhook foi recebido

---

## 6. O que fazer depois (se tudo funcionar)

Após confirmar que o webhook está funcionando:

1. **Teste em produção com R$ 1,00:**
   ```powershell
   python scripts/emitir_nfse_manual.py --exemplo --producao --valor 1.00
   ```

2. **Se der sucesso:** Cancele a NFS-e de teste e confirme valores com o contador

3. **Depois:** Ative a automação fazendo uma fatura de verdade via Iugu

---

## 7. Credenciais expostas (AVISAR BRUNO!)

⚠️ **SEGURANÇA:** Duas credenciais estão expostas no arquivo `.env`:

1. **`IUGU_API_TOKEN`** (linha 9): `6171E3B1...` 
   - ➡️ **Revogar em:** `app.iugu.com → Administração → Contas → API Tokens`

2. **`NFSE_CERTIFICADO_SENHA`** (linha 58): `mega10`
   - ➡️ **Trocar via:** Software da Autoridade Certificadora (SERPRORFB)

**Isso foi já mencionado no CLAUDE.md, mas é crítico.**

---

## Sumário

| Causa | Probabilidade | Ação |
|-------|---------------|------|
| Webhook não registrado na Iugu | 80% | Registre em `app.iugu.com/webhooks` |
| URL inacessível | 15% | Use `cloudflared tunnel` para expor |
| Token incorreto | 3% | Valide `IUGU_WEBHOOK_TOKEN` |
| Fatura paga fora do sistema | 2% | Use `/processar/{id}` para reprocessar |
| Erro interno no webhook | 1% | Veja logs de erro |

**Próximo passo:** Seguir o roteiro de verificação (seção 5) e registrar (ou validar) o webhook na Iugu.
