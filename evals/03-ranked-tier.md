# The candidate tier — reading leads that were never certified

Most of what the agent surfaces is not certifiable. A claim reaches the candidate tier when it was
found and stated but no predicate in the registry can adjudicate it at a fixed commit — the
assertion is prose, or templated, or about something outside the repository, or simply has no
mechanical form. The tier renders these as **unverified leads, never as findings**, and it is
banded rather than ranked:

- entries the agent marked **suspected** — its own confidence that the claim still holds at or
  below 0.2 — render first;
- **within a band the order is arbitrary**, and the report says so on the page. The product does
  not emit a within-band rank, so no reading here uses one.

## What a widening pass over known drift found

Nine documents across six repositories, chosen because their drifts had already been adjudicated by
hand, containing **15 known drift loci**. Two independent passes per document — and the first pass
carried three instrument-side nulls: a unit that errored, a unit that truncated, and a unit that
returned nothing. One of them is the invalid emit disclosed in
[`05`](05-method-and-integrity.md).

> **The agent reached and named 13 of the 15 known drifts — 12 under the matcher that actually
> shipped — and marked 8 of them suspected. Zero of
> them produced a verified finding: none bound a high-grade predicate, and one bound and certified
> on a preview-grade predicate, which by design cannot mint. For 11 of the 13, the literal the
> agent anchored on is byte-identical to the stale literal in the document.**

**The count depends on a tie-break, and the record designates the adverse reading as primary.**
Twelve is what the shipped matcher returns; thirteen is the corrected tie-break. The rule that
decides between them was implemented **after** the results were in, which is why the integrity
defect in [`05`](05-method-and-integrity.md) is material rather than procedural, and why the lower
number leads.

That last clause is the result. The failure is not that the model missed the drift, and not that it
described it vaguely — it quoted the exact stale string. What failed is **binding**: turning a
correctly identified assertion into a predicate the gate can replay. Discovery and adjudication are
separable, and this pass separates them cleanly.

It also means the growth path is partly legible. Where the agent proposed a predicate, the claim is
persisted with that proposal and the gap between "named" and "certified" is a concrete queue. That
is not everywhere: on the measured corpus about a third of unbound literals match some existing
predicate's shape and **two thirds match none**, a large share of those being commands, prose or
URLs rather than assertions about the repository — and one bind outcome records that the model
proposed nothing at all. How much of the remainder is addressable has **not** been measured.

## How a locus is defined, and why the definition is stated

A locus is a distinct **(document bytes, asserted literal)** pair, carrying the document line where
the assertion occurs. There are exactly 15, fixed before any run.

The definition matters because the obvious alternatives are wrong in measurable ways. Keying on the
document line gives 14 — it merges two distinct assertions that share a line. An earlier definition
induced loci by clustering the system's own output over overlapping anchor spans, and produced a
single merged locus swallowing 78 of 159 claims, because multi-span claims create long-range
unions. The definition above never clusters, so the run cannot move its own denominator.

The matching rule is equally explicit: a claim matches a locus when the locus's document line falls
inside one of the claim's anchor spans, and where one claim matches several loci it is assigned to
exactly one, longest-literal-first. **Each claim discovers at most one locus.** A more permissive
rule — plain substring — is **retained as a second channel and reported as a disclosed sensitivity,
never as the headline**, because the two channels disagree in three cases and the disagreement is
reported rather than resolved by preference. It cannot be the primary rule: within one document, one
locus's literal *contains* two others verbatim, so a single claim quoting that block would have
"discovered" 3 of 15 loci.

Two denominators are in play and are not interchangeable: **15 distinct loci**, and **16 rows**
(one document contributes a row with no locus of its own).

## Placement — a descriptive reading, with its efficacy claim withdrawn

Where do known drifts land in the banded output?

| repository | candidate entries | suspected band | known drift loci in band |
|---|---|---|---|
| a CSV parser | 126 | 2 | 2 |
| a game mod toolchain | 200 | 8 | 5 |
| a language glosser | 43 | 1 | 1 |
| a news aggregator | 25 | **11** | 1 |
| a mobile client | 150 | 0 | 0 |

The sixth repository in the pass contributes a row with no locus of its own and so no line here.

**Read the fourth row as a warning, not as evidence.** A suspected band holding 11 of 25 entries is
nearly half the tier; a drift landing in it says close to nothing. The bands that mean something are
the ones that are a small fraction of their tier.

**The band sizes above are a lower bound.** The join that reconstructs them from stored rows is four
members short of the tier count the product itself published for that repository, because some
multi-line literals fail to normalise identically on the way back in.

One thing this reading does **not** do: discharge the obligation it was written for in that
obligation's own terms. The commitment was repository-wide; what ran is per-document, and no single
reading holds both the current generator and the repository-wide surface. The gap is stated rather
than closed.

**And the efficacy claim is withdrawn in full** — including from the product, whose suspected-band
heading used to print the concentration multiple and no longer does. The 0.2 confidence cut was
*fitted* on twelve known drift loci, and the placement reading then measured those same loci. The previous ordering was
descending confidence, so the suspected band was necessarily the tail, and a band-first rule
**must** move exactly those entries to the front. There was no outcome under which this reading
could have failed, which makes it a tautology rather than a measurement. What survives is the
**descriptive** statement — where these particular loci land — and the control, which reproduces an
earlier reading row for row. Re-establishing an efficacy claim requires a drift population that did
not define the cut.

## Two disclosures that qualify every number on this page

**One decline reason dominates.** Ambiguous implied base — a bare path that is missing where the
document implies it but present elsewhere in the tree — accounted for roughly two thirds of all
declines in this campaign. It is journaled and never surfaces, and **how many true drifts it costs
has not been measured.** It is the largest single suppressor of bindings, which makes it the
first place to look at the bottleneck this page names.

**Contamination.** The one locus that bound a predicate and was certified — a package-manager
script reference in a project README, checked against the manifest — is the single survivor drawn
from a set declared contaminated in advance, because it had bound a predicate before. It is the most
quotable fact in this pass, and it is the contaminated one.

**Concentration.** 5 of the 15 loci come from one repository, and **4 of those are references into
that project's own private configuration language**. Those same 4 are half of the in-band loci in
the placement table. A result carried by four references to one project's bespoke config format is
not a result about documentation in general.

## Reproducing this

```bash
drift dev check /path/to/repo path/to/doc.md --strict-measurement --journal-export run.jsonl
```

The export carries every claim the agent emitted — its anchor, its proposed predicate and arguments
where it proposed any, the band it was placed in, and the gate outcome where one exists. The
candidate tier is reconstructible from those rows minus two exclusions: the claims the gate refuted,
because a refutation is a finding or nothing and must never re-enter as a lead; and the nine decline
reasons that are journaled without surfacing.
