# Guia de Setup — BigQuery + ETL (Nível 1)

> Para quem nunca usou BigQuery. Cobre: ativar, criar as tabelas, adaptar os scripts,
> rodar a carga e conectar no Looker. Mantém os scripts manuais (você dispara) — a
> automação total (Cloud Scheduler) fica para uma fase futura.

## Visão geral do que vamos montar

```
scripts Python (AWS, Jira)  →  BigQuery (dataset relatorio_pt)  →  Looker Studio
   (você roda manualmente)        (tabelas: custos, entregas)       (lê direto, sem planilha)
```

A planilha sai de cena como fonte do Looker. O BigQuery vira a fonte de verdade.

---

## PASSO 1 — Ativar o BigQuery no seu projeto GCP

1. Console GCP (console.cloud.google.com) → selecione seu projeto no topo
2. Menu → **BigQuery** (em "Analytics"). Na primeira vez, ele ativa a API sozinho.
3. Anote o **PROJECT ID** (não o nome — o id, ex.: `dimastec-prod-123456`).
   Você vai usar em todo lugar.

> Custo: BigQuery tem free tier generoso (1 TB de query/mês, 10 GB de storage grátis).
> O volume deste relatório é minúsculo (KB). Na prática, custo zero ou centavos.

## PASSO 2 — Criar o dataset e as tabelas

1. No BigQuery, clique em **+ SQL query** (ou "Compose new query")
2. Cole TODO o conteúdo de `schema.sql`
3. **Run** (Executar)

Isso cria:
- Dataset `relatorio_pt`
- Tabelas `custos`, `entregas`, `metricas_negocio`
- Views `vw_custo_mensal` e `vw_eficiencia` (o cruzamento de KPIs, sem blend!)

> Confira no painel esquerdo: deve aparecer `relatorio_pt` com as tabelas dentro.

## PASSO 3 — Autenticar localmente (para os scripts escreverem no BQ)

Os scripts Python precisam de credencial para escrever no BigQuery. A forma mais
simples para uso local:

```bash
# instala a CLI do gcloud se ainda não tiver (cloud.google.com/sdk/docs/install)
gcloud auth application-default login
gcloud config set project SEU_PROJECT_ID
```

Isso cria credenciais "Application Default" que o `bq_loader.py` usa automaticamente.
Não precisa de chave de service account para rodar do seu Mac.

> Permissão necessária: seu usuário precisa do papel **BigQuery Data Editor** (escrever)
> e **BigQuery Job User** (rodar queries) no projeto. Se for owner do projeto, já tem.

## PASSO 4 — Instalar a lib e posicionar o loader

```bash
pip install google-cloud-bigquery
```

Copie o `bq_loader.py` para a pasta de cada extractor (ou um local no PYTHONPATH),
para que `from bq_loader import BigQueryLoader` funcione.

## PASSO 5 — Aplicar os patches nos scripts

Abra `patches_extractors.py` e siga as instruções. Resumo:
- No `extractor.py` (AWS): adicione o import, os 2 argumentos `--bq-project`/`--bq-dataset`,
  e o bloco `PATCH_AWS_LOAD` depois do `write_csv`.
- No `jira_extractor.py`: idem com o bloco `PATCH_JIRA_LOAD`.

Os scripts continuam gerando CSV; o BigQuery é saída adicional, ativada com `--bq-project`.

## PASSO 6 — Rodar a carga

```bash
# AWS (CSV + BigQuery)
python extractor.py --month 2026-05 --usd-brl-rate 5.04 \
    --audit-sa tag_audit.csv --audit-ue tag_audit_useast.csv \
    --bq-project SEU_PROJECT_ID

# Jira (CSV + BigQuery)
python jira_extractor.py --month 2026-05 --bq-project SEU_PROJECT_ID
```

Confira no BigQuery: `SELECT * FROM relatorio_pt.custos LIMIT 10;` deve retornar os dados.

