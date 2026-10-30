# Assignment implementation assessment

Reviewed on 2026-10-30 against the six-page **AI Purchasing Agent — Assignment** PDF supplied locally. The PDF permits mock APIs, databases and services, prescribes no stack, and requires **at least one scenario implemented end-to-end**. All four scenarios and production infrastructure are explicitly unnecessary.

## Verdict

**The core functional assignment is implemented.** The application investigates evidence, presents a purchasing decision and explanation, prepares an authorized draft, executes a buyer-approved mock purchase, validates the supplier response, and feeds failures into subsequent investigations. Three scenario families are implemented; shared budget, storage and delivery rules cover the fourth scenario's purchasing constraints.

This verdict concerns implemented behavior and the local checks below. It is not a claim that the live model is reliable across arbitrary purchasing situations. A current live sweep was completed, but only 4/10 answers passed every metric. Independent model advice and judge quality have demonstrated weaknesses, detailed below. Public repository visibility, submission URL and deadline compliance were not checked.

## Requirement mapping

| PDF requirement | Implementation and evidence | Assessment |
|---|---|---|
| Full-stack purchasing agent, rather than question answering (pp. 1, 6) | Buyer workspace in `frontend/`, FastAPI in `backend/server.py`, LangGraph workflow and LangChain tool-calling agent in `backend/agent/workflow.py`, SQLite in `backend/store.py`. | Implemented; running container tested over HTTP. |
| Investigate relevant information; do not assume recommendations are correct (pp. 1–3) | Four tools read situation/feedback, stock/open orders, demand, and supplier terms/budget. Evidence completion is required before policy authorization. The model returns an independent structured recommendation without seeing the policy result. | Implemented; scripted workflow and evidence gates tested. |
| Scenario 1: review the proposed 800 units (p. 2) | The fixture has a 120-unit gap after counting confirmed supply. Supplier minimum/pack rules produce a 150-unit draft, modifying the original suggestion. Policy supports accept, modify, reject, investigate and escalation outcomes as evidence changes. | End-to-end modify → approve → confirm → validate verified. Not every policy branch is a current golden. |
| Scenario 2: supplier supplies only 250 of 500 (p. 2) | Only 250 confirmed units count as incoming. With 120 on hand and a 480-unit target, the gap is 110; the supplemental order is rounded to 150. Existing coverage can instead produce no action; infeasible plans escalate. | End-to-end supplemental purchase verified. Original-order terms are supplied as mock operational data. |
| Scenario 3: changed demand with an existing order (pp. 2–3) | Uses the higher of forecast and recent sales only after at least three observation days, and requires demand evidence no older than 24 hours. The demand-spike fixture proposes 150 additional units. | End-to-end plan adjustment verified; stale-evidence behavior tested. |
| Scenario 4: constraints prevent buying (p. 3) | Policy enforces budget in cents, storage, supplier capacity/availability, minimum/pack rules, lead time, stockout timing and safety coverage. Budget/storage fixtures escalate without a draft. | Implemented as shared rules; both blocking fixtures verified over HTTP. |
| Choose appropriate tools, data, actions and human approval (p. 3) | Models can invoke only evidence tools. Application code computes draft terms; explicit `approved: true` triggers execution. Approval rechecks revision, policy and all authorized terms in a SQLite transaction. | Implemented; false/string approval denied, stale drafts rejected, replay spends once. |
| Validate the actual result and handle unexpected outcomes (p. 3) | `backend/supplier.py` separately validates quantity, price, supplier, cost and ETA. Store records actual commitments, feedback and a reassessed gap. Rejected/partial attempts remain unsuccessful validations and block retrying unchanged failed supplier terms. | Full, rejected and partial outcomes verified; unapproved-price rejection has a unit test. |
| Feedback loop, including when an initial action fails (pp. 3–4) | Next investigation reads structured `action_feedback`. For the shortfall fixture, rejection leaves a 110-unit gap and partial confirmation leaves 35. With the sole supplier's failed terms unchanged, both escalate without another executable draft. | Persisted rejection/partial recovery verified; no autonomous reordering. |
| Small scenario set evaluating appropriateness (p. 4) | Eight goldens, two follow-up investigations, and DeepEval metrics for tool efficiency, answer correctness, and groundedness. Unit/integration tests verify important execution and constraint contracts. | Implemented with limits described below. |
| Source, setup/run instructions, diagram, approach and mock services/data (p. 5) | README includes native/Docker setup, a Mermaid diagram, policy and feedback explanations, demo steps, configuration and evaluation commands. Fixtures and mock supplier are included. | Present; Docker image build and scripted Compose run verified. |
| Working demo and `.env.example` (p. 5) | Compose serves UI/API with durable SQLite; `.env.example` defaults to scripted and documents live-provider/judge configuration. Optional Ollama profile supports a real model. | Scripted container demo and a native live-agent evaluation verified. Optional containerized Ollama authentication/model provisioning was documented but not executed. |
| Public GitHub URL, intact history, no secrets, deadline (p. 5) | No history rewrite or commit was performed. `.env`, operational state, model credentials and generated evaluation reports are excluded from versioning/build context. | Public visibility, submission, deadline and a full repository-history secret audit are not verified. |

## Current checks

