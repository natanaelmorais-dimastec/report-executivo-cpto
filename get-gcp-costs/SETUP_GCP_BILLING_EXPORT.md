# Setup: Cloud Billing → BigQuery Export

Pré-requisito do extractor de custos GCP. Faça **uma única vez** por billing account.
Sem isso, o `extractor.py` não tem fonte de dados para consultar.

> A exportação é **gratuita**. Os dados começam a aparecer ~24h depois de habilitar.
> Antes disso, o extractor falha com "Table not found".

---

## 1. Identifique o billing account

```bash
gcloud auth login
gcloud billing accounts list
```

Anote o `ACCOUNT_ID` (formato `01A2B3-456C7D-890E1F`). Esse ID vira o sufixo do nome
da tabela exportada (com `_` no lugar dos `-`).

## 2. Crie o dataset que receberá a exportação

No projeto **`executive-reports-cpto`**, na location **`US`** (mesma do `relatorio_pt`,
para que o JOIN não esbarre em location mismatch):

```bash
bq --location=US mk --dataset \
   --description="GCP Cloud Billing export (Standard usage cost)" \
   executive-reports-cpto:billing_export
```

Confirme:

```bash
bq ls executive-reports-cpto:billing_export    # vazio por enquanto, ok
```

## 3. Habilite o export no console

Console → **Billing** → escolha o billing account → **Billing export** → aba
**BigQuery export** → seção **Standard usage cost** → **Edit settings**:

| Campo | Valor |
|-------|-------|
| Project | `executive-reports-cpto` |
| Dataset | `billing_export` |

Clique **Save**.

> A aba "Detailed usage cost" é opcional e dobra o volume armazenado. Não habilite
> agora — o Standard já traz `project.labels` e `labels` (resource), que é o que o
> extractor usa para atribuição de produto.
>
> "Pricing data" também é opcional — só precisa se for fazer simulação de preço.

## 4. Aguarde a primeira carga (~24h)

A primeira escrita acontece no dia seguinte. Para checar:

```bash
bq ls executive-reports-cpto:billing_export
```

Deve aparecer uma tabela chamada:

```
gcp_billing_export_v1_<ACCOUNT_ID com _ no lugar dos ->
```

Exemplo: account `01A2B3-456C7D-890E1F` vira tabela
`gcp_billing_export_v1_01A2B3_456C7D_890E1F`.

## 5. IAM mínimo para rodar o extractor

A identidade que roda o extractor (sua conta via `gcloud auth application-default login`,
ou uma service account) precisa, dentro de `executive-reports-cpto`:

- `roles/bigquery.dataViewer` no dataset `billing_export` (leitura do export)
- `roles/bigquery.dataEditor` no dataset `relatorio_pt` (escrita no `custos`)
- `roles/bigquery.jobUser` no projeto (executar queries)

Se você já é Owner do `executive-reports-cpto`, tudo isso já vem incluso.

## 6. Sanity check

Antes de rodar o extractor, confirme que o export está populado:

```bash
bq query --use_legacy_sql=false --project_id=executive-reports-cpto "
SELECT
  invoice.month AS competencia,
  currency,
  ROUND(SUM(cost), 2) AS cost_raw,
  ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS credits_sum,
  ROUND(SUM(cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS effective_cost
FROM \`executive-reports-cpto.billing_export.gcp_billing_export_v1_<ACCOUNT_ID>\`
GROUP BY 1, 2
ORDER BY 1 DESC
"
```

Se retornar linhas com `competencia` no formato `YYYYMM` (ex: `202605`) e `effective_cost > 0`,
está pronto.

## 7. Próximo passo

Anote a **fully-qualified table name** (`project.dataset.table`) — você vai passá-la
para o extractor:

```bash
python3 get-gcp-costs/extractor.py --month 2026-05 \
    --billing-export-table executive-reports-cpto.billing_export.gcp_billing_export_v1_<ACCOUNT_ID> \
    --usd-brl-rate 5.04 \
    --bq-project executive-reports-cpto
```

---

## Troubleshooting

**"Table not found"** — a primeira carga ainda não rodou. Aguarde até o dia seguinte
da habilitação.

**Custos parecem baixos demais** — a Standard export NÃO inclui linhas de pricing data
(catálogo). Confirma se está vendo o `invoice.month` correto (formato `YYYYMM`, sem hífen,
diferente do nosso `competencia` `YYYY-MM`).

**Vários billing accounts** — habilite a exportação para cada um no mesmo dataset.
Cada um gera uma tabela `gcp_billing_export_v1_<ACCOUNT>`. O extractor consulta uma
tabela por vez (passe o `--billing-export-table` correto).

**Mudança retroativa de labels** — labels aplicadas hoje NÃO retroagem. Custo anterior à
aplicação ficará com label vazio e cairá no fallback (PROJECT_PRODUCT_MAP → Compartilhado).
