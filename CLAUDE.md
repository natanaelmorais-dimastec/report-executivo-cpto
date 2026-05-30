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
- `Time` — folha (PJ sem encargo; CLT com fator de encargo ~1,70 estimado; estágio)
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

**Custo total de referência (maio/2026):** R$ 365.187,62. Câmbio usado: USD/BRL = 5,04.

---

## Estrutura do repositório

```
.
├── aws-cost-extractor/
│   ├── extractor.py          # custo AWS via Cost Explorer, atribui produto por tag/nome
│   ├── tag_audit.py          # auditoria read-only de tags (gera os CSVs de peso)
│   ├── bq_loader.py          # módulo compartilhado de carga BigQuery (cópia)
│   ├── iam-policy-*.json      # policies mínimas de leitura (Cost Explorer, tags)
│   └── *.md                   # IAM_SETUP, TAGGING_* (guias)
├── jira-deliveries-extractor/
│   ├── jira_extractor.py     # entregas do Jira, agrupadas por épico, mapeadas a produto
│   └── bq_loader.py          # cópia do módulo de carga
├── etl-bigquery/
│   ├── schema.sql            # DDL: dataset relatorio_pt, tabelas, views
│   ├── bq_loader.py          # ORIGINAL do módulo de carga
│   ├── SETUP_BIGQUERY.md     # passo a passo de setup do zero
│   └── README.md
└── CLAUDE.md                 # este arquivo
```

> `bq_loader.py` é mantido em `etl-bigquery/` como original e COPIADO para a pasta de
> cada extractor (eles fazem `from bq_loader import BigQueryLoader`). Ao alterá-lo,
> atualize as três cópias ou centralize via PYTHONPATH.

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

### AWS (credenciais via perfil/role; usuário IAM DimastecCostExplorerReadOnly)
```bash
python extractor.py --month 2026-05 --usd-brl-rate 5.04 \
    --audit-sa tag_audit.csv --audit-ue tag_audit_useast.csv \
    --bq-project executive-reports-cpto
```

### Jira
```bash
python jira_extractor.py --month 2026-05 --bq-project executive-reports-cpto
```

> Sem `--bq-project`, os scripts só geram CSV (modo legado, sem risco).
> Com `--bq-project`, geram CSV **e** carregam no BigQuery.

### Criar/atualizar o schema BigQuery
Rode `etl-bigquery/schema.sql` no console BigQuery (ou `bq query --use_legacy_sql=false < schema.sql`).

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
- **Gryfo (maio) = estimativa = abril** (fatura abril paga em maio); regime de competência.
  Substituir pela fatura real quando sair.
- **Saturno/Azure = categoria própria** "Saturno (legado)"; Danysoft (Azure) e Beonup (AWS)
  como Parceiros/Operação separados do consumo de cloud.
- **Entregas por épico** (não por issue solta); status = nº de issues entregues; AI aparece
  como iniciativas WIP (não entrega mensal). ~28% das issues são "avulsas" (sem épico).
- **Looker:** 2 páginas (Investimentos / Resultados, framing estratégico). Campo calculado
  `mes = PARSE_DATE("%Y-%m", competencia)` para eixo de data. Filtro de período report-level.

---

## Pendências conhecidas (não são bugs)

- Fontes ainda SEM extractor (carga manual via SQL/CSV): Time, Gryfo, Azure, GCP,
  Ferramentas, Parceiros. Criar extractors para elas é trabalho futuro.
- O365: valor real pendente (billing via Ingram CSP, não visível no admin Microsoft).
- Coluna `impacto` das entregas: refinar placeholders manualmente.
- Fator de encargo CLT (1,70) é estimativa — confirmar com contabilidade.
- Jira API: endpoint migrado para `/rest/api/3/search/jql` (o antigo foi depreciado);
  paginação por `nextPageToken`.

---

## Ao implementar algo novo aqui

- Novo extractor: siga o padrão dos existentes (argparse com `--month` e `--bq-project`,
  saída CSV + carga via `bq_loader.replace_month`, logs em inglês, README).
- Mapear para o schema `custos` ou `entregas` existente (não criar tabela nova sem necessidade).
- Antes de commitar: mostre o diff e aguarde aprovação.
