# Runbook — Primeira emissão real de NFS-e (ABRASF 2.04 / ISSnet DF)

> **Quando usar:** quando um cliente pagar de verdade uma fatura e quisermos emitir a primeira NFS-e real em produção, de forma **controlada e manual** (sem deixar o webhook disparar de primeira).
> **Princípio fiscal:** **não emitir nota de teste para cancelar depois** (cancelamento de NFS-e é contraindicado). Validamos o XML/PDF em dry-run; o envio real só acontece sobre uma fatura **legitimamente paga**.
> **Pré-requisito de código:** backend ABRASF 2.04 pronto (`NFSE_PADRAO=abrasf204`), validado contra o XSD. Homologação self-service do ISSnet está **fora do ar** (transição) — por isso validamos por dry-run + a primeira emissão real é o teste de produção.

---

## Etapa 0 — Solicitar a numeração de RPS no portal (FAZER JÁ — ~48h)

**Sem a faixa de RPS aprovada, qualquer envio real é rejeitado.** Abra já para o relógio de 48h correr:

1. Acesse `https://df.issnetonline.com.br/online/Login/Login.aspx` e faça login (CPF + senha do responsável).
2. Menu **Solicitação de Documentos Fiscais → Solicitação**.
3. Solicite a numeração de **RPS série 3** (a série que o Nota Control nos habilitou).
4. Após aprovação (~48h), volte em **Solicitação de Documentos Fiscais → Consultar** e **anote a faixa liberada** (número inicial e final disponíveis).

### Alinhar o contador local com a faixa aprovada
Nosso contador fica em `nfse_emitidas/.contador_rps.json` e o próximo RPS = último + 1.
- Se a faixa aprovada **começa em 1** → não precisa fazer nada (o contador já começa em 1).
- Se começa em outro número **N** → editar o arquivo para que o último valor seja **N-1** (assim o próximo gerado é N). Ex.: faixa a partir de 1000 → gravar `{"ultimo_numero": 999}`. Se o arquivo não existir, criar com esse conteúdo.

---

## Etapa 1 — Escolher a fatura real paga

Pegue o `invoice_id` de uma fatura **realmente paga** na Iugu (de um cliente autorizado, `emitir_nf=True`). Pelo painel, ou:
```powershell
cd "C:\Users\bruno.reis\.claude\Workspace\Integração Iugo"
curl.exe -u "SEU_TOKEN_IUGU:" "https://api.iugu.com/v1/invoices?limit=5&status_filter=paid"
```

## Etapa 2 — Dry-run SOBRE A FATURA REAL (gera XML+PDF, NÃO envia)

Valida que os dados reais montam um RPS/PDF corretos **antes** de enviar:
```powershell
$env:NFSE_PADRAO="abrasf204"; $env:NFSE_AMBIENTE="producao"; $env:NFSE_DRY_RUN="true"
.\.venv\Scripts\python.exe scripts\emitir_nfse_manual.py <invoice_id>
```
- Abra o XML e o PDF gerados em `nfse_emitidas\` e confira tomador, valor, item (01.07), alíquota (2%), série (3).
- Se algo estiver errado nos dados → ajustamos antes de qualquer envio.

## Etapa 3 — Envio REAL (produção)

Só depois do dry-run conferido e da faixa de RPS aprovada:
```powershell
$env:NFSE_PADRAO="abrasf204"; $env:NFSE_AMBIENTE="producao"; $env:NFSE_DRY_RUN="false"
.\.venv\Scripts\python.exe scripts\emitir_nfse_manual.py <invoice_id>
```

## Etapa 4 — Ler a resposta da Receita/ISSnet

O resultado é impresso e arquivado em `nfse_emitidas\`:
```powershell
Get-ChildItem nfse_emitidas\ -Filter "*retorno*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content
```

| Resposta | Significa | Ação |
|---|---|---|
| `sucesso: true` + `numero_nfse` + `codigo_verificacao` | 🎉 NFS-e emitida de verdade | Conferir o PDF (agora com número real); guardar XML+PDF |
| Rejeição com `Codigo`/`Mensagem`/`Correcao` (E-xxx) | XML chegou, falta ajustar 1 detalhe | Colar os códigos → ajuste de constante/campo (namespace, SOAPAction, CDATA, versaoDados — tudo centralizado) |
| HTTP 404/500 / erro de certificado | Endpoint/cert/conectividade | Colar o erro → diagnóstico |
| "Prestador não habilitado" / faixa de RPS | Cadastro/numeração | Conferir Etapa 0 (faixa de RPS aprovada?) |

> Os pontos do ISSnet que só se confirmam no envio real (namespace de serviço, SOAPAction com/sem aspas, `nfseCabecMsg/nfseDadosMsg` aninhado vs CDATA, `versaoDados`) estão como **constantes no topo da seção ABRASF de `src/nfse_df.py`** — qualquer ajuste é "1 linha".

## Etapa 5 — Habilitar emissão automática (depois do 1º sucesso)

Confirmada a primeira emissão real, ligar o automático no `.env` da **VPS** e reiniciar:
```
NFSE_PADRAO=abrasf204
NFSE_AMBIENTE=producao
NFSE_DRY_RUN=false
```
```bash
# VPS
systemctl restart iugu-webhook
```
A partir daí, toda fatura paga de empresa com `emitir_nf=True` emite NFS-e automaticamente pelo webhook (que também envia o PDF+XML por e-mail ao tomador).

---

## Observações
- **Não cancelar** NFS-e como "teste". A validação prévia é o dry-run (Etapa 2).
- A emissão **manual** (`emitir_nfse_manual.py`) **não dispara o e-mail** ao tomador — o e-mail só é enviado no fluxo do **webhook** (Etapa 5). Para a 1ª emissão controlada, isso é proposital (sem efeitos colaterais).
- Pendência conhecida: bug latente no `src/pdf_nfse.py` sinalizado pela revisão — não impede a geração do PDF; tratar em sessão própria se necessário.
- Quando o Padrão Nacional (DPS) entrar em vigor (30/06/2026), a virada é trocar `NFSE_PADRAO=nacional` — sem reescrever nada (ADR-0005).
</content>
