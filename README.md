# drift

**Documentation goes stale silently.** A directory is renamed, a package is extracted, a parameter
is dropped — and the README that described it keeps looking fine. Nothing fails, no test goes red,
and the wrong instructions sit there until someone follows them.

drift finds those contradictions and reports the ones it can prove.

> **The model discovers, a pure function verifies.** A language model reads a document and states
> what it asserts about the repository. A function of the checked-out tree — with no model output in
> scope — decides whether each assertion still holds. The model cannot mint a finding; that is a
> property of the pipeline's shape, not an instruction in a prompt.

## Overview

A scan reads every document in a repository and reports in two tiers, and the difference between
them is the whole point.

- **Verified findings** — the refutation was re-derived outside the model's loop, at a fixed commit,
  and a judge confirmed the document still means to assert the thing. These are stated as fact.
- **Candidates** — everything else the agent surfaced: claims no predicate fits, claims a
  preview-grade predicate adjudicated, and the three decline reasons that are the document's problem
  rather than the repository's. Labelled unverified, banded by the agent's own confidence, and
  offered as leads rather than findings. Within a band the order is arbitrary, and the report says
  so.

A claim the gate *refuted* never appears as a candidate. A refutation is a finding or it is nothing.

## Installation

Requires Docker (for Postgres), Python 3.11 or later, and an `ANTHROPIC_API_KEY` in the environment
you run from.

```bash
make install        # venv, editable install, dev dependencies
make up             # Postgres and Redis
make migrate        # create the schema
export ANTHROPIC_API_KEY=sk-...
```

Database and broker URLs have local defaults that match `docker-compose.yml`; see `.env.example`.
`make help` lists every target.

## Usage

```bash
make scan REPO=/path/to/repo      # PAID — every document unit calls a model
```

The full two-tier report prints to stdout as the scan runs. `make results` is a separate thing: it
re-reads **persisted findings** from a previous scan, needs `psql`, and prints the verified tier
only — the candidate tier is not persisted.

### Commands

| command | what it does | needs |
|---|---|---|
| `drift units <repo>` | lists the documents a scan would read — `.md`, `.rst`, `.txt` | nothing |
| `drift check <repo> <doc>` | scans one document | API key, Postgres |
| `drift scan <repo>` | scans every document | API key, Postgres |
| `drift scan --async <repo>` | submits to workers instead | Redis and **both** workers; the key and database live in the workers, not this shell |
| `drift dev check` / `drift dev scan` | the same, with measurement options | as above |

Without `--async`, nothing is published to a queue: the frame runs in your own shell and each cell
runs inline with it, so a database and an API key are the whole requirement.

### Running asynchronously

**`drift scan --async` needs both workers running at once.** The frame is submitted to the default
queue and then waits on its cells, which are tasks on their own queue — a single worker consuming
both would hold its slot waiting for work it is itself blocking.

```bash
make worker         # the default queue: runs the frame
make worker-cells   # the drift.cells queue: runs one cell at a time
```

### Budget

**`make scan` defaults to a $5.00 ceiling**; `drift check`, being one document, defaults to $0.50.
Cap either explicitly:

```bash
.venv/bin/drift scan /path/to/repo --budget 0.50
```

The budget is checked before each cell is dispatched and never stops one halfway, so the overshoot
bound is the budget plus one cell. A budget stop is a normal outcome, not a failure: the
run finishes, reports what it covered, and says what it did not. Above a $1 estimate `drift scan`
asks for confirmation first; `--yes` skips the prompt.

With **no** API key the failure mode is not obvious: the SDK client constructs successfully — it
resolves no key and fails only when it sends — so every unit dies on its first request, the run
finishes marked unfit, and `make results` prints nothing. The authentication error appears on stderr,
not in the report; under `--async`, on the cells worker's stderr.

### What a scan sends

For each document, drift sends the model the document's text, a map of the repository's tracked
files, and the contents of files the agent chooses to read. The file reader is confined to the target
repository by realpath on both sides, tool output is budgeted per call and per document, and spend is
capped by the wallet. **If a repository has secrets in tracked files, a scan can send them to the
model API** — point it at repositories where that is acceptable.

## Results

[`evals/`](evals/) is the measured record, written to be read by someone who does not believe it.

| | measured |
|---|---|
| verified findings emitted | **6**, of which **0** are false positives |
| predicates that have ever minted a finding | **1** of 5 high-grade |
| semantic judge, 18 golden items | **0** false not-live over the **4** not-live items |
| known drifts reached and named, of 15 | **13** — and **0** of them produced a verified finding |

The last row is the interesting one. On documents whose drifts had already been adjudicated by hand,
the agent found and named thirteen of fifteen — and for eleven of those the literal it anchored on is
byte-identical to the stale literal in the document. What fails is not discovery. It is **binding**:
turning a correctly identified assertion into a predicate a pure function can replay.

