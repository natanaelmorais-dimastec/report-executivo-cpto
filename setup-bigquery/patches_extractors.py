"""
Patches para adaptar os extractors existentes (AWS e Jira) a escreverem no BigQuery.

Não reescreve os scripts — mostra exatamente o que ADICIONAR em cada um. A saída CSV
continua funcionando; o BigQuery vira uma saída adicional (--bq), então você pode rodar
dos dois jeitos durante a transição.

Aplique copiando os trechos indicados para os respectivos arquivos.
"""

# ============================================================================
# PATCH 1 — extractor.py (AWS cost)
# ============================================================================

# (a) No topo, junto dos outros imports, adicione:
#
#     from bq_loader import BigQueryLoader
#
#     (coloque o bq_loader.py na mesma pasta, ou ajuste o PYTHONPATH)

# (b) No argparse (função main), adicione dois argumentos:
#
#     parser.add_argument("--bq-project", default=None,
#                         help="GCP project id para carregar no BigQuery (opcional)")
#     parser.add_argument("--bq-dataset", default="relatorio_pt",
#                         help="BigQuery dataset")

# (c) DEPOIS de `write_csv(...)` em main(), adicione o bloco de carga BQ.
#     As linhas do AWS já têm competencia, produto, valor_brl. Precisamos mapear
#     para o schema da tabela `custos` (categoria='Cloud', cloud_provedor, item, fonte).

PATCH_AWS_LOAD = '''
    # --- Carga no BigQuery (opcional) ---
    if args.bq_project:
        from bq_loader import BigQueryLoader
        loader = BigQueryLoader(project_id=args.bq_project, dataset=args.bq_dataset)
        bq_rows = []
        for r in attributed:
            # mapear cloud_provedor a partir do ambiente
            ambiente = r["ambiente"]
            provedor = "Azure" if ambiente == "Azure" else ("GCP" if ambiente == "GCP" else "AWS")
            bq_rows.append({
                "competencia": r["competencia"],
                "categoria": "Cloud",
                "produto": r["produto"],
                "cloud_provedor": provedor,
                "item": ambiente,            # ex.: Produção, QA
                "valor_brl": float(r["valor_brl"]),
                "fonte": "aws",
            })
        # agrupa por competencia e substitui mês a mês (idempotente)
        meses = sorted({r["competencia"] for r in bq_rows})
        for mes in meses:
            mes_rows = [r for r in bq_rows if r["competencia"] == mes]
            loader.replace_month("custos", competencia=mes, rows=mes_rows)
'''

# ============================================================================
# PATCH 2 — jira_extractor.py (entregas)
# ============================================================================

# (a) No topo:
#     from bq_loader import BigQueryLoader

# (b) No argparse:
#     parser.add_argument("--bq-project", default=None, help="GCP project id (opcional)")
#     parser.add_argument("--bq-dataset", default="relatorio_pt")

# (c) DEPOIS de write_csv(...) em main(). As linhas de entrega têm produto, titulo,
#     status, n_issues. Mapeamos para a tabela `entregas`.

PATCH_JIRA_LOAD = '''
    # --- Carga no BigQuery (opcional) ---
    if args.bq_project:
        from bq_loader import BigQueryLoader
        loader = BigQueryLoader(project_id=args.bq_project, dataset=args.bq_dataset)
        bq_rows = []
        for r in rows:  # `rows` é o resultado de group_by_epic
            bq_rows.append({
                "competencia": args.month,
                "produto": r["produto"],
                "titulo": r["titulo"],
                "impacto": r.get("impacto", ""),
                "status": r["status"],
                "n_issues": int(r.get("n_issues", 0)),
            })
        loader.replace_month("entregas", competencia=args.month, rows=bq_rows)
'''

# ============================================================================
# COMO USAR depois de aplicar os patches
# ============================================================================
#
#   # AWS — gera CSV E carrega no BigQuery
#   python extractor.py --month 2026-05 --usd-brl-rate 5.04 \
#       --audit-sa tag_audit.csv --audit-ue tag_audit_useast.csv \
#       --bq-project SEU_PROJETO_GCP
#
#   # Jira — gera CSV E carrega no BigQuery
#   python jira_extractor.py --month 2026-05 --bq-project SEU_PROJETO_GCP
#
# Sem --bq-project, os scripts funcionam como antes (só CSV). Com ele, carregam
# também no BigQuery. Transição sem risco: você testa o BQ sem perder o fluxo atual.

if __name__ == "__main__":
    print("Este arquivo é um guia de patches, não um script executável.")
    print("Copie os blocos PATCH_AWS_LOAD e PATCH_JIRA_LOAD para os respectivos extractors.")
