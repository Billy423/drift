# drift report

## Verified findings — 0

_none_

## Ranked tier (candidates — UNVERIFIED) — 21

_Not certified by the replay gate. These are candidates the agent surfaced, banded by its own confidence that each claim still holds — read them as leads, never as findings._

### From the agent · unexamined (confidence > 0.2; not ranked within the band) — 21

- `ARCHITECTURE.md`: 58 findings of which 54 were false
  - Matches the numbers published in evals/06-retired-instruments.md (58 emitted, 54 false positives, 4 of 4 known drifts found); no mechanical predicate covers cross-doc numeric consistency.
- `ARCHITECTURE.md`: high     path_exists · link_resolves · symbol_resolves
                  signature_has_param · make_target_exists
         preview  manifest_key_exists · class_has_member
  - All seven names are registered Predicate objects in src/drift/kernels/__init__.py and registry.py, with the stated high/preview grade split confirmed in code.
- `ARCHITECTURE.md`: planning
  - Refers to src/drift/graph/planning.py, which exists; literal has no extension so a strict path check would likely miss it.
- `ARCHITECTURE.md`: dispatch
  - Refers to src/drift/graph/dispatch.py, which exists; literal lacks extension.
- `ARCHITECTURE.md`: fanin
  - Refers to src/drift/graph/fanin.py, which exists; literal lacks extension.
- `ARCHITECTURE.md`: journal_rows
  - Refers to src/drift/graph/journal_rows.py, which exists; literal lacks extension.
- `ARCHITECTURE.md`: session_read
  - Refers to src/drift/graph/session_read.py, which exists; literal lacks extension.
- `ARCHITECTURE.md`: Invoked through the command line, cells run in the calling process; the library's own default is the broker.
  - Behavioral description of dispatch defaults (celery_app.py / dispatch.py); not mechanically checkable by any predicate here.
- `ARCHITECTURE.md`: --async
  - Confirmed: scan command in src/drift/cli/main.py defines a `run_async` option flagged `--async`.
- `ARCHITECTURE.md`: `external` · `module-unreachable` · `variadic` · `no-makefile` · `gitignored` · `makefile-includes`
· `base-ambiguous` · `no-manifest` · `manifest-unparseable` · `no-signature` · `external-base` ·
`not-a-class`
  - Matches UNGATEABLE_REASONS frozenset exactly in src/drift/kernels/models.py (12 entries).
- `ARCHITECTURE.md`: in one HTTP library that is 29 documented parameters
  - Matches the 29-parameter example described in evals/06-retired-instruments.md's variadic-passthrough section.
- `ARCHITECTURE.md`: One append-only table, one writer, thirteen streams
  - Table lists agent_coverage, claim_inventory, gate_outcome, gate_kill, gate_ungateable, preview_verdict, s_verdict, s_judge_skipped, rail_stop, rail_config, cell_result, frame_plan, run_cost — 13 names; several (agent_coverage, frame_plan, rail_config, run_cost) confirmed as record_type literals written in src/drift/graph/journal_rows.py.
- `ARCHITECTURE.md`: The per-call tool trace is not its own stream: it rides inside `agent_coverage` and `s_verdict`.
  - Behavioral/structural claim about journal payload shape; not independently verified beyond partial code read.
- `ARCHITECTURE.md`: claims the gate declined **for one of three surfaced reasons** — the nine others
  - Consistent with the 12-member UNGATEABLE_REASONS set (3 surfaced + 9 hidden = 12), though which three are 'surfaced' isn't independently verified here.
- `ARCHITECTURE.md`: No result caches.
  - Design-decision claim about absence of a caching layer; not mechanically verifiable by any predicate here, would require exhaustive absence search.
- `ARCHITECTURE.md`: No pre-parse router.
  - Design-decision claim; not bindable to a predicate.
- `ARCHITECTURE.md`: No vector retrieval.
  - Design-decision claim; not bindable to a predicate.
- `ARCHITECTURE.md`: The run-fitness contract is not wired into the scan.
  - Confirmed by src/drift/journal/completeness.py's own docstring: 'Neither is called from `drift` itself'.
- `ARCHITECTURE.md`: Language coverage is uneven on purpose.
  - Path/link/make-target kernels are language-agnostic while symbol_resolves and signature_has_param rely on Python-specific static analysis (griffe); consistent with kernels/pysymbols.py and symbols/griffe_provider.py, but no predicate captures this claim.
- `ARCHITECTURE.md`: Each journal row is stamped with the agent version, the judge version and the model, and those three columns are not-null at the database.
  - Confirmed: JournalRecord.agent_ver, judge_ver, model are all `nullable=False` in src/drift/persistence/models.py.
- `ARCHITECTURE.md`: the wallet is
checked before a cell starts and **never cuts one in half**, so a budget stop always leaves whole
units done and whole units untouched
  - Behavioral claim about the dispatch/fan-in wallet logic in graph/fanin.py and graph/dispatch.py; not mechanically bindable, plausible from partial code reading.

