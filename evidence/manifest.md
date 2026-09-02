# Evidence Manifest

This directory contains a **curated** subset of real evidence produced by this
system against the live ParaBank demo application. It is not a dump of every
development run — `discovery_output/` and `run_output/` contain many more
runs (including failed/debugging iterations) that are intentionally excluded
here. Every file below is a direct copy of a real, unmodified evidence file,
with one narrow exception noted in **Redaction note** below.

## Redaction note

The curated copies in this directory have visible ParaBank account-holder
**display names** (e.g. `"Welcome Dev mewada"`, `"Welcome John Smith"` —
text ParaBank itself renders on the page for the logged-in synthetic test
account) replaced with `"Welcome [synthetic test user]"`, for privacy and
readability. This is the **only** transformation applied. Nothing else was
changed: run IDs, timestamps, step numbers, actions, `resolved_locator`
values, `value_source` (ParamRef/LiteralRef) values, outcomes, policy
decisions, control transitions, manual events, retry/injected-transient
events, and checkpoint results are all byte-for-byte identical to the
original source evidence in `discovery_output/`/`run_output/`. The original,
unredacted source files are untouched and remain available locally (they are
git-ignored, not part of this submission, precisely because they contain
many non-curated runs).

## Decline path — not included as a live artifact

The human-decline path (`operator types "decline"` → `ReplayInterventionOutcome(decision="declined")`)
was manually verified during development but no run producing usable
standalone evidence for it was preserved. Rather than fabricate one or claim
a curated artifact that doesn't exist, this path is instead demonstrated by
the automated test suite:
- `tests/replay/test_executor.py::test_decline_returns_clean_structured_outcome`
- `tests/replay/test_executor.py::test_amount_above_threshold_requests_intervention_before_risky_click`

The curated evidence below focuses on the two strictly stronger and more
informative handoff paths: **approve** (05) and **resume-without-acting**
(06) — the latter is arguably the more important proof, since it shows
automation refusing to trust an unconfirmed human claim.

---

## 01 — Genuine LLM discovery success

| | |
|---|---|
| **Behavior demonstrated** | A real Groq/Qwen LLM discovers the ParaBank transfer-funds workflow end to end against the live site, with zero hardcoded steps. |
| **Run ID** | `discovery-20260902T051912Z-d505f934` |
| **Provider / model** | `groq` / `qwen/qwen3.6-27b` |
| **Inputs** | amount=`20.00`, from_account_id=`14787`, to_account_id=`14898` |
| **Expected result** | `status=success`, independently-verified confirmation text |
| **Files** | `events.jsonl` (8 events), `final_success.png` |
| **What to look for** | Step 5 (`click`, `resolved_locator={"kind":"role","role":"button","name":"Transfer"}`) and step 6 (`action=finish, outcome=finished`) with `checkpoint_result.passed=true` — the model's own "finish" claim was independently re-verified against the live page, not taken on trust. |

## 02 — Generated capability artifact

| | |
|---|---|
| **Behavior demonstrated** | The deterministic compiler (zero LLM calls) converts run 01's evidence into a typed, schema-valid `CapabilityArtifact`. |
| **Source** | Compiled from `discovery-20260902T051912Z-d505f934` |
| **Files** | `v1.json` |
| **What to look for** | `steps[0..4]` (navigate/select_option×2/type/click) came directly from discovery evidence's `resolved_locator`/`value_source` fields; `steps[5..9]` (wait_for + 4 extracts), `session_requirement`, `business_outcomes`, `policy`, `success_checkpoint` came from the capability's `CompilationTemplate`, not the discovery run. Note `value: {"kind":"param", ...}` on the type/select steps — the artifact is parameterized, not hardcoded to 20.00/14787/14898. |

## 03 — Deterministic replay with DIFFERENT runtime inputs

