# Report

## 1. Architecture

The system is a strict pipeline: **discovery observes and decides, evidence
records, compilation transforms, replay executes** — each stage only
consumes the output of the previous one, never reaching backward into it.

**Discovery** (`cua.discovery.engine`) runs an observe → decide → act loop
against a live Playwright page. Each turn, a bounded, structured observation
(visible headings/buttons/links/text-inputs/selects — never raw DOM) is sent
to an LLM behind a provider-neutral interface (`LLMProvider`), implemented by
two concrete adapters (Gemini, Groq) and selected at runtime via the
`LLM_PROVIDER` environment variable through `cua.discovery.llm.factory`. The
engine itself never imports a provider SDK, so adding a provider means
adding an adapter file, not changing orchestration logic. The model must
call exactly one of six tools per turn; zero, multiple, or unknown-name
responses are rejected by provider-neutral validation and given a small,
bounded number of corrective retries before the run fails closed.

The **evidence recorder** (`cua.discovery.evidence`) persists every
attempted and executed action as a structured JSON event, tagged with an
`evidence_schema_version` so a compiler can refuse evidence it doesn't
understand rather than silently misinterpreting it.

The **deterministic compiler** (`cua.compiler`) consumes only the
successfully-executed subset of that evidence and maps it directly onto
artifact `ActionStep`s — it never re-runs discovery's locator resolution; it
consumes the `resolved_locator` the harness already recorded. Capability-
specific static knowledge (inputs, outputs, checkpoints, business outcomes,
policy, trailing steps) lives in a `CompilationTemplate`, never in the
generic compiler code, so a second capability is a new template, not a fork
of the compiler.

The **artifact** (`cua.artifact.schema`) is the one contract both the
compiler and replay depend on — a typed, versioned Pydantic model, not a
recording and not free-form text. **Replay** (`cua.replay.engine`) executes
an artifact's steps with zero LLM calls, enforcing a policy gate immediately
before every action and distinguishing three-plus-one outcome categories
(§3).

This boundary — LLM only in discovery, nothing but typed data crossing into
replay — is what gets both genuine adaptability (an LLM can find a workflow
on a UI it wasn't hardcoded for) and production reliability (replay's
behavior is fully determined by a reviewable JSON file, not a model's live
behavior that could vary run to run).

## 2. Artifact schema

`CapabilityArtifact` (`cua.artifact.schema`) is the full contract:

- **`schema_version`** and **`capability_version`** — schema-shape
  versioning and capability-content versioning are separate axes, so a
  schema migration and a capability's own revision history don't force
  each other.
- **Typed inputs/outputs** — `InputParameter` and `OutputField` give every
  declared value a real type (`string`/`number`/`decimal`/`boolean`).
- **`ActionStep`s** — one of `navigate`/`click`/`type`/`select_option`/
  `wait_for`/`extract`, each cross-validated so, e.g., a `navigate` step
  cannot also carry a `target`, and a `type` step cannot omit `value`.
- **`ParamRef`/`LiteralRef`** — a typed/selected value is either a symbolic
  reference to a declared input or a genuinely fixed, non-sensitive literal.
  Steps never persist the concrete value used during discovery, which is
  what makes an artifact reusable with new runtime inputs rather than a
  recording of one specific run.
- **Locator strategies** — `RoleLocator` (role+name), `LabelTextLocator`
  (visible text), `CssLocator` (a real CSS selector): a small, closed
  vocabulary. No XPath, pixel coordinates, or computer vision is
  representable here — an internal discovery-time mechanism that can't be
  expressed this way (e.g. an XPath-based associated-control fallback for
  legacy markup) is normalized to a real CSS selector before persistence,
  or the step fails to compile.
- **`Checkpoint`** — four assertion kinds, reused identically by replay's
  checkpoints and by discovery's own independent finish-verification — one
  implementation, two callers.
- **`RetryPolicy`** — `max_attempts`, `backoff_ms`, and a `retry_on` list
  restricted to conditions the executor actually understands (`timeout`,
  `navigation_pending`) — a step cannot declare a retry behavior the
  runtime can't fulfill.
- **`BusinessOutcomeDetector`s** — named, typed detectors (e.g.
  `INVALID_CREDENTIALS`) with an `origin`; legitimate application outcomes
  are first-class schema citizens, not bolted onto the error path.
- **`SessionRequirement`** — `authenticated: bool` + an opaque, slug-
  validated `auth_profile`, structurally incapable of holding a credential.
- **`PolicyMetadata`** — `allowed_domains`, `allowed_actions`, per-step
  `risk`, and an optional `approval_threshold_param`/`value` pair, read by
  replay's policy gate at runtime (§6).
