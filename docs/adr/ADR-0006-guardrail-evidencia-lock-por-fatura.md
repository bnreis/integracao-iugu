# ADR-0006 — Guardrail anti-duplicata baseado em evidência + lock por fatura (cross-process)

- **Status:** Aceito (implementado e deployado em produção — commit `909ac61`, 06/06/2026)
- **Contexto da decisão:** antes de **ligar a emissão automática** de NFS-e no pagamento.
- **Relação com outros ADRs:** mitigação tática que antecipa, em arquivo, parte do que o
  **ADR-0001 (SQLite)** + **ADR-0002 (UNIQUE(invoice_id))** resolveriam de forma definitiva.
  Não substitui esses ADRs — quando o SQLite entrar, o lock/guardrail migram para o banco.

---

## Contexto

A emissão de NFS-e gera **documento fiscal**, difícil de cancelar (o Bruno classificou o
cancelamento como contraindicado). Ao ligar a **auto-emissão** (fatura paga → emite sozinha),
duas classes de risco ficam expostas:

1. **Falso "já emitida" por flag.** O guardrail antigo bloqueava/pulava a emissão com base em
   sinais **frágeis**: a flag `nf_na_criacao` da empresa, a custom_variable
   `nfse_emitida_na_criacao` da fatura e a existência de arquivos `dps_*` (artefatos de
   dry-run). Isso causava **falso-positivo** (marcar como emitida sem nota real — bug que
   apareceu no painel) e a regra de anti-duplicata por CNPJ+mês+valor estava **morta** (comparava
   `valor_cents`/`competencia`, campos que o log nunca gravou).

2. **NFS-e duplicada por concorrência.** O webhook (uvicorn) e o **cron** de boletos rodam em
   **processos distintos**. A Iugu **reentrega** webhooks (retry em 502). Sem serialização, dois
   eventos da mesma fatura podiam passar pela verificação antes de qualquer log existir e **emitir
   duas notas reais**. Agravante: o cron emitia **fora** de qualquer guardrail.

## Decisão

1. **Guardrail baseado em EVIDÊNCIA.** A única prova de que a nota existe é o log de emissão
   **real bem-sucedida** `nfse_<invoice_id>.json` com `sucesso=true` (gravado por
   `_gravar_log_nfse` apenas em sucesso). Duas regras:
   - **Regra 1 (primária):** abrir o arquivo determinístico `nfse_<invoice_id>.json` e bloquear
     se `sucesso=true`. Barra a reemissão da **mesma fatura** (retry de webhook, cron×pagamento).
   - **Regra 2 (cliente+mês — atualizada 18/06/2026):** **no máximo 1 NFS-e por CNPJ por mês de
     emissão**, INDEPENDENTE do valor/fatura. O mês casa contra `paid_at[:7]` **ou** o mês
     corrente; o CNPJ é normalizado dos dois lados (`_normalizar_doc`, tolera máscara e prepara
     CNPJ alfanumérico). Barra a **2ª nota do mesmo cliente no mês** — automática OU manual —
     incluindo fatura cancelada+recriada no mês (mesmo com valor divergente, ex.: ISS retido
     líquido×bruto). **Antes** exigia também *mesmo valor*, o que deixava passar 2 faturas de
     valores diferentes e a recriação com ISS retido. Cross-mês não é coberto aqui (de
     propósito) — usar a **marcação manual "NF-e já emitida"**.
   - Removidos os gatilhos por flag (`nf_na_criacao`, custom_variable) e por `dps_*`.

2. **Lock por `invoice_id` (cross-process).** Módulo neutro **`src/nfse_guard.py`** com um
   context manager `_lock_invoice(invoice_id)` baseado em lockfile atômico
   (`os.open(O_CREAT|O_EXCL)`), compartilhado por **webhook e cron**. Serializa o trecho
   *verificar → emitir → gravar_log* da mesma fatura.
   - **Staleness robusto:** lock considerado órfão se `idade > TTL (300s)` **ou** se o **PID**
     gravado não estiver mais vivo (`os.kill(pid, 0)`, política conservadora: na dúvida, trata
     como vivo — nunca reclama lock de processo lento). TTL de 300s cobre o pior caso da seção
     crítica (SOAP ~60s + SMTP + IO).
   - **Ocupado por outro processo vivo** → não emite; webhook devolve `acao="em_processamento"`
     (HTTP 200, sem retry).

3. **Resiliência da evidência.** Se `_gravar_log_nfse` falhar ao gravar o índice, eleva para
   `logger.error` (alerta acionável) e tenta um **fallback de índice mínimo** no mesmo caminho
   determinístico, para a Regra 1 continuar barrando reemissão. Nunca derruba a emissão (a nota
   já foi aceita).

## Alternativas consideradas

- **SQLite com `UNIQUE(invoice_id)` (ADR-0001/0002).** Solução definitiva, mas maior escopo.
  Optou-se por destravar a auto-emissão agora com arquivo+lock e migrar depois.
