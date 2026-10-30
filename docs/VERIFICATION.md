# Verification evidence

For current Docker and assignment checks, see the [assignment assessment](../ASSIGNMENT_ASSESSMENT.md). Generated report JSON is now ignored and untracked; historical paths below refer to local artifacts and may be absent from a fresh clone.

The current suite has 12 unit tests, 3 integration tests, and three evaluation
metrics; see [the current test assessment](../ASSIGNMENT_ASSESSMENT.md). Reports and results below cover
earlier suites and datasets, and do not validate the current evaluator.

On 2026-10-30, all 15 current pytest tests passed. A scripted run over all ten
initial and recovery answers passed tool efficiency and answer correctness with
a fixed test judge. That judge always returns a passing groundedness score, so
the run verifies evaluator wiring, not actual groundedness quality. The subsequent current live-agent/judge sweep matched 8/10 independent answers, passed tool efficiency on 9/10 and groundedness on 5/10, and passed all metrics on 4/10. Both model errors and judge grading errors were observed; see the linked assignment assessment for details.

The historical results below predate the independent agent recommendation and comparison flow. The current feedback verification is recorded separately here.

The application implements purchase recommendation review, supplier shortfall resolution, and demand-change handling through the real LangChain `create_agent` runtime. Supplier/ERP data and order responses are mocked. The live runs use ChatOllama with `gpt-oss:20b-cloud`; scripted runs use a separately labelled test model.

## Historical feedback implementation - 2026-10-30

Policy v2.2.0 and the previous evaluation dataset added persisted action feedback to the
independent reviewer's situation evidence. Failed supplier terms cannot authorize
another order until their version advances; other feasible suppliers remain
eligible and every new draft requires fresh buyer approval.

| Check | Observed result |
|---|---|
| Prior full pytest suite, including real Chromium and HTTP journeys | 128 passed, 0 failures/errors/skips |
| Prior scripted DeepEval | 14/14 passed all five metrics; three cases include a follow-up investigation |
| Live Ollama feedback contracts | 2/2 passed action-feedback checks; rejection and partial recovery both requested review with gaps of 110 and 35 |
| Live Ollama strict overall result | 1/2 passed every metric; one initial run attempted an invented evidence tool |
| Ruff, JavaScript syntax, dependency lock consistency and whitespace | Passed |

The live model was `gpt-oss:20b-cloud`. On the initial investigation for
`supplier-partial-feedback`, it attempted `get_forecast_and_observation`, which
does not exist. The runtime did not execute it. The model then read the real tools
and returned the correct initial decision. Both later recovery investigations
read the saved action feedback, returned `needs_review`, and produced no draft.
The strict evaluator still failed the original unauthorized tool attempt. No
explanation judge was run for this change; these checks do not establish general
explanation quality or perfect tool-selection discipline.

The checked-in summary, timestamps, metric results and live recovery explanations
are in `evaluations/reports/feedback.json`. Full local reports
are `.data/evals-feedback-scripted.json`, `.data/evals-feedback-live.json` and
`.data/feedback-pytest.xml`. Earlier report files were preserved.

The commands for the current suite are in [the current test assessment](../ASSIGNMENT_ASSESSMENT.md).

The code and file-naming assessment is in
`docs/code-design-review.md` (local design notes), including the remaining
execution/persistence coupling and model-runtime responsibilities.

## Historical recorded checks

| Check | Result | Evidence |
|---|---|---|
| Pytest policy, database, LangChain graph, HTTP, evaluator and browser regression suite | 77 passed; no failures or errors | `evaluations/reports/pytest.json` |
| DeepEval with the scripted LangChain graph | 14/14 scenarios passed, five metrics each | `evaluations/reports/scripted.json` |
| DeepEval with the live Ollama agent | 13/14 passed every strict metric; 14/14 canonical decisions correct | `evaluations/reports/live.json` |
| Real model plus GEval explanation judge | 3/3 core scenarios passed, six metrics each | `evaluations/reports/judge.json` |
| Actual browser → HTTP → live model → tools → buyer approval → mock supplier → validation | Passed; desktop and mobile; updated budget visible; no JavaScript errors | `evaluations/reports/browser.json` |
| Python lint, JavaScript syntax, dependency lock consistency and whitespace checks | Passed | Commands below |

The six automated Chromium journeys cover all three feature approvals, supplier rejection, a constrained purchase on mobile, buyer rejection, and the DeepEval results view. Those regression journeys use the scripted model; the additional recorded browser smoke uses the real Ollama model.

The model evaluations retain their actual tool calls, policy decisions, authorized quantities, action results, model explanations, metric scores and reasons, prompt hash, model/provider, policy/dataset versions, and elapsed time. Their timestamps identify the specific observed runs. These are demonstrations on 14 curated situations, rather than evidence of general reliability for arbitrary retail data. Judge scores are diagnostic and the 0.8 explanation threshold is not a calibrated production release gate.

## Historical live quality finding retained in the report

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

## Run the current suite

See [the current test assessment](../ASSIGNMENT_ASSESSMENT.md) for current commands, the judge requirement,
and the distinction between deterministic checks and judge scores.
