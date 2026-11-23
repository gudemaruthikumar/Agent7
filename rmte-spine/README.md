# RMTE spine

Governed **Risk → Metric → Test → Evidence** catalog for every TrustLensAI archetype.

Authoring source is the TLAI workbook set (`TLAI/TLAI/*.xlsx`), not the two Agent 5 JSON folders. Each `*_Logs_Layers_Formulas.xlsx` file has the same three sheets:

1. Logs ↔ Architecture Layers
2. Logs ↔ Formula Inputs
3. *Archetype* RMTE (Master) — the 44-column spine row

Postgres is the system of record (`RMTE_DATABASE_URL`). Local default is SQLite at `data/rmte_spine.sqlite`.

## Load from TLAI

```bash
cd TrustLensAI/rmte-spine
pip install -e .
python -m rmte_spine ingest
python -m rmte_spine list
python -m rmte_spine metric agent AP-01
python -m rmte_spine spine rag --metric CQ-01
```

Postgres:

```bash
docker compose up -d
# RMTE_DATABASE_URL=postgresql+psycopg://trustlens:trustlens@127.0.0.1:5432/rmte_spine
pip install -e ".[postgres]"
python -m rmte_spine ingest
```

## Where to read the libraries

Open the same twelve-file set Agent 5 already uses, one folder per archetype:

`TrustLensAI/rmte-spine/libraries/<archetype>/v1/`

Agent and RAG are the existing gold libraries. Every other TLAI archetype is built to that same shape (metrics, risks, formula inputs, layers, pattern matrix, category rules, scenario templates, test-case contract).

```bash
python -m rmte_spine export-libraries
```

Start at `libraries/INDEX.json`, then e.g. `libraries/chat_assistant/v1/chat_assistant_metrics.json`.

