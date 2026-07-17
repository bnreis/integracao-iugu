# Incidente — Multi-empresa: painel/app mostrava dados de UMA empresa para as duas (2026-07-17)

- **Status:** ✅ Resolvido (2026-07-17)
- **Severidade:** Alta (confusão de dados entre empresas no painel/app) — **sem gravação errada** (só leitura), mas com **webhooks da MegaSuporte roteados ao backend errado** por ~1 dia.
- **Componente:** roteamento por caminho no Apache (ADR-0007, duas instâncias no mesmo domínio).

---

## 1. Resumo

Depois de subir a **2ª instância (MegaTeam)** ao lado da MegaSuporte (mesmo domínio,
roteado por caminho — ADR-0007), o painel web **e** o app passaram a mostrar **os mesmos
dados (da MegaTeam) para as DUAS empresas**, independentemente da empresa selecionada,
navegador, Ctrl+Shift+R, WARP ligado/desligado, ou plataforma (web/APK).

**Causa-raiz:** no vhost `:443` do Apache, as rotas **originais da MegaSuporte**
(`/api/`, `/auth/`, `/webhook/`, `/health`) estavam apontando para
`http://127.0.0.1:**8001**` (backend da **MegaTeam**) em vez de `:**8000**`. Ou seja,
`/api/...` e `/megateam/api/...` caíam **no mesmo backend (8001)**. As regras novas
`/megateam/*` estavam corretas; foram as **regras antigas** que se corromperam.

Provável origem: as edições **manuais** do vhost (nano) durante a Fase 5, quando as regras
"não salvavam" e foram refeitas — nesse vai-e-vem, os `8000` das rotas originais viraram
`8001` (copiar/colar do bloco novo por cima do antigo).

## 2. Impacto

- Painel/app mostravam dados da MegaTeam para ambas as empresas (a MegaSuporte "sumia").
- **Webhooks da Iugu MegaSuporte** (`/webhook/iugu`) foram entregues ao backend 8001 (MegaTeam)
  por ~1 dia (desde ~16/07 17:45). Como o **token do webhook não bate** entre as instâncias,
  foram **rejeitados** — nada foi emitido errado, mas **pagamentos da MegaSuporte no período
  podem não ter auto-emitido NFS-e**. → Ação em *Pendências*.
- Nenhum dado corrompido/gravado errado (o problema era 100% de roteamento de leitura).

## 3. Por que demorou (red herrings)

Perdemos tempo em hipóteses erradas porque **os testes iniciais olhavam o lugar errado**:

- ✅ `curl` **localhost** no 8000 e 8001 → dados **corretos e distintos** (582 vs 56 faturas).
  Isso "provou" que os backends estavam certos e **mascarou** o problema, que era no Apache.
- ✅ `/megateam/health` respondia → mas o **/health é idêntico nas duas instâncias**, então
  não provava qual backend atendia. **Probe inútil para roteamento.**
- 🔴 Perseguimos **build web desatualizado**, **token multi-empresa**, **cache do navegador**,
  **service worker**, **Cloudflare** e **WARP** — todos descartados um a um.

**O que quebrou o caso:** um teste **ponta a ponta pelo domínio PÚBLICO** (mesmo caminho do
app: `https://iugu.megasuporte.com/api/dashboard` autenticado) mostrou que `/api` devolvia os
dados da **MegaTeam** — aí o `grep ProxyPass` no vhost revelou o `8001` nas linhas erradas.

## 4. Correção

```bash
sudo cp .../iugu-megasuporte-le-ssl.conf{,.bak-fix}
# rotas SEM "megateam" que apontavam p/ 8001 voltam p/ 8000:
sudo sed -i '/megateam/!s|http://127\.0\.0\.1:8001|http://127.0.0.1:8000|g' \
  /etc/apache2/sites-available/iugu-megasuporte-le-ssl.conf
sudo apache2ctl configtest && sudo systemctl reload apache2
```
Validação (ponta a ponta, pelo domínio público) confirmou:
`MEGASUPORTE → 11.272,60 / 16 criadas / 5 pendentes` e `MEGATEAM → 4.210,00 / 0 / 10`.

## 5. Lições aprendidas

1. **Testar o caminho PÚBLICO, não só o localhost.** Quando o cliente vê dado errado mas o
   backend "parece certo", replique o request **exatamente como o app faz**: domínio +
   HTTPS + login + prefixo. Foi o único teste que isolou o problema.
2. **`/health` idêntico entre instâncias é um probe ruim de roteamento.** Para saber qual
   backend atende, use um endpoint **que devolve dados distintos** (autenticado) ou confira o
   **PID do uvicorn**/tamanho da resposta no log.
3. **Nunca editar vhost à mão linha a linha.** Use `sed`/script e **sempre** rode
   `grep -n ProxyPass <vhost>` depois, conferindo o **destino de TODAS as linhas** (não só as
   que você adicionou). Ao inserir regras num vhost compartilhado, as **regras existentes
   também correm risco** — revalide-as.
4. **Cache/CDN/WARP são bodes expiatórios tentadores.** Confirme com `cf-cache-status` /
   logs de origem **antes** de perseguir. (Adicionamos `no-store` na API mesmo assim — é
   higiene correta, mas **não** era a causa.)
5. **Backend certo + app certo + rota errada = sintoma "tudo igual".** Quando *duas fontes*
   convergem no mesmo resultado, suspeite do **elo compartilhado** entre elas (aqui, o Apache).

## 6. Pendências decorrentes

- 🔴 **Backfill de webhooks da MegaSuporte** (~16/07 17:45 → 17/07): conferir pagamentos que
  não auto-emitiram NFS-e por causa do roteamento errado e emitir pelo painel (guardrail
  impede duplicata):
  ```bash
  sudo journalctl -u iugu-webhook-megateam --since "2026-07-16 17:40" | grep -i webhook | tail -20
  ```
- Confirmar que o **gatilho da MegaSuporte** volta a bater no 8000 (já corrigido) e que o
  **gatilho da MegaTeam** aponta para `/megateam/webhook/...` (8001).
