# Rascunho de e-mail — Validação do XML do RPS (ABRASF 2.04) com o Nota Control

> **Objetivo:** enviar 1 RPS de exemplo (assinado) para o canal técnico do Nota Control validar, e
> confirmar os pontos do ISSnet DF que só se fecham com o servidor real (namespace, SOAPAction,
> CDATA, versaoDados, faixa de numeração). É o passo 2 da escada de testes da Fase 2.
> **Status:** rascunho — anexar o XML gerado e revisar a assinatura antes de enviar.
> **Anexo:** o RPS assinado mais recente em `nfse_emitidas/` (ver comando abaixo do e-mail).

---

**Para:** integracao.df@notacontrol.com.br
*(cópia: suporte.df@notacontrol.com.br · tel. (67) 3041-2075)*

**Assunto:** Validação de XML — RPS ABRASF 2.04 (GerarNfse) — MEGASUPORTE (CNPJ 36.342.291/0001-43)

---

Prezados, bom dia.

Sou responsável pela integração de NFS-e da **MEGASUPORTE SERVIÇOS DE TI LTDA** (CNPJ **36.342.291/0001-43**, IM/CF-DF **0796481500161**), recentemente **habilitada em produção** por vocês. Estamos integrando a emissão automática via webservice **ABRASF 2.04** (`df.issnetonline.com.br/webservicenfse204/nfse.asmx`), usando a operação **`GerarNfse`** (1 RPS → 1 NFS-e), **RPS série 3**.

Segue **anexo um XML de RPS de exemplo, já assinado** (certificado A1 ICP-Brasil), que valida sem erros contra o schema oficial ABRASF 2.04. **Gostaríamos que validassem este XML** e nos confirmassem os seguintes pontos da implantação de vocês no DF, para evitarmos rejeições no primeiro envio:

1. **Namespace** esperado nas mensagens (`nfseDadosMsg`/`nfseCabecMsg`) — usamos `http://www.abrasf.org.br/nfse.xsd` para os dados e `http://nfse.abrasf.org.br` para o serviço. Está correto para o ISSnet DF?
2. **SOAPAction** exato da operação `GerarNfse` (com ou sem aspas; formato completo).
3. O conteúdo de `nfseCabecMsg`/`nfseDadosMsg` deve ir como **XML aninhado** (como no anexo) ou **encapsulado em CDATA**?
4. Valor esperado de **`versaoDados`** no cabeçalho (estamos usando `2.04`).
5. **Numeração do RPS (série 3):** qual a **faixa liberada** para nós e o **número inicial** que devemos usar? (Entendemos que se solicita no portal em "Solicitação de Documentos Fiscais" — confirmam o procedimento?)
6. Confirmam a consistência entre **`ItemListaServico` 01.07** e **`CodigoTributacaoMunicipio` 1071** (alíquota 2%) para os nossos serviços de TI?

Adicionalmente: nossa integração também está pronta para o **Padrão Nacional (DPS v1.01)**. Como ele se torna obrigatório em **30/06/2026**, há a possibilidade de já emitirmos por ele em produção (evitando a transição pelo ABRASF 2.04), ou devemos permanecer no ABRASF 2.04 até a virada?

Ficamos à disposição.

Atenciosamente,
**Bruno Reis**
MEGASUPORTE SERVIÇOS DE TI LTDA
[telefone] · [e-mail]

---

## Como gerar o XML para anexar (na máquina do Bruno)

```powershell
cd "C:\Users\bruno.reis\.claude\Workspace\Integração Iugo"
# valida e gera o RPS assinado (Signature reposicionada dentro de <Rps>)
.\.venv\Scripts\python.exe scripts\validar_rps_xsd.py --com-assinatura
# localiza o XML assinado mais recente para anexar:
Get-ChildItem nfse_emitidas\ | Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name, LastWriteTime
```
