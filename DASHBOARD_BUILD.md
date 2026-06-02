# Construindo o Dashboard — Relatório Mensal P&T (Dimastec)

Guia clique-a-clique para montar as 4 páginas que o CEO pediu, usando o que
está em `relatorio_pt` no BigQuery. Estimativa: **1 h** se for a primeira vez
no Looker Studio, **20 min** com prática.

> Pré-requisito: o mês alvo (ex.: 2026-05) tem que estar carregado.
> Rode `python3 close_month.py --month 2026-05 --usd-brl-rate 5.04` antes.

---

## Setup (uma vez)

### 1. Criar relatório novo

1. Acesse [lookerstudio.google.com](https://lookerstudio.google.com)
2. **+ Create → Report**
3. Renomeie no topo para "**Relatório Mensal P&T — Dimastec**"

### 2. Conectar as 4 fontes de dados do BigQuery

Em **Resource → Manage added data sources → ADD A DATA SOURCE → BigQuery**, adicione
**uma por uma** estas 4 fontes (a do dataset `relatorio_pt`, projeto `executive-reports-cpto`):

| Fonte (Looker) | View/Tabela BQ | Para que serve |
|---|---|---|
| `Custos com cotação` | `vw_custos_com_cotacao` | Custos detalhados + cotação por linha |
| `Entregas` | `entregas` | Aba Resultados (páginas 1 e 2) |
| `KPIs eficiência` | `vw_eficiencia` | Página 3 — custo/usuário, custo/contrato |
| `Cotação USD/BRL` | `cotacoes` | Header com a taxa do mês |

Para cada uma:
- Marque "**Use Project's billing account**" (custo de query fica no projeto)
- **CONNECT → ADD TO REPORT**

### 3. Campo calculado `mes` (eixo de data) em cada fonte

Para cada uma das 4 fontes (botão **Resource → Manage added data sources → Edit**),
clique **+ ADD A FIELD** e crie:

| Campo | Fórmula |
|---|---|
| **mes** | `PARSE_DATE("%Y-%m", competencia)` |

Salve. Esse campo vira tipo **Date** — é o eixo X de todos os gráficos
temporais e a chave do filtro de período.

### 4. Filtro de período no nível do relatório

No menu superior do relatório, **Add a control → Date range control**, posicione
na lateral ou no topo. Em **Data → Default date range** escolha **Last 30 days**
ou **This month** (você ajusta depois). Isso liga em TODOS os gráficos que usem `mes`.

---

## Página 1 — Visão Geral (mensal)

Renomeie a página: clique no nome no menu lateral → "**1. Visão Geral**".

### Layout sugerido

```
┌───────────────────────────────────────────────────────────────┐
│  Relatório Mensal P&T — Dimastec        [filtro de período]  │
│  Maio/2026 · USD/BRL = 5,04                                  │
├──────────────┬──────────────┬──────────────┬──────────────────┤
│ Custo Total  │ Entregas     │ Issues       │ Cotação USD/BRL  │
│ R$ 389k      │ 20 epics     │ 89 issues    │ 5,04             │
├──────────────┴──────────────┴──────────────┴──────────────────┤
│  Custo por categoria        │  Custo por produto              │
│  (barras horizontais)        │  (barras verticais)             │
├──────────────────────────────┴──────────────────────────────┤
│  Custo total mês a mês (linha)                              │
└──────────────────────────────────────────────────────────────┘
```

### Gráficos passo a passo

**Header — Cotação USD/BRL (scorecard)**
- **Add a chart → Scorecard**
- Data source: `Cotação USD/BRL`
- Metric: `taxa` (mude o tipo de agregação para "Average" se aparecer SUM)
- Style: número grande, "USD/BRL" como rótulo
- Filter do scorecard: `par = "USD/BRL"` (Add filter → Include → par contains USD)

**Scorecard — Custo Total**
- **Add a chart → Scorecard**
- Data source: `Custos com cotação`
- Metric: `valor_brl` (SUM)
- Format: **Currency → BRL**
- Label: "Custo total do mês"

**Scorecard — Entregas (count de épicos)**
- Data source: `Entregas`
- Metric: **Record count** (default)
- Label: "Entregas"

**Scorecard — Issues entregues**
- Data source: `Entregas`
- Metric: `n_issues` (SUM)
- Label: "Issues entregues"

**Gráfico — Custo por categoria (barras horizontais)**
- **Add a chart → Bar → Horizontal bar**
- Data source: `Custos com cotação`
- Dimension: `categoria`
- Metric: `valor_brl` (SUM, formato BRL)
- Sort: por métrica desc

**Gráfico — Custo por produto (barras verticais)**
- **Add a chart → Bar → Column**
- Data source: `Custos com cotação`
- Dimension: `produto`
- Metric: `valor_brl` (SUM)
- Sort: por métrica desc

**Tendência — Custo mês a mês (time series)**
- **Add a chart → Time series**
- Data source: `Custos com cotação`
- Dimension (X): `mes`
- Metric: `valor_brl` (SUM)
- Style: linha + ponto
- Em maio você vê só 1 ponto (o mês atual). Quando entrar junho aparece a evolução.

---

## Página 2 — Entregas do mês (detalhe)

Adicione página: **Insert → New page** (ou +) → renomeie para "**2. Entregas**".

### Layout

```
┌──────────────────────────────────────────────────────────────┐
│  Entregas em Maio/2026                                       │
├──────────────────────────────────────────────────────────────┤
│  Por produto: [Faceum 14] [Mydhas 6]   (scorecards)         │
├──────────────────────────────────────────────────────────────┤
│  Tabela: produto | título do épico | n_issues | impacto     │
│  (sortável, filtra por produto via control)                 │
└──────────────────────────────────────────────────────────────┘
```

### Gráficos

**Scorecards por produto (2)**
- **Add a chart → Scorecard**
- Data source: `Entregas`
- Metric: Record count
- Filter no scorecard: `produto = Faceum` (1º) ou `produto = Mydhas` (2º)

**Tabela detalhada**
- **Add a chart → Table**
- Data source: `Entregas`
- Dimensions: `produto`, `titulo`, `impacto` (nessa ordem para leitura)
- Metric: `n_issues` (SUM)
- Sort: `n_issues` desc
- Style: linhas zebradas, coluna `impacto` com largura maior (vai ter texto longo)

**Filtro de produto (control)**
- **Add a control → Drop-down list**
- Data source: `Entregas`
- Control field: `produto`
- Conecta automaticamente em todos os gráficos da página

> A coluna `impacto` agora vem preenchida automaticamente com
> `<description do épico>. Entregas no mês: <lista de issues>`. Se algum
> épico ficar genérico (description vazia + issues técnicas), edite via
> SQL — veja MONTHLY_CLOSE.md passo 6.

---

## Página 3 — KPIs de Eficiência

Adicione página: "**3. Eficiência**".

> Esta é a página do CFO. O insight é a **variação mês a mês** de
> custo_por_usuario — vale repetir quando tiver março/abril carregados.

### Layout

```
┌──────────────────────────────────────────────────────────────┐
│  KPIs de Eficiência                                         │
├──────────────────────────────────────────────────────────────┤
│  FACEUM                       │  MYDHAS                     │
│  Custo  R$ 189.430,23        │  Custo  R$ 92.990,58       │
│  Usuários ativos  349.772    │  Usuários ativos  328       │
│  Contratos ativos  816       │  Contratos ativos  28       │
│  R$/usuário  R$ 0,54         │  R$/usuário  R$ 283,51      │
│  R$/contrato R$ 232,14       │  R$/contrato R$ 3.321,09    │
├──────────────────────────────────────────────────────────────┤
│  Comparativo R$/usuário      │  Comparativo R$/contrato    │
│  (bar chart, Faceum vs       │  (bar chart, mesma ideia)   │
│   Mydhas)                    │                              │
├──────────────────────────────────────────────────────────────┤
│  Tendência R$/usuário (time series) — só fica útil quando   │
│  tiver mar/abr/mai carregados                               │
└──────────────────────────────────────────────────────────────┘
```

### Gráficos

**Scorecards por produto (5 × 2 = 10 scorecards)**

Para Faceum, crie 5 scorecards, cada um com data source `KPIs eficiência` e filtro
`produto = Faceum`:

| Scorecard | Métrica | Formato |
|---|---|---|
| Custo Faceum | `custo_produto_brl` | BRL |
| Usuários ativos | `usuarios_ativos` | Number |
| Contratos ativos | `contratos_ativos` | Number |
| R$/usuário | `custo_por_usuario` | BRL |
| R$/contrato | `custo_por_contrato` | BRL |

Repita 5× para Mydhas com filtro `produto = Mydhas`. Coloque em duas colunas.

> Atalho: depois de criar os 5 de Faceum, **selecione → Ctrl+C → Ctrl+V**,
> mude só o filtro para Mydhas no novo grupo.

**Comparativo R$/usuário (column chart)**
- **Add a chart → Bar → Column**
- Data source: `KPIs eficiência`
- Dimension: `produto`
- Metric: `custo_por_usuario` (Average — não SUM, pois é razão)
- Filter no gráfico: `produto IN (Faceum, Mydhas)` (exclui Compartilhado/Saturno/AI/Integração que estão sem usuários)

**Comparativo R$/contrato**
- Igual o anterior mas com métrica `custo_por_contrato`

**Tendência R$/usuário (time series)**
- **Add a chart → Time series**
- Dimension (X): `mes`
- Metric: `custo_por_usuario`
- Breakdown dimension: `produto` (uma linha por produto)
- Hoje aparece só 1 ponto. Vai virar tendência quando mar/abr/mai estiverem todos carregados.

**Combo (avançado) — Custo total subindo, custo unitário caindo**
- **Add a chart → Combo chart**
- Dimension: `mes`
- Metric 1 (barras): `custo_produto_brl` (SUM)
- Metric 2 (linha, eixo direito): `custo_por_usuario` (Average)
- Filter: `produto = Faceum` (mais clean assim)
- Narrativa: "investimos mais no total, mas cada usuário custa menos" — só vira história com 2+ meses.

---

## Página 4 — Detalhamento Ferramentas × Ambiente × Produto

Adicione página: "**4. Detalhamento**".

> Aqui o CEO quer ver "banco mydhas, banco faceum, ecs mydhas, ecs faceum, etc".
> Hoje a granularidade de AWS é por (produto × ambiente) — não por serviço (RDS,
> ECS, EC2). Para fechar 100% o pedido seria necessário estender o
> `get-aws-costs/extractor.py` para incluir `Service` no `GroupBy` do Cost
> Explorer. Por enquanto montamos com o que temos: produto × cloud_provedor × item.

### Layout

```
┌──────────────────────────────────────────────────────────────┐
│  Filtros: [categoria] [produto] [cloud_provedor]            │
├──────────────────────────────────────────────────────────────┤
│  Tabela detalhada:                                          │
│  produto | categoria | cloud_provedor | item | R$           │
│  (com tooltip USD × taxa = BRL nas linhas USD-source)       │
├──────────────────────────────────────────────────────────────┤
│  Heatmap: produto × item   (treemap visual)                 │
└──────────────────────────────────────────────────────────────┘
```

### Gráficos

**3 filtros (controls)**
- **Add a control → Drop-down list**, repita para `categoria`, `produto`, `cloud_provedor`
- Data source: `Custos com cotação` (em cada)

**Tabela detalhada**
- **Add a chart → Table**
- Data source: `Custos com cotação`
- Dimensions: `produto`, `categoria`, `cloud_provedor`, `item`
- Metrics: `valor_brl` (SUM), `valor_usd` (SUM), `taxa_usd_brl` (AVG)
- Style: linhas zebradas; coluna USD com formato `$0.00`; BRL com `R$ 0,00`
- Mostrar `valor_usd` só faz sentido em linhas USD-source. Para BRL-native a coluna fica `NULL` (Looker mostra como `-`)

**Treemap — produto × item**
- **Add a chart → Tree map**
- Hierarchy dimensions: `produto` → `item`
- Metric: `valor_brl` (SUM)
- Color: por `categoria`
- Bom pra "ver visualmente quem custa mais por produto"

---

## Header com cotação (todas as páginas)

Para repetir o "USD/BRL = 5,04" no header de todas as páginas:

1. Crie o scorecard de cotação na página 1 (já feito acima)
2. **Selecione o scorecard → botão direito → "Make report-level"**
3. Ele aparece em todas as páginas automaticamente

Faça o mesmo com o controle de período de data.

---

## Estilo e cores

- Paleta dos produtos (sugestão de cores consistentes ao longo do dash):

  | Produto | Cor sugerida (hex) |
  |---|---|
  | Faceum | `#FB923C` (laranja) |
  | Mydhas | `#3B82F6` (azul) |
  | AI | `#A855F7` (roxo) |
  | Integração | `#64748B` (cinza) |
  | Compartilhado | `#94A3B8` (cinza claro) |
  | Saturno | `#9CA3AF` (cinza chumbo — legado) |

  Em cada gráfico, **Style → Color by dimension → produto** e aplique manualmente.

- Cabeçalho com logo: **Insert → Image** → upload do logo Dimastec
- Fonte do dashboard: **Theme → Custom → Font → "Roboto"** (legível em projeção)
- Background dark vs light: dashboards para CEO/CFO funcionam bem em **light** com cabeçalho em azul-escuro

---

## Checklist final antes de mandar

- [ ] Filtro de período funciona em todas as páginas
- [ ] Cotação aparece no header de todas as páginas
- [ ] Coluna `impacto` em entregas revisada (auto-populada pelo extractor; refine manualmente só os que ficarem vagos)
- [ ] Em página 3, Faceum e Mydhas têm valores em todos os 5 scorecards
- [ ] Compartilhado / Saturno / AI mostram NULL nos KPIs de eficiência (esperado — não têm usuário próprio)
- [ ] Cores consistentes por produto
- [ ] Logo no cabeçalho
- [ ] Permissão de visualização: compartilhar com CEO/CFO em modo "**View**" (não Edit)

---

## Próximos passos (futuro)

1. **Backfill mar/abr** para o KPI de tendência fazer sentido — precisa rodar
   AWS/GCP/Atlas/Folha/etc. de cada mês histórico. Trabalho moderado.
2. **Estender AWS extractor com SERVICE** para o CEO ver "banco/ecs por produto"
   na página 4 com a granularidade exata que ele descreveu. Mudança no
   `fetch_cost_by_account_and_tag` em `get-aws-costs/extractor.py:146`.
3. **Métricas de negócio mensais** automatizadas — hoje é YAML manual; se
   tiver API que entrega usuários/contratos ativos, vira mais um extractor.
4. **Rateio de Compartilhado** — view `vw_eficiencia_com_rateio` que aloca os
   custos `Compartilhado` proporcionalmente em Faceum/Mydhas para o KPI de
   custo unitário ficar 100% atribuído.
