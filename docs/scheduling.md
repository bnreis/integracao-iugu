# Agendamento — Geração diária de boletos recorrentes

Este documento explica como configurar o script `run_scheduled_invoices.py` para rodar automaticamente uma vez por dia.

---

## Windows Task Scheduler (ambiente local atual)

### 1. Criar script .bat para facilitar o agendamento

Crie o arquivo `scripts\run_scheduled_invoices.bat` no projeto:

```batch
@echo off
cd /d "C:\Users\bruno.reis\.claude\Workspace\Integração Iugo"
call .venv\Scripts\activate.bat
python scripts\run_scheduled_invoices.py --saida-json logs\lote_%date:~-4,4%-%date:~-7,2%-%date:~-10,2%.json
```

Crie a pasta `logs/` no projeto:

```powershell
mkdir logs
```

### 2. Abrir Task Scheduler

1. Pressione **Win + R**, digite `taskschd.msc` e Enter
2. Painel direito → **Create Task** (Criar Tarefa)

### 3. Configurar a tarefa

**Aba "General" (Geral):**
- **Name**: `Iugu - Geração diária de boletos`
- **Description**: `Gera boletos recorrentes na Iugu para clientes cadastrados na planilha`
- Marcar: **Run whether user is logged on or not** (Executar mesmo com usuário deslogado)
- Marcar: **Run with highest privileges** (opcional, nem sempre necessário)

**Aba "Triggers" (Acionadores):**
- Clica **New...** (Novo)
- Begin: **On a schedule**
- Settings: **Daily**
- Start: **09:00** (ou horário de sua preferência — recomendo manhã cedo)
- Recur every: **1 days**
- Marcar: **Enabled**
- OK

**Aba "Actions" (Ações):**
- Clica **New...**
- Action: **Start a program**
- Program/script: `C:\Users\bruno.reis\.claude\Workspace\Integração Iugo\scripts\run_scheduled_invoices.bat`
- Start in: `C:\Users\bruno.reis\.claude\Workspace\Integração Iugo`
- OK

**Aba "Conditions" (Condições):**
- Desmarcar **Start the task only if the computer is on AC power** (para rodar mesmo no notebook na bateria)

**Aba "Settings" (Configurações):**
- Marcar: **Allow task to be run on demand**
- Marcar: **If the task fails, restart every: 1 hour** (tenta 3 vezes)
- **If the task is already running**: Do not start a new instance

### 4. Testar

Na lista de tarefas, clica com direito na sua → **Run** (Executar)
Vê o log em `logs/lote_YYYY-MM-DD.json`.

---

## Rodar manualmente (fora do agendamento)

Para criar os boletos do dia a qualquer hora (ex: esqueci e quero rodar agora):

```powershell
cd "C:\Users\bruno.reis\.claude\Workspace\Integração Iugo"
.\.venv\Scripts\Activate.ps1
python scripts\run_scheduled_invoices.py
```

Para **testar** sem criar boletos de verdade:

```powershell
python scripts\run_scheduled_invoices.py --dry-run
```

Para reprocessar um dia específico (**CUIDADO**: pode gerar boletos duplicados se já rodou):

```powershell
python scripts\run_scheduled_invoices.py --data 2026-04-10
```

---

## VPS Hostinger (Fase 3)

Quando migrar para a VPS, usar `cron` em vez de Task Scheduler.

Editar o crontab:

```bash
crontab -e
```

Adicionar:

```cron
# Iugu - Geração diária de boletos (09:00 todo dia, horário local da VPS)
0 9 * * * cd /opt/integracao-iugu && /opt/integracao-iugu/.venv/bin/python scripts/run_scheduled_invoices.py --saida-json /var/log/iugu/lote_$(date +\%Y-\%m-\%d).json >> /var/log/iugu/cron.log 2>&1
```

Não esqueça de criar o diretório de logs:

```bash
sudo mkdir -p /var/log/iugu
sudo chown $USER:$USER /var/log/iugu
```

Para verificar se o cron está rodando:

```bash
journalctl -u cron -f
```

---

## Recebendo alertas de falha

O script retorna **exit code 2** quando pelo menos um boleto falha. Opções:

**Windows Task Scheduler:** Abra o Event Viewer → Windows Logs → Application, filtre por `TaskScheduler`. Tarefas que falham geram evento.

**VPS / cron:** use um script wrapper que envia e-mail em caso de erro, por exemplo:

```bash
python scripts/run_scheduled_invoices.py --saida-json /var/log/iugu/$(date +\%F).json
if [ $? -ne 0 ]; then
  echo "Falhas no lote diário. Ver log." | mail -s "[IUGU] Falha no lote" bruno.reis@grupontsec.com
fi
```

---

## Auditoria — onde olhar os logs

**Por execução:** `logs/lote_YYYY-MM-DD.json` contém detalhes completos (sucessos, falhas, URLs dos boletos criados).

**Agregado diário:** Cada linha do log tem timestamp, nível (INFO/WARNING/ERROR) e contexto.

**Na Iugu:** Cada boleto criado tem `custom_variables`:
- `origem`: `integracao_iugu_nfse_df`
- `tipo`: `boleto_recorrente`
- `data_referencia`: data do lote
- `cnpj_tomador`: CNPJ da empresa

Isso permite filtrar no painel Iugu todas as faturas geradas por este sistema.

---

## Troubleshooting

| Sintoma | Possível causa | Solução |
|---------|----------------|---------|
| Task Scheduler roda mas nada acontece | Script .bat com erro de caminho | Rode o .bat manualmente pelo PowerShell e veja o erro |
| Boletos duplicados | `--data` foi usado em dia já processado | Cancelar duplicados manualmente na Iugu; depois só usar `--data` com cautela |
| "CNPJ inválido" nos logs | Planilha com CNPJ incompleto ou CPF | Verifique a linha em questão; CPF é ignorado automaticamente |
| Nenhuma empresa elegível hoje | `dia_criacao_fatura` não bate | Confirme na planilha; lembre-se que dia 31 vira último dia do mês |
| Falha 401 na API Iugu | `IUGU_API_TOKEN` inválido | Veja `scripts/test_connection.py` |
