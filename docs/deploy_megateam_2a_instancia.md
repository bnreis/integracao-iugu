# Runbook — 2ª instância (MegaTeam) na VPS (ADR-0007)

Sobe uma **cópia isolada** do backend para a **MegaTeam**, ao lado da MegaSuporte, sem
tocar no que já roda. Cada instância tem código, venv, `.env`, contador de RPS, serviço,
subdomínio e cron **próprios**.

> ⚠️ VPS compartilhada (Asterisk/Apache/MariaDB). **Só adicionar** unit e vhost — não mexer
> em firewall, fuso nem `apt upgrade`. Rode **um bloco por vez** conferindo a saída.

| Recurso | MegaSuporte (atual) | MegaTeam (nova) |
|---|---|---|
| Diretório | `/opt/integracao-iugu` | `/opt/integracao-iugu-megateam` |
| Serviço systemd | `iugu-webhook` | `iugu-webhook-megateam` |
| Porta uvicorn (local) | 8000 | **8001** |
| Acesso (MESMO domínio) | `iugu.megasuporte.com` `/api/…` | `iugu.megasuporte.com` **`/megateam/api/…`** |
| Pasta de NFS-e/RPS | `/opt/integracao-iugu/nfse_emitidas` | `/opt/integracao-iugu-megateam/nfse_emitidas` |

> **Mesmo domínio** (decisão 2026-07-14): **sem subdomínio, sem DNS novo, sem certbot novo**.
> O Apache roteia por **caminho** `/megateam/…` para a instância da MegaTeam.

---

## 1) Clonar o código + venv (usuário `iugu`)
```bash
sudo -u iugu git clone https://github.com/bnreis/integracao-iugu.git /opt/integracao-iugu-megateam
cd /opt/integracao-iugu-megateam
sudo -u iugu python3 -m venv .venv
sudo -u iugu .venv/bin/pip install -r requirements.txt
sudo -u iugu mkdir -p nfse_emitidas certs
```

## 2) Certificado A1 da MegaTeam
Envie o `.pfx` da MegaTeam para `certs/` (do seu Windows, via scp — **não colar conteúdo**):
```powershell
& "C:\Program Files\Git\usr\bin\scp.exe" "C:\Users\bruno.reis\173485328_MEGATEAM_SERVICOS_DE_TI_LTDA_27987745000142.pfx" root@72.62.11.230:/tmp/megateam.pfx
```
Na VPS:
```bash
sudo mv /tmp/megateam.pfx /opt/integracao-iugu-megateam/certs/megateam.pfx
sudo chown iugu:iugu /opt/integracao-iugu-megateam/certs/megateam.pfx
sudo chmod 600 /opt/integracao-iugu-megateam/certs/megateam.pfx
```

## 3) `.env` próprio da MegaTeam
```bash
sudo -u iugu nano /opt/integracao-iugu-megateam/.env
```
Conteúdo (troque os valores `<...>`; os **segredos** só aqui, nunca no chat):
```ini
# --- Iugu (conta da MegaTeam) ---
IUGU_API_TOKEN=<TOKEN_IUGU_MEGATEAM>
IUGU_WEBHOOK_TOKEN=<GERAR_NOVO_TOKEN_WEBHOOK>

# --- API/login (MESMO usuário/senha da MegaSuporte p/ o seletor funcionar) ---
API_USUARIO=<mesmo_usuario_atual>
API_SENHA=<mesma_senha_atual>
API_JWT_SECRET=<gerar_proprio: python -c "import secrets;print(secrets.token_hex(32))">
CORS_ORIGINS=https://iugu.megasuporte.com

# --- NFS-e DF (prestador MegaTeam) ---
NFSE_PADRAO=abrasf204
NFSE_AMBIENTE=producao
NFSE_CNPJ_PRESTADOR=27987745000142
NFSE_RAZAO_SOCIAL_PRESTADOR=MEGATEAM SERVICOS DE TI LTDA
NFSE_INSCRICAO_MUNICIPAL=<IM_CF_DF_DA_MEGATEAM>
NFSE_SERIE_RPS=<serie_rps_megateam>
NFSE_CERTIFICADO_PATH=/opt/integracao-iugu-megateam/certs/megateam.pfx
NFSE_CERTIFICADO_SENHA=<SENHA_DO_PFX_MEGATEAM>
NFSE_OUTPUT_DIR=/opt/integracao-iugu-megateam/nfse_emitidas
# mesmo problema de TLS da ISSnet — reusa o bundle GoDaddy da 1a instancia:
NFSE_CA_BUNDLE_PATH=/opt/integracao-iugu/certs/issnet_ca_bundle.pem
```
```bash
sudo chown iugu:iugu /opt/integracao-iugu-megateam/.env
sudo chmod 600 /opt/integracao-iugu-megateam/.env
# valida credenciais/planilha/conexao (sem emitir nada):
cd /opt/integracao-iugu-megateam && sudo -u iugu .venv/bin/python scripts/test_connection.py
```

## 4) Serviço systemd (porta 8001)
`/etc/systemd/system/iugu-webhook-megateam.service`:
```ini
[Unit]
Description=Integracao Iugu MEGATEAM - Webhook + API (FastAPI/uvicorn)
After=network.target

[Service]
Type=simple
User=iugu
Group=iugu
WorkingDirectory=/opt/integracao-iugu-megateam
ExecStart=/opt/integracao-iugu-megateam/.venv/bin/uvicorn src.webhook_server:app --host 127.0.0.1 --port 8001
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now iugu-webhook-megateam
sudo systemctl status iugu-webhook-megateam --no-pager | head -5
curl -s http://127.0.0.1:8001/health
```

