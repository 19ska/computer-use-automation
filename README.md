# Computer-Use Automation System

## What this is

An LLM discovers a UI workflow **once**, live, against a real web application.
The successful discovery run is converted — deterministically, with zero LLM
calls — into a typed, versioned **capability artifact**. Production replay
then executes that artifact with no LLM involvement at all: same locators,
same steps, new runtime parameters.

```
   ┌─────────────┐     ┌──────────────┐     ┌───────────┐     ┌──────────────┐     ┌─────────────┐
   │  LLM         │──▶ │  Structured  │──▶ │  Deterministic│──▶ │  Typed        │──▶ │  Zero-LLM   │
   │  Discovery   │     │  Evidence    │     │  Compiler     │     │  Artifact     │     │  Replay     │
   │ (Groq/Gemini)│     │ (events.jsonl)│    │ (no LLM calls)│    │ (Pydantic v2) │     │ (Playwright)│
   └─────────────┘     └──────────────┘     └───────────┘     └──────────────┘     └─────────────┘
```

The LLM's job ends the moment a workflow is proven once. Everything after
that is ordinary, auditable, deterministic automation.

## Core properties

- **LLM only during discovery** — replay never imports or calls an LLM SDK.
- **Deterministic replay** — same artifact + same inputs → same actions, every time.
- **Typed, versioned artifacts** — a Pydantic v2 schema, not a screen-recording.
- **Parameterized inputs** — `ParamRef`s, not baked-in literal values.
- **Robust locators** — role/name → label/text → CSS fallback chain, never a blind guess.
- **Checkpoints** — independent, page-state verification, not "the model said so."
- **Business outcomes vs. failures** — a legitimate app result (e.g. invalid credentials) is never conflated with an automation crash.
- **Bounded recovery** — a small, understood set of transient conditions retry; everything else fails clearly.
- **Policy enforcement** — exact-domain allowlist and risk thresholds, enforced in code before every action.
- **Same-session human handoff** — a real pause-and-resume in the *same* browser session, not a simulated one.
- **Structured evidence** — every run produces a reviewable JSONL trail.

## Architecture

**Discovery → Evidence → Compiler → Artifact → Replay**

| Stage | What it does |
|---|---|
| **Discovery** (`cua.discovery`) | An LLM (Groq or Gemini, pluggable via `LLM_PROVIDER`) observes the live page, proposes exactly one of six tools (`navigate`/`click`/`type_text`/`select_option`/`finish`/`give_up`) per turn, and the harness executes it. A policy gate and independent finish-verification run outside the model's control. |
| **Evidence** (`cua.discovery.evidence`) | Every executed action is recorded as a structured JSON event — including the *exact* resolved locator and parameter reference, not just a description. |
| **Compiler** (`cua.compiler`) | Deterministically converts a *successful* discovery run's evidence into a `CapabilityArtifact`. Makes zero LLM calls. Capability-specific knowledge (inputs/outputs/checkpoints/policy) lives in a `CompilationTemplate`, kept separate from the generic compiler logic. |
| **Artifact** (`cua.artifact.schema`) | The typed, versioned, human-reviewable contract between discovery and replay. |
| **Replay** (`cua.replay`) | Executes an artifact against live inputs with zero LLM calls, enforcing policy before every action and handling recoverable/hard/business/human-gated outcomes distinctly. |

## Implemented capability

