# Three retired instruments, and what each one decided

Three pieces of software were built, run against real repositories, and then retired. Each answered
a question that changed the product's shape, and each is worth more as a finding than as code. The
first ran in four separate measurement passes; the third never ran against a live model at all.
**Their code is not in this repository**, for a reason that is not tidiness: shipping it would
not make their numbers reproducible. Every sample was third-party repositories pinned at fixed
commits, never vendored — so what makes a result checkable is the frozen input list and a real
output, both of which are here.

## 1. The deterministic extractor — the finding that inverted the architecture

**What it did.** Extracted claims from document syntax directly: fenced code blocks, inline paths,
docstring signatures. No model anywhere. It resolved each claim against the repository and emitted
what it could not satisfy.

**The measurement.** A 16-repository blind sample, drawn and frozen before anything was run.

| | |
|---|---|
| emitted findings | **58** |
| true positives | **4** — and the source tightens this itself: under its own strictest reading two of the four are arguably false, which would make it 2 |
| false positives | **54** |

The probe explicitly declines to offer this as a product metric; it is a measurement of one engine on
one sample, and it is published here because of what it caused, not as a precision figure.

**The finding, and why it is not the obvious one.** The obvious reading — *determinism is too blunt,
use the model to judge* — is refuted by the same run. Against a separately measured set of known
drifts, the deterministic checks found **4 of 4** of those that survived anchoring. Determinism was
never the ceiling on *finding* things.

What failed was deterministic **claim identification**: deciding, from syntax alone, that a
particular string is an assertion about the repository at all. Most of the 54 false positives are
that failure — a `*args` fragment parsed as a parameter, a locator suffix read as a symbol, a
generated file treated as source, a path that belongs to another project.

So the architecture inverted rather than switched sides. A model became responsible for *identifying
and stating* claims, where its tolerance for ambiguity is an asset; a pure function pinned at a
commit stayed responsible for *adjudicating* them, where a model's plausibility is a liability. That
split is the design this repository implements, and this instrument is why.

**Artifact.** [`artifacts/deterministic-engine-sample.json`](artifacts/deterministic-engine-sample.json)
— one repository's output from that sample, in the tool's own report format: 23 records, each with
the document claim, the code truth and the location.

**Read it knowing how it was chosen**: it is the highest-yield of the sixteen (the next are 17, 12
and 8 records, and **eight of the sixteen are empty**), and all 23 records are one check and
effectively one cause — a markdown bullet prefix parsed as part of a signature. Of the four failure
kinds named above it shows **one**. It is published because an empty file demonstrates nothing, and
the selection is stated because a reader would otherwise take it for a representative draw.

## 2. The diff-scoping harness — drift is stock, not flow

**What it did.** Took a repository at a commit and a pull request's diff, mapped the changed lines
to the symbols they touched, and intersected those symbols with the claims documents made about
them. The intended product was event-driven: watch pull requests, comment when one breaks a
documented claim.

**The measurement.** 299 pull requests across 6 repositories, two popularity tiers, with the
repository list and the pull-request windows frozen before any of them were walked.

| | |
|---|---|
| pull requests examined | **299** |
| intersections found (mechanism fires) | 14–22% of pull requests in the higher-traffic tier; 0–22% in the mid-tier |
| **documentation drifts introduced** | **0**, in both tiers |

**The finding.** The mechanism worked — it found the overlaps it was built to find. What it did not
find was a single instance of a pull request introducing a documentation contradiction, in either
well-maintained or mid-tier projects.

Drift is **stock, not flow**. It accumulates over years through renames, extractions and moves, and
it is rarely created by an identifiable commit. An event-driven product built on this signal would
demo as *"found nothing"* on almost any repository, however much drift that repository actually
contains. The snapshot scanner became the product, and the event form was demoted to a delivery
vehicle.

**Artifact.** [`artifacts/diffscope-sample.json`](artifacts/diffscope-sample.json) — the walk's
output over the three highest-traffic repositories in the sample, keyed by repository, each carrying
its intersection records tagged with the pull request they came from: 78 records over 28 distinct
pull requests of the 150 walked in that tier.

## 3. The variadic-passthrough judge — the case for a residual

**What it did.** Adjudicated one narrow class the deterministic layer deliberately refused to
answer: a documented parameter that a function does not name in its own signature, because it
accepts `**kwargs` and forwards them.

**The measurement.** In one widely used HTTP library, **29** documented parameters are of exactly
this shape — the request helpers document `params`, `data` and others that they forward rather than
declare.

**The finding.** Those 29 are precisely where a naive documentation checker emits 29 false positives:
by signature the parameter is absent, and by behaviour it is honoured. The deterministic layer
withheld **all 29** rather than emitting them. **Zero false positives** came out of that class —
and the zero is the mechanical layer's *refusal*, not a model getting 29 adjudications right. No
model ran in this measurement at all; the residual was routed to one by design and never
adjudicated here.

**Artifact.** The same file — [`artifacts/diffscope-sample.json`](artifacts/diffscope-sample.json) —
carries this instrument's evidence in its `conf` column: **17 records marked `NEEDS_LLM`**, every one
of them in that HTTP library, and none anywhere else in the walk. That is the withholding happening,
recorded live.

⚑ **Two counts, two denominators, deliberately not merged.** **29** is how many documented parameters
in that library are of the passthrough shape. **17** is how many intersection records in this
particular walk were withheld. They are different populations and neither is the other's subset;
publishing one under the other's label is the kind of thing this directory exists to avoid.

That is the residual pattern the whole system now runs on, stated in its smallest form: the
mechanical layer must be allowed to answer *"I decline"* as a first-class outcome, distinct from
*"refuted"*. Today that outcome is a closed, enforced vocabulary of twelve reasons — external
target, unreachable module, variadic signature, ambiguous base, and so on — every one of which is
mechanically decidable and journaled. A skip is never silent, and it is never routed to the judge as
if it were a semantic question.

## Why these are here rather than in a commit history

Each of these instruments cost real time and produced a number that redirected the project. Deleting
them without publishing what they measured would leave the current architecture looking like a set
of arbitrary preferences. The three findings — *the mechanical layer's problem was identification,
not adjudication*; *the signal is stock, not flow*; *a decline must be a first-class outcome* — are
the load-bearing arguments for the design in [`../ARCHITECTURE.md`](../ARCHITECTURE.md).
