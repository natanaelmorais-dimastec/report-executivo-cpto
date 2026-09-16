# AWS Cost Extractor — Relatório Mensal P&T (Dimastec)

Extrai o custo da AWS via Cost Explorer API e gera um CSV no formato da aba
`custos_cloud` do relatório mensal, separado por **ambiente** (conta AWS) e por
**produto** (tag `Produto`).

## O que ele faz

- Consulta a Cost Explorer API (`GetCostAndUsage`), agrupando por `LINKED_ACCOUNT` + tag `product`
- Traduz a conta AWS no rótulo de ambiente (Produção / QA)
- Converte USD → BRL quando uma cotação é informada
- Gera CSV pronto para colar na aba `custos_cloud` (que alimenta o Looker Studio)
- Emite um **diagnóstico do custo sem tag por serviço** (`untagged_by_service.csv`),
  separando o que é taggável do que é compartilhado por natureza

## Como o produto é atribuído

Dois passos, de propósito:

1. **Tag `product`** — o valor é normalizado (minúsculas, sem acento, separadores
   colapsados em `-`) e procurado em `PRODUCT_TAG_MAP`. Então `Integração`,
   `integracao` e `INTEGRACAO` caem todos em Integração. Valores que citam mais de
   um produto (`mydhas/faceum`) vão para `Compartilhado`.
2. **Sem tag utilizável → `Compartilhado`.**

**Não existe split estimado do custo sem tag, por decisão.** Uma versão anterior
rateava o custo sem tag pela **fração de recursos** por produto, tirada de um CSV de
tag audit. Isso não se sustenta: contagem de recursos não tem relação com custo (um
recovery point de backup e uma instância RDS contam igual), e a Resource Groups
Tagging API só devolve recursos que **já têm tag** — ou seja, ela não consegue ver os
recursos sem tag que geram o custo sem tag. O custo sem tag fica em `Compartilhado`,
que a `relatorio_pt.vw_eficiencia` já rateia entre os produtos. **Rateio acontece em
um lugar só.**

Se aparecer um valor de tag novo que o mapa não conhece, o extractor emite um
**WARNING** com o valor e sua forma normalizada, em vez de inflar `Compartilhado`
silenciosamente.

### Diagnóstico do custo sem tag

Cada run consulta o custo sem tag `product` agrupado por serviço e classifica em:

| Bucket | Significado |
|---|---|
| `TAGGABLE` | ECS, ELB, RDS, CloudWatch, WAF… pertence a um produto — aplicar a tag `product` tira esse custo de Compartilhado |
| `SHARED` | Tax, Support, VPC/NAT, Route 53, CI/CD… sem dono por natureza |
| `UNCLASSIFIED` | serviço fora das duas listas de hints — emite WARNING para você classificar |

O detalhe vai para `untagged_by_service.csv`. Isso é **diagnóstico**, nunca é
carregado no BigQuery — serve para priorizar o backlog de tagging.

## Pré-requisitos

- Python 3.10+
- `boto3` (`pip install -r requirements.txt`)
- Credenciais AWS com permissão `ce:GetCostAndUsage`
- A tag `product` ativada como **Cost Allocation Tag** (ver `TAGGING_STANDARD.md`)

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
    "295574221328": "Produção",
    "058264268348": "QA",
}
```

E `PRODUCT_TAG_MAP` para traduzir valor de tag → produto do relatório. **As chaves
precisam estar na forma normalizada**: minúsculas, sem acento, `_` / `.` / `/` /
espaço trocados por `-`. Ou seja, a chave de `Integração` é `integracao`.

## Uso

Rode a partir da **raiz do repositório** (`report-executivo-cpto/`):

```bash
# Comando completo (extração + carga no BigQuery)
python3 get-aws-costs/extractor.py --month 2026-05 --usd-brl-rate 5.04 \
    --bq-project executive-reports-cpto \
    --profile dimastec-mgmt

# Apenas extração CSV (sem BigQuery)
python3 get-aws-costs/extractor.py --month 2026-05 --usd-brl-rate 5.04 \
    --profile dimastec-mgmt \
    --output custos_cloud_2026-05.csv

# Sem o diagnóstico de custo sem tag
python3 get-aws-costs/extractor.py --month 2026-05 --profile dimastec-mgmt \
    --untagged-report ""
```

### Parâmetros

| Flag | Obrigatório | Descrição |
|------|:-----------:|-----------|
| `--month` | ✅ | Mês no formato `YYYY-MM` |
| `--profile` | ✅* | Profile nomeado da AWS CLI (`dimastec-mgmt`) |
| `--usd-brl-rate` | — | Cotação USD→BRL (ex: `5.04`). Sem ela, o valor fica em USD |
| `--untagged-report` | — | CSV do diagnóstico de custo sem tag (default: `untagged_by_service.csv`; `""` desliga) |
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

---

## Histórico

- **set/2026** — removido o rateio do custo sem tag por **contagem de recursos**
  (flags `--audit-sa` / `--audit-ue`, ainda aceitas mas ignoradas com WARNING).
  Motivo no bloco "Como o produto é atribuído". No mesmo passe, `PRODUCT_TAG_MAP`
  passou a normalizar acento — a tag real na conta é `integração`, que antes não
  casava com a chave `integracao` e mandava ~R$ 5,4k/mês para `Compartilhado` em
  vez de `Integração`.
