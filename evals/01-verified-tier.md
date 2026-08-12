# The verified tier — what drift has actually certified

The verified tier is the only place drift makes a claim in its own voice. A finding reaches it only
if both of these hold:

1. **A pure function re-derives the refutation** from the repository at a fixed commit. The
   predicate, its normalized arguments and its two legs are stored with the claim, and the gate
   replays them. No model output participates in this step.
2. **The semantic judge holds the assertion live** — that the document still means to assert the
   thing, rather than recording history or describing a superseded state.

The model proposes claims; it cannot certify one. That is a structural property, not a policy: a
claim whose predicate is preview-grade leaves the candidate set before the gate's inputs are built,
and the judge only ever sees what the gate certified.

## The number

Over the development corpus, drift has emitted **6 verified findings**. **0 of the 6 are false
positives.**

**The six are a union over three runs** of the same document at the same commit, which produced 5,
6 and 6; the identity curve had not saturated at three. Discovery is not deterministic, and the
shipped command is single-pass — so one run is not guaranteed to return all six. That is a property
of the discovery half; the verification half is deterministic by construction.

Counts, not a rate: with six emitted findings a percentage would imply a precision the sample does
not carry. The acceptance target for this measure was a false-positive rate at or under one in
five, with the explicit provision that a sample of fewer than ten emitted findings is reported as
counts.

**Per predicate:** 6 of 6 `path_exists`. The **four** other high-grade predicates —
`link_resolves`, `symbol_resolves`, `signature_has_param`, `make_target_exists` — were live during
the same runs and certified nothing. The two preview-grade predicates, `class_has_member` and
`manifest_key_exists`, ran and journaled but cannot certify by construction. One predicate has
minted every finding drift has.

## The six

All six are on one document: **`jupyter_server/i18n/README.md`** in `jupyter-server/jupyter_server`,
at commit **`5b6f030`** — taken after that project was extracted from the Jupyter Notebook codebase.
The document still refers to paths under the old package. The repository and commit are named
because a reproduction claim without them is not one.

| line, as reported by this run | asserted path | why it is stale |
|---|---|---|
| 32 | `notebook/i18n/` | directory moved with the extraction |
| 40 | `notebook/i18n/notebook.pot` | as above |
| 42 | `notebook/i18n/nbui.pot` | as above |
| 44 | `noteook/i18n/nbjs.pot` | as above, **and misspelled in the source document** |
| 53 | `babel_nbjs.cfg` | file no longer present at the repository root |
| 78 | `notebook/i18n/nbjs.json` | as above |

The line column is the anchor **this** run reported; the other two runs anchored the same paths at
different lines, so the path is the stable key and the line is not. Each finding was adjudicated by
hand against a checkout pinned at the same commit before it was counted —
the refute-verification duty applies to every emitted finding, not to a sample. The fourth row is
worth its own sentence: the document contains a typo in the path, and the system reported the typo
as its own drift rather than silently normalising it away.

Three further paths in the same document are genuinely unresolvable rather than stale — their
implied base is ambiguous — and the gate declined to certify them instead of guessing. That decline
is journaled with its reason and **does not surface in the report**: of the twelve decline reasons,
only three are routed to the reader, and they are the ones that are the document's problem rather
than the repository's.

## What "6" is the denominator over, stated rather than assumed

**The six come from the current configuration.** An earlier configuration emitted one further
finding, and it was a false positive: a class-member assertion bound to a symbol-existence
predicate, which reported a missing symbol for a member that the class did in fact expose through
its parent. Two fixes followed — the symbol kernel now declines rather than refutes when it cannot
see a signature, and class-targeted assertions bind a preview-grade `class_has_member` predicate,
which cannot mint. Under the current configuration that same claim is structurally unable to
produce a finding.

So there are two defensible denominators, and this document uses the first:

- **current configuration** — 6 emitted, 0 false.
- **every configuration ever run** — 7 emitted, 1 false.

Both are under the acceptance target. They are different sentences, so the choice is stated here
rather than left to the reader. The earlier false positive is disclosed above rather than folded
into the count, on the same ground that separates the retired deterministic engine's record from
this one.

**What the fixes removed, measured over the whole corpus.** Replayed over 2045 stored claims at
pinned commits, with the pre-fix kernels restored from history on one side and the shipped source on
the other, certifications fall from **49 to 20** at the gate. Two further claims leave earlier than
the gate — a narrowed link-jurisdiction rule declines them at normalisation — so the final certified
population is **18**. Both numbers describe the same replay at two layers, and they are given
together because a count without its layer is the kind of thing that gets re-litigated.

Of the 29 removed, 25 were one class: a symbol whose signature could not be derived statically was
being refuted rather than declined.

**The fix was demonstrated, not assumed.** The document that produced that false positive was
re-scanned five times under the current configuration: **zero certified, zero emitted, every time**.
The claim that used to mint now binds a preview-grade predicate and appears in the candidate tier,
which is where an assertion the gate cannot adjudicate belongs.

## What was not done to reach this number

The rules the measurement ran under, stated because a false-positive count is only as good as the
freedom its author had to tune it:

- **the predicate set was chosen on development data only.** No predicate was added, narrowed or
  withdrawn on the strength of what it did to a published figure;
- **nothing was re-selected after seeing a result.** Where a number came in worse than intended it
  was published with its per-item adjudication rather than tuned away — the cost target in
  [`04`](04-cost.md) is the visible instance;
- **no architecture question was reopened to improve a number.**

Those constraints are why the earlier configuration's false positive is disclosed above instead of
being absorbed into a cleaner denominator.

## Concentration — the limit that matters most

**All six findings are in one file, in one repository.** A later widening pass over nine documents
across six repositories, on material with fifteen independently known drifts, added **zero**.

This is the honest shape of the result: the verification mechanism works and has never certified
something false, and the population it has certified over is one document. Any reading of "0 of 6"
that treats it as a precision estimate for arbitrary repositories is unsupported by this evidence.

## A separate population, so that "six true positives" is not read as the whole record

Before the agent-driven pipeline existed, drift extracted claims deterministically from document
syntax. On a 16-repository blind sample that engine emitted **58** findings: **4 true, 54 false**.
That system is retired, its code is not in this repository, and its number belongs to it rather
than to the pipeline measured above — but it is the reason the current design puts a replay gate
between the model and every published claim. The full account is in
[`06-retired-instruments.md`](06-retired-instruments.md).

## Reproducing this

Every figure above from the current pipeline came from the single-document measurement form, which
is part of the shipped command surface for exactly that reason (the retired engine's 58/4/54 came
from a different tool — see [`06`](06-retired-instruments.md)):

```bash
drift dev check /path/to/repo path/to/doc.md --strict-measurement --journal-export run.jsonl
```

The export contains one row per claim, per gate outcome and per judge verdict, each stamped with
the agent version, judge version and model. A real output of the run that produced the six is
committed at [`artifacts/verified-findings-sample.md`](artifacts/verified-findings-sample.md).

The repositories these numbers were measured on are third-party projects pinned at fixed commits.
They are not vendored here, so reproducing a figure means cloning that project at that commit and
pointing the command at it. Discovery is not deterministic, so expect the set to take more than one
pass — see the note under *The number*.
