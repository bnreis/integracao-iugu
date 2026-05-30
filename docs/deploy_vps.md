# Deploy na VPS Hostinger — Runbook (Fase 3)

> Runbook vivo. Executamos **um bloco por vez**: você cola no SSH e me devolve a saída; eu confirmo ou corrijo antes do próximo passo. Os comandos rodam **na VPS**, não na sua máquina Windows.

> ⚠️ **ATENÇÃO — VPS compartilhada com produção.** Esta máquina já roda **Asterisk (PBX/telefonia)**, **Apache+PHP 7.4** (porta 80) e **MariaDB**. Por isso o plano foi adaptado: **não** mexemos no firewall (quebraria SIP/RTP do Asterisk), **não** mudamos o fuso do sistema (afetaria CDR), **não** fazemos `apt upgrade` geral, e usamos o **Apache existente como proxy reverso** em vez de instalar nginx.

## Dados deste deploy

| Item | Valor |
|---|---|
| IP da VPS | `72.62.11.230` |
| Acesso SSH | `ssh root@72.62.11.230` |
| Subdomínio | `iugu.megasuporte.com` |
| Pasta do projeto | `/opt/integracao-iugu` |
| Usuário do serviço | `iugu` (criado na Fase 1, não-root) |
| Proxy reverso | **Apache existente** (porta 80/443) — vhost dedicado, sem nginx |
| Roteamento | `/` → painel web · `/api/*` e `/webhook/*` → uvicorn 127.0.0.1:8000 |

---

## Fase 0 — DNS (faça AGORA, antes de tudo)

A propagação leva minutos a horas, então criamos o registro logo no começo para o certificado HTTPS (Fase 4) já encontrar tudo no ar.

No painel onde o DNS de `megasuporte.com` é gerenciado (provavelmente o **hPanel da Hostinger → Domínios → Zona DNS**), crie:

| Tipo | Nome | Aponta para | TTL |
|---|---|---|---|
| A | `iugu` | `72.62.11.230` | padrão |

Depois confirme a propagação (pode rodar no seu PowerShell):

```powershell
nslookup iugu.megasuporte.com
```

Quando aparecer `72.62.11.230` na resposta, o DNS está pronto.

---

## Fase 1 — Base do servidor (adaptada para VPS com produção)

**▶ COMECE AQUI.** Conecte no SSH (`ssh root@72.62.11.230`) e cole bloco a bloco.

> Nada aqui derruba Asterisk/Apache/MariaDB: só atualizamos o índice de pacotes, criamos um usuário e instalamos pacotes novos. **Sem** `apt upgrade`, **sem** mexer em fuso, **sem** firewall.

### 1.1 — Atualizar só o índice de pacotes (seguro)

Refresca a lista de pacotes disponíveis; **não** altera nada instalado.

```bash
apt update
```

### 1.2 — Criar o usuário de serviço (não-root)

A aplicação roda como `iugu`, isolada do resto.

```bash
adduser --system --group --home /opt/integracao-iugu --shell /bin/bash iugu
```

### 1.3 — Instalar apenas os pacotes que precisamos

Note: **sem nginx** (usaremos o Apache existente). `python3-certbot-apache` é o plugin do certbot para o Apache. Os `*-dev` cobrem a compilação de `lxml`/`cryptography` caso não haja wheel pronto.

```bash
apt install -y python3-venv python3-pip build-essential \
  libxml2-dev libxslt1-dev libssl-dev libffi-dev \
  git certbot python3-certbot-apache

python3 --version   # confirme: 3.10 ou superior
```

**Pare aqui e me mande a saída dos blocos 1.1 e 1.3.**

---

## Fase 2 — Levar o código para a VPS

O projeto **não é um repositório git** hoje. Há dois caminhos — escolha um (eu detalho o escolhido):

- **Opção A (recomendada): repositório Git privado.** Melhor para atualizações futuras (`git pull` e pronto). Exige uma conta GitHub/GitLab e uma chave de deploy. O `.gitignore` do projeto já protege `.env` e `certs/`.
- **Opção B: enviar um .zip via scp.** Sem git: você zipa só os arquivos necessários no Windows, envia e descompacta. Mais rápido de começar, porém atualizações futuras são manuais.

