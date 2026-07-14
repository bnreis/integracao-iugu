# Incidente — TLS da ISSnet derrubou todas as emissões de NFS-e (2026-07-14)

- **Status:** ✅ Resolvido (2026-07-14)
- **Severidade:** Alta — **100% das emissões de NFS-e paradas** (automáticas, manuais, baixa manual e cron).
- **Componente:** conexão TLS ao webservice da ISSnet (`df.issnetonline.com.br`).
- **Commits:** `77ef90c` (correção de código) · `b01a32f` (documentação).

---

## 1. Resumo

Entre **~30/06/2026** e **14/07/2026**, toda tentativa de emitir NFS-e falhou porque o
backend, ao abrir a conexão HTTPS com o webservice da ISSnet, **rejeitava o certificado
TLS do servidor deles**. O erro não tinha nada a ver com o cliente, o cadastro, o
certificado A1 ou o schema — era **verificação de cadeia de certificado (server-side TLS)**.

O gatilho foi a **renovação do certificado TLS da ISSnet**: a cadeia GoDaddy nova não é
ancorada por nenhum dos trust stores da VPS (nem o `certifi` do Python, nem o
`ca-certificates` do sistema operacional).

## 2. Sintoma (erro exato)

No log do serviço (`journalctl -u iugu-webhook`):

```
src.nfse_df:_emitir_abrasf204 - Falha ao enviar RPS ABRASF ao webservice
httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
  self-signed certificate in certificate chain (_ssl.c:1007)
src.webhook_server:processar_pagamento - ❌ NFS-e REJEITADA para fatura … :
  Erro ao enviar ao webservice: [SSL: CERTIFICATE_VERIFY_FAILED] …
```

No painel/app o operador via apenas "não foi possível emitir / nota rejeitada".

## 3. Impacto

- **Todas** as empresas afetadas (não só a que estava sendo testada — Star Corretora).
  No log aparece, por exemplo, a **KEMMI PHARMA LTDA** falhando pelo mesmo motivo.
- Faturas pagas no período **não geraram NFS-e**. → ver *Pendências* (seção 8).
- A falha é **terminal** por tentativa (não adianta re-tentar): re-enviar dá o mesmo erro
  enquanto a âncora de confiança não existir na VPS.

## 4. Diagnóstico (passo a passo, com evidências)

1. **Log** mostrou `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`
   ao chamar `https://df.issnetonline.com.br/webservicenfse204/nfse.asmx`.
   → Conclusão: problema de **confiança na cadeia TLS do servidor**, não de payload.

2. **Cadeia apresentada pela ISSnet** (`openssl s_client -showcerts`):
   ```
   leaf: CN = *.issnetonline.com.br
     └ GoDaddy TLS Intermediate CA DV - R1v1
        └ GoDaddy TLS Root CA - R1
           └ Go Daddy Root Certificate Authority - G2
              └ Go Daddy Class 2 Certification Authority   (autoassinado / legado)
   ```
   → Certificado **GoDaddy comercial** (não ICP-Brasil). A cadeia termina numa raiz
   **legada autoassinada** (Class 2) e passa pela raiz **nova** `GoDaddy TLS Root CA - R1`.

3. **Testar quais trust stores confiam** (`openssl s_client -CAfile …`):
   | Bundle | Versão | Resultado |
   |---|---|---|
   | `certifi` do venv | **2026.05.20** (recente!) | `verify return code: 19 (self-signed certificate in certificate chain)` |
   | SO `/etc/ssl/certs/ca-certificates.crt` | — | `verify return code: 19` |
   → **Os dois falham.** Logo, **não** era certifi desatualizado: a cadeia GoDaddy dessa
   renovação simplesmente **não tem âncora** em nenhum dos bundles (raiz nova ainda não
   entrou; raiz legada Class 2 foi removida por obsolescência).

## 5. Causa-raiz

A ISSnet renovou o **certificado TLS do servidor** para uma cadeia GoDaddy cujas raízes
não são âncoras confiáveis nos trust stores da VPS. O Python (`httpx` com `verify=True`,
que usa `certifi`) então **aborta o handshake** antes de enviar o XML. Como isso é o
transporte comum a todos os backends de emissão, **todas** as notas pararam.

