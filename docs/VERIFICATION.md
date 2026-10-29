# Verification evidence

The application implements purchase recommendation review, supplier shortfall resolution, and demand-change handling through the real LangChain `create_agent` runtime. Supplier/ERP data and order responses are mocked. The live runs use ChatOllama with `gpt-oss:20b-cloud`; scripted runs use a separately labelled test model.

## Recorded checks

| Check | Result | Evidence |
|---|---|---|
| Pytest policy, database, LangChain graph, HTTP, evaluator and browser regression suite | 77 passed; no failures or errors | [`pytest.json`](../evaluations/reports/pytest.json) |
| DeepEval with the scripted LangChain graph | 14/14 scenarios passed, five metrics each | [`scripted.json`](../evaluations/reports/scripted.json) |
| DeepEval with the live Ollama agent | 13/14 passed every strict metric; 14/14 canonical decisions correct | [`live.json`](../evaluations/reports/live.json) |
| Real model plus GEval explanation judge | 3/3 core scenarios passed, six metrics each | [`judge.json`](../evaluations/reports/judge.json) |
| Actual browser → HTTP → live model → tools → buyer approval → mock supplier → validation | Passed; desktop and mobile; updated budget visible; no JavaScript errors | [`browser.json`](../evaluations/reports/browser.json) |
| Python lint, JavaScript syntax, dependency lock consistency and whitespace checks | Passed | Commands below |

The six automated Chromium journeys cover all three feature approvals, supplier rejection, a constrained purchase on mobile, buyer rejection, and the DeepEval results view. Those regression journeys use the scripted model; the additional recorded browser smoke uses the real Ollama model.

The model evaluations retain their actual tool calls, policy decisions, authorized quantities, action results, model explanations, metric scores and reasons, prompt hash, model/provider, policy/dataset versions, and elapsed time. Their timestamps identify the specific observed runs. These are demonstrations on 14 curated situations, rather than evidence of general reliability for arbitrary retail data. Judge scores are diagnostic and the 0.8 explanation threshold is not a calibrated production release gate.

## Live quality finding retained in the report

The latest live sweep has one strict quality failure: on `review-reject`, the model invented an unavailable `prepare_draft` tool with its own proposed quantity. LangChain rejected the unknown tool. No purchasing tool executed that call, no draft or order was created, and the model continued to the deterministic policy's correct `reject` decision. The custom safety/approval-discipline metric deliberately flags unexpected model tool attempts, even when enforcement blocks them. This failure is retained in `live.json`; the runner returned a nonzero exit code for that sweep.

All 14 canonical decisions matched their expected outcomes, and all action-feedback checks passed. This does not establish perfect tool-selection discipline or perfectly grounded explanations for arbitrary future model runs. The separate GEval run covers the three core feature explanations. A regression test drives an invented execution tool through the actual LangChain runtime and verifies that it is denied, captured in the model-call trace, and creates no order.

## Problems found and corrected during verification

- The first LangChain graph exhausted its recursion budget before draft preparation. The graph budget now accommodates the bounded workflow, while model and tool call limits remain enforced.
- Live model runs requested policy or draft tools at the wrong stage. Capability middleware now offers only stage-appropriate tools and supplies matching stage instructions. Tool implementations still enforce evidence completeness and decision-ID authorization independently.
- The model sometimes gave a final answer before completing evidence collection. A completion gate now requests bounded continuation, with at most two recoveries. Persistent incompleteness fails closed without an authorized draft.
- The first explanation evaluation omitted the actual draft and tool evidence, causing the judge to call supported details hallucinations. GEval now receives that authoritative context.
- A real explanation confused the current budget with the projected balance after a future purchase. The policy now supplies explicit before/after values and the prompt distinguishes them. Regression checks assert both balances; the explanation was reevaluated with the judge.
- Chromium found a reset/investigation race. Controls now stay locked until the operation finishes. The UI also refreshes committed incoming stock and budget after approval.
- Supplier validation was extracted from the mocked supplier API so a separate validator can reject changed price, supplier, quantity, arrival or cost terms. Mutation tests verify that unapproved terms do not debit the budget or create incoming commitments.

## Reproduce

```bash
uv sync --locked
uv run playwright install chromium
uv run pytest --junitxml=.data/pytest-results.xml
uv run ruff check backend evaluations tests
node --check frontend/app.js
uv lock --check
git diff --check
uv run python -m evaluations.run_eval --output .data/evals-scripted.json
uv run python -m evaluations.run_eval --provider ollama --output .data/evals-live.json
uv run python -m evaluations.run_eval --provider ollama --judge \
  --cases review-modify supplier-shortfall demand-spike \
  --output .data/evals-judge.json
```

The live commands require a running, authenticated Ollama service and the configured tool-capable model. Model outputs and judge scores can vary on reruns. DeepEval and LangSmith emit Python 3.14 deprecation warnings; these were dependency warnings, with no failed assertions. The local demo uses a mock buyer identity and mock supplier outcomes; it does not provide production authentication or a live ERP transaction boundary.
