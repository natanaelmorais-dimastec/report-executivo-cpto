# GitHub Copilot Cost Extractor — Relatório Mensal P&T (Dimastec)

Extrai o custo mensal do GitHub Copilot via Enhanced Billing API e carrega no
`relatorio_pt.custos` como `categoria=Ferramentas, produto=Compartilhado, fonte=github-copilot`.

## O que ele faz

- `GET /organizations/{org}/settings/billing/usage?year=YYYY&month=M`
- Filtra `product` == `Copilot` (case-insensitive), agrega `netAmount` por SKU
  (Business vs Enterprise aparecem como linhas separadas)
- Converte USD → BRL
- Tudo vira `produto=Compartilhado` — Copilot é per-seat e o report atribui
  produto em nível de app/serviço, não por engenheiro
- Idempotente por `(competencia, fonte="github-copilot")`

## Pré-requisitos

1. Python 3.10+ — `pip install -r requirements.txt`
2. PAT do GitHub com escopo de billing
3. `cp .env.example .env` e preencher `GITHUB_TOKEN` (e opcionalmente `GITHUB_ORG`)

### Escolhendo o token

| Tipo | Configuração |
|------|--------------|
| Classic PAT | Escopo `manage_billing:copilot` (read-only billing) |
| Fine-grained | Resource owner = a org; Org permission "Plan" → Read |

Token expira — anote a data de expiração e recrie antes.

## Uso

```bash
# Carga completa (CSV + BigQuery)
python3 get-github-copilot-costs/extractor.py --month 2026-05 \
    --usd-brl-rate 5.04 \
    --bq-project executive-reports-cpto

# Apenas CSV
python3 get-github-copilot-costs/extractor.py --month 2026-05 \
    --org dimastec --usd-brl-rate 5.04 \
    --output custos_copilot_2026-05.csv
```

## Parâmetros

| Flag | Obrigatório | Descrição |
|------|:-----------:|-----------|
| `--month` | ✅ | Mês `YYYY-MM` |
| `--org` | ✅* | Slug da organização GitHub (ou `GITHUB_ORG` no `.env`) |
| `--usd-brl-rate` | — | Cotação USD→BRL (default `1.0`) |
| `--bq-project` | — | GCP project id para carga no BigQuery |
| `--bq-dataset` | — | Dataset (default `relatorio_pt`) |
| `--output` | — | CSV de saída (default `custos_github_copilot.csv`) |

> *Pode vir do `.env`.

## Saída CSV

```
competencia,produto,item,valor_brl
2026-05,Compartilhado,GitHub Copilot - copilot_business,4845.60
2026-05,Compartilhado,GitHub Copilot - copilot_enterprise,1572.40
```

## Limitações / notas

- A Enhanced Billing API é a sucessora da `/orgs/{org}/copilot/billing`. Se o
  endpoint mudar, ajuste em `extractor.py` (`fetch_usage`).
- **403** geralmente significa que o token não tem o escopo correto, OU que
  a org tem "Enhanced billing" desabilitada (legacy billing). Nesse caso,
  ative no console em **Org → Settings → Billing & Plans** ou caia no
  `ingest-manual-costs` com `fonte: manual-github-copilot`.
- Bilhetagem é em USD; sempre informar `--usd-brl-rate`.

## Estrutura

```
get-github-copilot-costs/
├── extractor.py
├── bq_loader.py
├── requirements.txt
├── .env.example
└── README.md
```
