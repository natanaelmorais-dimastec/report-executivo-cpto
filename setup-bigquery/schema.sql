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
  valor_brl      NUMERIC  NOT NULL,   -- valor em reais
  fonte          STRING,              -- de qual extractor veio: 'aws', 'azure', 'gcp', 'jira', 'manual'
  carregado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP()  -- auditoria de quando foi inserido
)
PARTITION BY DATE_TRUNC(PARSE_DATE('%Y-%m', competencia), MONTH)
OPTIONS (description = 'Custos consolidados por mês, categoria, produto e provedor');

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
-- 4. Tabela de MÉTRICAS DE NEGÓCIO (usuários, contratos) — entrada manual ou futura API
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `relatorio_pt.metricas_negocio` (
  competencia      STRING NOT NULL,  -- 'YYYY-MM'
  usuarios_ativos  INT64,
  contratos_ativos INT64,
  carregado_em     TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
OPTIONS (description = 'Usuários e contratos ativos por mês, para KPIs de eficiência');

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
-- 6. VIEW de KPIs de eficiência (custo por usuário / contrato)
--    Faz o cruzamento que na planilha exigia "blend". Aqui é um JOIN simples.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW `relatorio_pt.vw_eficiencia` AS
SELECT
  c.competencia,
  c.custo_total_brl,
  m.usuarios_ativos,
  m.contratos_ativos,
  SAFE_DIVIDE(c.custo_total_brl, m.usuarios_ativos)  AS custo_por_usuario,
  SAFE_DIVIDE(c.custo_total_brl, m.contratos_ativos) AS custo_por_contrato,
  SAFE_DIVIDE(m.usuarios_ativos, m.contratos_ativos) AS usuarios_por_contrato
FROM `relatorio_pt.vw_custo_mensal` c
LEFT JOIN `relatorio_pt.metricas_negocio` m
  ON c.competencia = m.competencia;

-- ============================================================================
-- Pronto. No Looker, conecte:
--   - relatorio_pt.custos          (gráficos de custo)
--   - relatorio_pt.entregas        (entregas)
--   - relatorio_pt.vw_eficiencia   (KPIs de custo por usuário/contrato — SEM blend!)
-- O JOIN que exigia blend na planilha agora vive na view. Muito mais simples.
-- ============================================================================