> Em **qualquer** opção: `.env` e `certs/*.pfx` **não** vão junto. O `.env` será criado **novo** na VPS (momento de rotacionar as credenciais) e o `.pfx` é transferido à parte com permissão restrita.

### 2.1 — (após o código estar em `/opt/integracao-iugu`) criar o ambiente Python

```bash
cd /opt/integracao-iugu
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 2.2 — Transferir o certificado A1 (.pfx)

Do seu **PowerShell** (na sua máquina), enviando o `.pfx`:

```powershell
scp "C:\Users\bruno.reis\.claude\Workspace\Integração Iugo\certs\SEU_CERTIFICADO.pfx" root@72.62.11.230:/opt/integracao-iugu/certs/
```

Depois, na VPS, restringir a permissão:

```bash
mkdir -p /opt/integracao-iugu/certs
chmod 600 /opt/integracao-iugu/certs/*.pfx
```

### 2.3 — Criar o `.env` novo (com credenciais ROTACIONADAS)

Na VPS:

```bash
nano /opt/integracao-iugu/.env
```

Cole o modelo abaixo e preencha **com valores novos** (não reaproveite os que vazaram no chat). Eu te ajudo campo a campo quando chegarmos aqui.

```dotenv
# --- Iugu (TOKEN NOVO, rotacionado no painel) ---
IUGU_API_TOKEN=
IUGU_WEBHOOK_TOKEN=

# --- API de gestão / app (valores FORTES) ---
API_USUARIO=admin
API_SENHA=
API_JWT_SECRET=

# --- NFS-e DF ---
NFSE_INSCRICAO_MUNICIPAL=
NFSE_CNPJ_PRESTADOR=
NFSE_RAZAO_SOCIAL_PRESTADOR=MEGASUPORTE SERVICOS DE TI LTDA
NFSE_CERTIFICADO_PATH=/opt/integracao-iugu/certs/SEU_CERTIFICADO.pfx
NFSE_CERTIFICADO_SENHA=
NFSE_AMBIENTE=homologacao

# --- SMTP (envio da NFS-e ao tomador) ---
SMTP_HOST=
SMTP_USUARIO=
SMTP_SENHA=
SMTP_REMETENTE_EMAIL=
```

Sugestão para gerar segredos fortes (rode na VPS e cole o resultado nos campos `API_JWT_SECRET` / `API_SENHA`):

```bash
openssl rand -hex 32
```

Por fim, proteger o arquivo e dar a posse ao usuário do serviço:

```bash
chmod 600 /opt/integracao-iugu/.env
chown -R iugu:iugu /opt/integracao-iugu
```

### 2.4 — Validar conexões

Agora a VPS alcança a internet real (diferente do sandbox local):

```bash
sudo -u iugu /opt/integracao-iugu/.venv/bin/python scripts/test_connection.py
```

Esperado: ✅ Planilha e ✅ Iugu. NFS-e pode dar ⚠️ até a IM ser liberada em produção (bloqueio conhecido do Nota Control).

---

## Fase 3 — Serviço systemd (a detalhar quando chegarmos)

Arquivo `/etc/systemd/system/iugu-webhook.service`:

```ini
[Unit]
Description=Integracao Iugu - Webhook + API (FastAPI/uvicorn)
After=network.target

[Service]
Type=simple
User=iugu
Group=iugu
WorkingDirectory=/opt/integracao-iugu
ExecStart=/opt/integracao-iugu/.venv/bin/uvicorn src.webhook_server:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now iugu-webhook
systemctl status iugu-webhook
curl -s http://127.0.0.1:8000/health
```

---

## Fase 4 — Apache (vhost) + HTTPS (a detalhar)

Usamos o **Apache já instalado** como proxy reverso. Adicionamos um vhost dedicado a `iugu.megasuporte.com` — os sites existentes (com seus próprios ServerName) não são afetados. A porta 443 está livre.

`/etc/apache2/sites-available/iugu-megasuporte.conf`:

```apache
<VirtualHost *:80>
    ServerName iugu.megasuporte.com

    DocumentRoot /opt/integracao-iugu/web-build

    ProxyPreserveHost On
    ProxyPass        /api/     http://127.0.0.1:8000/api/
    ProxyPassReverse /api/     http://127.0.0.1:8000/api/
    ProxyPass        /webhook/ http://127.0.0.1:8000/webhook/
    ProxyPassReverse /webhook/ http://127.0.0.1:8000/webhook/
    ProxyPass        /health   http://127.0.0.1:8000/health
    ProxyPassReverse /health   http://127.0.0.1:8000/health

    <Directory /opt/integracao-iugu/web-build>
        Require all granted
        FallbackResource /index.html   # SPA do painel web (Fase 7)
    </Directory>

    ErrorLog  ${APACHE_LOG_DIR}/iugu_error.log
    CustomLog ${APACHE_LOG_DIR}/iugu_access.log combined
</VirtualHost>
```

```bash
# placeholder do painel (a build real vem na Fase 7)
mkdir -p /opt/integracao-iugu/web-build
echo "ok" > /opt/integracao-iugu/web-build/index.html
chown -R iugu:iugu /opt/integracao-iugu/web-build

# módulos necessários (proxy + ssl); reload é gracioso, não derruba o Apache
a2enmod proxy proxy_http ssl
a2ensite iugu-megasuporte
apache2ctl configtest          # valida ANTES de aplicar
apache2ctl -S                  # confere que iugu NÃO virou o vhost default
systemctl reload apache2

# HTTPS — o plugin cria o vhost :443 e o redirect 80→443
certbot --apache -d iugu.megasuporte.com
```

> ⚠️ Antes do `reload`, o `configtest` tem que dizer `Syntax OK`. Se acusar erro, **não** recarregue — me mande a saída.

---

## Fase 5 — Virar a chave do webhook na Iugu (a detalhar)

No painel Iugu, atualizar o gatilho `invoice.status_changed` para:

```
https://iugu.megasuporte.com/webhook/iugu?token=NOVO_WEBHOOK_TOKEN
```

Teste: `POST https://iugu.megasuporte.com/processar/{invoice_id}`.

---

## Fase 6 — Boletos recorrentes via cron (a detalhar)

Base já documentada em `docs/scheduling.md` (seção VPS). Resumo:

Como o **sistema fica em UTC** (não mexemos no fuso, por causa do Asterisk/CDR), usamos `CRON_TZ` para o nosso job rodar às 09:00 de Brasília sem afetar o resto:

```bash
mkdir -p /var/log/iugu && chown iugu:iugu /var/log/iugu
crontab -u iugu -e
# CRON_TZ=America/Sao_Paulo
# 0 9 * * * cd /opt/integracao-iugu && .venv/bin/python scripts/run_scheduled_invoices.py --saida-json /var/log/iugu/lote_$(date +\%F).json >> /var/log/iugu/cron.log 2>&1
```

---

## Fase 7 — Celular: app + painel web (a detalhar)

- **APK:** apontar a URL base da API do app (`mobile/src/services/`) para `https://iugu.megasuporte.com` e rebuildar via EAS.
- **Painel web:** `npx expo export --platform web` → copiar saída para `/opt/integracao-iugu/web-build`. **A verificar:** compatibilidade das libs RN do app com web.

---

## Fase 8 — Operação e segurança (a detalhar)

- Renovação automática do certbot (timer já vem ativo) — testar com `certbot renew --dry-run`
- Backup diário de `empresas_autorizadas.xlsx` e `nfse_emitidas/`
- Alerta de falha no cron por e-mail (snippet em `docs/scheduling.md`)

---

## Pendências conhecidas (não bloqueiam o deploy)

- **IM não liberada em produção no Nota Control** → emissão real de NFS-e dá HTTP 404 até abrir chamado em `suporte.df@notacontrol.com.br`. Webhook, boletos e homologação funcionam.
- **Rotação de credenciais** → fazer na Fase 2.3 (token Iugu, webhook token, e idealmente a senha do A1).
