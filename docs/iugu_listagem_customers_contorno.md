# Contorno — listagem de clientes da Iugu retornando incompleta

> **Quando:** descoberto em 18/06/2026. **Status:** contornado no código; bug é do lado da Iugu.
> Quando a Iugu corrigir, **nada precisa mudar** aqui (o código usa as duas fontes).

## Sintoma
- A aba **Empresas** do app/painel passou a mostrar **só 1–6 clientes** (de ~17).
- **Faturas pagas paravam de emitir NFS-e** ("CNPJ não cadastrado — pulando emissão"),
  porque o repositório de empresas carregava incompleto.

## Causa raiz (é bug da Iugu, não nosso)
`GET /v1/customers` **sem filtro** retorna `totalItems: 1` (só 1 cliente), embora:
- o painel da Iugu mostre ~17 clientes;
- `GET /v1/customers/{id}` retorne **200** para cada um;
- `GET /v1/customers?query=<termo>` **funcione** e retorne os clientes corretamente.

Ou seja, a **listagem base** (sem filtro) está quebrada/indexada errado na conta; o
acesso por ID e a **busca por termo** funcionam. (Testado com `limit=100/200`, `start`,
`sortBy` — sempre 1; token Live correto, o mesmo do painel.)

Agravante: a conta teve clientes **recriados ~21/04/2026**; faturas antigas apontam para
IDs **apagados** (404). Por isso enumerar via faturas trazia lixo (404) e era lento.

## Contorno implementado (commits 18/06/2026)
1. **Emissão robusta** (`src/iugu_empresas.py` → `buscar_por_customer_id`): em cache miss,
   busca o cliente **direto por ID** (`get_customer`) usando o `customer_id` da fatura.
   A emissão via webhook/painel resolve a empresa mesmo com a listagem quebrada.
2. **Enumeração por BUSCA** (`carregar`): em vez da listagem base (que dá 1), busca por
   **vogais** (`a,e,i,o,u`) em paralelo via `query=` e une os IDs. Rápido (~2s), sem 404.
3. **Registro local persistido** (`nfse_emitidas/registro_customer_ids.json`): o `carregar`
   lê o registro + as fontes da API e busca cada cliente por ID; os IDs resolvidos são
   **re-gravados (auto-cura)**. `buscar_por_customer_id` on-demand também alimenta o registro.
4. **Script de semeadura** (`scripts/seed_customer_ids.py`): enumera por busca **a–z + 0-9**
   (cobertura ampla) e grava todos os IDs no registro. Rode quando quiser garantir a lista
   completa (ex.: após adicionar clientes novos que ainda não foram tocados pelo sistema).

## Como operar
- **Re-semear o registro** (lista completa garantida):
  ```bash
  cd /opt/integracao-iugu
  sudo -u iugu .venv/bin/python scripts/seed_customer_ids.py
  sudo systemctl restart iugu-webhook
  ```
- **Validar a contagem** carregada:
  ```bash
  sudo -u iugu .venv/bin/python -c "from src.iugu_empresas import get_repo; print(len(get_repo(forcar=True).listar_ativas()))"
  ```
  Deve mostrar ~17 em ~2s.

## Chamado na Iugu (recomendado)
> A `GET /v1/customers` (sem filtro) da nossa conta retorna `totalItems: 1`, mas o painel tem
> ~17 clientes e tanto `GET /v1/customers/{id}` quanto `GET /v1/customers?query=<termo>`
> funcionam. Ex.: "SINDICONDOMINIO-DF" (id `0E3D2A2CF0A04E4584E70584A8F72875`) responde 200
> por ID e aparece na busca, mas não na listagem. Solicito a correção do índice/listagem de
> clientes da conta.

## Quando a Iugu corrigir
Nada a fazer — `carregar` continua usando a listagem base + busca + registro. A listagem
voltando a funcionar só torna a enumeração ainda mais redundante.
