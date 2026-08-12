# Architecture

## The one decision everything else follows from

A language model reads documentation far better than any parser will. It also produces confident,
plausible, wrong claims. Both are true at once, so the design question is not *"model or not"* — it
is **which half of the job each side is allowed to do**.

drift splits it like this:

> **The model discovers. A pure function verifies.**
> The model reads a document and states what it asserts. A function of the repository at a fixed
> commit — no model output in scope — decides whether each assertion still holds. **The model cannot
> mint a finding**; that is enforced by the shape of the pipeline, not by a prompt.

The inverse arrangement was built first and measured: a deterministic extractor pulled claims out of
document syntax and checked them, and on a 16-repository blind sample it emitted 58 findings of
which 54 were false. The failure was not in *checking*: against a separately measured set of known
drifts it found 4 of 4 of those that survived anchoring.
It was in *identification*: deciding from syntax alone that a string is an assertion about this
repository at all. That measurement is in [`evals/06-retired-instruments.md`](evals/06-retired-instruments.md),
and it is why the responsibilities are the way round they are.

## The shape

```
     documents + docstrings
              │
              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  DISCOVERY — free cognition, no authority                 │
   │                                                           │
   │  · agent tool-loop over a document: reads it, explores    │
   │    the repository (read and glob — read-only, no          │
   │    network, no execution), and emits the document's full  │
   │    claim inventory. A cartographer, not a bug hunter.     │
   │  · a deterministic parser over the docstring corpus,      │
   │    where the anchor is self-evident and cognition buys    │
   │    nothing.                                               │
   └──────────────────────────────────────────────────────────┘
              │
       ═══════ THE WAIST ═══════
       a closed, typed, human-admitted predicate registry.
       Each entry is a pure function of the checked-out tree
       at one revision, and each carries a grade:

         high     path_exists · link_resolves · symbol_resolves
                  signature_has_param · make_target_exists
         preview  manifest_key_exists · class_has_member
                  (run, journal, annotate — cannot mint,
                   cannot suppress)
              │
              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  REPLAY GATE — the only truth authority                   │
   │                                                           │
   │  Re-executes each claim's check outside the loop, from    │
   │  stored arguments: one leg confirms the document still    │
   │  contains the literal, the other runs the predicate       │
   │  against the tree. Anything the model said is irrelevant  │
   │  here. Claims it cannot adjudicate raise a typed decline. │
   └──────────────────────────────────────────────────────────┘
              │
              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  SEMANTIC JUDGE — one question, leashed                   │
   │                                                           │
   │  Adjudicates only what the gate certified. Asks whether   │
   │  document still means to assert the thing, or is          │
   │  recording history. Never sees the discovery transcript.  │
   └──────────────────────────────────────────────────────────┘
              │
              ▼
   VERIFIED FINDINGS            CANDIDATES (unverified)
   gate-certified ∧ live        everything else, banded and labelled
```

## The invariants

These are the rules the code is built to make unbreakable, rather than intentions it tries to
honour.

**1. A finding is mechanical truth and semantic truth, both.** The mechanical half is guaranteed by
replay; the semantic half is adjudicated by a model and its error rate is measured and published.
There is no third category and no taxonomy of liveness in any schema — the judge answers one
question for every claim class.

**2. No replayed failure, no finding.** A claim whose refutation cannot be re-derived outside the
loop can never be reported as a finding. Tool calls made during discovery are cognition aids; they
are not evidence.

**3. Cognition is open, authority is closed.** The agent may read anything inside the repository.
Only registry predicates confer the power to assert, and only high-grade ones. A predicate may enter
the registry at `preview` grade on evidence of demand: it runs, it is journaled, it annotates the
candidate tier — and it cannot mint a finding or suppress a claim. So the vocabulary can grow with
demand without the growth being able to move a published number.

**4. Identity is predicate-owned, commit-free and line-free.** A claim's key is a hash of the
predicate, the document path and the normalized arguments. The same drift found by any route
produces the same key, so a finding does not churn when a document is reformatted or a line moves.

**5. Binding eligibility is decided without semantic judgment.** Whether a predicate *applies* to a
literal is a pure function of the literal plus declared, versioned jurisdiction lists. Anything that
would require inferring what the author meant — dialect, intent, whether the target is even this
project's — either declines to bind or raises a typed decline. Skip, don't guess.

**6. Lifecycle is deterministic.** An open issue is resolved by replaying its stored check, not by
the model failing to mention it again. A discovery that varies between runs delays a finding; it
never closes one.

**7. Coverage is a contract** — computed offline today rather than at scan time; see the closing
section. What was scanned is mechanically statable. A truncated document, a
kernel error or a budget stop is counted and reported — a partial run is never presented as a clean
one.

**8. Every number carries its version.** Each journal row is stamped with the agent version, the
judge version and the model, and those three columns are not-null at the database. The *bump* is a
convention rather than a mechanism — the version strings are hand-written — so a content hash of
each prompt surface is journaled beside them, which is what tells two variants shipped under one
stamp apart. A model change re-baselines every published figure.

## Declining is a first-class outcome

The single most important thing the gate can do is refuse to answer. There are twelve reasons it may
give, they are a closed enforced set, and every one is mechanically decidable:

`external` · `module-unreachable` · `variadic` · `no-makefile` · `gitignored` · `makefile-includes`
· `base-ambiguous` · `no-manifest` · `manifest-unparseable` · `no-signature` · `external-base` ·
`not-a-class`

Each exists because a measured false-positive class demanded it. `variadic` is the clearest: a
function documenting a parameter it forwards through `**kwargs` does not name that parameter in its
own signature, so absence proves nothing — in one HTTP library that is 29 documented parameters, and
a naive checker reports 29 false positives. A decline is journaled, never silently dropped, and it
is **never** routed to the judge: an unanswerable mechanical question is not a semantic question.

