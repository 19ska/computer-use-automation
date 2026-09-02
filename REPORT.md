# Report

## 1. Architecture

The system is a strict pipeline: **discovery observes and decides, evidence
records, compilation transforms, replay executes** — each stage only ever
consumes the output of the previous one, never reaches backward into it.

**Discovery** (`cua.discovery.engine`) runs an observe → decide → act loop
against a live Playwright page. Each turn, a bounded, structured observation
(visible headings/buttons/links/text-inputs/selects — never raw DOM) is sent
to an LLM behind a provider-neutral interface
(`cua.discovery.llm.base.LLMProvider`), which two concrete adapters implement
(`gemini_provider.py`, `groq_provider.py`), selected at runtime by
`cua.discovery.llm.factory.create_llm_provider()` via the `LLM_PROVIDER`
environment variable. The engine itself never imports a provider SDK — this
boundary exists specifically so a new provider is a new adapter file, never a
change to orchestration logic. The model must call exactly one of six tools
per turn; zero, multiple, or unknown-name responses are rejected by
provider-neutral validation (`validate_single_call`) and given a small,
bounded number of corrective retries before the run fails closed — the model
never gets an unbounded number of chances.

The **evidence recorder** (`cua.discovery.evidence`) persists every attempted
and executed action as a structured JSON event, tagged with an
`evidence_schema_version` so a compiler can refuse evidence it doesn't
understand rather than silently misinterpreting it.

The **deterministic compiler** (`cua.compiler`) consumes only the
successfully-executed subset of that evidence (`outcome="ok"` events for
`navigate`/`click`/`type_text`/`select_option`) and maps it directly onto
artifact `ActionStep`s — it never re-runs any part of discovery's locator
resolution; it consumes the `resolved_locator` the harness already recorded.
Capability-specific static knowledge (inputs, outputs, checkpoints, business
outcomes, policy, trailing steps) lives in a `CompilationTemplate`
(`cua.compiler.templates`), never in the generic compiler code
(`events.py`/`steps.py`/`compile.py`) — this separation exists so a second
capability is a new template, not a fork of the compiler.

The **artifact** (`cua.artifact.schema`) is the one contract both discovery's
compiler and replay depend on — a typed, versioned Pydantic model, not a
video/screen recording and not free-form text.

**Replay** (`cua.replay.engine`) executes an artifact's steps with zero LLM
calls, enforcing a policy gate immediately before every action and
distinguishing three-plus-one outcome categories (§3).

