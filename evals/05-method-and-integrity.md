# How to check these numbers, and where the machinery failed

Every figure in this directory **from the current pipeline** came out of the same instrument: an
append-only run journal. The three retired instruments in [`06`](06-retired-instruments.md) predate
it, and their outputs are committed instead. This page describes the journal, states the property
that makes a number re-derivable, and then records the four places where the measurement apparatus
itself went wrong. The last part is the point of the page.

## The journal

Every run writes rows, never updates them. There is one writer, so a stream cannot acquire a second
schema by accident. The streams cover each stage of the pipeline: what the agent claimed, what the
gate decided, what it declined to decide and why, what the judge decided, which rails fired, what each
call cost, and what each tool call returned.

Three columns are mandatory and not null on every row: **the agent version, the judge version and
the model**. A prompt change bumps a version; a model change is a re-baselining event for every
number produced under the old one. That is why this directory labels figures by configuration rather
than presenting one merged history.

The price table used to convert token counts into dollars is itself versioned and journaled with the
run, and an unpriced model raises before any paid work starts rather than silently costing the
wrong amount.

## The fitness contract

A run states whether it is fit to publish from. The contract enumerates eleven ways a run can be
incomplete — a rail stopped it, a unit errored, a unit was truncated, coverage fell short, cells
went unreported, a unit produced nothing, a kernel raised, the judge errored, there were no units,
and so on — and splits them into those that defeat publication and those that are forgiven because
they are incompleteness by design. An empty list means publishable.

**Every pipeline number in this directory was gated by that contract** — computed offline, by the
driver that ran the measurement campaigns, over each run's own journal rows. The retired
instruments predate the contract as well as the journal.

**And the module ships unwired.** Nothing in the running system calls it: the fitness verdict is not
computed at scan time, and the function is a library that only tests and analysis code invoke. This
is stated rather than quietly left for a reader to discover, because finding an uncalled module in a
repository normally means something was forgotten. What happened is more specific: it was written
for the offline analysis, its only non-test caller was ever the campaign driver, and the report
already carries its own incompleteness banner — the module was written to *agree* with that banner,
not to replace it. A naive wiring was never available either: one of the eleven modes asks whether
the run finished, which cannot be answered at the moment the report is rendered.

Wiring it into the scan is the honest form of the contract and is the strongest sentence this
repository could make about its own numbers. It is deferred, not overlooked.

## Prompt fingerprints

Both model-facing prompts hash six surfaces together — the system text, the rendering function's
source, the live predicate vocabulary, the emit tool's name and description, and the output schema —
and journal the digest with every run, so any figure can be tied to the exact prompt that produced
it.

| prompt | under which the published figures were produced | in this repository |
|---|---|---|
| discovery agent | `efb09415f4e64006…` | `f500d006b2e012a7…` |
| semantic judge | `f61c3eb7f9bd7261…` | `5d612c329c687174…` |

The digests differ because this repository's comments and docstrings were rewritten for publication,
and the hash covers the rendering function's source including its comments. **The invariant that was
actually verified, at every batch of that rewrite, is that the rendered prompt text is byte
identical between the two columns** — the model saw the same bytes before and after.

**The fingerprint does not cover everything the model sees.** The tool descriptions in the agent's
toolbelt are model-facing text sent on every turn, and they are outside the hash. The digest is an
integrity check on the prompt renderers, not on the whole request.

## Four failures in the measurement apparatus

**1. A measurement plan was violated by its own tooling.** A campaign's plan was written and frozen
before any paid run. Two of its frozen sentences claim the aggregation script was built and
round-trip tested before the freeze. It was not — it was written after the paid runs completed. A
reader named in the same document as the fail-closed loader does not exist, and a rule for combining
independent passes was added after the freeze without the amendment ritual the document specifies
for exactly that.

This is material rather than embarrassing-but-harmless: the matching rule's *implementation* decides
**the headline count of drifts reached — 12 or 13** — and it was written with the results already in
hand. That is why [`03`](03-ranked-tier.md) leads with the lower number. One of the
campaign's pre-registered predictions is void as a prediction for the same reason — see the
withdrawn efficacy claim in [`03-ranked-tier.md`](03-ranked-tier.md).

**2. A headline was published that reproduced from nothing.** An earlier version of one campaign's
findings led with a correlation coefficient that could not be re-derived from the data it cited; the
correct value was substantially different, and the conclusion drawn from it was contradicted by a
table in the same document. It was caught by an adversarial review round, withdrawn in full, and is
not quoted anywhere in this directory.

**3. One structured emit in eighteen runs came back invalid, and cannot be diagnosed.** The API's
stop reason is journaled nowhere, so there is no record distinguishing a length cut-off from a
malformed emit. The per-call instrument that would settle it was considered and declined at the time
the campaign was designed, on the ground that taking it would have changed what the campaign
measured. The failure is disclosed here rather than dropped from the denominator.

**4. Counting rules have been wrong more often than data has.** Across the project's reviews, the
defects concentrate in reading and inference rather than in measured values: figures computed by a
committed script have survived independent re-derivation, and the errors have been in metrics,
descriptions and denominators built on top of them. Two of the corrections in this directory —
a certified result that a counting rule structurally could not see, and an efficacy claim fitted on
its own calibration set — are of exactly that kind.

## Two smaller disclosures, for completeness

**The measurement campaigns declared their own degradation rule in advance.** Each ran under a fixed
budget with the run order frozen before launch, and that order *was* the rule: if the program ran
hot, the runs at the end were the ones that did not happen, and which run would be dropped first was
named in writing beforehand rather than chosen once the money was short.

**Not everything the campaigns proposed was carried out.** One planned reading was voided by a defect
in its own instrument and was not redone; a proposed cost-from-the-user's-side view was never
published. Neither is reported here as a result, and neither is quietly missing: they were dropped,
and this is the sentence that says so.

## What a scan sends, and what contains it

A scan is not local. For each document unit it sends to the model API:

- the document's full text;
- a map of the repository's tracked files;
- the contents of files the agent chooses to read while forming its claims.

The containment around that is deliberate and worth stating as a feature:

- **the file reader is confined to the target repository**, resolved through the real filesystem
  path so that a symbolic link cannot escape it;
- **tool output is budgeted**, per call and cumulatively per document unit, which caps how much of a
  repository one unit can pull into a transcript — this was added after an early measurement run
  where uncapped reading made a single repository responsible for the majority of that run's cost;
- **spend is capped** by a wallet checked before each unit is dispatched.

If a repository contains secrets in tracked files, a scan can send them to the model API. Point it
at repositories where that is acceptable.
