# Incidente — E232 intermitente da ISSnet, RPS queimado nas reentregas e data errada à noite (2026-10-14 a 2026-10-17)

- **Status:** ✅ Resolvido (2026-10-17) — E232 cessou do lado da ISSnet; RPS e fuso corrigidos no código.
- **Severidade:** Alta — emissões da MegaTeam falhando (automáticas pelo webhook e manuais pelo app).
- **Componente:** emissão ABRASF 2.04 (`src/nfse_df.py`), webhook (`src/webhook_server.py`), guardrail (`src/nfse_guard.py`), nova `src/tempo.py`.
- **Instância afetada:** MegaTeam (`iugu-webhook-megateam`, porta 8001).
- **Commits:** `87c0588` (correção + testes) · `828e2a7` (HANDOFF).

---

## 1. Resumo

Em 14/10 e 16/10/2026 a emissão de NFS-e da MegaTeam falhou em massa com **`E232 — Ocorreu um
erro no processamento do arquivo`** e com `ReadTimeout` (60 s). A investigação mostrou **três
coisas distintas**:

1. **E232 = instabilidade do processamento da ISSnet (causa externa).** O XML não tinha defeito:
   o mesmo payload falhou dezenas de vezes e depois foi aceito. Voltou ao normal sozinho.
2. **Bug nosso (agravante): cada reentrega do webhook queimava um nº de RPS** (contador 99 → 189
   em 3 dias). Pior: após um *timeout*, a nova tentativa saía com RPS novo, o que abre brecha
   para **NFS-e duplicada** se a ISSnet tivesse aceitado a anterior.
3. **Bug nosso (latente): data em UTC.** A VPS está em UTC; entre 21h e 24h (Brasília) a data de
   emissão do RPS era "amanhã" e a ISSnet devolvia **E016**.

## 2. Sintomas (erros exatos)

No `journalctl -u iugu-webhook-megateam`:

```
❌ NFS-e REJEITADA para fatura … : ['[E232] Ocorreu um erro no processamento do arquivo. — Correção: Procure a Prefeitura para regularização.']
❌ NFS-e REJEITADA para fatura … : Erro ao enviar ao webservice: The read operation timed out
httpx.ReadTimeout: The read operation timed out
```

Retorno da ISSnet arquivado em `nfse_emitidas/rps_<n>_retorno_*.xml`:

```xml
<MensagemRetorno><Codigo>E232</Codigo>
  <Mensagem>Ocorreu um erro no processamento do arquivo.</Mensagem>
  <Correcao>Procure a Prefeitura para regularização.</Correcao></MensagemRetorno>
```

E, à noite (15/10 00:32–00:33 UTC = 14/10 21:32 em Brasília), RPS 144–146:

```
E016 A data da emissão do RPS não poderá ser superior a data de hoje.(Numero RPS: 146)
L016 A data de competência deve estar dentro da vigência da atividade informada.
```

## 3. Impacto

- Faturas pagas **sem NFS-e** nas tentativas dos logs: KEMMI PHARMA (R$ 2.200), ABRIG (R$ 580),
  FIPECQ (R$ 3.276) e Grau Técnico (R$ 1.500, até 15/10).
- Contagem de retornos por dia (a partir de `rps_*_retorno_*.xml`): **14/10 → 56 E232 + ~11
  sem resposta/HTTP erro; 15/10 → 2 E232 + 3 E016 + 1 sucesso; 16/10 → 16 E232 + 8 sem resposta**.
  Houve E232 esporádico desde 01/09 (4×) e em 05/10 e 08/10 (1× cada).
- **~90 números de RPS consumidos sem nota** (99 → 189), todos em tentativas rejeitadas.

## 4. Diagnóstico (passo a passo, com evidências)

1. **Log do serviço** → mensagens E232 e `ReadTimeout` repetidas, sempre a mesma fatura em
   sequência (loop de re-tentativas).
2. **O XML é culpado?** Comparei o payload que falhou (RPS 99, 14/10) com o que foi aceito
   (RPS 149, 15/10) — **mesmo tomador, mesmo valor**: `diff` mostrou só nº do RPS, `Id`s e
   datas. → **não é conteúdo do XML**.
3. **É ISS retido?** Tabulando `IssRetido` × código de retorno: **59 dos E232 eram `IssRetido=2`
   (sem retenção)**. → **não é a retenção**.
4. **O endpoint está de pé?** `curl --cacert <bundle> …/nfse.asmx?wsdl` → HTTP 200 em ~150 ms
   (TLS ok com o bundle GoDaddy). → problema não é rede/TLS; é o **processamento** do
   `GerarNfse`.
