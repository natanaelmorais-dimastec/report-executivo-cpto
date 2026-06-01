# CLAUDE.md — Relatório Mensal de Produtos & Tecnologia (Dimastec)

Guia para o Claude Code trabalhar neste repositório. Lê este arquivo antes de qualquer tarefa.

---

## O que é este projeto

Pipeline de dados (ETL Nível 1) que alimenta o **relatório executivo mensal de Produtos
& Tecnologia** apresentado ao CEO e CFO da Dimastec. Coleta custos e entregas de várias
fontes, consolida no BigQuery, e o Looker Studio lê de lá para gerar o dashboard.

```
extractors Python (AWS, Jira)  →  BigQuery (dataset relatorio_pt)  →  Looker Studio
   + cargas manuais (SQL/CSV)        tabelas: custos, entregas,         (dashboard CEO/CFO)
                                     metricas_negocio; views            2 páginas:
                                                                        Investimentos / Resultados
```

Fase atual: **Nível 1** — scripts rodados manualmente, carregam no BigQuery.
Fase futura (não implementada): Nível 2 — Cloud Run + Cloud Scheduler para rodar sozinho.

---

## Contexto de negócio (essencial para decisões)

**Empresa:** Dimastec (B2B SaaS, Brasil). Produtos: **Faceum** (compliance trabalhista /
ponto / reconhecimento facial), **Mydhas** (HR tech), **AI** (iniciativas de IA), e
**Saturno** (legado .NET em migração para Faceum/Mydhas).

**Naturezas de custo do relatório (categoria):**
- `Time` — folha. O manifest YAML guarda valores **brutos**; o ingest aplica o
  **fator de encargo automaticamente** via `ENCARGO_BY_FONTE` em
  `ingest-manual-costs/ingest_manual.py`: CLT × 1,70, Estágio × 1,05, PJ × 1,00.
  Os números no BigQuery já são **onerados** (= custo real para o report).
- `Cloud` — AWS, Azure, GCP (campo cloud_provedor distingue)
- `Fornecedor de Produto` — Gryfo (reconhecimento facial, ~R$ 80k/mês, 100% Faceum)
- `Parceiros/Operação` — Beonup (sustenta AWS), Danysoft (sustenta Azure)
- `Ferramentas` — GitHub Copilot, SendGrid, MongoDB Atlas, etc. (O365 pendente, via Ingram CSP)

**Produtos no recorte de custo/entrega:**
Faceum, Mydhas, AI, Integração, Compartilhado, Saturno (legado).
- Integração (NiFi/Airflow) entra só no CUSTO de cloud, NÃO nas entregas (demandas geridas
  pela operação, não pela área de infra).

**Narrativa estratégica central:** o legado Saturno custa ~R$ 28,6k/mês (Azure R$ 15,9k +
Danysoft R$ 12,7k) = ~10% do custo total de tecnologia. A migração Saturno→Faceum/Mydhas
elimina esse custo. Conectar custo do legado com entregas de migração é o argumento de ROI.

**Custo total de referência (maio/2026):** R$ 351.370,78 parcial, 11 fontes carregadas
(falta Copilot, MongoDB Atlas, SendGrid — extractors aguardando credenciais — e O365).
Câmbio usado: USD/BRL = 5,04.

**Regime contábil do relatório:** **CASH BASIS** (regime de caixa). `competencia=YYYY-MM`
é o mês em que a fatura foi **paga**, não o mês de uso. Para a maioria das fontes
(cloud, fornecedores, folha) isso corresponde ao **mês anterior de uso/trabalho**.
Para assinaturas pré-pagas (Atlassian, Excalidraw), uso e pagamento caem no mesmo mês.
O usuário reconcilia o relatório contra invoices, não contra consumo — por isso a
escolha. Ver memória `report-cash-basis-convention.md`.

---

## Estrutura do repositório