- **Provenance** — `created_at`, `created_by`, `discovery_run_id`, and a
  free-text `notes` field for human-readable provider/model context (e.g.
  "provider=groq model=qwen/qwen3.6-27b") rather than a dedicated field,
  since that's compile-time context for a reader, not data replay depends on.

## 3. Determinism & error handling

Replay makes **zero** LLM calls anywhere in `cua.replay` or anything it
imports — enforced by the module boundary itself, not just convention.

**Target resolution** (`cua.replay.locators.resolve_target`) tries a step's
locator strategies in order and accepts one only if it resolves to
**exactly one visible** match. It never falls back to a universal match and
never guesses between candidates — an ambiguous match is a distinct,
immediately fatal condition, never retried, since waiting doesn't resolve
ambiguity. **Checkpoints** and **output extraction** are both generic over
the artifact's declared targets/patterns — no capability-specific code
exists in either.

The system distinguishes three-plus-one outcome categories, enforced by
distinct Pydantic result types rather than one generic status+message shape:

- **Expected business outcome** (`ReplayBusinessOutcome`) — a legitimate
  application result (e.g. `INVALID_CREDENTIALS`), never conflated with an
  automation error.
- **Recoverable runtime condition** — not its own result type, by design: a
  step attempt that fails on an understood retryable condition and succeeds
  within `RetryPolicy.max_attempts` ends the run in an ordinary
  `ReplaySuccess`. The evidence log makes the recovery explicit regardless
  (an intermediate `outcome="retrying"`/`"injected_transient"` event
  immediately followed by `"ok"`), so a reviewer can see it happened even
  though the final result type doesn't distinguish "recovered" from "never
  had a problem."
- **Hard failure** (`ReplayFailure`, with a closed `failure_category` set
  covering locator/checkpoint/extraction/policy/session/input/artifact
  errors) — stops the run, captures a screenshot, records evidence.
- **Human decision point** (`ReplayInterventionOutcome`, decision
  `"declined"` or `"not_confirmed"`, §5). A *successfully completed*
  human-assisted run is deliberately not a distinct type — it returns the
  same `ReplaySuccess` full automation would, since the end state genuinely
  is identical; the evidence log, not the result model, is what proves a
  run was human-assisted.

Every path writes structured evidence — a JSONL trail with one event per
meaningful state transition, never free-form text. Credentials and secrets
are never passed to evidence writers; runtime values are recorded only as
symbolic parameter references or bounded non-secret context (e.g. the
transfer amount during an intervention request) where needed for debugging
or handoff.

## 4. Heterogeneity & multi-tenant (design only — NOT implemented)

Everything below is design intent, not shipped code — nothing here exists
beyond the single Playwright/Chromium path already implemented.

`cua.replay.locators`/`cua.discovery.observation` depend on small `Protocol`
interfaces rather than importing Playwright directly through most of the
resolution/checkpoint logic — done for testability (fakes implement the
same protocol with no browser), and incidentally the seam a different
browser engine could implement, though none exists today.

The `ActionStep`/target abstraction separates *what* to do (click, type, on
a described target) from *how* a specific engine does it, which is the
conceptual seam a different actuator could honor — but the current
role/label/CSS locator vocabulary is itself browser/DOM-specific and would
not directly carry over. A desktop implementation would need its own
surface-specific locator/target vocabulary (e.g. OS accessibility-tree
identifiers, not CSS) plus its own observation and execution adapters (an
accessibility-tree query instead of a DOM query, OS-level input events
instead of Playwright calls) implementing the same seams — a real design
direction, not a partially-built feature.

The associated-control fallback already built for discovery (matching a
visible label, then walking to the nearest editable/select element in DOM
order) is the existing example of handling a surface with no clean
accessible name; extending that family of strategies (e.g. frame-aware
traversal) is a natural, unimplemented extension of the same pattern.

A `CompilationTemplate` already isolates one capability's fixed knowledge
from generic compiler logic (§1); a multi-tenant system would extend this to
per-tenant overrides (different domains, thresholds) — conceptually, but no
tenant concept, override mechanism, or storage was built.

## 5. Escalation & handoff

This is a **real**, implemented same-session handoff — not a simulation.

**Trigger**: `cua.replay.policy.check_policy` evaluates, for a `risk="risky"`
step, whether the resolved runtime value for the declared threshold
parameter exceeds its configured value — deterministic, artifact-and-input
driven, never an LLM decision.