5. **Por que tantas tentativas?** Nos logs de 14/10 cada "Webhook recebido" aparece **~2 ms
   depois** do fim da emissão anterior; o IP é sempre o da Iugu. Lendo o código: a emissão é
   síncrona dentro do handler (30–60 s) e `_enviar_soap_abrasf` usa `httpx` bloqueante dentro de
   função `async` → **congela o event loop**; as reentregas da Iugu ficam enfileiradas e rodam
   em sequência. *(Que a Iugu reentrega por timeout curto é **hipótese** coerente com os
   tempos, não confirmada.)*
6. **Por que o contador subiu tanto?** `_proximo_numero_rps()` incrementava **antes** de enviar
   e nunca devolvia o número, então cada tentativa falha deixava um buraco.
7. **E016?** VPS em UTC (`timedatectl` → `Etc/UTC`); o código usava `date.today()`. Às 00:33 UTC
   (21:33 em Brasília) o servidor já estava no dia seguinte.

## 5. Causa-raiz

| # | Causa | Origem |
|---|---|---|
| 1 | E232 / timeouts: processamento do `GerarNfse` instável na ISSnet (respostas de 30–60 s e erro genérico; depois normalizou para 2–5 s) | **Externa** |
| 2 | RPS consumido a cada tentativa + risco de duplicata após timeout | **Nossa** (`_proximo_numero_rps` sem reserva por fatura) |
| 3 | Event loop travado pela chamada SOAP síncrona → reentregas empilhadas | **Nossa** |
| 4 | Data de emissão/competência em UTC → E016 entre 21h e 24h (Brasília) | **Nossa** (`date.today()` + VPS em UTC) |

> O E232 **não** foi causado por nenhuma das falhas nossas; elas só o amplificavam. Não é
> possível provar que a correção do código "curou" o E232 — ele parou do lado da ISSnet.

## 6. Correção aplicada (commit `87c0588`)

- **Reserva de RPS por fatura** (`src/nfse_df.py`): `_proximo_numero_rps(invoice_id)` grava em
  `nfse_emitidas/.contador_rps.json` um campo `pendentes: {invoice_id: {numero, ts}}` (TTL 30
  dias). Nova tentativa da **mesma fatura reusa o mesmo RPS** até a nota sair;
  `_liberar_rps_pendente()` limpa a reserva após o sucesso. Tudo sob o lock entre processos já
  existente; gravação atômica (`.tmp` + `os.replace`); formato antigo do arquivo segue válido.
  **Por que é seguro:** a ISSnet recusa RPS repetido **já aceito** com `E010 RPS já informado`
  (visto em set/2026, RPS 49) — em vez de emitir nota duplicada com número novo.
- **SOAP fora do event loop**: `await asyncio.to_thread(_enviar_soap_abrasf, …)`.
- **Fuso de Brasília sem mexer na VPS** (compartilhada com o PBX): novo `src/tempo.py`
  (`hoje_br()` / `agora_br()` com `ZoneInfo("America/Sao_Paulo")`), aplicado em `nfse_df.py`
  (DataEmissao, Competência, log, `dhEmi`/`dCompet` do backend nacional), `webhook_server.py` e
  **`nfse_guard.py:272`** (mês de "hoje" do guardrail — precisa casar com o `data_emissao`
  gravado no log; sem isso, entre 21h–24h do último dia do mês o guard compararia com o mês
  seguinte e deixaria passar duplicata). Regras 1 e 2 do guardrail **inalteradas**.
- `requirements.txt`: `tzdata` (necessário no Windows; na VPS o `zoneinfo` usa o tzdata do SO).
- Testes: `tests/test_rps_fuso.py` (novo, 24 checagens), `scripts/validar_guardrail_nfse.py`
  (9/9) e `tests/test_webhook_status.py` consertado (fakes sem `competencia` desde `98c0b9b`).

## 7. Deploy e validação

- `git push` → na VPS `/opt/integracao-iugu-megateam`: `sudo -u iugu git pull --ff-only`
  (`b439410 → 87c0588`) → testes offline na VPS (24/24, contador real intacto em 189) →
  `systemctl restart iugu-webhook-megateam`.
- Domínio público: `/megateam/health` 200; `/health` 200 (8000); webhook com token inválido →
  401 na instância 8001; `ProxyPass` conferido (megateam → 8001, resto → 8000).