```
.
├── get-aws-costs/                  # AWS Cost Explorer + cascade de produto por tag/nome
├── get-gcp-costs/                  # GCP Cloud Billing BigQuery Export + cascade por label
├── get-github-copilot-costs/       # GitHub Enhanced Billing usage (Ferramentas, Compartilhado)
├── get-mongodb-atlas-costs/        # Atlas Billing API por invoice mensal (Ferramentas)
├── get-sendgrid-costs/             # Twilio Usage Records → SendGrid email (Ferramentas)
├── jira-deliveries-extractor/      # entregas do Jira, agrupadas por épico → produto
├── ingest-manual-costs/            # ingestor YAML para fontes sem API (Gryfo, Danysoft,
│                                   # Beonup, folha CLT/PJ/Estágio, Atlassian, Excalidraw,
│                                   # Azure Saturno)
├── setup-bigquery/                 # DDL (schema.sql), bq_loader.py ORIGINAL, setup doc
├── manual-invoices/                # GITIGNORED — manifests YAML por mês + PDFs evidência
│   └── 2026-05/manifest.yaml
└── CLAUDE.md
```

> `bq_loader.py` é mantido em `setup-bigquery/` como original e COPIADO para a pasta de
> cada extractor/ingestor (eles fazem `from bq_loader import BigQueryLoader`). Ao alterá-lo,
> atualize todas as cópias ou centralize via PYTHONPATH.

---

## Stack e dependências

- **Python 3.10+** (usa type hints com `|`, ex.: `str | None`)
- `boto3` (AWS), `requests` (Jira), `google-cloud-bigquery` (carga), `python-dotenv`
- **BigQuery** projeto GCP `executive-reports-cpto`, dataset `relatorio_pt`, location `US`
- **Looker Studio** lê BigQuery direto (conector nativo)

---

## Como rodar

### Pré-requisitos (uma vez)
```bash
pip install -r requirements.txt          # em cada pasta de extractor
gcloud auth application-default login
gcloud config set project executive-reports-cpto
```

### Variáveis de ambiente (Jira) — use um .env (NÃO comitar)
```
JIRA_BASE_URL=https://dimastec.atlassian.net
JIRA_EMAIL=voce@dimastec.com.br
JIRA_API_TOKEN=...        # id.atlassian.com -> API tokens
```

### AWS (perfil `dimastec-mgmt`, IAM `DimastecCostExplorerReadOnly`)
```bash
cd get-aws-costs && python3 extractor.py --month 2026-05 --usd-brl-rate 5.04 \
    --profile dimastec-mgmt \
    --audit-sa tag_audit.csv --audit-ue tag_audit_useast.csv \
    --bq-project executive-reports-cpto
```

### GCP (via Cloud Billing BigQuery Export)
```bash
python3 get-gcp-costs/extractor.py --month 2026-05 --usd-brl-rate 5.04 \
    --billing-export-table executive-reports-cpto.billing_export.gcp_billing_export_v1_01ED44_5AEFA1_EE5D4B \
    --audit get-gcp-costs/project_audit.csv \
    --bq-project executive-reports-cpto
```

### Jira
```bash
python3 jira-deliveries-extractor/jira_extractor.py --month 2026-05 \
    --bq-project executive-reports-cpto
```

### Manuais (YAML)
```bash
# Copie o template; edite valor_brl em manual-invoices/<mês>/manifest.yaml
cp ingest-manual-costs/manifest.yaml.example manual-invoices/2026-05/manifest.yaml
python3 ingest-manual-costs/ingest_manual.py --month 2026-05 --dry-run        # valida
python3 ingest-manual-costs/ingest_manual.py --month 2026-05 \
    --bq-project executive-reports-cpto                                       # carrega
```

> Sem `--bq-project`, os scripts só geram CSV / validam (modo seguro). Com `--bq-project`,
> também carregam no BigQuery (`replace_month` por fonte — idempotente).

### Criar/atualizar o schema BigQuery
Rode `setup-bigquery/schema.sql` no console BigQuery (ou `bq query --use_legacy_sql=false < schema.sql`).

---

