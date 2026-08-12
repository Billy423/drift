# Cost — what a scan spends, and which levers were pulled

A scan costs real money: every document unit is at least one model call, and the discovery agent
reads files under a tool budget until it can state its claims. This page publishes the measured
distribution, names the optimisations that were made, names the ones that were evaluated and
declined, and names the largest one that was not available.

**The headline: drift is over its own cost target, and this document says so rather than
re-denominating until it is not.**

## The target, and the disclosure that goes with it

The acceptance target was a median whole-repository scan at or under $0.50, with a 90th percentile
at or under $2.50.

**The target was set before any per-repository cost had been measured.** It was an intention, not a
regression threshold, and there is no earlier validated baseline that a later number could be said
to have regressed against. Stating this is part of the result: without it, an over-target figure
reads as a system that got worse, and it did not.

## The whole-repository record

| | measured |
|---|---|
| median | **about $2.06** |
| 90th percentile | **$13.05** — see below |

**n = 8 repositories, so the 90th percentile is the maximum — and it is censored.** That scan
stopped on its own cap, which makes $13.05 a floor rather than a cost. Three of the nine scans
behind these figures stopped short the same way.

**These are pre-fix numbers and are labelled as such.** They were measured before two kernel
corrections and before the emit-shape fix described below, on an earlier agent version. They stand
as the record because nothing has replaced them.

**No post-fix whole-repository figure exists**, and the reason is a denominator rule rather than
laziness. After the fixes, the corpus holds essentially one whole-repository scan — and that scan
was itself stopped by its budget at **40 of 122 document units**. Every other post-fix run is a
single-document run. Aggregating single-document runs into a per-repository median would be
computing a different measure and publishing it under the old name.

## What the post-fix corpus does support

**Per document, discovery half only**, over 20 observations:

| | |
|---|---|
| min | $0.03 |
| median | $0.26 |
| max | $0.88 |

**Discovery half only** is not a footnote. Across all eighteen runs of the widening pass the
semantic judge produced **zero verdicts** — nothing reached it — so this distribution excludes the
adjudication half entirely and is a lower bound of unknown size. It must not be quoted in place of
a per-repository figure.

## The most publication-relevant cost fact

On an ordinary open-source repository under a $4.20 budget, the wallet **bound before the corpus was
covered**: 40 of 122 document units funded, 82 deferred, none silently dropped. The run's total is
therefore a floor for that repository, not its cost. The run finished, was
judged fit to publish, and said in its own output what it had not covered.

That is the honest operating shape of this system today: on a large repository you buy a prefix of
the work, and the tool tells you which prefix.

The spend inside that scan is fat-tailed. Per document unit: min $0.01, median $0.02, max $1.20 —
the most expensive unit is about **54 times the median**, and about 134 times the cheapest. **The six most expensive of 40 units accounted for roughly three
quarters of the discovery spend**, and output tokens were about half of it. Cost is driven by
document count and by a small number of very large documents, not by a per-call price.

## The levers

**Pulled.**

- *Emit shape.* The final structured-output call was reshaped so that it can read the prompt cache
  instead of invalidating it. Before the change the emit turn was a full-prefix cache miss that then
  re-wrote the prefix; after it, the same turn reads its cached prefix exactly. Measured on a live
  arm, the attributable saving is about a fifth of that unit's cost — **one document, n = 1**, and
  no campaign figure is extrapolated from it. A blended figure from the same arm
  looked much larger and is not quoted, because most of that difference was variance in adaptive
  thinking rather than the fix.

**Evaluated and declined, each with its number.**

- *A pre-parse router* — skipping or reordering document units before spending on them. Four
  measurement probes were run across two sessions. No cut earned a warrant: the safety proxy was
  falsified
  (a zero-anchorable prediction mispredicted zero-bound units on 35 of 146, which would have
  destroyed 86 bound claims and 3 certified ones), the ignore-file cut caught nothing, no ordering
  key separated from the null, and the mid-distribution scans a router would target contain nothing
  that probe's cuts would touch. The router is therefore **not built**, and this is a measured decision rather
  than a deferral.
- *Dynamic per-unit output ceilings.* Billing is by tokens generated, so a ceiling saves only by
  truncating — and a truncated structured emit is invalid, which costs the unit. Median per-call
  output is about 162 tokens; 1 of 40 units averages above 4,096 output tokens — a candidate reduced
  ceiling, well under the 16,384 actually in force — and the document that motivated the idea clamps
  to the cap under any document-length policy. **At every safe ceiling the measured saving
  is about zero.**

**Not pulled, with its number.** The one lever of the right order of magnitude is a cheaper model
tier: modelled at a median of **$0.51** and a 90th percentile of **$4.35** — pre-fix figures, and
**still over the target on both** ($0.50 and $2.50). Both are modelled, and modelled **before** the
emit-shape fix; a re-derivation on post-fix numbers is owed and has not been done. That is the finding, not a near miss: every
lever measured, at its physical ceiling, lands over the line.

The pair is modelled from a per-scan median of about $1.53 across nine scans, a different base from
the $2.06 this page headlines; the two are not points on one curve.

It is unavailable to this version for four reasons, of which two are structural: every published
figure here was produced by one pinned model, so a swap re-baselines all of them; and the largest
document in the measured corpus exceeds the smaller tier's context window by a wide margin. This is
named here so the result reads as *the largest available lever is held down by a version lock, and
its magnitude is already computed* — not as *we tried and missed*.

## The estimator, and why the wallet is the control

A pre-flight estimate is printed before a paid run. It has four observations against actual spend:
two under by 6 and 4.6 times, a third under by 1.47, and one **over** by 1.2. **The direction is not
stable, the magnitude is not either, and no coefficient should be fitted to four points.** The estimate is a courtesy; the control is the
`--budget` ceiling, which is checked before each unit is dispatched and stops the scan at a document
boundary rather than cutting one in half.

## What is not measured

- The judge's cost after the fixes. Exactly **one** judge verdict exists across every post-fix run,
  and none of it is in the eighteen campaign runs, which produced none at all. One observation is
  not a cost.
- Two instrumentation gaps make some cost questions unanswerable without new runs: the API stop
  reason is not journaled anywhere, and a single-document run is not distinguishable from a
  whole-repository run by the journal alone.

One more thing before comparing these figures to your own bill: the price table used to compute them
is the model's **list** price, while the account was billed at a lower introductory rate during the
measurement period. Every figure here is conservative — the runs cost less than the numbers say.

Cost is reported here because it is real and because someone evaluating this should see it. It is
not one of the project's headline numbers.
