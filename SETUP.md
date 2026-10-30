# Setup and configuration

Start with the [README](README.md) for the assignment overview, demo, architecture, and evaluation results. This page contains the detailed runtime options and troubleshooting.

## Run with Docker Compose

Requires Docker Engine/Desktop with the Compose plugin. Run these commands from the repository root:

```bash
cp .env.example .env
docker compose up --build -d --wait
```

Open <http://localhost:8000> for the buyer workspace, <http://localhost:8000/docs> for the API schema, or <http://localhost:8000/health> for process health. If port 8000 is occupied, set `PORT=8020` in `.env` and use that port instead.

The default `AGENT_PROVIDER=scripted` runs a repeatable demo without Ollama, credentials, or model downloads. It executes the real LangChain/LangGraph workflow, tools, purchasing policy, SQLite transactions, approval, and supplier validation using a deterministic model double. The UI labels this mode; it does not demonstrate LLM reasoning. **Run DeepEval still needs a real judge**, even in scripted mode; configure Ollama before using that button.

```bash
docker compose logs -f app              # server/model errors
docker compose ps                      # app should become healthy
docker compose down                    # stop; keep purchasing data
```

The image serves both FastAPI and the static frontend; no Node build or external database is required. It installs runtime dependencies from `uv.lock` and runs as a non-root user. Compose stores SQLite and CLI evaluation reports in the `purchasing_data` volume at `/app/.data`. Rebuilding or restarting preserves purchases and budgets. Use the UI's reset control to restore one scenario. `docker compose down -v` deletes **all Compose purchasing data and Ollama models/authentication** for this project.

### Run the real agent with containerized Ollama

Start the optional model service and provision the model:

```bash
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama signin
docker compose exec ollama ollama pull gpt-oss:20b-cloud
```

Complete the sign-in link printed by Ollama. The example model is cloud hosted: it requires an Ollama account and network access. Its authentication stays in the `ollama_data` volume, outside Git and the app image. For an entirely local model, pull a tool-capable model supported by your machine instead and use its exact name for both model variables below; cloud sign-in is then unnecessary.

Set these values in `.env`:

```dotenv
AGENT_PROVIDER=ollama
AGENT_MODEL=gpt-oss:20b-cloud
DEEPEVAL_JUDGE_MODEL=gpt-oss:20b-cloud
DOCKER_OLLAMA_BASE_URL=http://ollama:11434
```

Then recreate the application so it reads the changed environment:

```bash
docker compose --profile ollama up -d --wait
```

Wait for the pull to finish before investigating. `/health` reports the selected provider, but does **not** probe model availability or cloud authentication. Inspect `docker compose logs app` and `docker compose logs ollama` if a run returns an unavailable recommendation.

### Use an existing Ollama service

Leave the `ollama` profile off and set `DOCKER_OLLAMA_BASE_URL` to a URL reachable **from the app container**. For Ollama on the Docker host, use `http://host.docker.internal:11434`; the host service must listen on a container-reachable interface and the firewall must permit it. Docker maps `host.docker.internal` through `host-gateway`. A host Ollama listener bound only to `127.0.0.1` may need reconfiguration. Alternatively, use the containerized service above.

`127.0.0.1` inside the app container means the app container itself. Native Python uses `OLLAMA_BASE_URL`; Compose deliberately uses `DOCKER_OLLAMA_BASE_URL` to avoid mixing the two addresses. After any `.env` change, run `docker compose up -d` to recreate affected containers.

## Run directly with Python

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Dependencies are pinned in `uv.lock`.

```bash
cp .env.example .env
uv sync --locked
uv run python -m backend.server
```

Open <http://127.0.0.1:8000>. This uses the same scripted default. To run a live agent against a host Ollama installation:

```bash
ollama signin                          # required only for a cloud model
ollama pull gpt-oss:20b-cloud
AGENT_PROVIDER=ollama uv run python -m backend.server
```

Ollama must already be serving at `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`). Start its application/service or `ollama serve` if needed. Native state is `.data/purchasing-v2.sqlite3`, separate from the Docker volume.

### Configuration

Copy `.env.example` to `.env` and edit it. No application API key is required. Do not commit `.env` or model credentials.

| Variable | Purpose / example |
|---|---|
| `AGENT_PROVIDER` | `scripted` for the offline demo; `ollama` for real model reasoning. The example/Compose default is scripted; Python without an `.env` defaults to Ollama. |
| `AGENT_MODEL` | Ollama agent model; default `gpt-oss:20b-cloud`. Must support tool calls. |
| `OLLAMA_BASE_URL` | Native Python model/judge endpoint; default `http://127.0.0.1:11434`. |
| `DOCKER_OLLAMA_BASE_URL` | Compose model/judge endpoint; default `http://ollama:11434`. |
| `DEEPEVAL_JUDGE_MODEL` | Judge supporting structured output; defaults to `AGENT_MODEL` if unset. Used for every DeepEval run. |
| `HOST` | Native bind address, default `127.0.0.1`. Docker always uses `0.0.0.0` internally. |
| `PORT` | Native port or Compose host port, default `8000`; Docker's internal port is always `8000`. |
| `DEEPEVAL_TELEMETRY_OPT_OUT` | `YES` disables DeepEval telemetry. |

## Useful API endpoints

The interactive schema is available at <http://localhost:8000/docs>.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Process health and selected model provider. |
| `GET /api/cases` | Current purchasing situations. |
| `POST /api/agent/run` | Investigate a situation, for example `{"case_id":"supplier-shortfall"}`. |
| `POST /api/drafts/{id}/approve` | Approve with `{"approved":true,"outcome":"confirmed"}`. Mock outcomes also include `partial` and `rejected`. |
| `POST /api/drafts/{id}/reject` | Decline a pending draft without buying. |
| `GET /api/events` | Persisted audit history; optionally filter by `case_id`. |
| `POST /api/cases/{id}/reset` | Restore a demo fixture and invalidate old drafts. |
| `GET /api/evals` | Run the scripted evaluation suite with the configured real judge. |

## Save evaluation reports from Docker

Configure Ollama and the judge first, then run:

```bash
docker compose exec app python -m evaluations.run_eval --provider scripted --output .data/evals-scripted.json
docker compose exec app python -m evaluations.run_eval --provider ollama --output .data/evals-live.json
mkdir -p .data
docker compose cp app:/app/.data/evals-live.json .data/evals-live.json
```

Use `--cases review-modify supplier-rejection-feedback` to select evaluation IDs. Evaluations use temporary databases and leave the buyer workspace's budget unchanged. The CLI exits nonzero if any metric fails. Reports contain recommendations, expected terms, evidence traces, scores and reasons, model/provider, versions, and timestamps.

Generated JSON under `.data/` and `evaluations/reports/` is ignored by Git. The versioned inputs remain in `evaluations/dataset.json`.
