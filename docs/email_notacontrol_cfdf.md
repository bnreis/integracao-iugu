# Rascunho de e-mail — Liberação/validação do CF/DF (Fase 2)

> **Objetivo:** destravar a emissão de NFS-e em produção/homologação confirmando se o CF/DF
> `0796481500161` da MEGASUPORTE está correto e ativo no webservice do Padrão Nacional (ISSnet/Nota Control).
> **Contexto técnico:** ver `docs/relatorio-integracao-nfse-df.md` (o DF usa CF/DF no lugar da IM tradicional;
> o valor entra no campo único `<IM>` do prestador — não há campo separado). Diagnóstico: o erro não é de
> schema (E160 resolvido) nem de mapeamento de código — é cadastral/valor.
> **Status:** rascunho pronto para envio. Revisar assinatura antes de mandar.

---

**Para:** suporte.df@notacontrol.com.br
*(alternativo, se voltar: suporte@notaeletronica.com.br — tel. (67) 3041-2075 · chat https://www.notaeletronica.com.br/painel/)*

**Assunto:** Liberação/validação de CF/DF para emissão de NFS-e – Padrão Nacional – MEGASUPORTE (CNPJ 36.342.291/0001-43)

---

Prezados, bom dia.

Somos a **MEGASUPORTE SERVIÇOS DE TI LTDA** (CNPJ **36.342.291/0001-43**, Brasília/DF, Simples Nacional ME/EPP) e estamos integrando a emissão automática de NFS-e pelo **webservice do Padrão Nacional (DPS v1.01)** do ISSnet/Nota Control para o Distrito Federal.

O XML da DPS já passa na validação de schema (o erro E160 foi resolvido), mas estamos travados em uma questão **cadastral** relacionada ao **CF/DF (Cadastro Fiscal do Distrito Federal)** informado no campo `<IM>` do prestador. O valor que utilizamos é **0796481500161**.

Os sintomas atuais são:

1. **Homologação** — retorna **E043: "Inscrição Municipal do prestador do serviço não encontrada na base de dados do município."**
   Endpoint: `https://nfse.issnetonline.com.br/wsnfsenacional/homologacao/nfse.asmx`
2. **Produção** — a chamada retorna **HTTP 404**.
   Endpoint: `https://nfse.issnetonline.com.br/wsnfsenacional/producao/nfse.asmx`

Gostaríamos da ajuda de vocês para confirmar:

1. O número **0796481500161** é o **CF/DF correto e ativo** da MEGASUPORTE (CNPJ 36.342.291/0001-43) para emissão de NFS-e no Padrão Nacional? Se não, qual o valor/formato correto que devemos informar no campo `<IM>`?
2. Esse cadastro está **habilitado nos ambientes de homologação e de produção** do webservice `wsnfsenacional`? Em caso negativo, **qual o procedimento e o prazo** para liberação?
3. Há alguma **solicitação prévia** (liberação de acesso, numeração, série) que precise ser feita no portal antes de conseguirmos emitir?

Ficamos à disposição para qualquer informação adicional (certificado A1 ICP-Brasil válido até 05/03/2027, regime Simples Nacional ME/EPP).

Desde já agradecemos.

Atenciosamente,
**Bruno Reis**
MEGASUPORTE SERVIÇOS DE TI LTDA
[telefone] · [e-mail]
