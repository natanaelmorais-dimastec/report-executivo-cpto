# Fechamento Mensal — Relatório P&T (Dimastec)

Passo a passo para fechar o relatório executivo de um mês. **Tempo
estimado: 30-60 min**, sendo a maior parte preenchimento de valores
manuais. Os scripts em si rodam em ~1 minuto.

> **Convenção contábil:** o relatório é **cash basis**.
> `competencia=YYYY-MM` significa "R$ pagos naquele mês civil".
> Para cloud/fornecedor/folha isso = consumo do mês ANTERIOR.
> Para assinatura pré-paga (Atlassian, Excalidraw) = mesmo mês.
> Detalhes em [CLAUDE.md](CLAUDE.md).

---

## Antes do primeiro mês (one-time setup)

Cada item abaixo só precisa ser feito **uma vez** por máquina:

| Item | Como |
|---|---|
| `gcloud` ADC | `gcloud auth application-default login` |
| Projeto GCP padrão | `gcloud config set project executive-reports-cpto` |
| Perfil AWS | `~/.aws/credentials` com `[dimastec-mgmt]` apontando para o usuário IAM `DimastecCostExplorerReadOnly` |
| `.env` por extractor | `cp <pasta>/.env.example <pasta>/.env` e preencher (ver tabela abaixo) |
| Deps Python | `pip install -r <pasta>/requirements.txt` em cada pasta |

### Credenciais necessárias por extractor

| Extractor | `.env` em | Variáveis | Onde gerar |
|---|---|---|---|
| AWS | (não usa .env) | usa perfil `dimastec-mgmt` | console AWS IAM |
| GCP | (não usa .env) | usa ADC | `gcloud auth application-default login` |
| Jira | `jira-deliveries-extractor/.env` | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | `id.atlassian.com` → API tokens |
| Copilot | `get-github-copilot-costs/.env` | `GITHUB_TOKEN`, `GITHUB_ORG` | GitHub → Settings → Developer settings → PAT (escopo `manage_billing:copilot`) |
| MongoDB Atlas | `get-mongodb-atlas-costs/.env` | `MONGODB_ATLAS_PUBLIC_KEY`, `_PRIVATE_KEY`, `_ORG_ID` | Atlas → Organization Access Manager → API Keys (role "Organization Billing Viewer") |
| SendGrid (Twilio) | `get-sendgrid-costs/.env` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | `twilio.com` → Console → Account Info |

> Os `.env` estão no `.gitignore` — nunca commitar.

---

## Roteiro mês a mês

### 1. Cotação USD/BRL do fechamento

Use a cotação **PTAX de fechamento do último dia útil do mês de pagamento**
(o mês do relatório). Exemplo: para competência 2026-05 (= valores pagos em
maio), use a cotação de 31/mai (ou 30/mai se 31 for fim de semana).

Essa taxa será passada no `--usd-brl-rate` e o `close_month.py` grava
automaticamente em `relatorio_pt.cotacoes` (uma linha por mês). Looker
consulta essa tabela para mostrar a taxa no dashboard. Cada linha USD em
`custos` carrega `valor_usd` e `taxa_usd_brl` para audit por linha.

### 2. Preparar o manifest manual

```bash
mkdir -p manual-invoices/2026-05
cp ingest-manual-costs/manifest.yaml.example manual-invoices/2026-05/manifest.yaml
$EDITOR manual-invoices/2026-05/manifest.yaml
```

Preencha cada `valor_brl` com o **valor pago em 2026-05** de cada fornecedor.
A pasta `manual-invoices/2026-05/` também é onde você guarda os **PDFs de
fatura** como auditoria (o script não parseia — só lê o YAML).

**Folha (Time):** os valores no YAML são **brutos** (salário base / NF). O
script aplica encargo automaticamente (CLT × 1,70, Estágio × 1,05, PJ ×
1,00) via `ENCARGO_BY_FONTE` em `ingest-manual-costs/ingest_manual.py`.
**Não multiplique no YAML.**

**Linhas em USD:** use `valor_usd` em vez de `valor_brl` para fornecedores
que cobram em dólar (Atlassian, Excalidraw, Copilot, SendGrid). O ingestor
lê a taxa de `relatorio_pt.cotacoes` (gravada pelo `close_month.py`) e
converte. **Não converta manualmente no YAML.** Exemplo:

```yaml
- categoria: Ferramentas
  produto: Compartilhado
  item: "Atlassian (Jira)"
  valor_usd: 759.03       # ← USD direto da fatura
  fonte: manual-atlassian
```

**Compartilhado:** se uma pessoa divide tempo entre produtos, lance como
`produto: Compartilhado` numa linha só. Não tente ratear pelo Faceum/Mydhas
no YAML — o relatório lida com isso.

### 3. Coletar valores manuais que você precisa

Para o mês 2026-05 (= pago em maio, na maioria dos casos consumo de abril):

| Fonte | Onde olhar | Comentário |
|---|---|---|
| Folha CLT/PJ/Estágio | Planilha da contabilidade | Por colaborador, valor BRUTO |
| Gryfo | Email da fatura recebida em maio | Estimar = abril se a fatura de maio ainda não saiu |
| Beonup (AWS) | NF recebida em maio | Sustentação AWS |
| Danysoft (Azure) | NF recebida em maio | Sustentação Azure Saturno |
| Atlassian | Conta de cobrança Atlassian (USD) | × cotação USD/BRL |
| Excalidraw | Conta de cobrança Excalidraw (USD) | × cotação USD/BRL |
| Azure Saturno | Console Azure (fatura abril paga em maio) | Não tem extractor próprio |
| GitHub Copilot | Fatura cobrada no cartão em maio | Só se Enhanced Billing não estiver habilitado |
| SendGrid | Console SendGrid (fatura abril paga em maio) | Só se a conta for standalone |
| O365 | Ingram CSP | Pendente — adicionar quando tiver |

### 4. Rodar tudo

```bash
python3 close_month.py --month 2026-05 --usd-brl-rate 5.04
```

Esse comando executa em sequência:

1. **AWS** (Cost Explorer, perfil `dimastec-mgmt`) — uso de abril, paga em maio
2. **GCP** (Billing Export) — invoice.month = 202604
3. **MongoDB Atlas** (API) — fatura abril
4. **GitHub Copilot** (API) — uso abril
5. **SendGrid** (Twilio API) — uso abril
6. **Manuais** (YAML) — todas as linhas em `manual-invoices/2026-05/manifest.yaml`
7. **Jira** (Deliveries) — issues resolvidas em maio (uso, não cash basis — entregas não são pagas)

No final, o script imprime um **summary BQ** com total por categoria + nº de
linhas de entregas. Se algum step falhar, os outros continuam — você vê o
status no SUMMARY.

#### Variantes

```bash
# Pular Copilot e SendGrid (se você sabe que vão falhar — fallback manual)
python3 close_month.py --month 2026-05 --skip copilot,sendgrid

# Rodar SÓ um step (corrigir manifest e re-ingerir, por ex.)
python3 close_month.py --month 2026-05 --only manuais

# Cotação diferente
python3 close_month.py --month 2026-05 --usd-brl-rate 5.12
```

### 5. Validar no BigQuery

O summary do próprio script já mostra os totais. Para inspecionar mais:

```bash
# Total por fonte
python3 -c "
from google.cloud import bigquery
c = bigquery.Client(project='executive-reports-cpto')
for r in c.query('''
SELECT fonte, COUNT(*) n, ROUND(SUM(valor_brl),2) total
FROM \`executive-reports-cpto.relatorio_pt.custos\`
WHERE competencia=\"2026-05\"
GROUP BY fonte ORDER BY total DESC
''').result():
    print(f'  {r[\"fonte\"]:30s} {r[\"n\"]:3d}  R\$ {float(r[\"total\"]):>12,.2f}')
"
```

### 6. Refinar entregas (manual)

O extractor do Jira preenche `entregas.impacto` com placeholder. Antes de
mandar pro CEO/CFO, refine a coluna para uma frase de impacto de negócio
por épico:

```sql
UPDATE `executive-reports-cpto.relatorio_pt.entregas`
SET impacto = 'Reduziu tempo de processamento de folha em 40%'
WHERE competencia = '2026-05'
  AND produto = 'Mydhas'
  AND titulo = 'Backend Mydhas e Saturno';
```

### 7. Abrir o Looker

O Looker lê direto do BQ (`relatorio_pt.custos`, `entregas`, `metricas_negocio`).
Se os números não baterem, refresh o cache do Looker (Resource → Manage cached
queries → Refresh).