This boundary — LLM only in discovery, nothing but typed data crossing into
replay — exists because it is the only way to get both genuine adaptability
(an LLM can find a workflow on a UI it wasn't hardcoded for) and genuine
production reliability (replay's behavior is fully determined by a
reviewable JSON file, not by a model's live behavior that could vary run to
run).

## 2. Artifact schema

`CapabilityArtifact` (`cua.artifact.schema`) is the full contract:

- **`schema_version`** (currently `"1.0"`) and **`capability_version`**
  (integer) — schema-shape versioning and capability-content versioning are
  deliberately separate axes; a schema migration and a capability's own
  revision history don't force each other.
- **Typed inputs/outputs** — `InputParameter` (`name`, `type`, `required`,
  `example`) and `OutputField` (`name`, `type`, `extraction: ExtractionSpec`)
  give every declared value a real type (`string`/`number`/`decimal`/
  `boolean`), not an untyped string.
- **`ActionStep`s** — one of `navigate`/`click`/`type`/`select_option`/
  `wait_for`/`extract`, each cross-validated (a `model_validator`) so, e.g.,
  a `navigate` step cannot also carry a `target`, and a `type` step cannot
  omit `value`.
- **`ParamRef`/`LiteralRef`** (`ValueRef` union) — a typed/selected value is
  *either* a symbolic reference to a declared input (`ParamRef(name=...)`)
  *or* a genuinely fixed, non-sensitive literal (`LiteralRef(value=...)`).
  Steps never persist the concrete value used during discovery — this is
  what makes an artifact reusable with new runtime inputs rather than a
  recording of one specific run.
- **Locator strategies** — `RoleLocator` (role+name), `LabelTextLocator`
  (visible text), `CssLocator` (a real CSS selector) — deliberately a small,
  closed vocabulary. No XPath, no pixel coordinates, no computer vision is
  ever representable here; an internal discovery-time mechanism that can't
  be expressed this way (e.g. an XPath-based associated-control fallback for
  legacy markup) is normalized down to a real CSS selector
  (`cua.discovery.resolved_locator`) before it's ever persisted, or the step
  fails to compile at all.
- **`Checkpoint`** — four assertion kinds (`url_matches`/`element_visible`/
  `element_hidden`/`text_contains`), reused identically by replay's
  step/success checkpoints and by discovery's own independent
  finish-verification — one implementation, two callers, so "did this
  actually work" is answered the same way regardless of who's asking.
- **`RetryPolicy`** — `max_attempts`, `backoff_ms`, and a `retry_on` list
  restricted to conditions the executor actually understands
  (`timeout`, `navigation_pending`) — a step cannot silently declare a retry
  behavior the runtime has no implementation for.
- **`BusinessOutcomeDetector`s** — named, typed detectors
  (e.g. `INVALID_CREDENTIALS`) with an `origin` (`session_establishment` or
  `capability_execution`) — legitimate application outcomes are first-class
  schema citizens, not something bolted onto the error path.
- **`SessionRequirement`** — `authenticated: bool` +
  `auth_profile: str | None`, where `auth_profile` is a slug-validated
  *opaque reference*, structurally incapable of holding a credential.
- **`PolicyMetadata`** — `allowed_domains`, `allowed_actions`, `risk`
  per-step, and an optional `approval_threshold_param`/`value` pair — the
  data replay's policy gate (§6) reads at runtime.
- **Provenance** — `created_at`, `created_by` (`"discovery_agent"` or
  `"human_edited"`), `discovery_run_id`, and a free-text `notes` field used
  for human-readable provider/model provenance (e.g. "Compiled from
  discovery run ... provider=groq model=qwen/qwen3.6-27b") rather than a
  dedicated schema field — provider/model identity is compile-time context
  worth recording for a human reader, not executable data replay depends on,
  so a schema field wasn't justified for it.

## 3. Determinism & error handling

Replay makes **zero** LLM calls anywhere in `cua.replay` or anything it
imports — this is enforced by the module boundary itself (no LLM SDK is even
importable from that package), not just by convention.

**Target resolution** (`cua.replay.locators.resolve_target`) tries a step's
locator strategies in declared order and accepts a strategy only if it
resolves to **exactly one visible** match. It never falls back to a
universal match (e.g. `body`) and never guesses between multiple candidates
— an ambiguous match (`LocatorAmbiguousError`) is a distinct, immediately
fatal condition, never retried (waiting doesn't resolve ambiguity).

**Checkpoints** (`cua.replay.checkpoints`) and **output extraction**
(`cua.replay.extraction`) are both generic over the artifact's declared
targets/patterns — no capability-specific code exists in either module.

The system explicitly distinguishes three-plus-one outcome categories,
enforced by distinct Pydantic result types (`cua.replay.results`) rather
than one generic status+message shape:

- **Expected business outcome** (`ReplayBusinessOutcome`) — a legitimate
  application result (e.g. `INVALID_CREDENTIALS`), never conflated with an
  automation error.
- **Recoverable runtime condition** — not its own result type, by design: a
  step attempt that fails on an *understood* retryable condition
  (`retry_on` contains `timeout`/`navigation_pending`) and succeeds on a
  later attempt within `RetryPolicy.max_attempts` ends the run in an
  ordinary `ReplaySuccess`. The evidence log makes the recovery explicit
  regardless (an intermediate `outcome="retrying"` or, for the deterministic
  demo injection, `outcome="injected_transient"` event, immediately followed
  by `outcome="ok"`) — a reviewer can see it happened even though the final
  result type doesn't distinguish "recovered" from "never had a problem."
- **Hard failure** (`ReplayFailure`, with a closed `failure_category` set:
  `locator_not_found`, `locator_ambiguous`, `checkpoint_failed`,
  `extraction_error`, `policy_violation`, `session_establishment_error`,
  `artifact_load_error`, `input_validation_error`, `unexpected_error`) —
  stops the run, captures a screenshot, records structured evidence.
- **Human decision point** (`ReplayInterventionOutcome`, `decision`:
  `"declined"` or `"not_confirmed"`) — see §5. A *successfully completed*
  human-assisted run is deliberately **not** a distinct result type; it
  returns the same `ReplaySuccess` full automation would, because the
  end state genuinely is identical — the evidence log, not the result
  model, is what proves a run was human-assisted.

Every one of these paths writes structured evidence
(`cua.replay.evidence`/`cua.discovery.evidence`) — a JSONL trail with one
event per meaningful state transition, never free-form text, and never a
credential or a typed/selected value (enforced by calling convention: the
evidence writer's methods simply don't accept those as parameters).

## 4. Heterogeneity & multi-tenant (design only — NOT implemented)

Everything in this section is **design intent**, not shipped code. Nothing
described here exists in the runtime today beyond the single Playwright/
Chromium path already implemented.

**Browser adapter today**: `cua.replay.locators`/`cua.discovery.observation`
depend only on small `Protocol` interfaces (`SupportsPageProtocol`,
`SupportsLocatorProtocol`) — the concrete implementation is Playwright, but
nothing above those protocols imports Playwright directly by name in most of
the resolution/checkpoint logic. This was done for testability (fakes
implement the same protocol with no browser at all), and it happens to also
be the seam a different browser automation engine could implement — but no
second implementation was built or is claimed to exist.

**Artifact-level intent vs. surface execution**: the schema's locator
vocabulary (role/name, label/text, CSS) is already an abstraction over "how
a specific engine finds an element" — an `ActionStep` says *what* to do
(`click`, `type`, with a described target), not *how* a particular browser
API does it. This separation is what would let a different actuator honor
the same artifact shape, in principle.

**How legacy/frames/no-test-id surfaces would fit**: the associated-control
fallback already built for discovery (matching a visible label, then
walking to the nearest following editable/select element in DOM order —
`cua.discovery.target_resolution._associated_control_xpath`) is the existing
example of "handle a surface with no clean accessible name." Extending that
family of fallback strategies (e.g. frame-aware traversal) is a natural,
unimplemented extension of the same pattern, not a new architecture.

**How desktop automation could fit**: the same `LLMProvider` /
observe-decide-act loop shape does not depend on Playwright conceptually —
a desktop implementation would need its own "observation" builder (e.g. an
OS accessibility tree instead of a DOM query) and its own "executor" (OS-level
input events instead of Playwright calls), implementing the same seams. This
is a real design direction, not a partially-built feature.

**Tenant-specific compatibility/overrides**: a `CompilationTemplate` is
already how one capability's fixed knowledge (session requirement, policy,
business outcomes) is isolated from generic compiler logic (§1). A
multi-tenant system would extend this to per-tenant template overrides
(different allowed domains, different approval thresholds) — the seam
exists conceptually in today's template/generic split, but no tenant
concept, override mechanism, or storage for it was built.

## 5. Escalation & handoff

This is a **real**, implemented same-session handoff — not a simulation.

**Trigger**: `cua.replay.policy.check_policy` evaluates, for a `risk="risky"`
step, whether the resolved runtime value for `policy.approval_threshold_param`
exceeds `policy.approval_threshold_value`. This is deterministic, artifact-
and-input-driven — never an LLM decision.

**Pause**: on `requires_approval=True`, `cua.replay.executor._handle_intervention`
stops *before* dispatching the risky step. The **same** `Page`/`BrowserContext`
object stays open — `run_replay`'s `with sync_playwright()` scope is never
exited during the wait, and the blocking wait itself is a synchronous
Python call (`operator.request_intervention`), not a new process or a
browser restart.

**`control_owner`**: tracked via evidence events
(`control_transition`, `owner="human"`/`"automation"`) recorded immediately
before and after the blocking wait. Automation issues zero further
Playwright calls for as long as that call hasn't returned — enforced
structurally by the blocking call itself, not by a flag automation has to
remember to check.

**Manual event capture** (`cua.replay.manual_capture`): a `page.expose_function`
+ one `page.evaluate()` install click/input/change listeners on the live
page. Only click events on non-text-entry elements report a short (≤50 char)
static label (e.g. a button's visible text "Transfer") — input/change events
report only `{tag, id, name}`, **never** `.value`, regardless of field
sensitivity.

**Resume + revalidation**: on "resume," the code does not advance to the
next step. It re-evaluates the artifact's own `success_checkpoint` against
the *current* live page state. If it passes, the risky step is treated as
already performed and the run continues to the trailing steps. If it does
not pass, `ReplayInterventionOutcome(decision="not_confirmed")` is returned
— the system never assumes a human did what was asked. Evidence item 06
(`evidence/06-resume-without-acting/`) is the live proof of exactly this
path.

**Decline**: `ReplayInterventionOutcome(decision="declined")`, with no
attempt at the risky action ever made. Verified live during development and
covered by the automated test suite (see `evidence/manifest.md`'s decline
note) rather than a separately curated live artifact.

## 6. Safety

- **Exact-domain allowlist** (`cua.replay.policy._host_allowed`) — string
  equality against `policy.allowed_domains`, never substring/suffix
  matching, so no accidental subdomain trust.
- **Allowed actions** — every step's `action` is checked against
  `policy.allowed_actions` immediately before dispatch; a step type not
  declared allowed is a hard `policy_violation` failure, never silently
  skipped or attempted.
- **Current-origin check before every browser action** — not just navigate:
  `click`/`type`/`select_option`/`wait_for`/`extract` all re-check the
  *current* page's host against the allowlist immediately before running,
  specifically so that if the page has unexpectedly drifted to an
  unapproved origin (redirect, unexpected link), automation stops rather
  than continuing to interact with it.
- **Risk metadata + threshold** — `ActionStep.risk` plus
  `PolicyMetadata.approval_threshold_param`/`value`; exceeding the
  threshold on a risky step routes to human approval instead of automatic
  execution (§5). This is deterministic policy code, not a model's
  judgment call.
- **Credentials from environment only** — `SessionRequirement.auth_profile`
  is a validated slug resolved against environment variables
  (`PARABANK_USERNAME`/`PARABANK_PASSWORD`) at the narrowest possible
  boundary (`cua.replay.session`); no artifact, evidence event, log line,
  or exception message is ever constructed from a credential value — this
  is enforced by calling convention (those functions are simply never
  passed one).
- **Secrets absent from artifacts/evidence** — verified directly, not just
  asserted: the automated suite includes explicit tests asserting no
  credential/API-key string appears in generated artifacts or evidence
  output, and this submission's `/evidence` was manually re-scanned before
  inclusion (see the secret-scan note in the final packaging report).
- **Page content is data, not policy authority** — the LLM's observations
  and the live page's text/DOM are never consulted by the policy gate; only
  artifact metadata and runtime inputs are. A compromised or misleading page
  cannot talk its way past the policy layer.

**Limitations of this safety model**: the allowlist/threshold model is
necessarily coarse — it does not reason about the *content* of a risky
action beyond a single numeric threshold on one declared parameter, it
trusts the artifact's own `risk`/`policy` fields (a malicious or
carelessly-authored artifact could under-declare risk), and the human
operator interface is a terminal prompt with no independent verification of
the operator's identity. These are acceptable for a single-operator,
single-machine demonstration system and are explicitly not claimed to be
production-grade access control.

## 7. Cuts

Deliberately not built, listed explicitly so nothing here is mistaken for
an oversight:

- **Remote operator dashboard** — the operator interface is a local
  terminal prompt only.
- **VNC / remote co-browsing** — handoff is same-machine, same-process.
- **Desktop automation implementation** — discussed as a design direction
  in §4, not built.
- **Multi-tenant runtime** — no tenant concept, isolation, or per-tenant
  configuration exists.
- **Distributed workers** — single-process execution only.
- **Database / capability catalog** — capabilities are files
  (`generated_capabilities/<id>/v<version>.json`), not a queryable store.
- **Automatic artifact repair** — a broken locator or failed step is a
  terminal failure; nothing attempts to self-heal an artifact.
- **A generalized workflow DSL** — the artifact schema is a fixed, closed
  set of step/locator/checkpoint kinds, not an extensible scripting
  language.
- **Production auth/secret manager integration** — credentials come from
  plain environment variables (`.env`), not a vault or managed secret
  store.
- **A broad capability library** — one capability (`parabank.transfer_funds`)
  is implemented end to end; the architecture is designed to generalize,
  but generalization itself was not exercised with a second capability.

**Why**: the goal of this submission is a small, **fully real, fully
verified** vertical slice — genuine LLM discovery, genuine deterministic
compilation, genuine zero-LLM replay, genuine same-session human handoff —
rather than a wider set of partially-implemented or simulated features. A
correct narrow slice is more useful evidence of engineering judgment than a
broad platform with untested edges.