## 5) Apache — roteamento por caminho no vhost EXISTENTE (sem subdomínio/cert novo)
A MegaTeam entra no **mesmo domínio** via caminho `/megateam/…`. **Não** cria vhost novo —
adiciona regras de proxy ao vhost **:443** já existente da MegaSuporte (o que o certbot gerou,
ex.: `/etc/apache2/sites-available/iugu-megasuporte-le-ssl.conf`).

Dentro do `<VirtualHost *:443>` de `iugu.megasuporte.com`, **antes** das regras `/api/` atuais,
adicione (a ordem importa — o prefixo mais específico primeiro):
```apache
    # --- MegaTeam (2ª instância, porta 8001) ---
    # Cobrir TODOS os prefixos que o app usa: /auth (login) e /api, além de
    # /webhook (gatilho Iugu) e /health. Sem /megateam/auth, o login quebra.
    ProxyPass        /megateam/auth/    http://127.0.0.1:8001/auth/
    ProxyPassReverse /megateam/auth/    http://127.0.0.1:8001/auth/
    ProxyPass        /megateam/api/     http://127.0.0.1:8001/api/
    ProxyPassReverse /megateam/api/     http://127.0.0.1:8001/api/
    ProxyPass        /megateam/webhook/ http://127.0.0.1:8001/webhook/
    ProxyPassReverse /megateam/webhook/ http://127.0.0.1:8001/webhook/
    ProxyPass        /megateam/health   http://127.0.0.1:8001/health
    ProxyPassReverse /megateam/health   http://127.0.0.1:8001/health
```
> O painel web (SPA) continua sendo servido **uma vez** no root pela instância atual — o
> mesmo app serve as duas empresas (o seletor troca o prefixo). Não precisa de `web-build`
> nem `DocumentRoot` para a MegaTeam.

```bash
sudo apache2ctl configtest       # valida ANTES de aplicar
sudo systemctl reload apache2    # gracioso, não derruba o Apache
```

> 🔴 **OBRIGATÓRIO após editar o vhost — confira o destino de TODAS as linhas** (uma edição
> manual já apontou as rotas da MegaSuporte pro 8001 por engano — ver
> `docs/incidente_multiempresa_apache_2026-07.md`):
> ```bash
> sudo grep -n ProxyPass /etc/apache2/sites-available/iugu-megasuporte-le-ssl.conf
> ```
> Esperado: `/api /auth /webhook /health` → **:8000** · `/megateam/*` → **:8001**.
> E valide **pelo domínio público** (não localhost) — deve devolver dados **diferentes**:
> ```bash
> sudo -u iugu /opt/integracao-iugu/.venv/bin/python - <<'PY'
> import httpx
> from dotenv import dotenv_values
> c = dotenv_values("/opt/integracao-iugu/.env")
> for nome, px in [("MEGASUPORTE",""),("MEGATEAM","/megateam")]:
>     b=f"https://iugu.megasuporte.com{px}"
>     t=httpx.post(f"{b}/auth/login",json={"usuario":c["API_USUARIO"],"senha":c["API_SENHA"]},timeout=30).json()["access_token"]
>     d=httpx.get(f"{b}/api/dashboard",headers={"Authorization":f"Bearer {t}"},timeout=60).json()
>     print(nome, d["mes"]["valor_pago"], d["mes"]["criadas"])
> PY
> ```
> (`/health` é idêntico nas duas instâncias — **não serve** para provar roteamento.)

## 6) Gatilho (webhook) na Iugu da MegaTeam
No painel da Iugu **da conta MegaTeam**, aponte o gatilho de `invoice.status_changed` para o
**caminho `/megateam`** no mesmo domínio:
```
https://iugu.megasuporte.com/megateam/webhook/iugu?token=<IUGU_WEBHOOK_TOKEN_do_.env>
```
(confirme o caminho/param exato do webhook conforme o da MegaSuporte, só prefixando `/megateam`).

## 7) Cron de boletos recorrentes (MegaTeam)
```bash
sudo -u iugu crontab -e -u iugu
# adicione (mesmo horário do atual, apontando para a pasta da MegaTeam):
# 0 9 * * * cd /opt/integracao-iugu-megateam && .venv/bin/python scripts/run_scheduled_invoices.py --saida-json /var/log/iugu/lote_megateam_$(date +\%F).json >> /var/log/iugu/cron_megateam.log 2>&1
```

## 8) Validação final
- `curl -s https://iugu.megasuporte.com/megateam/health` → OK
- Login no painel/app com o **mesmo usuário**, selecionando **MegaTeam** → ver dados da
  MegaTeam (vazio no início).
- Importar clientes (script `importar_clientes_entre_contas.py`, dry-run primeiro).
- Emissão de teste R$1 (fatura → paga → NFS-e → e-mail), respeitando o guardrail.

## Atualizações futuras
`git pull` + restart **nas duas** instâncias:
```bash
for d in /opt/integracao-iugu /opt/integracao-iugu-megateam; do
  (cd "$d" && sudo -u iugu git pull); done
sudo systemctl restart iugu-webhook iugu-webhook-megateam
```
