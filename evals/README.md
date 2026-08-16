# What drift has actually been measured to do

drift finds contradictions between a repository's documentation and its code. A model reads a
document and states what it asserts; a pure function pinned at a commit decides whether each
assertion still holds. Only claims that survive both, in that order, are reported as findings —
everything else is offered as an unverified lead. The design, and why it is that way round, is in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

This directory is the measured record: what was run, on what, what came out, and what the numbers
cannot support.

## How to read these numbers

**This is depth of record, not generalisation.** Every figure here comes from material that
*informed the system's design* — repositories inspected while building it, documents whose drifts
were adjudicated by hand before they were ever scanned. There is no held-out sample. What these
measurements support is a detailed account of how one system behaves on known material; they do not
support a claim about arbitrary repositories.

Three consequences worth having in mind before the table:

- **The verified findings are concentrated.** All of them are in one file, in one repository.
- **Counts, not rates.** Where the sample is small the numbers are given as counts; a percentage
  over six observations would imply a precision that is not there.
- **No recall claim is made.** Nothing here measures what fraction of a repository's real drift is
  found, and nothing here should be read as measuring it.

## The numbers

| | measured | where |
|---|---|---|
| verified findings emitted | **6** | [`01`](01-verified-tier.md) |
| false positives among them | **0** | [`01`](01-verified-tier.md) |
| predicates that have ever minted | **1 of 5** high-grade | [`01`](01-verified-tier.md) |
| semantic judge, 18 golden items | **0** false not-live over the **12** live items · 2 false live under the looser mapping, 0 under the stricter, over the **4** not-live | [`02`](02-semantic-judge.md) |
| known drifts reached and named, of 15 | **13** | [`03`](03-ranked-tier.md) |
| of those, verified findings produced | **0** | [`03`](03-ranked-tier.md) |
| whole-repository scan cost, median | about **$2.06**, pre-fix | [`04`](04-cost.md) |
| retired instruments, each with its finding and a real output | **3** | [`06`](06-retired-instruments.md) |

The judge figure is a development number with no held-out check, and the cost figure is over the
project's own target. Both are labelled as such where they live.

## The finding worth the read

On nine documents containing fifteen drifts that had already been adjudicated by hand, the agent
**reached and named thirteen of them — and minted no verified finding.** One bound and was
certified on a preview-grade predicate, which by design cannot mint; none bound a high-grade one.
For eleven of the thirteen, the literal it anchored on is byte-identical to the stale literal in the
document.

So the model is not failing to see drift, and it is not describing it vaguely. What fails is
**binding**: turning a correctly identified assertion into a predicate that a pure function can
replay at a commit. That is a much more tractable problem than "the model missed it", and it is
visible in the data rather than inferred — every unbound claim is persisted with the argument the
agent proposed, which makes the gap a queue of concrete predicate demand.

It is also the design working as specified rather than failing. The gate is the only authority that
can mint a finding; a claim it cannot adjudicate is not promoted on the strength of the model's
confidence. Zero certified out of thirteen named is the cost of that rule, stated plainly, and
[`01`](01-verified-tier.md) is what the rule buys.

## The directory

| file | subject |
|---|---|
| [`01-verified-tier.md`](01-verified-tier.md) | what has been certified, its false-positive count, and the concentration limit |
| [`02-semantic-judge.md`](02-semantic-judge.md) | judge accuracy against the golden set, and the causal story that is deliberately not published |
| [`03-ranked-tier.md`](03-ranked-tier.md) | the unverified candidate tier, the banding, and a withdrawn efficacy claim |
| [`04-cost.md`](04-cost.md) | what a scan spends; levers pulled, declined, and unavailable |
| [`05-method-and-integrity.md`](05-method-and-integrity.md) | how to check any of this — and four places the measurement apparatus itself failed |
| [`06-retired-instruments.md`](06-retired-instruments.md) | three instruments that were run once, deleted, and decided the architecture |
| [`artifacts/`](artifacts/) | real outputs, with the provenance of each |

If you read one other page, read
[`05-method-and-integrity.md`](05-method-and-integrity.md). A measured record is only worth as much
as its account of where the measuring went wrong.
