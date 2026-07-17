# ADR-0007 — Suporte a múltiplas empresas via DUAS INSTÂNCIAS + seletor no login

- **Status:** ✅ **Implementado em produção** (2026-07-17) — MegaSuporte (`:8000`) + MegaTeam (`:8001`), mesmo domínio via caminho `/megateam`, seletor de empresa no app/painel.
- **Atualização 2026-07-14:** por decisão do Bruno, **mesmo domínio** (`iugu.megasuporte.com`),
  sem subdomínio novo. As duas instâncias convivem atrás do **mesmo domínio, roteadas por
  CAMINHO** no Apache (`/api/…` → MegaSuporte:8000, `/megateam/api/…` → MegaTeam:8001). O
  resto do ADR (duas instâncias isoladas, núcleo fiscal intocado) permanece.
- **Contexto:** o sistema hoje é **mono-empresa** (MegaSuporte). Entra a **MegaTeam**
  (CNPJ 27.987.745/0001-42), com **conta Iugu própria** e **certificado A1 próprio**.
- **Relação:** não altera ADR-0005 (dual ABRASF/nacional) nem ADR-0006 (guardrail) —
  cada instância roda essa mesma lógica isolada, com seus próprios dados/contador de RPS.

---

## Contexto

Precisamos faturar e emitir NFS-e para **duas empresas** distintas:

| | MegaSuporte | MegaTeam |
|---|---|---|
| CNPJ | 36.342.291/0001-43 | 27.987.745/0001-42 |
| Conta Iugu | atual (token em uso) | **nova** (token próprio) |
| Certificado A1 | `certs/*.pfx` atual | `.pfx` próprio (novo) |
| Prestador (IM, série RPS, cód. município) | atual | **próprio** |

O sistema single-tenant assume UM token Iugu, UM certificado e UM prestador (via `.env`
e `settings`). Esses módulos (`iugu_client`, `webhook_server`, `nfse_df`, `config`,
`auth`, `api_routes`) são **estáveis em produção** — refatorá-los para carregar contexto
de empresa é grande e arriscado.

## Decisão

**Duas instâncias independentes do backend + seletor de empresa no app/painel.**

- Cada empresa roda **sua própria cópia** do serviço (`/opt/integracao-iugu` e, p.ex.,
  `/opt/integracao-iugu-megateam`), com **`.env` próprio**: token Iugu, gatilho/webhook,
  certificado A1, senha, dados de prestador, `NFSE_OUTPUT_DIR` (contador de RPS/evidência),
  `NFSE_CA_BUNDLE_PATH`, cron de boletos. **Zero mudança no core** — o mesmo código, dois
  `.env`.
- **Mesmo domínio** (`iugu.megasuporte.com`), roteado por **CAMINHO** no Apache: `/api/…`
  e `/webhook/…` → MegaSuporte (8000); `/megateam/api/…` e `/megateam/webhook/…` → MegaTeam
  (8001). Sem subdomínio/DNS/cert novo — só regras de proxy no vhost já existente.
- **Frontend (app + painel)** ganha um **seletor de empresa**. Como o usuário é o mesmo
  (mesmas credenciais nas duas instâncias), o app mantém um **mapa empresa → baseUrl** e,
  ao selecionar/alternar, autentica de forma transparente contra o backend daquela empresa
  e passa a usar o token dela. Alternar não exige "deslogar".

```
                    ┌─────────────────────────────┐
   App/Painel ──────┤ seletor: [MegaSuporte ▾]     │
   (mesmo login)    └───────────┬─────────────────┘
                                │ prefixo de caminho da empresa selecionada
                                ▼
                   iugu.megasuporte.com  (Apache, mesmo domínio)
              ┌─────────────────┴──────────────────┐
       /api/…, /webhook/…                 /megateam/api/…, /megateam/webhook/…
              ▼                                     ▼
   :8000 (systemd iugu-webhook)          :8001 (systemd iugu-webhook-megateam)
   .env MegaSuporte:                     .env MegaTeam:
    IUGU_API_TOKEN / cert / IM            IUGU_API_TOKEN / cert / IM  (próprios)
    NFSE_OUTPUT_DIR próprio               NFSE_OUTPUT_DIR próprio
              │                                     │
              ▼                                     ▼
        Iugu conta A                          Iugu conta B
```