## Running a scan: a frame and its cells

A scan splits into two scopes, and the split is what makes the budget and the failure semantics
honest.

**The frame** (`graph/frame.py`, with `planning`, `dispatch`, `fanin`, `journal_rows`,
`session_read` alongside it) owns everything scoped to the run: it enumerates the worklist once,
creates the run row, holds the wallet, dispatches cells, fans their results back in, and renders the
report. It never blocks on a task handle — it polls the terminal rows the cells write, which is what
keeps the cost accounting reachable even when a run is killed.

**A cell** (`graph/cell.py`) is one producer applied to one **unit** — a document for the agent, the
whole docstring corpus for the deterministic producer, which gets a single corpus-wide cell because
it walks the package itself. Discover, gate, judge, and one terminal row. It is the unit of dispatch, the unit of retry, and the unit of funding — the wallet is
checked before a cell starts and **never cuts one in half**, so a budget stop always leaves whole
units done and whole units untouched, and the report says which. The cost of that rule is stated
rather than hidden: the overshoot bound is the budget plus one cell, not the budget.

Invoked through the command line, cells run in the calling process; the library's own default is
the broker. Under `--async` the frame relocates too — it becomes a task on the default queue while
its cells go to theirs, which is why that mode needs both workers. Same code path, same graph.

Both discovery and gating are graph nodes (LangGraph state machines, with the model calls made
directly against the SDK). The graph is used for the thing it is good at — an explicit state machine
with an inspectable transition structure — and nothing else is delegated to a framework.

## The journal

One append-only table, one writer, thirteen streams:

| stream | what it records |
|---|---|
| `agent_coverage` | what the agent claimed to have covered, per unit |
| `claim_inventory` | one row per claim, both producers — the candidate tier and the predicate-demand queue both derive from it |
| `gate_outcome` | one row per claim the gate adjudicated |
| `gate_kill` | claims the gate dropped: a dead anchor, or a kernel that raised |
| `gate_ungateable` | every typed decline, with its reason |
| `preview_verdict` | preview-grade predicate results |
| `s_verdict` | judge verdicts, with confidence |
| `s_judge_skipped` | candidates a per-run cap kept from the judge — off by default, set for measurement |
| `rail_stop` | one row per rail firing — a truncated run is partitionable from a complete one |
| `rail_config` | the run's knobs, so a later reader knows what bound it |
| `cell_result` | one terminal row per cell — the fan-in's only input |
| `frame_plan` | what the run planned, and what it funded, deferred or never dispatched |
| `run_cost` | derived from the run's own usage rows, written even on the aborted path |

The per-call tool trace is not its own stream: it rides inside `agent_coverage` and `s_verdict`.

Rows are never updated and never migrated. A normalization change is a declared migration event
handled by re-deriving keys offline from stored raw inputs — the journal itself stays as written,
because a measurement record you can rewrite is not a measurement record.

## The two tiers

**Verified findings** are gate-certified and judged live. They go to the issue store, which
reconciles them against previous scans by identity and closes an issue only when a replay says the
drift is gone.

**Candidates** are everything else: claims with no predicate that fits, claims a preview predicate
adjudicated, and claims the gate declined **for one of three surfaced reasons** — the nine others
are journaled and never reach the reader, because they are the repository's ambiguity rather than
the document's problem. They render as **unverified leads, banded** — entries the
agent marked suspected first, and *within a band the order is explicitly arbitrary*, because the
product does not have evidence that would justify a rank. A claim the gate **refuted** never appears
here; a refutation is a finding or nothing, and letting it back in as a "lead" would launder the
gate's own judgment.

## What is deliberately not here

- **No result caches.** An inventory cache keyed on the document and a verdict cache keyed on identity were
  both designed and both left out: the usage they pay off under — scanning the same commit
  repeatedly — is not the usage this version has.
- **No pre-parse router.** Skipping or reordering units before spending on them was measured across
  three probes and no cut earned a warrant. See [`evals/04-cost.md`](evals/04-cost.md).
- **No vector retrieval.** Nothing in this design consumes embedding search; discovery is agent
  cognition over a repository map and the file reader.
- **The run-fitness contract is not wired into the scan.** It exists, it gated every published
  number offline, and it is not called at runtime —
  [`evals/05-method-and-integrity.md`](evals/05-method-and-integrity.md) says so plainly rather than
  leaving a reader to find an uncalled module.
- **Language coverage is uneven on purpose.** Path, link and make-target predicates are
  language-free and work anywhere. Symbol and signature predicates are Python-only. Adding a
  language is two kernel functions and a symbol reader, not a new architecture.

## Module map

| area | what lives there |
|---|---|
| `agent/` | the discovery loop: runner, prompt, repository map, sandboxed toolbelt |
| `kernels/` | the predicate registry and every kernel; `models.py` holds the decline vocabulary |
| `gate/` | replay — the two-leg re-check, outside the loop |
| `judge/` | the semantic judge |
| `graph/` | the frame, the cell, the nodes, the read model, the candidate tier |
| `journal/` | the writer, the row serializers, cost derivation, export, the fitness contract |
| `persistence/` | issue lifecycle and reconciliation |
| `docstrings.py` | the deterministic producer for the docstring corpus |
| `symbols/` | static symbol resolution |
| `fsguard.py` | containment and unit shape — realpath on both sides |
| `domain/` | findings and repository references |
| `cost.py`, `runconfig.py` | the versioned price table; budgets and rail defaults |
| `report/` | rendering |
| `cli/`, `app/`, `tasks/` | command surface, the submission seam, the worker tasks |
