# Artifacts — real outputs, and where each came from

Every file here was produced by running the software against a real repository, not written to
illustrate a point. This page records what produced each one and any change made to it on the way
in, so that "real output" is a checkable statement rather than an assurance.

| file | produced by | cited from |
|---|---|---|
| `verified-findings-sample.md` | a single-document run over a Python server project's internationalisation README, at a pinned commit | [`../01-verified-tier.md`](../01-verified-tier.md), [`../03-ranked-tier.md`](../03-ranked-tier.md) |
| `deterministic-engine-sample.json` | the retired deterministic extractor, over one repository of a 16-repository blind sample | [`../06-retired-instruments.md`](../06-retired-instruments.md) |
| `diffscope-sample.json` | the retired diff-scoping harness, over the three highest-traffic repositories of its sample | [`../06-retired-instruments.md`](../06-retired-instruments.md) |
| `self-scan-sample.md` | `drift check . ARCHITECTURE.md` on this repository — the run that demonstrates it passes its own tool | [`../README.md`](../README.md) |

## Changes made on the way in

- **`verified-findings-sample.md`: heading lines only.** The report's title changed when the
  command surface was unified; the suspected-band heading carried a concentration multiple that
  this package withdraws — see [`../03-ranked-tier.md`](../03-ranked-tier.md); and the candidate
  sections were renamed when `lane` was dropped in favour of `producer`, the term
  [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) actually defines. All of them now match what the
  current renderer emits. **Every finding row, every candidate row and every count is exactly as
  produced.**
- **`self-scan-sample.md`: nothing.** It is the renderer's output for that run, rebuilt from the
  run's own journal rows rather than copied from a terminal, which is why it can be regenerated
  without paying for the scan twice.
- Nothing else was edited, reordered, filtered or truncated. The empty and near-empty outputs from
  the same samples are not hidden by selection — the deterministic sample is one of sixteen, and
  several of the other fifteen contain no records at all, which is itself part of that instrument's
  finding.

## What these artifacts cannot show

They are outputs, not proofs of reproducibility. The repositories are third-party projects pinned at
fixed commits and are not vendored here, so re-deriving these files means pointing the tool at the
same commit of the same project. The commands that produce this shape of output are in
[`../01-verified-tier.md`](../01-verified-tier.md).