**Pause**: on `requires_approval=True`, `executor._handle_intervention`
stops *before* dispatching the risky step. The **same** `Page`/
`BrowserContext` stays open — `run_replay`'s `sync_playwright()` scope is
never exited during the wait, and the blocking wait itself is a synchronous
Python call, not a new process or browser restart.

**`control_owner`**: tracked via evidence events (`control_transition`,
`owner="human"`/`"automation"`) recorded immediately before and after the
blocking wait. Automation issues zero further Playwright calls for as long
as that call hasn't returned — enforced structurally by the blocking call
itself, not a flag automation has to remember to check.

**Manual event capture** (`cua.replay.manual_capture`): `page.expose_function`
plus one `page.evaluate()` install click/input/change listeners on the live
page. Only click events on non-text-entry elements report a short (≤50 char)
static label (e.g. a button's visible text "Transfer") — input/change
events report only `{tag, id, name}`, never `.value`, regardless of field
sensitivity.

**Resume + revalidation**: on "resume," the code does not advance to the
next step — it re-evaluates the artifact's own `success_checkpoint` against
the *current* live page state. If it passes, the risky step is treated as
already performed and the run continues to the trailing steps. If it does
not pass, `ReplayInterventionOutcome(decision="not_confirmed")` is returned
— the system never assumes a human did what was asked. Evidence item 06
(`evidence/06-resume-without-acting/`) is the live proof of exactly this
path.

**Decline**: `ReplayInterventionOutcome(decision="declined")`, with no
attempt at the risky action ever made — verified live during development
and covered by the automated test suite rather than a separately curated
live artifact.

## 6. Safety

- **Exact-domain allowlist** — string equality against `policy.allowed_domains`,
  never substring/suffix matching, so no accidental subdomain trust.
- **Allowed actions** — every step's `action` is checked against
  `policy.allowed_actions` immediately before dispatch; an undeclared step
  type is a hard `policy_violation` failure, never silently skipped.
- **Current-origin check before every browser action** — not just navigate:
  click/type/select_option/wait_for/extract all re-check the *current*
  page's host against the allowlist immediately before running, so that if
  the page has unexpectedly drifted to an unapproved origin, automation
  stops rather than continuing to interact with it.
- **Risk metadata + threshold** — `ActionStep.risk` plus the policy's
  approval-threshold pair; exceeding it on a risky step routes to human
  approval instead of automatic execution (§5) — deterministic policy code,
  never a model's judgment call.
- **Credentials from environment only** — `SessionRequirement.auth_profile`
  is a validated slug resolved against environment variables at the
  narrowest possible boundary (`cua.replay.session`); no artifact, evidence
  event, log line, or exception message is ever constructed from a
  credential value, enforced by calling convention.
- **Secrets absent from artifacts/evidence** — verified, not just asserted:
  the automated suite includes explicit tests asserting no credential/
  API-key string appears in generated artifacts or evidence output, and
  this submission's `/evidence` was manually re-scanned before inclusion.
- **Page content is data, not policy authority** — the LLM's observations
  and the live page's text/DOM are never consulted by the policy gate; only
  artifact metadata and runtime inputs are, so a compromised or misleading
  page cannot talk its way past the policy layer.

**Limitations**: the allowlist/threshold model is necessarily coarse — it
doesn't reason about the *content* of a risky action beyond a single
numeric threshold on one declared parameter, it trusts the artifact's own
`risk`/`policy` fields (a careless or malicious artifact could under-declare
risk), and the operator interface is a terminal prompt with no independent
verification of the operator's identity. These are acceptable for a
single-operator, single-machine demonstration system and are not claimed to
be production-grade access control.

## 7. Cuts

Deliberately not built, so nothing here is mistaken for an oversight:
remote operator dashboard (terminal only); VNC/remote co-browsing (handoff
is same-machine); desktop automation (§4 design direction only);
multi-tenant runtime; distributed workers; a database/capability catalog
(capabilities are versioned files); automatic artifact repair (a broken
locator is terminal, nothing self-heals); a generalized workflow DSL (a
fixed, closed set of step/locator/checkpoint kinds); production auth/secret
manager integration (plain environment variables); and a broad capability
library (one capability, `parabank.transfer_funds`, end to end — designed
to generalize, but not exercised with a second capability).

**Why**: the goal here is a small, fully real, fully verified vertical
slice — genuine LLM discovery, genuine deterministic compilation, genuine
zero-LLM replay, genuine same-session human handoff — rather than a wider
set of partially-implemented or simulated features. A correct narrow slice
is stronger evidence of engineering judgment than a broad platform with
untested edges.
