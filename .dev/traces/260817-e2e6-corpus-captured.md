# Trace: E2E-6 calibration corpus captured, 20/20, awaiting the owner's scores

The first clean calibration corpus in the project's history. `compute_correlation.py --check`
now reports `scenarios 20 / recorded responses 20 / 0 of 3 human scores filled`, exit 3
(NOT READY, which is neither pass nor fail).

## What unblocked it

The owner credited $5 to Voyage, moving the account to usage Tier 1 (`7.21`). The proof is an
absence: **zero `rerank.voyage_failed_falling_back` and zero rate-limit errors** across the whole
run, against both in the throttled attempt the day before. That absence is the thing to check on
any future capture, because the fallback degrades quality with only a warning.

## Two defects the capture found, both real

1. **`7.23` — the widget JWT never renews.** The harness minted one token for the whole run;
   scenarios S-012 through S-020 failed `401 Unauthorized`, nine in a row, because the run outlived
   the 900s expiry (`widget.py:178`). Harness fixed by minting per scenario. **The product has the
   same hole**: the widget holds its token in module scope and `api.js:14` throws on the 401 with
   no re-fetch, so a customer who leaves the widget open for 15 minutes and asks a follow-up gets
   an error. Filed for the M3 widget work.
2. **A 101-second turn.** S-012 (the prompt-extraction attack) exceeded the capture's 120s SSE
   window. It was NOT a hang or a refusal to answer: the job reached `complete` with
   `agent.response` emitted, and the whole judge chain ran after it. Re-captured at a 300s timeout.
   Worth remembering as a latency data point on an adversarial turn, not as a defect.

## The corpus

20 files, no empties, none under 120 chars, none containing provider-error text (the exact
contamination that forced the previous set to be deleted). 13,999 characters total.

Responses live in a gitignored directory, so the corpus is a local artefact: it can be regenerated
by re-running the capture, and it is not evidence anyone else can audit. If a calibration result
ever needs defending, the corpus has to be attached to the record deliberately.

## Next, and it is the owner's

`apps/api/tests/evals/calibration/human_scores.csv`, the `human_score` column, 1 to 5, ten rows of
which three are the minimum the gate needs. Nothing may fill that column but a human: a judge
calibrated against model-written labels measures its agreement with itself. Then
`compute_correlation.py` runs the Spearman gate, and the result is **per-provider** - these scores
calibrate DeepSeek, and a later move back to Anthropic re-runs it.