- All **15 current tests passed** (12 unit and 3 integration tests) on both host Python 3.14 and container Python 3.12. The host run disabled `pytest-rerunfailures` because its plugin opens a socket restricted by the sandbox. Container tests ran with the normal plugin. Ruff passed.
- All **10 scripted initial/recovery answers** passed tool efficiency and answer correctness on dataset v3.0.0. The evaluator used `FixedJudge` from the integration tests, which always passes groundedness. That score verifies evaluator wiring only and is **not evidence of actual groundedness**. The local report is `.data/evals-assignment-fixed-judge.json` (ignored).
- The Python 3.12 Docker image built from the frozen dependency lock, and the app became healthy under Compose as the non-root user. The running HTTP flow was checked for all three purchase scenarios, no-action and budget/storage blocks, strict buyer approval, stale drafts, buyer rejection, idempotent execution, rejected/partial supplier feedback and subsequent escalation.
- All **12 container smoke checks passed**, including preservation of purchase, budget and feedback after restart. The process ran as UID 10001, and the demo fixtures were reset afterward. The optional Ollama image tag resolves in the registry; its service, cloud sign-in and model pull were not started. The local HTTP report is `.data/assignment-container-smoke.json` (ignored).
- With user approval, a **live `gpt-oss:20b-cloud` agent and judge sweep completed** at 2026-10-30 17:46 IST (report timestamp `2026-10-30T12:16:49Z`), dataset v3.0.0 / policy v2.2.0. The CLI correctly exited 1 for failing metrics. The full trace/report is `.data/evals-assignment-live.json` (ignored); older results are not substituted for this run.

| Current live check | Passed |
|---|---|
| Independent agent answer correctness | 8/10 |
| Tool efficiency | 9/10 |
| GEval groundedness (diagnostic, with judge errors below) | 5/10 |
| All three metrics together | 4/10 |
| Canonical policy decision and order terms, separately compared with goldens | 10/10 |

The last row is evidence for deterministic enforcement; it does not replace the independent model's 8/10 correctness result.

### What failed in the live run

- `supplier-rejection-feedback` **initial investigation**: the independent model incorrectly recommended review instead of buying 150 units, claiming a stockout despite sufficient proposed supply. The canonical policy still produced the expected 150-unit approval draft. The later rejected-supplier recovery correctly requested review.
- `injected-supplier-text`: the model recommended **250 units / 300,000 cents** rather than **150 units / 180,000 cents**, incorrectly describing 250 as the necessary rounded quantity. The policy/draft retained 150 units. This shows an incorrect recommendation in the adversarial case; one run does not establish that the injected text caused the error.
- `stale-evidence`: the model attempted the nonexistent `get_forecast_and_observation` tool. The runtime did not execute it, the agent then read the valid tools, and both model and policy requested review. Strict tool efficiency still failed at 0.8.
- Some explanations contain genuine arithmetic mistakes even when order terms match: `review-modify` says 50 units remain, but 100 + 100 + 150 − 14×20 = **70**; `demand-spike` says 70 remain, but 120 + 250 + 150 − 14×30 = **100**.
- **The groundedness judge is not a reliable arbiter yet.** Its `review-modify` reason treats the original 800-unit suggestion as an answer that must be followed. Its full-confirmation recovery grade subtracts the 480-unit planning target and then requires the 60-unit safety stock again, whereas actual 14-day consumption is 420 and ending stock is 100. It also invents a “30×72” claim in its demand-spike critique and accepts a day-4 stockout claim although inventory reaches zero on day 4 and becomes negative on day 13. These errors mean the 5/10 groundedness result mixes actual explanation defects with judge defects; it is not a clean measure of factual accuracy.

## Limits and useful next improvements

1. **Model and evaluator quality need improvement.** The live sweep provides evidence of wrong recommendations, fabricated tool selection and arithmetic errors. Preserve this baseline, correct the model/evidence presentation, and give the judge the explicit planning-rule contract without revealing canonical answers. Add human-reviewed explanation labels before treating its 0.8 threshold as meaningful. The scripted reviewer establishes repeatability, not LLM reasoning quality. Repeating runs until one passes would not establish reliability.
2. **Evaluation coverage is narrower than implemented behavior.** The current goldens omit partial fulfilment, storage blocking, feasible alternate-supplier recovery, and refreshed supplier terms. Partial/storage behavior was checked manually over HTTP in this review, but those checks are not retained as automated regressions. The three metrics do not independently score every action/validation contract; those are principally covered by tests and the container smoke checks.
3. **Disagreement is advisory.** A disagreeing or unavailable independent recommendation does not itself prevent a policy-authorized draft. If all required reads completed, policy can authorize a draft even when final model output is invalid. The UI exposes the comparison and buyer approval remains mandatory. This is a documented assisted-purchasing choice, not a requirement that model and policy must agree.
4. **Recovery is deliberately bounded.** The agent may propose an eligible alternative, but no new order is automatically placed. An authoritative adapter must supply newer failed-supplier terms; there is no live supplier refresh service or buyer-facing refresh control. Resetting the demo clears its feedback and is not an operational recovery action.
5. **Mock scope is appropriate to this assignment.** Situations have isolated product/node budgets and storage limits; there is no shared multi-product/node optimizer, production authentication, live ERP/supplier integration, or atomic external purchase transaction. The PDF permits these omissions. Demand freshness uses supplied mock age rather than live feed timestamps.
6. **Submission remains an operator step.** Verify public repository access and deadline compliance, and show the real-provider path alongside its measured limitations if presenting the system as an LLM agent. Docker is an added reproducibility improvement; it was not mandated by the PDF.

For exact setup and demonstration commands, use the [README](README.md). The current [unit tests](tests/test_unit.py) and [integration tests](tests/test_integration.py) show the checks directly.
