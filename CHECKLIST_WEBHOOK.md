# ⚡ Checklist Rápido: Por que o Webhook não disparou?

**Fatura:** 9D6CFFE2621440C3B4D50F78A317EFA0  
**Data:** 21/04/2026

---

## 🚀 Ação imediata (5 minutos)

### PASSO 1: Rode o script de debug
```powershell
cd C:\Users\bruno.reis\.claude\Workspace\Integração Iugo
python scripts/debug_webhook.py 9D6CFFE2621440C3B4D50F78A317EFA0
```

Este script vai:
- ✅ Testar conexão com Iugu
- ✅ Buscar status da fatura
- ✅ Verificar se CNPJ está na planilha
- ✅ Testar servidor webhook local
- ✅ Simular o webhook (emitir NFS-e manualmente)

**Se tudo passar:** Webhook está OK, e você já tem a NFS-e emitida! 🎉

---

## 🔍 Se o teste falhar

### ❌ Erro: Servidor não está rodando
**Solução:**
```powershell
# Terminal 1: Rode o servidor
uvicorn src.webhook_server:app --reload --host 0.0.0.0 --port 8000
```

Você deve ver:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

### ❌ Erro: Conexão recusada com Iugu API
**Motivo:** Token inválido ou expirado  
**Solução:**
1. Vá em `app.iugu.com` → Administração → API Tokens
2. Copie o token atual
3. Atualize em `.env`: `IUGU_API_TOKEN=<novo-token>`

### ❌ Erro: CNPJ não está na planilha
**Motivo:** Fatura foi paga por empresa não autorizada  
**Solução:**
1. Abra `empresas_autorizadas.xlsx`
2. Adicione o CNPJ na planilha
3. Coloque `emitir_nf=Verdadeiro` (em português)
4. Salve

Depois tente novamente:
```powershell
python scripts/debug_webhook.py 9D6CFFE2621440C3B4D50F78A317EFA0
```

---

## 📋 O que o webhook faz

```
1. Iugu detecta: fatura paga ✅
2. Iugu envia POST para: /webhook/iugu
3. Servidor recebe o evento
4. Extrai CNPJ do pagador
5. Busca CNPJ na planilha
6. Se autorizado: emite NFS-e automaticamente
7. Gera PDF e envia por e-mail
```

**Para isso funcionar, você precisa registrar o webhook na Iugu (veja seção abaixo).**

---

## 📌 Registrar Webhook na Iugu (CRÍTICO!)

Este é o passo que frequentemente falta:

### Se você ainda NÃO registrou:

1. **Expose o servidor localmente:**
   ```powershell
   # Terminal 2: (deixe rodar)
   cloudflared tunnel --url http://localhost:8000
   ```
   Você vai ver algo como:
   ```
   https://seu-uuid.trycloudflare.com
   ```
   Copie esta URL.

2. **Registre na Iugu:**
   - Acesse: `https://app.iugu.com/`
   - Vá em: **Configurações** (ou ⚙️ Settings)
   - Procure: **Webhooks** ou **Integrações**
   - Clique: **Adicionar Webhook**
   - Preencha:
     - **URL:** `https://seu-uuid.trycloudflare.com/webhook/iugu`
     - **Token (secret):** `a7f3c982e4b51d8760fa93c5ad0e6b2891f4d7c3e5a82b4f96d1e0a8c3b57f29`
     - **Eventos:** `invoice.status_changed` (ou similar)
   - Clique: **Ativar** ou **Salvar**

3. **Teste:**
   - Crie uma fatura de teste (R$ 1,00)
   - Marque como paga
   - Veja se aparece log no Terminal 1 (uvicorn)

### Se você JÁ registrou:

Vá em `app.iugu.com/webhooks` e verifique:
- ✅ URL está correta e acessível?
- ✅ Token bate com o que está em `.env`?
- ✅ Evento é `invoice.status_changed`?
- ✅ Webhook está **ATIVO**?

---

## 🔐 SEGURANÇA: Credenciais expostas!

⚠️ **Suas credenciais estão visíveis no `.env`:**

1. **IUGU_API_TOKEN** - vai poder acessar sua conta Iugu
   - ➡️ Vá em `app.iugu.com` → Revogue este token
   - ➡️ Gere um novo em **Administração → API Tokens**
   - ➡️ Atualize em `.env`

2. **NFSE_CERTIFICADO_SENHA** (`mega10`) - abre seu certificado digital
   - ➡️ Troque a senha via software da Autoridade Certificadora (SERPRORFB)
   - ➡️ Atualize em `.env`

**Faça isso agora, antes de usar em produção.**

---

## 📊 Fluxo após o webhook funcionar

```
1. Cliente paga fatura no Iugu (R$ X)
   ↓
2. Webhook dispara automaticamente
   ↓
3. Servidor emite NFS-e no DF
   ↓
4. PDF é gerado
   ↓
5. E-mail é enviado ao cliente com NFS-e
   ↓
6. Fatura marcada como "NFS-e emitida" no dashboard
```

---

## 📞 Se ainda tiver dúvidas

Veja o documento detalhado:
- **`docs/ANALISE_WEBHOOK_NAO_DISPAROU.md`** — Análise completa com 5 possíveis causas

Ou rode o debug:
```powershell
python scripts/debug_webhook.py 9D6CFFE2621440C3B4D50F78A317EFA0 --verbose
```

---

## ✅ Checklist final

- [ ] Rodei `debug_webhook.py` com a fatura
- [ ] Servidor webhook está rodando (Terminal 1)
- [ ] Cloudflared tunnel está ativo (Terminal 2)
- [ ] Webhook registrado em `app.iugu.com/webhooks`
- [ ] Token de webhook bate com `.env`
- [ ] CNPJ da fatura está na planilha `empresas_autorizadas.xlsx`
- [ ] `emitir_nf=Verdadeiro` para a empresa na planilha
- [ ] Rodei `debug_webhook.py` de novo e passou!
- [ ] Credenciais foram rotacionadas (token Iugu + senha A1)

**Quando tudo estiver ✅, o webhook funcionará automaticamente.**