---

## O que é automático vs manual

| Fonte | Automático? | Cobertura para 2026-05 |
|---|---|---|
| AWS | ✅ extractor `get-aws-costs` | ~R$ 54k |
| GCP | ✅ extractor `get-gcp-costs` | ~R$ 2k |
| MongoDB Atlas | ✅ extractor `get-mongodb-atlas-costs` | ~R$ 545 |
| Jira (entregas) | ✅ extractor `jira-deliveries-extractor` | 20 entregas |
| Copilot | ⚠️ extractor existe mas requer Enhanced Billing — hoje cai em manual | ~R$ 447 |
| SendGrid | ⚠️ extractor existe mas conta é standalone — hoje cai em manual | ~R$ 756 |
| Folha (CLT/PJ/Estágio) | ❌ manual (YAML) | ~R$ 211k |
| Gryfo | ❌ manual (YAML) | ~R$ 80k |
| Beonup | ❌ manual (YAML) | ~R$ 4k |
| Danysoft | ❌ manual (YAML) | ~R$ 13k |
| Atlassian | ❌ manual (YAML) | ~R$ 4k |
| Excalidraw | ❌ manual (YAML) | ~R$ 71 |
| Azure Saturno | ❌ manual (YAML) | ~R$ 16k |
| O365 | ❌ pendente (Ingram CSP) | — |

---

## Troubleshooting

### "Failed to fetch cost data: Unable to locate credentials" (AWS)

Você esqueceu `--profile dimastec-mgmt`. O orchestrator passa
automaticamente, mas se rodar `extractor.py` direto, precisa do flag.

### "No invoice with startDate in YYYY-MM" (MongoDB Atlas)

A fatura do mês anterior ainda não foi emitida pela Atlas. Atlas emite no
início do mês seguinte, então rodar muito cedo no início do mês (ex.: 01)
pode falhar. Espere 2-3 dias.

### "GitHub 404 — org not found or not visible" (Copilot)

A org não tem **Enhanced Billing Platform** habilitada (vê CLAUDE.md
"Pendências"). Caminho atual: adicionar linha manual no manifest com
`fonte: manual-github-copilot` e valor da cobrança no cartão.

### "Email-related categories matched: X records out of Y total" mas TOTAL R$ 0 (SendGrid)

A conta SendGrid não passa pela Twilio billing API (é standalone).
Adicione linha manual no manifest com `fonte: manual-sendgrid` e o valor
visto no console SendGrid.

### "Unmapped clusters fell back to 'Compartilhado'" (Atlas)

O Atlas extractor encontrou um cluster que ainda não está em
`CLUSTER_PRODUCT_MAP`. Abra `get-mongodb-atlas-costs/extractor.py:47`,
adicione o mapeamento, re-rode.

### Looker mostra número diferente do BQ

O Looker pode estar:
- Lendo de uma planilha intermediária (não direto do BQ)
- Com cache antigo (Resource → Manage cached queries → Refresh)
- Aplicando regras de cálculo extras (campos calculados)

**BQ é a fonte da verdade.** Se diverge, primeiro confira o que está no BQ
com a query do passo 5, depois ajuste o Looker.

### "Bug GCP export parou em 2026-05-07"

Memória `gcp-billing-export-stalled.md`. Vá em
GCP Console → Billing → Export → BigQuery export e verifique:
- A configuração está apontando pro dataset `executive-reports-cpto.billing_export`
- A Transfer Service tem permissão `roles/bigquery.dataEditor` no dataset
- Histórico de runs não está com falha

Sem isso, o fechamento de **junho/2026** vai pegar `invoice.month=202605`
incompleto. Resolver antes do dia 1º de julho.

---

## Convenções

Documentação canônica em [CLAUDE.md](CLAUDE.md). Resumo das regras que
mais aparecem:

- **Cash basis** — `competencia=YYYY-MM` = pago naquele mês
- **Folha bruta no YAML, onerada em BQ** — encargo aplicado em `ingest_manual.py`
- **Idempotência por fonte** — re-rodar mesmo mês não duplica
- **Logs em inglês** — mesmo com tudo mais em PT-BR
- **Commits/pushes requerem confirmação humana** — nunca commitar sozinho
- **CSV preservado** — toda a sequência mantém CSV de auditoria além de
  carregar no BQ