## Convenções OBRIGATÓRIAS deste projeto

Estas regras valem para qualquer código gerado ou alterado aqui:

1. **Scripts de automação sempre em Python.**
2. **Logs sempre em inglês** (mesmo com o resto do projeto em PT-BR).
3. **Todo script/projeto novo tem documentação inline + README.** Se o README já existe,
   atualize só o que mudou, preservando a estrutura.
4. **Git: commits e pushes exigem aprovação humana explícita.** Sempre mostre o diff,
   descreva as mudanças, e ESPERE confirmação antes de commitar/pushar. Nunca commite sozinho.
5. **Idempotência:** cargas no BigQuery usam `replace_month` (apaga o mês e reinsere).
   Rodar o mesmo mês duas vezes não duplica. Mantenha esse comportamento.
6. **Saída CSV preservada:** o BigQuery é saída adicional, não substitui o CSV.
7. **Nunca exponha credenciais** em código, logs ou `ps aux` (ex.: não passe secrets via
   flags `-D` da JVM; use env vars ou Secret Manager). Tokens em `.env` fora do git.
8. **Cash basis em TODAS as fontes.** Qualquer extractor novo precisa shiftar a query para
   o mês anterior e rotular a competência com o mês do relatório (ver `month_to_invoice` em
   `get-gcp-costs/extractor.py`, `prev_month` em `get-aws-costs/extractor.py`). Não misturar
   convenções entre fontes — números do CFO batem invoice por invoice.
9. **Folha em valor BRUTO no manifest, ONERADO em BQ.** O YAML do
   `ingest-manual-costs` recebe o salário base / NF bruta. O script aplica o
   encargo via `ENCARGO_BY_FONTE` (CLT 1,70 / Estágio 1,05 / PJ 1,00) no
   momento do `replace_month`. NÃO multiplique no YAML — duplica.

---

## Schema do BigQuery (dataset relatorio_pt)

**Tabela `custos`** (chave temporal: competencia STRING 'YYYY-MM')
`competencia, categoria, produto, cloud_provedor, item, valor_brl (NUMERIC), fonte, carregado_em`
- `categoria`: Time | Cloud | Ferramentas | Parceiros/Operação | Fornecedor de Produto
- `fonte`: aws | azure | gcp | jira | manual

**Tabela `entregas`**
`competencia, produto, titulo, impacto, status, n_issues, carregado_em`
- `impacto` sai com placeholder; é refinado manualmente (frase de impacto de negócio)
- `status` virou "N issues entregues" (não binário Entregue/Em progresso)

**Tabela `metricas_negocio`** (entrada manual ou API futura)
`competencia, usuarios_ativos, contratos_ativos, carregado_em`

**View `vw_custo_mensal`** — custo total por mês (SUM agrupado)
**View `vw_eficiencia`** — KPIs custo_por_usuario, custo_por_contrato (JOIN custo×negócio;
substitui o "blend" que o Looker faria; usa SAFE_DIVIDE)

---

## Mapeamentos de domínio (não reinventar)

**Jira project keys → produto** (em jira_extractor.py, PROJECT_PRODUCT_MAP):
- AI: AIEPI, AIMCP, AIE, AIRF
- Faceum: FACEUMAPP, DTCOLAB, FLA, FACEUMBK, FACEUMCL, DAU, FMC, DTDOC, FMR
- Mydhas: MD

**Status "concluído" no Jira:** o status real usado é **"Finalizado"**. O JQL usa
`statusCategory = Done` (robusto a nome de status) + janela por `resolved`, com fallback
por `updated`. NÃO voltar a usar `status changed to ("Done","Concluído")` — quebrou porque
o status não se chama Done.

**AWS → produto** (em extractor.py): cascata (1) tag `product`, (2) inferência por nome de
recurso, (3) rateio por pesos do tag audit, (4) Compartilhado. Conta de produção =
295574221328. Tags aplicadas valem de junho/2026 em diante (maio ficou só por ambiente).

