# ETL BigQuery — Relatório Mensal P&T (Dimastec)

Migra a fonte de dados do relatório de Google Sheets para **BigQuery**, e adapta os
extractors existentes (AWS, Jira) para carregar os dados lá. O Looker passa a ler do
BigQuery direto — fim da planilha como fonte e do blend manual.

Esta é a **Fase 1 (Nível 1)**: BigQuery como warehouse + scripts rodados manualmente.
A automação total (Cloud Scheduler + Cloud Run) é uma fase futura.

## Arquivos

| Arquivo | O que é |
|---|---|
| `schema.sql` | DDL das tabelas/views no BigQuery. Rode primeiro. |
| `bq_loader.py` | Módulo Python compartilhado que escreve no BigQuery (idempotente por mês). |
| `patches_extractors.py` | O que adicionar nos extractors AWS e Jira para usarem o loader. |
| `SETUP_BIGQUERY.md` | Passo a passo completo, do zero ao Looker conectado. |

## Início rápido

1. Leia `SETUP_BIGQUERY.md` (é o guia principal)
2. Ative o BigQuery e rode `schema.sql`
3. `gcloud auth application-default login`
4. `pip install google-cloud-bigquery`
5. Aplique os patches, rode os scripts com `--bq-project`
6. Conecte o Looker no BigQuery

## Schema (resumo)

- `relatorio_pt.custos` — custos consolidados (Cloud via AWS extractor, resto manual por ora)
- `relatorio_pt.entregas` — entregas do Jira
- `relatorio_pt.metricas_negocio` — usuários/contratos ativos
- `relatorio_pt.vw_custo_mensal` — custo total por mês (view)
- `relatorio_pt.vw_eficiencia` — custo por usuário/contrato (view, substitui o blend)

## Princípios mantidos

- Scripts em Python, logs em inglês, idempotentes (re-rodar um mês não duplica)
- Saída CSV preservada — BigQuery é saída adicional (`--bq-project`), transição sem risco
- Commits/cargas exigem ação explícita sua (nada roda sozinho nesta fase)

## Próxima fase (Nível 2 — opcional)

Cloud Run + Cloud Scheduler + Secret Manager para execução automática mensal.
Só vale se o ganho justificar a infra a manter.
