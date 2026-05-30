# SendGrid (via Twilio) Cost Extractor — Relatório Mensal P&T (Dimastec)

Extrai o custo mensal do SendGrid via **Twilio Usage Records API** (Twilio
adquiriu o SendGrid e unifica billing). Carrega no `relatorio_pt.custos` como
`categoria=Ferramentas, produto=Compartilhado, fonte=sendgrid`.

## ⚠️ Caveat importante

Nem toda conta SendGrid expõe o custo de email via Usage Records do Twilio.
Contas SendGrid **legacy/standalone** (anteriores à integração de billing)
**não aparecem** na API do Twilio. Este extractor:

1. Busca todos os Usage Records do mês
2. Filtra categorias que contêm `email` ou `sendgrid` (case-insensitive)
3. Se **nenhuma** categoria de email for encontrada, loga TODAS as categorias
   vistas no mês para você ver se há outro nome a mapear
4. Se ainda assim não houver, use o `ingest-manual-costs` com
   `fonte: manual-sendgrid` — uma linha por mês resolve

Você descobre rápido qual é o seu caso: rode uma vez e veja o output.

## O que ele faz (quando a API funciona)

- `GET /2010-04-01/Accounts/{SID}/Usage/Records/Monthly.json?StartDate=...&EndDate=...`
- Pagina via `next_page_uri` até o fim
- Filtra categorias contendo `email`/`sendgrid`, agrega `price` por categoria
- Converte USD → BRL
- Idempotente por `(competencia, fonte="sendgrid")`

## Pré-requisitos

1. Python 3.10+ — `pip install -r requirements.txt`
2. Conta Twilio com SendGrid integrado (caso contrário, ver caveat acima)
3. `cp .env.example .env` e preencher `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN`
   (pegue no twilio.com → Console → Account Info)

## Uso

```bash
# Carga completa
python3 get-sendgrid-costs/extractor.py --month 2026-05 \
    --usd-brl-rate 5.04 \
    --bq-project executive-reports-cpto

# Apenas CSV (validação)
python3 get-sendgrid-costs/extractor.py --month 2026-05 --usd-brl-rate 5.04
```

## Parâmetros

| Flag | Obrigatório | Descrição |
|------|:-----------:|-----------|
| `--month` | ✅ | Mês `YYYY-MM` |
| `--usd-brl-rate` | — | Cotação USD→BRL (default `1.0`) |
| `--bq-project` | — | GCP project id para carga no BigQuery |
| `--bq-dataset` | — | Dataset (default `relatorio_pt`) |
| `--output` | — | CSV de saída (default `custos_sendgrid.csv`) |

## Saída CSV (caso a API funcione)

```
competencia,produto,item,valor_brl
2026-05,Compartilhado,SendGrid (Twilio) - email-sg-essentials,420.00
2026-05,Compartilhado,SendGrid (Twilio) - email-sg-additional-mail,12.50
```

## Se não funcionar — fallback manual

No `ingest-manual-costs/manifest.yaml.example` já existe a entrada modelo:

```yaml
- categoria: Ferramentas
  produto: Compartilhado
  item: "SendGrid (manual — Twilio API não expõe a categoria)"
  valor_brl: 0.00
  fonte: manual-sendgrid
```

Copie pro seu manifest do mês, atualize o `valor_brl` (vê no console do
Twilio/SendGrid) e rode `ingest_manual.py`. O `fonte` distinto garante
idempotência sem conflitar com a API.

## Estrutura

```
get-sendgrid-costs/
├── extractor.py
├── bq_loader.py
├── requirements.txt
├── .env.example
└── README.md
```
