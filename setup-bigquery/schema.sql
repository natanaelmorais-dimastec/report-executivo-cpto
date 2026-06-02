-- ============================================================================
-- BigQuery schema — Relatório Mensal P&T (Dimastec)
-- ============================================================================
-- Cria o dataset e as tabelas que recebem os dados dos extractors (AWS, Jira,
-- e futuramente Azure/GCP/Gryfo/ferramentas/time/métricas de negócio).
--
-- Como rodar:
--   1. Console GCP → BigQuery → abra o editor de SQL
--   2. Cole e execute este script (ajuste PROJECT_ID se quiser nome diferente)
--   3. Ou via CLI: bq query --use_legacy_sql=false < schema.sql
--
-- Convenção: um dataset `relatorio_pt`, tabelas por natureza de dado.
-- Todas têm `competencia` (STRING 'YYYY-MM') como chave temporal.
-- ============================================================================

-- 1. Criar o dataset (equivalente a um "banco/schema")
CREATE SCHEMA IF NOT EXISTS `relatorio_pt`
OPTIONS (
  location = 'southamerica-east1',   -- São Paulo, perto dos seus dados
  description = 'Dados do relatório mensal de Produtos & Tecnologia'
);

-- ----------------------------------------------------------------------------
-- 2. Tabela de CUSTOS (consolidada, alimentada por vários extractors)
--    Espelha a aba dados_custos da planilha.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `relatorio_pt.custos` (
  competencia    STRING   NOT NULL,   -- 'YYYY-MM'
  categoria      STRING   NOT NULL,   -- Time, Cloud, Ferramentas, Parceiros/Operação, Fornecedor de Produto
  produto        STRING,              -- Faceum, Mydhas, AI, Integração, Compartilhado, Saturno (legado)
  cloud_provedor STRING,              -- AWS, Azure, GCP (vazio quando não-cloud)
  item           STRING,              -- nome do item (serviço, ferramenta, perfil, fornecedor)
  valor_brl      NUMERIC  NOT NULL,   -- valor em reais (final, com encargo onde aplicável)
  fonte          STRING,              -- 'aws', 'gcp', 'mongodb-atlas', 'jira', 'manual-<slug>', ...
  carregado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
  valor_usd      NUMERIC,             -- valor em USD ANTES da conversão (NULL para linhas BRL-nativas)
  taxa_usd_brl   NUMERIC              -- taxa usada nesta linha (NULL para linhas BRL-nativas)
)
PARTITION BY DATE_TRUNC(PARSE_DATE('%Y-%m', competencia), MONTH)
OPTIONS (description = 'Custos consolidados por mês, categoria, produto e provedor. valor_usd/taxa_usd_brl preenchidos quando a fonte original é USD.');