| | |
|---|---|
| **Behavior demonstrated** | Zero-LLM replay of the artifact from 02, using runtime inputs that are the **reverse** of the original discovery run — proves the artifact is genuinely parameterized, not a recording. |
| **Run ID** | `parabank.transfer_funds-20260902T053505Z-6dc7f994` |
| **Inputs** | amount=`10.00`, from_account_id=`14898`, to_account_id=`14787` |
| **Expected result** | `status=success` |
| **Files** | `events.jsonl` (13 events) |
| **What to look for** | No LLM-related fields anywhere in this log (no `provider`/`model` on any event — replay never calls one); final `success_checkpoint` event has `outcome=passed`. |

## 04 — Recoverable transient condition + bounded retry

| | |
|---|---|
| **Behavior demonstrated** | A deterministic, disabled-by-default injected transient failure (`--inject-transient-once`) on one step's first attempt, recovered by the artifact's own `RetryPolicy` on the very next attempt — the real browser action still executes for real on recovery. |
| **Run ID** | `parabank.transfer_funds-20260902T062014Z-1e4b19ea` |
| **Files** | `events.jsonl` (24 events) |
| **What to look for** | Two consecutive `wait_for` events for the same step: `outcome=injected_transient` (`detail` explicitly says `"injected transient condition (--inject-transient-once)"`) immediately followed by `outcome=ok` — then the run continues normally to `success_checkpoint: passed`. This is evidence of category **B** (recoverable), explicitly distinct from a hard failure. |

## 05 — Human handoff: approved

| | |
|---|---|
| **Behavior demonstrated** | amount (600.00) exceeds the artifact's approval threshold (500) → automation pauses before the risky Transfer click → the SAME live browser session is handed to a human → the human manually clicks Transfer → control returns to automation → the artifact's own success checkpoint is independently re-evaluated (never assumed) → run continues to a normal `ReplaySuccess`. |
| **Run ID** | `parabank.transfer_funds-20260902T062402Z-7294e9c5` |
| **Inputs** | amount=`600.00` |
| **Expected result** | `status=success` (human-assisted, not automation-performed) |
| **Files** | `events.jsonl` (29 events), `intervention_step_5.png` |
| **What to look for**, in order: `policy_check → approval_required` (`detail: "amount=600.00 exceeds approval threshold 500"`) → `intervention_requested` → `control_transition → human` → `manual_event → human` (`event_type=click`, target text "Transfer" — no input value ever recorded) → `control_transition → automation` → `intervention_decision → resume` → `intervention_resume_check → passed` → normal `extract`/`success_checkpoint` events to the end. |

## 06 — Resume without acting (automation does not assume success)

| | |
|---|---|
| **Behavior demonstrated** | The single most important safety proof: the operator says "resume" without ever actually performing the risky action, and automation independently detects this and refuses to continue as if it succeeded. |
| **Run ID** | `parabank.transfer_funds-20260902T062519Z-56fea6e2` |
| **Inputs** | amount=`600.00` |
| **Expected result** | `status=intervention`, `decision=not_confirmed` |
| **Files** | `events.jsonl` (16 events), `intervention_step_5.png` |
| **What to look for**: same opening sequence as 05 up through `intervention_decision → resume`, but note there is **no** `manual_event` line this time (the human never actually clicked anything), and `intervention_resume_check → failed` — the run stops there rather than proceeding to extraction/checkpoint. |

## 07 — Expected business outcome: INVALID_CREDENTIALS

| | |
|---|---|
| **Behavior demonstrated** | A legitimate, expected application-level outcome (bad login) is modeled distinctly from a technical failure — `status=business_outcome`, not `status=failure`. |
| **Run ID** | `discovery-20260902T051644Z-4df05a77` |
| **Files** | `events.jsonl` (2 events) |
| **What to look for**: `session_establish → business_outcome` with `detail=INVALID_CREDENTIALS`. This is intentionally the shortest evidence file here — the whole point is that this outcome is detected immediately at session establishment, before any discovery/replay steps run at all. |