> Não confundir com o **certificado A1** (`certs/*.pfx`), que é o NOSSO certificado de
> assinatura/mTLS (cliente). O problema aqui é a verificação do certificado **do servidor
> deles** (peer/server-side).

## 6. Correção aplicada

### 6.1 Código (`77ef90c`) — `verify` configurável, verificação sempre LIGADA
- **`src/config.py`**: novo campo `nfse_ca_bundle_path` (env **`NFSE_CA_BUNDLE_PATH`**).
  Vazio = usa o `certifi` padrão; setado = usa o bundle informado para **verificar** o
  servidor.
- **`src/nfse_df.py`**: helper `_verify_ssl()` aplicado nas **3** chamadas httpx à ISSnet
  (SOAP ABRASF 2.04, SOAP DPS nacional, GET ConsultarUrl). Retorna o bundle custom quando
  existe, senão `True`. **Nunca retorna `False`** — não desabilitamos verificação de
  certificado num endpoint fiscal (evita MITM).

### 6.2 Infra (VPS) — bundle = certifi + cadeia GoDaddy da ISSnet
```bash
CB=$(sudo -u iugu /opt/integracao-iugu/.venv/bin/python -m certifi)
echo | openssl s_client -connect df.issnetonline.com.br:443 -servername df.issnetonline.com.br -showcerts 2>/dev/null \
 | awk '/-----BEGIN CERTIFICATE-----/{f=1} f{print} /-----END CERTIFICATE-----/{f=0}' > /tmp/issnet_chain.pem
cat "$CB" /tmp/issnet_chain.pem | sudo tee /opt/integracao-iugu/certs/issnet_ca_bundle.pem >/dev/null
sudo chown iugu:iugu /opt/integracao-iugu/certs/issnet_ca_bundle.pem
sudo chmod 644 /opt/integracao-iugu/certs/issnet_ca_bundle.pem
# .env:
#   NFSE_CA_BUNDLE_PATH=/opt/integracao-iugu/certs/issnet_ca_bundle.pem
sudo systemctl restart iugu-webhook
```
Por que funciona: ao incluir as **CAs GoDaddy que o próprio servidor apresenta** no bundle
de verificação, o OpenSSL passa a ter uma âncora confiável e a cadeia valida. A verificação
criptográfica continua **ativa** — só ampliamos as âncoras confiáveis com CAs GoDaddy
legítimas.

## 7. Validação (o que confirmou o conserto)

```
verify c/ bundle NOVO:  Verify return code: 0 (ok)      # openssl -CAfile bundle
TLS OK - HTTP 200                                        # httpx real, do venv, com o bundle
```
E, em seguida, emissão real bem-sucedida pelo painel (Star Corretora).

## 8. Pendências / próximos passos

- 🔴 **Backfill:** faturas pagas de **~30/06 a 14/07** que deveriam ter emitido NFS-e e
  **não emitiram** (o TLS barrava). Levantar as pagas sem NFS-e no período e emitir
  manualmente pelo painel (o guardrail de **1 NFS-e por cliente/mês** evita duplicata).
- 🟡 **Vai repetir:** quando a ISSnet renovar o certificado de novo, o erro volta. O
  procedimento de refazer o bundle está na seção 6.2 (e no HANDOFF, seção 14/07/2026).

## 9. Prevenção / melhoria futura (sugestões)

- **Healthcheck de TLS**: um check periódico (cron) que faz o handshake com a ISSnet e
  alerta se `CERTIFICATE_VERIFY_FAILED` — pega a renovação **antes** de uma fatura paga
  falhar silenciosamente.
- **Alerta de emissão rejeitada**: notificar (e-mail/log agregado) quando uma emissão cair
  em `nfse_rejeitada` por erro de transporte, separando de rejeição de schema/cadastro.
- **Automatizar o rebuild do bundle** num script (`scripts/atualizar_bundle_issnet.py`) que
  reexecuta a captura + validação da seção 6.2.