**`parabank.transfer_funds`** — transfer funds between two accounts on the
[ParaBank](https://parabank.parasoft.com/) demo banking application.

- **Inputs**: `amount` (decimal), `from_account_id` (string), `to_account_id` (string)
- **Outputs**: `confirmation_message`, `transferred_amount`, `from_account_id`, `to_account_id` — all extracted from the live confirmation page via a proven regex
- **Business outcome**: `INVALID_CREDENTIALS` — detected at session establishment, modeled as a legitimate result, not a failure
- **Success checkpoint**: page text contains "Transfer Complete!" plus the exact amount/from/to values requested at runtime

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
```

Copy `.env.example` to `.env` and fill in your own values:

```bash
cp .env.example .env
```

You'll need: an LLM provider key (`GROQ_API_KEY` or `GEMINI_API_KEY`, matching
`LLM_PROVIDER`), and a dedicated **synthetic** ParaBank test account
(`PARABANK_USERNAME`/`PARABANK_PASSWORD`) — register one for free at
https://parabank.parasoft.com/parabank/register.htm. **Never commit `.env`
or put real secrets in this README.**

## Commands

Account IDs on the public ParaBank demo are **not stable** — they're
assigned per registered account and reset periodically. Replace
`<from-id>`/`<to-id>` below with two real account IDs from your own
synthetic test account (visible on the "Accounts Overview" page after login).

**Run discovery** (real LLM calls, live browser):
```bash
python -m cua discover \
  --goal "Transfer 20.00 from account <from-id> to account <to-id> and reach the transfer confirmation page." \
  --amount 20.00 --from-account-id <from-id> --to-account-id <to-id>
```

**Compile a successful discovery run into an artifact** (zero LLM calls):
```bash
python -m cua compile \
  --discovery-run discovery_output/<run_id> \
  --capability parabank.transfer_funds
```

**Replay the generated artifact** (zero LLM calls, can use different inputs than discovery):
```bash
python -m cua replay \
  --artifact generated_capabilities/parabank.transfer_funds/v1.json \
  --amount 10.00 --from-account-id <to-id> --to-account-id <from-id>
```

**Recoverable-condition demo** (deterministic injected transient failure, disabled by default):
```bash
python -m cua replay --artifact generated_capabilities/parabank.transfer_funds/v1.json \
  --amount 20.00 --from-account-id <from-id> --to-account-id <to-id> --inject-transient-once
```

**Human-handoff demo** (amount above the artifact's approval threshold triggers it automatically — run headed, not `--headless`):
```bash
python -m cua replay --artifact generated_capabilities/parabank.transfer_funds/v1.json \
  --amount 600.00 --from-account-id <from-id> --to-account-id <to-id>
```
Automation pauses before the Transfer click and prints an intervention
request to the terminal. Perform the click yourself in the open browser
window, then type `resume` (or type `decline` to cancel without acting).

**Tests**:
```bash
pytest -q
```

## Testing

```
341 passed, 0 failed, 0 skipped, 0 warnings
```
All discovery/compiler/policy/handoff tests run against fakes — no real
browser or LLM calls happen in the automated suite. See `/evidence` for real,
live-run proof of every behavior.

## Evidence

`/evidence` contains a curated set of real run logs proving each required
behavior — genuine LLM discovery, deterministic compilation, parameterized
replay, recoverable transient conditions, human handoff (both a confirmed
approval and a correctly-rejected unconfirmed resume), and the
`INVALID_CREDENTIALS` business outcome. Start at
[`evidence/manifest.md`](evidence/manifest.md), which indexes every file to
the behavior it demonstrates and what to look for.

## Safety

- **Exact-domain allowlist** — no implicit subdomain trust; enforced in code (`cua.replay.policy`) immediately before every browser-interacting action, including a check that the *current* page hasn't unexpectedly drifted off-domain.
- **Risk threshold** — a capability declares an `approval_threshold_param`/`value`; exceeding it gates the risky action behind human approval instead of executing automatically.
- **Credentials never touch artifacts** — `SessionRequirement.auth_profile` is an opaque slug resolved against environment variables at runtime; no password/API key is ever representable in artifact JSON.
- **Redaction by construction** — evidence writers are called with only bounded, structured fields (action, locator, outcome) — typed/selected values and credentials are never passed to them, by calling convention.
- **Page content is data, not policy authority** — the model never decides what's safe; policy decisions come from artifact metadata and runtime inputs only.

## Known limitations / cuts

- Single browser target (Playwright/Chromium) — no other browser engines.
- One implemented capability (`parabank.transfer_funds`) — the architecture generalizes, but only one capability is built and proven.
- The public ParaBank demo app is occasionally unstable/slow — evidence and demos assume a reasonably responsive instance.
- The human-operator interface is a minimal terminal prompt — no GUI, no dashboard.
- No remote co-browsing, VNC, or any networked control-plane for handoff — it is same-process, same-machine only.
- No desktop/native-app automation — web only.
- No multi-tenant production infrastructure, database, or capability catalog.
- No automatic artifact repair — a broken locator fails closed and stays failed until a human/discovery fixes it.