-- ----------------------------------------------------------------------------
-- 3. Tabela de ENTREGAS (alimentada pelo jira_extractor)
--    Espelha a aba entregas.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `relatorio_pt.entregas` (
  competencia  STRING  NOT NULL,   -- 'YYYY-MM'
  produto      STRING  NOT NULL,   -- Faceum, Mydhas, AI
  titulo       STRING,             -- título do épico/entrega
  impacto      STRING,             -- impacto de negócio (refinado manualmente)
  status       STRING,             -- 'N issues entregues' / fase (WIP, etc.)
  n_issues     INT64,              -- número de issues entregues no épico
  carregado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE_TRUNC(PARSE_DATE('%Y-%m', competencia), MONTH)
OPTIONS (description = 'Entregas do mês agrupadas por épico, por produto');

-- ----------------------------------------------------------------------------
-- 4. Tabela de COTAÇÕES (taxa de câmbio por mês — alimenta o Looker e o ingest manual)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `relatorio_pt.cotacoes` (
  competencia  STRING NOT NULL,   -- 'YYYY-MM'
  par          STRING NOT NULL,   -- 'USD/BRL'
  taxa         NUMERIC NOT NULL,  -- taxa de câmbio aplicada ao mês
  fonte        STRING,            -- 'PTAX-fechamento', 'manual', 'CLI', etc. (rastreabilidade)
  carregado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
OPTIONS (description = 'Cotação mensal por par de moedas. Uma linha por (competencia, par). Gravada pelo close_month.py no início do run; lida pelo ingest_manual.py quando uma linha YAML tem valor_usd.');

-- ----------------------------------------------------------------------------
-- 5. Tabela de MÉTRICAS DE NEGÓCIO (usuários, contratos) — entrada manual ou futura API
--    Chave lógica: (competencia, produto). Carregada pelo
--    `ingest-manual-costs/ingest_metricas.py` a partir de
--    `manual-invoices/<YYYY-MM>/metricas.yaml`.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `relatorio_pt.metricas_negocio` (
  competencia      STRING NOT NULL,  -- 'YYYY-MM'
  produto          STRING,           -- Faceum, Mydhas, AI (NULL = total/legado)
  usuarios_ativos  INT64,
  contratos_ativos INT64,
  carregado_em     TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
OPTIONS (description = 'Usuários e contratos ativos por (mês × produto), para KPIs de eficiência por produto');

-- ----------------------------------------------------------------------------
-- 5. VIEW de custo total por mês (o Looker usa para cruzar com métricas)
--    View = consulta salva, sempre atualizada. Substitui o SUMIFS da planilha.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW `relatorio_pt.vw_custo_mensal` AS
SELECT
  competencia,
  SUM(valor_brl) AS custo_total_brl
FROM `relatorio_pt.custos`
GROUP BY competencia;

-- ----------------------------------------------------------------------------
-- 6.5. VIEW custos com cotação (LEFT JOIN custos × cotacoes em competencia)
--      Looker conecta aqui em vez de em `custos` direto — `taxa_mes_usd_brl` já
--      vem em cada linha (sem blend) para exibir no header do dashboard.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW `relatorio_pt.vw_custos_com_cotacao` AS
SELECT
  c.competencia,
  c.categoria,
  c.produto,
  c.cloud_provedor,
  c.item,
  c.valor_brl,
  c.fonte,
  c.carregado_em,
  c.valor_usd,
  c.taxa_usd_brl,                  -- taxa usada NESTA linha (NULL p/ BRL-nativa)
  cot.taxa AS taxa_mes_usd_brl,    -- taxa OFICIAL do mês (sempre presente quando cotacoes existe)
  cot.fonte AS taxa_fonte
FROM `relatorio_pt.custos` c
LEFT JOIN `relatorio_pt.cotacoes` cot
  ON c.competencia = cot.competencia AND cot.par = 'USD/BRL';

-- ----------------------------------------------------------------------------
-- 7. VIEW de KPIs de eficiência POR PRODUTO (custo Faceum / usuários Faceum, etc.)
--    Granularidade: uma linha por (competencia × produto).
--    Substitui o blend manual do Looker — o JOIN vive aqui.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW `relatorio_pt.vw_eficiencia` AS
SELECT
  c.competencia,
  c.produto,
  c.custo_produto_brl,
  m.usuarios_ativos,
  m.contratos_ativos,
  SAFE_DIVIDE(c.custo_produto_brl, m.usuarios_ativos)  AS custo_por_usuario,
  SAFE_DIVIDE(c.custo_produto_brl, m.contratos_ativos) AS custo_por_contrato,
  SAFE_DIVIDE(m.usuarios_ativos, m.contratos_ativos)   AS usuarios_por_contrato
FROM (
  SELECT competencia, produto, ROUND(SUM(valor_brl), 2) AS custo_produto_brl
  FROM `relatorio_pt.custos`
  WHERE produto IS NOT NULL
  GROUP BY competencia, produto
) c
LEFT JOIN `relatorio_pt.metricas_negocio` m
  ON c.competencia = m.competencia AND c.produto = m.produto;

-- ============================================================================
-- Pronto. No Looker, conecte:
--   - relatorio_pt.custos          (gráficos de custo)
--   - relatorio_pt.entregas        (entregas)
--   - relatorio_pt.vw_eficiencia   (KPIs de custo por usuário/contrato — SEM blend!)
-- O JOIN que exigia blend na planilha agora vive na view. Muito mais simples.
-- ============================================================================
