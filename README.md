# TrustLensAI Agent 7

Assurance revalidation for a registered product.

**Agent work** is only: given a prompt or corpus change, call the LLM to find
the affected ValidAIte RMTE risks, then map metrics from those risks. IDs must
come from the RMTE catalog; the model cannot invent risks or metrics.

The agent reads **risks and metrics from the RMTE spine** in Postgres
(`RMTE_DATABASE_URL`). It still uses the product fixture only for registry
versions, seed changes, and simulated evaluation. If the spine is unreachable,
it falls back to the bundled fixture catalog.

Copy `.env.example` to `.env`. Set `GROQ_API_KEY` for the agent LLM call and
`RMTE_DATABASE_URL` for the spine. `RMTE_SPINE_DISABLE=1` forces the fixture
catalog.

**Not agent work:** mark those metrics stale, send the stale metrics into an
evaluation run, then compare the new scores with the previous evidence pack.
A human must still approve or reject the pack.

## Journey

1. Agent — change → RMTE risks → mapped metrics
2. Pipeline — stale those metrics
3. Pipeline — run evaluation from the stale metric set
4. Pipeline — compare new scores to past scores
5. Human approval — required on every run

## Run locally

```powershell
cd agent7
python -m pip install -e ".[dev]"
python -m uvicorn agent7.api:app --reload
```

Open `http://127.0.0.1:8000/`.

Copy `.env.example` to `.env` and set `GROQ_API_KEY` so the agent can call Groq.
If the key is missing, mapping uses the deterministic RMTE table.

## Tests

```powershell
python -m pytest
python -m ruff check .
python -m mypy agent7
```
