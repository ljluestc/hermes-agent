# Tribunal rubric

`scripts/cajal_paper.py review` and the review step of `generate` run the draft past
up to ten reviewer personas, in this order (`--judges K` takes the first K):

| # | Persona | What it is biased to catch |
|---|---------|----------------------------|
| 1 | methodologist | design cannot answer the research question |
| 2 | statistician | numbers not justified by the protocol |
| 3 | domain expert | claims out of step with the state of the art |
| 4 | sceptical reviewer | overclaiming, unsupported generalisation |
| 5 | reproducibility auditor | another lab could not rerun it from the text |
| 6 | citation checker | a citation does not support its sentence |
| 7 | clarity editor | structure, precision, readability |
| 8 | senior area chair | novelty and significance for a top venue |
| 9 | ethics and impact reviewer | missing limitations, societal risk |
| 10 | early-career researcher | the paper does not teach clearly |

Each judge returns one JSON object scoring eight dimensions from 0 to 10:
`novelty`, `methodology`, `citation_quality`, `argument_strength`,
`reproducibility`, `clarity`, `technical_depth`, `publishability`, plus a one-line
verdict and up to three concrete improvements.

## Aggregation

- A judge's score is the mean of its dimensions; `overall` is the mean across
  judges, `median` the median, and the range is reported so one outlier is visible.
- Judges whose reply carries no parseable scores are dropped and counted in
  `judges_requested` vs `judges_counted`.
- Recommendation thresholds follow CAJAL's convention: below 7.0 major revision,
  7.0–8.5 minor revision, 8.5 and above accept with polish.
- Improvements are de-duplicated across judges and capped at ten.

A previous `## Tribunal Review` section in the draft is cut before judging, so
re-running `review --append` replaces the old table instead of reviewing it.
