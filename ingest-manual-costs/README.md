# Manual Cost Ingestor — Relatório Mensal P&T (Dimastec)

Carrega no `relatorio_pt.custos` as fontes de custo que **não têm API utilizável**:
folha (CLT/PJ/estágio), parceiros (Beonup, Danysoft, Gryfo), Atlassian, Excalidraw,
Azure do legado Saturno, etc. A entrada é um arquivo YAML por mês — sem parsing
de PDF, sem OCR.

## Por que YAML e não parser de PDF/planilha?

- Cada fornecedor formata a fatura do seu jeito. Parser quebra a cada mudança.
- Para ~6 linhas/mês, digitar leva menos tempo do que manter um parser.
- O YAML versionado é o **registro auditável** do que foi carregado — clareza
  total para CFO/contabilidade questionar uma linha.
- Os PDFs continuam guardados na mesma pasta (`manual-invoices/<mês>/`) como
  evidência; o script só não os parseia.

## Estrutura de pastas

```
manual-invoices/                  (gitignored — contém valores reais)
├── 2026-05/
│   ├── manifest.yaml             ← fonte da verdade para o BigQuery
│   ├── gryfo-fatura-abril.pdf    ← evidência (não parseada)
│   ├── danysoft-nf-122.pdf
│   ├── folha-clt-abril.xlsx
│   └── ...
├── 2026-06/
│   ├── manifest.yaml
│   └── ...
```

> A pasta `manual-invoices/` está no `.gitignore` porque carrega valores reais.
> Mantenha local; o BigQuery é a fonte para análise.

## Como começar um mês novo

```bash
# 1. Crie a pasta do mês e copie o template
mkdir -p manual-invoices/2026-05
cp ingest-manual-costs/manifest.yaml.example manual-invoices/2026-05/manifest.yaml

# 2. Edite o YAML: preencha valor_brl, ajuste itens, adicione/remova linhas
$EDITOR manual-invoices/2026-05/manifest.yaml

# 3. Valide sem tocar no BigQuery
python3 ingest-manual-costs/ingest_manual.py --month 2026-05 --dry-run

# 4. Carregue no BigQuery
python3 ingest-manual-costs/ingest_manual.py --month 2026-05 \
    --bq-project executive-reports-cpto
```

## Formato do manifest

Lista YAML. Cada item vira uma linha em `relatorio_pt.custos`.

```yaml
- categoria: Fornecedor de Produto       # obrigatório
  produto: Faceum                        # obrigatório
  item: "Gryfo - reconhecimento facial"  # obrigatório
  valor_brl: 80000.00                    # obrigatório, > 0
  fonte: manual-gryfo                    # obrigatório
  cloud_provedor: null                   # opcional (só p/ Cloud)
```

### Valores aceitos (warning se diferente)

| Campo | Valores conhecidos |
|-------|--------------------|
| `categoria` | `Time`, `Cloud`, `Ferramentas`, `Parceiros/Operação`, `Fornecedor de Produto` |
| `produto`   | `Faceum`, `Mydhas`, `AI`, `Integração`, `Compartilhado`, `Saturno` |

Valor desconhecido **só dá warning** — pode aparecer categoria/produto novo
sem mudar o script. Mas confirme que não é typo antes de carregar.

### Convenção de `fonte`

Use **um `fonte` por fornecedor/bucket de folha**. Exemplos:

- `manual-gryfo`, `manual-danysoft`, `manual-beonup`
- `manual-folha-clt`, `manual-folha-pj`, `manual-folha-estagio`
- `manual-atlassian`, `manual-excalidraw`, `manual-azure-saturno`

Por quê: o `replace_month` deleta+insere apenas as linhas com aquele `fonte`.
Editar só o Gryfo e re-rodar **não** mexe nas linhas do Danysoft, AWS, GCP ou
qualquer outra fonte do mesmo mês.

## Idempotência

Rodar o mesmo mês duas vezes:

- Para cada `fonte` PRESENTE no YAML: as linhas existentes daquela `fonte` no
  mês são deletadas e recriadas.
- Para `fonte`s NÃO presentes no YAML: nada é tocado.

⚠️ Consequência prática: se você **remover** uma linha do YAML (digamos, tirou
`manual-gryfo` e re-rodou), as linhas do Gryfo **continuam** no BigQuery porque
o `fonte` não está mais no arquivo. Para **apagar de fato**, ou:

1. Deixe o `fonte` no YAML com `valor_brl: 0.01` e edite no Looker para ignorar
   (gambiarra)
2. Rode `DELETE FROM relatorio_pt.custos WHERE competencia='2026-05' AND fonte='manual-gryfo'`
   direto no BigQuery (jeito correto).

## Parâmetros

| Flag | Obrigatório | Descrição |
|------|:-----------:|-----------|
| `--month` | ✅ | Mês no formato `YYYY-MM` |
| `--invoices-dir` | — | Pasta raiz dos manifests (default: `manual-invoices`) |
| `--manifest` | — | Caminho explícito; sobrescreve `--invoices-dir/<month>/manifest.yaml` |
| `--bq-project` | — | Project id GCP para carga; sem ele, modo validate-only |
| `--bq-dataset` | — | Dataset (default: `relatorio_pt`) |
| `--dry-run` | — | Valida + sumariza, não carrega no BigQuery |

## Saída

Log com resumo agregado por `fonte` e por `categoria`:

```
--- Summary (R$) ---
  By fonte:
    manual-folha-clt                  150000.00
    manual-gryfo                       80000.00
    manual-danysoft                    12700.00
    ...
  By categoria:
    Time                              180000.00
    Fornecedor de Produto              80000.00
    Parceiros/Operação                 17700.00
    ...
  TOTAL                                277700.00
```

Nada é gravado em CSV — o YAML já é o registro humano. O BigQuery é o registro
analítico.

## Estrutura

```
ingest-manual-costs/
├── ingest_manual.py        # script principal
├── bq_loader.py            # cópia do loader compartilhado
├── requirements.txt
├── manifest.yaml.example   # template para copiar a cada mês
└── README.md
```
