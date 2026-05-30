# MongoDB Atlas Cost Extractor — Relatório Mensal P&T (Dimastec)

Extrai o custo mensal da fatura do MongoDB Atlas via Billing API e carrega no
`relatorio_pt.custos` como `categoria=Ferramentas, fonte=mongodb-atlas`.

## O que ele faz

- Lista as faturas (`GET /orgs/{ORG-ID}/invoices`) e seleciona a do mês
  alvo (match em `startDate[:7]` == `YYYY-MM`)
- Busca o detalhe da fatura (lineItems com `clusterName`, `groupName`, `service`, `totalPriceCents`)
- Agrega por (produto, item) seguindo o cascade:
  1. `CLUSTER_PRODUCT_MAP[clusterName]`
  2. `GROUP_PRODUCT_MAP[groupName]`
  3. fallback → `Compartilhado`
- Converte USD → BRL (USD é a moeda padrão de billing do Atlas)
- Idempotente por `(competencia, fonte="mongodb-atlas")`: não toca em linhas de outras fontes

## Pré-requisitos

1. Python 3.10+ — `pip install -r requirements.txt`
2. API key Programmatic gerada em **Atlas → Organization Access Manager → API Keys**
   com role mínima **Organization Billing Viewer** (least privilege)
3. Org ID (Atlas → Organization Settings → Organization ID)
4. `cp .env.example .env` e preencher as três variáveis

## Configuração

Edite no topo de `extractor.py`:

```python
CLUSTER_PRODUCT_MAP = {
    "faceum-prod":  "Faceum",
    "mydhas-prd":   "Mydhas",
    "ai-sandbox":   "AI",
}

GROUP_PRODUCT_MAP = {
    "Production":   "Compartilhado",
    "Faceum":       "Faceum",
}
```

A primeira execução já loga `Unmapped clusters fell back to 'Compartilhado': X, Y, Z`
— use a lista para preencher o map.

## Uso

```bash
# Carga completa (CSV + BigQuery)
python3 get-mongodb-atlas-costs/extractor.py --month 2026-05 \
    --usd-brl-rate 5.04 \
    --bq-project executive-reports-cpto

# Apenas CSV (validação)
python3 get-mongodb-atlas-costs/extractor.py --month 2026-05 \
    --usd-brl-rate 5.04 \
    --output custos_mongodb_2026-05.csv

# Passando org-id pela CLI (sobrescreve o .env)
python3 get-mongodb-atlas-costs/extractor.py --month 2026-05 \
    --org-id 5e9f1234567890abcdef1234 \
    --usd-brl-rate 5.04 --bq-project executive-reports-cpto
```

## Parâmetros

| Flag | Obrigatório | Descrição |
|------|:-----------:|-----------|
| `--month` | ✅ | Mês `YYYY-MM` — bate com `startDate` da fatura Atlas |
| `--org-id` | ✅* | Organization ID (ou `MONGODB_ATLAS_ORG_ID` no `.env`) |
| `--usd-brl-rate` | — | Cotação USD→BRL (default `1.0` = sem conversão) |
| `--bq-project` | — | GCP project id para carga no BigQuery |
| `--bq-dataset` | — | Dataset (default `relatorio_pt`) |
| `--output` | — | CSV de saída (default `custos_mongodb_atlas.csv`) |

> *Obrigatório, mas pode vir do `.env`.

## Saída CSV

```
competencia,produto,item,valor_brl
2026-05,Faceum,faceum-prod (Atlas),3214.40
2026-05,Compartilhado,(no-cluster) (BackupStorage),420.00
```

## Limitações / notas

- **Faturas saem com defasagem.** Atlas costuma emitir a fatura do mês N no
  início do mês N+1. Se você rodar no dia 2 e o mês ainda não fechou na Atlas,
  o script avisa "No invoice with startDate in YYYY-MM".
- **Credits/discounts** aparecem como linhas com `totalPriceCents` negativo;
  o agregador soma normalmente (subtrai do bruto).
- **Atlas bills em USD.** Sempre informe `--usd-brl-rate` para a cotação
  efetiva do mês.
- **Sem PII no extractor.** A chave da API só tem `Organization Billing Viewer`,
  não enxerga dados de cluster.

## Estrutura

```
get-mongodb-atlas-costs/
├── extractor.py
├── bq_loader.py            # cópia do loader
├── requirements.txt
├── .env.example            # template; .env real fica gitignored
└── README.md
```