- **Lock só in-process (`asyncio.Lock`).** Não cobre webhook×cron (processos distintos). Descartado.
- **Confiar na numeração de RPS/contador.** O lock do contador garante números distintos, **não**
  impede duas notas da mesma fatura. Insuficiente.

## Consequências

**Positivas**
- Fecha a janela de NFS-e duplicada em reentrega da Iugu e em corrida webhook×cron.
- Elimina os falso-positivos de "já emitida" por flag; corrige a anti-duplicata que estava morta.
- Cobertura por testes offline (`tests/test_webhook_status.py`): lock ocupado/obsoleto/liberado,
  cron sob lock, e-mail com anexo — 10/10.

**Limitações / riscos residuais (aceitos)**
- **Premissa de 1 worker uvicorn + 1 cron.** Com múltiplos workers, migrar para exclusão real
  (SQLite UNIQUE / flock). Documentado em `_lock_invoice`.
- **Contador de RPS é por máquina.** Emitir de máquinas diferentes diverge os contadores e o
  ISSnet rejeita com **E010**. Regra operacional: **emitir apenas pela VPS**.
- Se a gravação do índice e o fallback falharem juntos (ex.: disco cheio), a Regra 1 fica sem
  evidência — daí o alerta `logger.error` exigir ação manual.

## Implementação

- `src/nfse_guard.py` (novo): `_LOCK_INVOICE_TTL_SEGUNDOS`, `_lock_invoice`, `_verificar_nfse_duplicada`, `_pid_vivo`.
- `src/webhook_server.py`: importa do `nfse_guard`; `processar_pagamento` envolve guardrail+emissão+e-mail no lock.
- `src/scheduled_invoices.py`: `_emitir_nfse_para_fatura` passa a usar o mesmo lock + guardrail.
- `src/nfse_df.py`: `_gravar_log_nfse` com alerta elevado + fallback de índice mínimo.

## Complemento (18/06/2026) — marcação manual "NF-e já emitida"

**Problema:** uma fatura **cancelada+recriada** depois de já ter emitido a NFS-e. A
fatura **nova** tem outro `invoice_id` → sem índice `nfse_<id>.json` → o painel a mostra
como **pendente** e, ao ser paga, o guardrail (Regra 1) não acha evidência e **emitiria
uma segunda nota** (duplicada). A Regra 2 **não** cobre o caso de forma confiável: em ISS
retido o valor cobrado é o **líquido** (≠ bruto do log) e, entre meses, não casa.

**Decisão:** ação manual, **baseada na mesma evidência**. `registrar_nfse_emitida_manual`
(`src/nfse_df.py`) grava o índice `nfse_<invoice_id>.json` com `sucesso=true` e o flag
**`marcada_manualmente=true`**, apontando opcionalmente para o número/código da nota de
origem — **sem** emitir no provedor nem enviar e-mail. A partir daí a Regra 1 **bloqueia**
a reemissão e todos os leitores (lista/detalhe/dashboard) mostram a fatura como **emitida**.

- **Endpoint:** `POST /api/nfse/{invoice_id}/marcar-emitida` (idempotente — se já há nota
  real ou marcada, apenas confirma).
- **App/painel:** botão **"Marcar NF-e como já emitida"** no detalhe da fatura (empresa que
  emite NF-e + fatura ainda sem nota).
- **Por que é seguro:** decisão explícita do operador (que sabe que a nota antiga cobre o
  serviço); reusa o mecanismo de evidência (Regra 1), sem heurística de valor/competência.
- **Limitação aceita:** "Reenviar NF-e" numa fatura marcada-manualmente envia e-mail **sem
  XML anexo** (o índice não tem `xml_enviado_path`). Não é o fluxo esperado dessa ação.

### Guardrail "1 NFS-e por cliente por mês" (18/06/2026)

A Regra 2 passou a bloquear por **CNPJ + mês**, sem olhar valor (ver Decisão acima). Vale para
**automático e manual** (todos os caminhos passam por `_verificar_nfse_duplicada`: webhook,
`/emitir`, `/emitir-manual`, `/baixa-manual`, cron).

- **Trade-off aceito:** se um cliente legitimamente precisar de **2 notas no mesmo mês**, a 2ª é
  barrada. É o comportamento desejado (cobrança recorrente = 1 nota/mês/cliente). Caso real e
  raro de 2ª nota legítima exigiria um override explícito (não implementado — manter o bloqueio).
- **Validação:** `scripts/validar_guardrail_nfse.py` exercita 9 cenários offline (mesma fatura,
  2ª fatura mesmo/diferente valor, ISS retido recriado, cliente/mês diferente, rejeição, máscara
  de CNPJ, marcação manual) — todos verdes. Rodar após mexer no guardrail.
- **Bypass residual conhecido:** `scripts/emitir_nfse_manual.py` (CLI) chama `emitir_nfse`
  direto, **fora** do lock/guardrail. Regra operacional já existente: emitir só pela VPS pelos
  fluxos do painel; o CLI é ferramenta de diagnóstico.