> Idempotente: rodar o mesmo mês de novo NÃO duplica — o loader apaga o mês e reinsere.

## PASSO 7 — Carregar os dados que ainda são manuais

Time, Ferramentas, Parceiros, Gryfo, Azure, GCP e métricas de negócio ainda não têm
extractor. Por enquanto, carregue via SQL direto no BigQuery (ou crie extractors depois):

```sql
-- exemplo: inserir o custo do time de maio (uma linha por produto)
INSERT INTO `relatorio_pt.custos` (competencia, categoria, produto, item, valor_brl, fonte)
VALUES
  ('2026-05', 'Time', 'Faceum', 'Folha (custo real)', 97836.09, 'manual'),
  ('2026-05', 'Time', 'Mydhas', 'Folha (custo real)', 89482.19, 'manual'),
  ('2026-05', 'Time', 'AI',     'Folha (custo real)',  6800.00, 'manual');

-- exemplo: métricas de negócio
INSERT INTO `relatorio_pt.metricas_negocio` (competencia, usuarios_ativos, contratos_ativos)
VALUES ('2026-05', 12345, 678);  -- troque pelos números reais
```

> Dica: você pode exportar as abas atuais da planilha para CSV e carregar em lote no
> BigQuery (Create table → Upload), em vez de digitar INSERTs.

## PASSO 8 — Conectar o Looker no BigQuery (a recompensa)

1. No relatório Looker → **Resource → Manage added data sources → Add a data source**
2. Conector **BigQuery** → seu projeto → dataset `relatorio_pt`
3. Conecte: `custos`, `entregas`, e a view `vw_eficiencia`
4. Refaça os gráficos apontando para as tabelas do BigQuery (ou troque a fonte de cada
   gráfico existente: editar gráfico → Data source → trocar para a tabela BQ)

**Ganhos imediatos:**
- Acaba a planilha como fonte (sem re-subir no Drive)
- O `competencia` no BQ já é string limpa; crie o campo `mes` igual antes se precisar de eixo de data
- Os KPIs de eficiência (custo por usuário) vêm da view `vw_eficiencia` — **sem blend**,
  o JOIN já está feito no SQL. Muito mais simples que na planilha.
- Looker lê do BQ em tempo real; rodou o script, atualizou.

---

## Comparação: antes x depois

| | Planilha (antes) | BigQuery (agora) |
|---|---|---|
| Fonte de verdade | Google Sheets | BigQuery |
| Atualizar | rodar script → CSV → colar → re-subir Drive | rodar script com `--bq-project` |
| Cruzar custo × usuários | blend manual no Looker | JOIN na view (pronto) |
| Escala | trava com volume | serverless, sem limite prático |
| Custo | grátis | grátis/centavos (free tier) |

---

## O que fica para a fase 2 (automação total — Nível 2)

Quando quiser que rode sozinho, sem você disparar:
- **Cloud Run** para executar os scripts containerizados
- **Cloud Scheduler** para agendar (ex.: dia 1 de cada mês, 8h)
- **Secret Manager** para os tokens (Jira, AWS keys)

Mas só vale se o ganho de não rodar manualmente justificar o esforço de manter a infra.
Por ora, Nível 1 já elimina a planilha e o trabalho repetitivo de colar dados.

---

## Checklist

- [ ] Ativar BigQuery no projeto, anotar PROJECT_ID
- [ ] Rodar `schema.sql` (cria dataset, tabelas, views)
- [ ] `gcloud auth application-default login` + set project
- [ ] `pip install google-cloud-bigquery`
- [ ] Copiar `bq_loader.py` para a pasta dos extractors
- [ ] Aplicar patches no extractor.py e jira_extractor.py
- [ ] Rodar os scripts com `--bq-project` e conferir os dados no BQ
- [ ] Carregar os dados manuais (Time, Gryfo, etc.) via INSERT/upload
- [ ] Conectar o Looker no BigQuery e trocar as fontes dos gráficos
- [ ] Validar que os números batem com a versão da planilha