**Cloud provider:** ambiente "Azure" → Azure; "GCP" → GCP; resto → AWS.

---

## Decisões tomadas (e o porquê) — não reverter sem motivo

- **BigQuery (não Athena/RDS):** conector Looker nativo grátis + serverless. Athena exigiria
  conector parceiro pago; RDS paga 24/7. Para dados agregados sem PII, location US é ok (LGPD
  não exige Brasil aqui; se entrar PII, criar dataset separado em São Paulo).
- **Sem particionamento nas tabelas:** competencia é STRING e o volume é pequeno (dezenas de
  linhas/mês). Particionar deu erro e era over-engineering.
- **Cash basis (regime de caixa) em todas as fontes** — decidido em 2026-06-01 após gap
  entre GCP extractor (R$ 402) e fatura do usuário (R$ 2.220). `competencia=YYYY-MM` = R$
  pago naquele mês = (para cloud/fornecedor/folha) uso/trabalho do mês ANTERIOR. Vale
  uniformemente para AWS, GCP, Atlassian, Excalidraw, Gryfo, Danysoft, Beonup, folha,
  Azure-Saturno. Reverter isso requer re-rodar todas as fontes do mês e reconciliar com
  a planilha do CFO.
- **Gryfo (maio) = estimativa = fatura de abril** (R$ 80.484,50), porque a fatura de maio
  ainda não foi emitida. Substituir pelo valor real quando vier (re-roda o ingest do
  manifest com o novo valor — idempotente).
- **Saturno/Azure = categoria própria** "Saturno (legado)"; Danysoft (Azure) e Beonup (AWS)
  como Parceiros/Operação separados do consumo de cloud.
- **Entregas por épico** (não por issue solta); status = nº de issues entregues; AI aparece
  como iniciativas WIP (não entrega mensal). ~28% das issues são "avulsas" (sem épico).
- **Looker:** 2 páginas (Investimentos / Resultados, framing estratégico). Campo calculado
  `mes = PARSE_DATE("%Y-%m", competencia)` para eixo de data. Filtro de período report-level.

---

## Pendências conhecidas (não são bugs)

- **Extractors API com código pronto mas SEM credenciais configuradas em `.env`:**
  GitHub Copilot (`GITHUB_TOKEN`), MongoDB Atlas (`MONGODB_ATLAS_PUBLIC_KEY` +
  `PRIVATE_KEY` + `ORG_ID`), SendGrid (precisa `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN`,
  NÃO uma SendGrid API key — o cobrança vem pela API Twilio).
- **O365:** valor real pendente (billing via Ingram CSP, não visível no admin Microsoft).
  Entra como linha manual em `ingest-manual-costs/` quando obtido.
- **Bug GCP billing export:** parou de receber dados em **2026-05-07** (memória
  `gcp-billing-export-stalled.md`). Não atrapalhou maio (cash basis consulta abril), mas
  **quebra junho** se não for resolvido — `invoice.month=202605` está incompleto. Ação:
  console GCP > Billing > Export antes do fechamento de junho.
- **Coluna `impacto` das entregas:** refinar placeholders manualmente após cada rodada
  do Jira extractor.
- **Fatores de encargo (CLT 1,70, Estágio 1,05):** estimativa — confirmar com
  contabilidade. Aplicados em `ingest-manual-costs/ingest_manual.py` (constante
  `ENCARGO_BY_FONTE`). Mudar lá quando o real for confirmado e re-rodar o mês.
- **Jira API:** endpoint migrado para `/rest/api/3/search/jql` (o antigo foi depreciado);
  paginação por `nextPageToken`.

---

## Ao implementar algo novo aqui

- Novo extractor: siga o padrão dos existentes (argparse com `--month` e `--bq-project`,
  saída CSV + carga via `bq_loader.replace_month`, logs em inglês, README).
- Mapear para o schema `custos` ou `entregas` existente (não criar tabela nova sem necessidade).
- Antes de commitar: mostre o diff e aguarde aprovação.