The limits are stated alongside the numbers rather than in a footnote:

- **all six verified findings are in one file, in one repository**;
- everything measured comes from material that informed the system's design — depth of record,
  **not** generalisation, with no held-out sample;
- **no recall claim is made**. Nothing here measures what fraction of a repository's real drift is
  found;
- the judge figure is a development number with no held-out check, and scan cost is over the
  project's own target.

[`evals/05-method-and-integrity.md`](evals/05-method-and-integrity.md) records four places where the
measurement apparatus itself went wrong.

To see the shape of a run rather than the numbers,
[`evals/artifacts/self-scan-sample.md`](evals/artifacts/self-scan-sample.md) is this repository
checked against its own architecture document: twenty-one assertions surfaced, none certified as
drift.

## Architecture

Discovery is a model loop with no authority; a closed registry of pure predicates is the waist; a
replay gate outside the loop is the only thing that can certify a finding; a semantic judge sees only
what the gate certified. A scan splits into a **frame** that owns the run, the worklist and the
wallet, and **cells** that each own one producer applied to one file.

Full account, including the invariants and the measurement that produced this arrangement:
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Stack

| | |
|---|---|
| language | Python 3.11+ (CI is configured for 3.11 and 3.14) |
| agent | LangGraph for the state machines with the Anthropic SDK |
| model | one pinned Anthropic model, `claude-sonnet-5` |
| store | Postgres, SQLAlchemy, Alembic |
| queue | Celery on Redis |
| symbols | Griffe in static mode — reads the AST, never imports the scanned code |
| CLI | Typer |
| dev | pytest, ruff, Docker Compose, GitHub Actions |

Dependencies carry upper bounds rather than a lockfile: a lockfile pins one machine's resolution,
while the bounds state the range the project claims. The CI matrix proves the **interpreter** range,
not the dependency range.

## Project structure

```
src/drift/
  agent/          the discovery loop: runner, prompt, repository map, sandboxed toolbelt
  kernels/        the closed predicate registry and every predicate implementation
  gate/           replay — the two-leg re-check, outside the model's loop
  judge/          the semantic judge
  graph/          the frame, the cell, their nodes, the read model, the candidate tier
  journal/        append-only writer, row serializers, cost derivation, export, fitness
  persistence/    issue lifecycle and reconciliation across scans
  symbols/        static Python symbol resolution
  domain/         findings and repository references
  report/         rendering
  cli/            the command surface
  app/, tasks/    the submission seam and the worker tasks
  docstrings.py   the deterministic producer for the docstring corpus
  fsguard.py      containment and unit shape
  cost.py         the versioned price table
  runconfig.py    budgets and rail defaults

tests/            unit, database-backed, and end-to-end, mirroring src/drift
migrations/       Alembic
evals/            the measured record, with the committed outputs it cites
```

## Roadmap

The measurements decide the order, and they point somewhere specific: **the bottleneck is binding,
not discovery.** The agent already reaches and names drift it cannot get certified, so the work with
the highest return is not better prompting.

- **Predicate vocabulary.** Unbound claims are persisted with the argument the agent proposed, which
  makes demand a queue rather than a guess. A new predicate enters at preview grade, where it runs
  and is measured without being able to move a published number, and is promoted on measured
  precision over its own fires.
- **A second language.** Path, link and make-target predicates are already language-free. Symbol and
  signature predicates are Python-only, and adding a language is two kernel functions and a symbol
  reader — not an architecture change.
- **Delivery as a pull-request check.** A GitHub Action or review comments are the obvious product
  surface, and they sit deliberately behind the command line. Over 299 pull requests across six
  repositories, the diff-scoped form found **zero** drift events: drift accumulates over years
  through renames and extractions and is rarely introduced by an identifiable commit, so an
  event-driven product would demo as *"found nothing"* on almost any repository.
- **Wiring the run-fitness contract into the scan**, so a scan states its own publishability instead
  of that verdict being computed offline.
- **Cost.** The one lever of the right magnitude is a cheaper model tier, held down by the model pin
  while figures are published under one model.
- **Caches**, both designed and both deliberately unbuilt: they pay off when the same commit is
  scanned repeatedly, and whether that is a real usage pattern has not been measured.

## Development

```bash
make corpus   # clone the pinned third-party repository six tests replay against
make test     # full suite; needs `make up` and `make migrate` first
make lint     # ruff
make fmt      # ruff --fix and format
```

`make corpus` is not optional if you want the whole suite to mean something: six tests replay the
regression pin behind the six verified findings in [`evals/`](evals/), and without the clone they
skip with a message rather than failing.

Database-backed tests run against real Postgres rather than a mock, and the end-to-end test drives
the real command surface down to a real row.