### Por que NÃO o serviço multi-tenant único
Exigiria propagar "empresa ativa" por `config`/`iugu_client`/`nfse_df`/`auth`/rotas, com
credenciais e certificado por requisição — refactor amplo em código crítico e risco de
regressão no fluxo que já fatura/emite. O isolamento por processo entrega o mesmo resultado
funcional (seleção no login, dados por empresa) com risco muito menor. Se um dia o número de
empresas crescer muito, reavaliar a migração para multi-tenant.

## Consequências

**Positivas**
- Fluxo da MegaSuporte **intocado** (a 2ª instância é aditiva).
- Isolamento **forte** por natureza: dados, logs, contador de RPS, guardrail (1 nota/mês) e
  credenciais totalmente separados — sem risco de "vazar" nota de uma empresa na outra.
- Webhooks triviais: cada conta Iugu aponta o gatilho para o **caminho da sua instância** no
  mesmo domínio (`/webhook/…` vs `/megateam/webhook/…`) — nada de rotear por account_id no código.

**Limitações / riscos aceitos**
- **Infra duplicada** na VPS (2º systemd + vhost + cron + certificado). A VPS é compartilhada
  (Asterisk/Apache/MariaDB) — seguir o runbook (`docs/deploy_vps.md`) sem mexer em firewall/
  fuso/`apt upgrade`; só **adicionar** unit e vhost.
- Atualização de código passa a exigir `git pull`+restart **nas duas** instâncias.
- Painel **web** e **app nativo**: o seletor troca o **prefixo de caminho** (`""` vs
  `/megateam`) na mesma origem — sem CORS extra, sem trocar de domínio. Cada empresa tem seu
  token (mesmo login nas duas), então alternar é só trocar prefixo + token.

## Plano em fases

1. **Infra MegaTeam (VPS):** clonar o serviço, `.env` próprio (token Iugu + certificado A1 +
   prestador MegaTeam + `NFSE_OUTPUT_DIR` próprio + `NFSE_CA_BUNDLE_PATH`), systemd unit,
   regras de proxy por caminho (`/megateam/…` → :8001) no vhost já existente (sem DNS/cert),
   cron. Validar com `scripts/test_connection.py`.
2. **Importar clientes** MegaSuporte → Iugu MegaTeam (`scripts/importar_clientes_entre_contas.py`,
   idempotente, `--dry-run` primeiro).
3. **Seletor de empresa** no app/painel (login mantém, alterna baseUrl+token por empresa).
4. **Emissão de teste** ponta a ponta na MegaTeam (fatura R$1 → paga → NFS-e → e-mail),
   respeitando o guardrail.

## ⚠️ Armadilha crítica do roteamento (postmortem 2026-07-17)

Ao adicionar as regras `/megateam/*` no vhost `:443` **à mão (nano)**, as rotas
**originais da MegaSuporte** (`/api/`, `/auth/`, `/webhook/`, `/health`) foram corrompidas
para `127.0.0.1:**8001**` (MegaTeam) em vez de `:**8000**`. Resultado: as duas empresas
mostravam os dados da MegaTeam por ~1 dia (e os webhooks da MegaSuporte iam pro backend
errado). **Detalhes: `docs/incidente_multiempresa_apache_2026-07.md`.**

**Regra de ouro ao mexer no vhost:** depois de QUALQUER edição, rode
`sudo grep -n ProxyPass <vhost>` e confira o **destino de TODAS as linhas** —
`/api /auth /webhook /health` → **8000** (MegaSuporte); `/megateam/*` → **8001** (MegaTeam).
E valide **pelo domínio público** (não só localhost): `curl` autenticado em
`https://iugu.megasuporte.com/api/dashboard` vs `.../megateam/api/dashboard` deve devolver
**dados diferentes**. `/health` é idêntico nas duas instâncias — **não serve** para provar
roteamento.

## Inputs necessários do Bruno (de forma segura — NUNCA colar no chat)
- **Token da API Iugu da MegaTeam** (conta nova). Vai só no `.env` da 2ª instância.
- **Certificado A1 `.pfx` da MegaTeam** (arquivo em `certs/`) + **senha** (só no `.env`).
- **Dados de prestador MegaTeam:** Inscrição Municipal (CF/DF), série RPS, código do
  município, alíquota/código de serviço padrão (se diferentes da MegaSuporte).
- (Subdomínio dispensado — mesmo domínio via caminho `/megateam`.)
