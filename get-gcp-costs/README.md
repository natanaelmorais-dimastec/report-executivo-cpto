# GCP Cost Extractor — Relatório Mensal P&T (Dimastec)

Extrai o custo do GCP via **Cloud Billing BigQuery Export (Standard)** e carrega
no `relatorio_pt.custos`, usando o mesmo cascade de atribuição por produto do
extractor da AWS.

## O que ele faz

- Consulta o billing export por `invoice.month = YYYYMM`
- Atribui produto via cascade: label da linha → label do projeto → `PROJECT_PRODUCT_MAP` →
  pesos do `project_audit.csv` → `Compartilhado`
- Soma `cost + credits` por (projeto, label), converte para BRL quando a moeda é USD
- Gera CSV (`custos_cloud_gcp.csv`) com o detalhe por produto × projeto
- Opcionalmente, carrega no BigQuery em `relatorio_pt.custos` com
  `categoria=Cloud, cloud_provedor=GCP, fonte=gcp`
- Idempotente: rodar o mesmo mês duas vezes não duplica (e não derruba linhas
  de AWS/Azure, porque o `replace_month` filtra também por `fonte`)

## Pré-requisitos

1. **Billing export habilitado** → ver `SETUP_GCP_BILLING_EXPORT.md` (~24h para
   popular o primeiro dia de dados)
2. Python 3.10+
3. `pip install -r requirements.txt`
4. ADC autenticada: `gcloud auth application-default login`
5. IAM mínimo na identidade que roda:
   - `roles/bigquery.dataViewer` no dataset `billing_export`
   - `roles/bigquery.dataEditor` no dataset `relatorio_pt`
   - `roles/bigquery.jobUser` no projeto `executive-reports-cpto`

## Configuração

Edite no topo de `extractor.py`:

### `PROJECT_PRODUCT_MAP` — projetos GCP que pertencem a um único produto

```python
PROJECT_PRODUCT_MAP = {
    "executive-reports-cpto": "Compartilhado",   # seed: o próprio projeto do report
    # "dimastec-faceum-prod": "Faceum",
    # "dimastec-mydhas-prd":  "Mydhas",
}
```

Atualize após rodar `project_audit.py` — ele lista todos os projetos com custo no mês.

### `PRODUCT_LABEL_MAP` — valores aceitos da label `product`

```python
PRODUCT_LABEL_MAP = {
    "faceum": "Faceum", "dtfaceum": "Faceum", "dt-faceum": "Faceum",
    "mydhas": "Mydhas",
    "ai": "AI",
    "integracao": "Integração", "integracao-faceum": "Integração",
    "compartilhado": "Compartilhado",
}
```

Padrão de label sugerido (igual ao tag da AWS): chave `product`, valor minúsculo sem acento.

## Uso

Rode a partir da **raiz do repositório** (`report-executivo-cpto/`):

### 1. Audit (opcional, mas recomendado antes do primeiro extractor)

```bash
python3 get-gcp-costs/project_audit.py \
    --billing-export-table executive-reports-cpto.billing_export.gcp_billing_export_v1_<ACCOUNT_ID> \
    --month 2026-05 \
    --usd-brl-rate 5.04 \
    --output get-gcp-costs/project_audit.csv
```

Saída: lista de projetos com custo, % com label aplicada, e pesos derivados
(cost-share por produto, calculados a partir do que ESTÁ labelado em cada projeto).

### 2. Extractor (CSV + BigQuery)

```bash
python3 get-gcp-costs/extractor.py --month 2026-05 \
    --billing-export-table executive-reports-cpto.billing_export.gcp_billing_export_v1_<ACCOUNT_ID> \
    --usd-brl-rate 5.04 \
    --audit get-gcp-costs/project_audit.csv \
    --bq-project executive-reports-cpto
```

### Apenas CSV (sem BigQuery)

```bash
python3 get-gcp-costs/extractor.py --month 2026-05 \
    --billing-export-table executive-reports-cpto.billing_export.gcp_billing_export_v1_<ACCOUNT_ID> \
    --usd-brl-rate 5.04 \
    --output custos_cloud_gcp_2026-05.csv
```

### Mínimo (sem audit, sem BigQuery — só PROJECT_PRODUCT_MAP + Compartilhado)

```bash
python3 get-gcp-costs/extractor.py --month 2026-05 \
    --billing-export-table executive-reports-cpto.billing_export.gcp_billing_export_v1_<ACCOUNT_ID>
```

## Parâmetros

| Flag | Obrigatório | Descrição |
|------|:-----------:|-----------|
| `--month` | ✅ | Mês no formato `YYYY-MM` (convertido para `YYYYMM` na query) |
| `--billing-export-table` | ✅ | `project.dataset.tabela` do export (ver setup) |
| `--usd-brl-rate` | — | Cotação USD→BRL quando a moeda do billing é USD. Default `1.0` |
| `--audit` | — | `project_audit.csv` para weighted split em projetos misturados |
| `--bq-project` | — | Project id GCP para carga no BigQuery (sem ele, só CSV) |
| `--bq-dataset` | — | Dataset destino (default: `relatorio_pt`) |
| `--query-project` | — | Project do client BigQuery (default: `executive-reports-cpto`) |
| `--output` | — | Caminho do CSV (default: `custos_cloud_gcp.csv`) |

## Saída CSV

```
competencia,produto,project_id,project_name,valor_brl
2026-05,Compartilhado,executive-reports-cpto,Executive Reports CPTO,42.17
2026-05,Faceum,dimastec-faceum-prod,Dimastec Faceum,1850.40
```

## Limitações conhecidas

- **Labels não retroagem.** Custo anterior à aplicação da label cai no fallback
  (PROJECT_PRODUCT_MAP → audit weights → Compartilhado). Para meses antigos, use a
  rota project-name → produto.
- **Apenas Standard export.** Não inclui dados de catálogo de preços (Pricing
  data). Para análises de simulação, habilite a aba separada no console.
- **Currencies não-USD/BRL:** o extractor avisa e trata como BRL (sem conversão).
  Se o billing for em outra moeda, ajustar `to_brl()`.
- **Audit weights são proxy.** Quando um projeto mistura produtos e parte do custo
  não está labelada, os pesos vêm da PARTE labelada do mesmo projeto. Se o uso é
  heterogêneo entre o labelado e o não-labelado, o split pode enviesar — o jeito
  certo é aplicar labels diretamente.

## Estrutura

```
get-gcp-costs/
├── extractor.py                    # script principal (cascade + carga BQ)
├── project_audit.py                # auditoria read-only (gera weights)
├── bq_loader.py                    # cópia do loader compartilhado
├── requirements.txt
├── SETUP_GCP_BILLING_EXPORT.md     # como habilitar o export (uma vez)
└── README.md
```
