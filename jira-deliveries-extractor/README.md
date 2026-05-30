# Jira Deliveries Extractor — Relatório Mensal P&T (Dimastec)

Extrai as entregas do mês do Jira Cloud, **agrupadas por épico**, mapeia cada uma para
um produto (Faceum / Mydhas / AI / Integração) e gera um CSV no formato da aba
`entregas` do relatório.

## Abordagem

- Base = issues marcadas **Done / Concluído** no mês (status alterado para Done no período)
- Agrupa por **épico pai** — o épico é a "entrega" que CEO/CFO entendem
- Produto vem da **project key** da issue (Faceum tem vários projetos; Mydhas tem um só)
- `status` = Entregue se o épico está Done, senão Em progresso
- Issues sem épico viram uma linha "Entregas avulsas" por produto

## Pré-requisitos

- Python 3.10+
- `requests` (`pip install -r requirements.txt`)
- Jira Cloud (atlassian.net)
- Um **API token** do Jira

## Gerar o API token

1. Acesse **id.atlassian.com → Security → API tokens** (ou id.atlassian.com/manage-profile/security/api-tokens)
2. **Create API token** → dê um nome (ex.: "relatorio-pt") → copie o token
3. O token é como uma senha — não comite, não compartilhe

## Configurar (variáveis de ambiente)

```bash
export JIRA_BASE_URL="https://dimastec.atlassian.net"   # sua URL Jira Cloud
export JIRA_EMAIL="voce@dimastec.com.br"                # e-mail da sua conta Atlassian
export JIRA_API_TOKEN="seu_token_aqui"
```

> Dica: coloque num arquivo `.env` (NÃO comitado) e carregue com `source .env`,
> ou exporte na sessão do terminal antes de rodar.

## Mapear os projetos (passo obrigatório)

Edite o `PROJECT_PRODUCT_MAP` no topo de `jira_extractor.py` com as suas project keys
(o prefixo dos tickets). Faceum terá várias; Mydhas uma só:

```python
PROJECT_PRODUCT_MAP = {
    "FAC":  "Faceum",
    "FACE": "Faceum",
    "INT":  "Integração",
    "MYD":  "Mydhas",
    "AI":   "AI",
}
```

Para descobrir as keys: no Jira, abra qualquer ticket — o prefixo antes do número é a key
(ex.: `FAC-1234` → key `FAC`). Ou em **Projects**, a coluna "Key".

## Uso

```bash
python jira_extractor.py --month 2026-05 --output entregas_2026-05.csv
```

## Saída

CSV no formato da aba `entregas`:

```
competencia,produto,titulo,impacto,status
2026-05,Faceum,Rate limiting multi-camada,8 issue(s) concluída(s) no mês,Entregue
2026-05,Mydhas,Devolução de EPI,3 issue(s) concluída(s) no mês,Em progresso
```

> A coluna **`impacto`** sai com um placeholder ("N issues concluídas"). **Refine
> manualmente** antes de apresentar — troque por uma frase de impacto de negócio
> (o que a entrega resolve/habilita). É o que torna o relatório executivo, não técnico.

## Limitações / notas

- O JQL usa `status changed to ("Done","Concluído") DURING (...)`. Se seus status de
  conclusão têm outros nomes, ajuste `DONE_STATUSES` e o JQL.
- Issues cujo "parent" não é épico (ex.: subtarefa de story) são agrupadas pelo parent
  mesmo assim. Se sua hierarquia for diferente, me avise para ajustar.
- Read-only: o script só faz busca (search). Não altera nada no Jira.

## Estrutura

```
jira-deliveries-extractor/
├── jira_extractor.py     # script principal
├── requirements.txt
└── README.md
```