- **Prova em produção (17/10 00:08 UTC = 21:08 Brasília — a janela do E016):** 3 emissões pelo
  app, todas OK em 2–5 s — **ABRIG nº 63 (RPS 190)**, **KEMMI nº 64 (RPS 191)**, **Grau Técnico
  Taguatinga nº 65 (RPS 192)**; sem buracos na numeração; `pendentes` vazio ao final; e-mail
  enviado ao tomador.
- A instância **MegaSuporte** (desativada) **não** foi atualizada de propósito (`0d8ad72`).

## 8. Pendências / próximos passos

- 🔴 **Conferir duplicatas no portal da ISSnet** para ABRIG e KEMMI: tentativas de 14/10 e 16/10
  que deram *timeout* podem ter sido **aceitas pela ISSnet** sem que recebêssemos a resposta
  (o RPS dessas tentativas era diferente do 190/191 de hoje, então a ISSnet **não** barraria
  a duplicata). Se houver nota repetida, cancelar a excedente. (Não há evidência de que isso
  ocorreu; é a única forma de saber.)
- 🟡 **FIPECQ (R$ 3.276, ISS retido)** ainda não reemitida — falhou só em 16/10; deve passar.
- 🟡 **Webhook ainda é síncrono (30–60 s se a ISSnet degradar).** Responder 200 na hora e
  emitir em background muda o contrato (perde o 502 que faz a Iugu reentregar) — ficou como
  follow-up. Com RPS reservado + lock por fatura, as reentregas agora são inofensivas.
- 🟡 `api_routes.py` (dashboard/vencimento) e `scheduled_invoices.py` ainda usam `date.today()`
  (UTC) — fora do escopo; só afetam de 21h–24h.
- Se o E232 reaparecer em volume: abrir chamado ISSnet/Nota Control com horários + os
  `rps_<n>_retorno_*.xml`.

## 9. Como diagnosticar de novo (receita rápida)

Na máquina do Bruno o alias SSH é `studio-vps` (`ssh root@IP` direto não autentica).

```bash
# 1) O que a ISSnet respondeu por dia (códigos de retorno):
cd /opt/integracao-iugu-megateam/nfse_emitidas
for f in rps_*_retorno_*.xml; do d=$(echo $f | sed -E 's/.*_([0-9]{8})_.*/\1/'); \
  c=$(grep -o '<Codigo>[^<]*' $f | head -1 | sed 's/<Codigo>//'); echo "$d ${c:-OK_ou_outro}"; done | sort | uniq -c

# 2) O XML mudou entre uma falha e um sucesso? (ignora assinatura)
F='DigestValue|SignatureValue|X509Certificate'
diff <(sed 's/></>\n</g' rps_<falha>_enviado_*.xml | grep -v -E $F) \
     <(sed 's/></>\n</g' rps_<sucesso>_enviado_*.xml | grep -v -E $F)

# 3) O endpoint está de pé? (usar o bundle do .env, NFSE_CA_BUNDLE_PATH)
curl -sS -o /dev/null -w 'http=%{http_code} total=%{time_total}s\n' \
  --cacert /opt/integracao-iugu/certs/issnet_ca_bundle.pem \
  'https://df.issnetonline.com.br/webservicenfse204/nfse.asmx?wsdl'

# 4) Reentregas / reaproveitamento de RPS nos logs:
journalctl -u iugu-webhook-megateam --since '1 hour ago' --no-pager | grep -E 'RPS\]|Iniciando emiss|REJEITADA|Emissão OK'
cat /opt/integracao-iugu-megateam/nfse_emitidas/.contador_rps.json
```

**Leitura rápida dos códigos:**
`E232` = erro interno da ISSnet (se o XML é idêntico ao de uma nota aceita, é externo — aguardar/abrir chamado) ·
`E016` = data de emissão > hoje (fuso) · `E010` = RPS já informado (conferir se a nota já existe no portal; **não** reemitir) ·
`E090` = nº de RPS inválido (fora da faixa autorizada) · `ReadTimeout` = ISSnet lenta (nota pode ter sido aceita — conferir no portal antes de reemitir).

## 10. Prevenção / melhoria futura

- **Alerta de rejeição por erro de transporte/ISSnet** separado de rejeição de schema/cadastro.
- **Healthcheck periódico** do `GerarNfse` (ou do WSDL) para avisar degradação antes de uma
  fatura paga falhar.
- **Consulta por RPS** (`ConsultarNfsePorRps`) após timeout, para saber se a nota foi aceita
  antes de qualquer nova tentativa.
- Webhook assíncrono (200 imediato + emissão em background), se a degradação se repetir.
