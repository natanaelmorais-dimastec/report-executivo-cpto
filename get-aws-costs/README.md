# AWS Cost Extractor — Relatório Mensal P&T (Dimastec)

Extrai o custo da AWS via Cost Explorer API e gera um CSV no formato da aba
`custos_cloud` do relatório mensal, separado por **ambiente** (conta AWS) e por
**produto** (tag `Produto`).

## O que ele faz

- Consulta a Cost Explorer API (`GetCostAndUsage`), agrupando por `LINKED_ACCOUNT` + tag `Produto`
- Traduz a conta AWS no rótulo de ambiente (Produção / Staging / Dev-CI)
- Converte USD → BRL quando uma cotação é informada
- Gera CSV pronto para colar na aba `custos_cloud` (que alimenta o Looker Studio)
- Reporta a % de custo **sem tag**, para acompanhar o avanço do tagging

## Pré-requisitos

- Python 3.10+
- `boto3` (`pip install -r requirements.txt`)
- Credenciais AWS com permissão `ce:GetCostAndUsage`
- A tag `Produto` ativada como **Cost Allocation Tag** (ver `TAGGING_STANDARD.md`)

Rode preferencialmente a partir da **conta de gestão/pagadora** (consolidated billing)
para enxergar todas as contas vinculadas em uma só chamada. Também funciona por conta.

## IAM mínimo

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["ce:GetCostAndUsage"], "Resource": "*" }
  ]
}
```

## Configuração

Edite o dicionário `ACCOUNTS` no topo de `extractor.py`, mapeando o id de cada conta
ao rótulo de ambiente:

```python
ACCOUNTS = {
    "111111111111": "Produção",
    "222222222222": "Staging",
    "333333333333": "Dev-CI",
}
```

## Uso

Rode a partir da **raiz do repositório** (`report-executivo-cpto/`):

```bash
# Comando completo (extração + carga no BigQuery)
python3 get-aws-costs/extractor.py --month 2026-05 --usd-brl-rate 5.04 \
    --audit-sa get-aws-costs/tag_audit.csv \
    --audit-ue get-aws-costs/tag_audit_useast.csv \
    --bq-project executive-reports-cpto \
    --profile dimastec-mgmt

# Apenas extração CSV (sem BigQuery)
python3 get-aws-costs/extractor.py --month 2026-05 --usd-brl-rate 5.04 \
    --audit-sa get-aws-costs/tag_audit.csv \
    --audit-ue get-aws-costs/tag_audit_useast.csv \
    --profile dimastec-mgmt \
    --output custos_cloud_2026-05.csv

# Mínimo (sem audit weights — todo custo untagged vai para 'Compartilhado')
python3 get-aws-costs/extractor.py --month 2026-05 --profile dimastec-mgmt
```

### Parâmetros

| Flag | Obrigatório | Descrição |
|------|:-----------:|-----------|
| `--month` | ✅ | Mês no formato `YYYY-MM` |
| `--profile` | ✅* | Profile nomeado da AWS CLI (`dimastec-mgmt`) |
| `--usd-brl-rate` | — | Cotação USD→BRL (ex: `5.04`). Sem ela, o valor fica em USD |
| `--audit-sa` | — | CSV do tag audit de sa-east-1 (Produção) |
| `--audit-ue` | — | CSV do tag audit de us-east-1 (QA) |
| `--bq-project` | — | Project ID do GCP para carga no BigQuery |
| `--bq-dataset` | — | Dataset do BigQuery (default: `relatorio_pt`) |
| `--output` | — | Caminho do CSV de saída (default: `custos_cloud.csv`) |

> *Se as credenciais AWS estiverem no profile `default`, o `--profile` pode ser omitido.

## Saída

CSV com as colunas exatas da aba `custos_cloud`:

```
competencia,produto,ambiente,valor_brl
2026-05,Faceum,Produção,4200.00
2026-05,Mydhas,Produção,2800.00
2026-05,Sem tag (revisar),Produção,1310.00
```

Cole o conteúdo na aba `custos_cloud` da planilha. O Looker Studio atualiza sozinho.

## Limitações conhecidas

- **Tags não são retroativas.** Meses anteriores ao tagging saem só por ambiente
  (o produto aparece como "Sem tag"). Ver `TAGGING_STANDARD.md`.
- **Moeda:** a API retorna na moeda de cobrança da conta. Se for USD, informe `--usd-brl-rate`.
  A cotação comercial não inclui IOF + spread do cartão; para número contábil exato,
  use o câmbio efetivo da fatura.
- **Granularidade:** o relatório executivo para no nível produto × ambiente. Detalhe por
  serviço (EC2/RDS/etc.) é útil para o time otimizar custo, mas polui a visão do CEO/CFO.

## Próximo passo (opcional): escrever direto no Google Sheets

Hoje a saída é CSV (colagem manual). Para automatizar a escrita na planilha, dá para
usar `gspread` + uma service account do Google, escrevendo direto na aba `custos_cloud`.
Fica como evolução quando o fluxo manual estiver validado.

## Estrutura

```
aws-cost-extractor/
├── extractor.py          # script principal
├── requirements.txt
├── README.md
└── TAGGING_STANDARD.md   # padrão da tag Produto + ativação no billing
```
