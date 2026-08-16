# The semantic judge — what it decides, and how accurate it is

The judge answers exactly one question, for every claim class: **is this assertion still live?**
Does the document still mean to assert the thing, or is it recording history, describing a
superseded state, or quoting something it has already marked as outdated?

It is deliberately not asked whether the assertion is *correct*. Whether the repository agrees with
the document is a mechanical question, answered by replaying a pure predicate at a fixed commit.
Splitting the two is the whole design: a live assertion may well be wrong about the current
repository, and detecting that is the rest of the system's job — possible only if genuinely live
claims are kept alive.

The two error modes are not symmetric, and they land in different places:

- a **false live** would become a bad verified finding, and so lands in the false-positive count of
  [`01-verified-tier.md`](01-verified-tier.md) — where none has;
- a **false not-live** silently drops a real drift, and **nothing else in the system can see it**.

That asymmetry is why the golden set exists.

## The golden set

**18 measurement items, 16 of them independent** (12 framed live, 4 framed not-live). Items were
drawn from historical measured corpora rather than written for the occasion. **One item's label is
contested, and it is excluded from every count on this page** — the counts below are over the
remaining set. Excluding it does not move them; it is stated because a reader cannot see an
exclusion that is not disclosed.

Four limits are structural, and none of them is worked around.

**The labelling was not blind.** The pass was run separately from selection, but the labeller's
context carried the project's own working hypothesis and one item's name. The instruction to
disregard prior context is an instruction, not a control, and the record classifies the result as
one disclosed-contaminated opinion rather than an independent adjudication. Nothing here should be
read as a clean-label figure.

**Each direction has its own denominator, and neither is 18.** A false not-live can only occur on an
item labelled live — **12** of those. A false live only on one labelled not-live — **4**.

**The false-live class is the small one, and its strongest reading is a single item.** Of those four,
**2** survive both defensible label mappings, and **1** survives both mappings *and* carries no
distribution caveat. Both mappings are reported for every count below, because neither is
privileged: some labels carry caveats, and how a caveated label is read changes the count.

**The distribution is not the shipped system's.** Five of the eighteen items are literals produced by
the retired deterministic extractor, which today's agent may never propose. **No negative item was
produced by the shipped pipeline at all.** Two of the items are scans of this project's own
repository.

**Labels are liveness labels**, so they stay valid across judge versions — which is what lets
accuracy be re-measured against the same labels whenever the prompt changes, without re-adjudicating
anything.

## The number

Over the 18 items, the shipped judge version produces:

| | count |
|---|---|
| independent false not-live | **0** (of 12 live items) |
| false live, looser mapping | 2 (of 4 not-live items; 2 mapping-robust, 1 caveat-free) |
| false live, stricter mapping | 0 |

For contrast, two earlier versions of the same prompt over the same items: the baseline produced
**3** independent false not-live, and the first iteration produced **6** — it got worse before it
got better. The shipped version is the second iteration.

**Each item was scored once, and the judge is not deterministic at a fixed version** — one golden
item is recorded both killed and passed under the same judge version, in two committed measurements.
These are single-draw counts, not stable ones.

**And it is a development figure with no held-out check.** The items were used while the prompt was
being changed, so the number describes the prompt's fit to the material it was tuned against.
Establishing a held-out figure needs a second, independently assembled golden set; that was never
built, and no paid re-run is proposed here in place of it. The label is part of the result.

The two false lives above are golden-set items, measured against stored labels. They are **not**
among the six verified findings and are not inside that page's false-positive count: the two
measurements have different populations, and conflating them would double-count.

## What is deliberately not published: why the shipped version is better

The obvious next sentence — *"the shipped prompt works because of X"* — is not available, and the
reason is worth more than the sentence would have been.

Two experiments were run to establish the cause, and both are disqualified as evidence:

1. The controlled comparison was described as removing the mechanical outcome from the judge's
   input. It did not: the rendering function emitted that outcome into user content identically in
   every version compared. The experiment therefore compared *leak with framing* against *leak
   without framing*, which is not the comparison it was recorded as making.
2. The iteration that produced the improvement changed **two things at once** — it separated the
   liveness criterion from the accuracy criterion, and it restricted the judge's tool scope. The
   second change has its own visible signature in the data (citations of repository evidence went
   from four to eight to zero across the three versions), so the effect cannot be attributed to
   either change alone.

The **outcome** counts above stand — they are direct measurements over a fixed item set. The
**causal story** does not, and reporting it would be reporting an unseparated pair as if it were a
controlled result.

## The one time it adjudicated a real finding

The six verified findings in [`01`](01-verified-tier.md) went through this judge. It returned
**17 verdicts across the three runs, all live, with no kills**, and none of the six was declined by
the gate for any reason. That is the judge's only appearance in a minting path, and it is the whole
of it.

## What is unmeasured

The judge's behaviour on **fresh material after the kernel fixes is unmeasured**. In the widening
pass over nine documents, nothing reached the gate's certified output, so the judge was never
invoked: **zero verdicts across all eighteen runs**. Every cost and behaviour figure from that pass
therefore describes the discovery half of the pipeline only — see
[`04-cost.md`](04-cost.md).

## Reproducing this

Because the labels are judge-version independent, a prompt change is re-measured against the same
18 items rather than re-adjudicated. That is a **paid** run — the three recorded passes cost about
$2 each. What is free is re-scoring verdicts already stored:

```bash
drift dev check /path/to/repo path/to/doc.md --strict-measurement --journal-export run.jsonl
```

Each verdict row in the export carries the judge version, the model and the confidence, so a
re-measurement is a join between the export and the label set rather than a re-adjudication.
