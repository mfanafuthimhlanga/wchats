# Trace: E2E-6 calibration capture, blocked on retrieval quality (7.20, 7.21)

The capture ran 20-scenario mode against the live agent and was stopped after 6 scenarios. Four
response files were written, then deleted: they were captured through degraded retrieval and a
calibration set built on them would measure the degradation, not the judges.

## Why the captures were discarded

`S-003.json` said it in customer-facing text: *"The semantic search is hitting a provider error"*.
The worker log gives the cause verbatim:

```
retrieve_tool.cache_error exc='You have not yet added your payment method in the billing page and
will have reduced rate limits of 3 RPM and 10K TPM...'
rerank.voyage_failed_falling_back error_type=RateLimitError
```

**Voyage is on the no-payment-method tier: 3 requests per minute.** Every retrieval embeds a query
and every turn reranks, so a 20-scenario capture cannot run without throttling. The rerank failure
is the dangerous half: it **falls back to unranked results and logs a warning**, so answer quality
drops without an error anywhere a reader would look. Filed as `7.21`, owner action.

Responses live in a gitignored directory, so deleting them cost nothing and removes a trap: the
capture script skips scenarios whose file already exists, so contaminated captures would have
survived silently into the calibration set.

## The second finding: the predicted boundary failed

`run_retrieval_faithfulness.ragas_call_failed error="Error code: 400 ... 'Thinking mode does not
support this tool_choice'"` — the same defect `7.7` fixed for our six judge call sites, now hit
from inside instructor, which ragas uses for structured output. `7.18` was proven against real
ragas with a **canned** judge, and its own report named this exact gap as the one thing left
unobserved. It is now observed, and it fails. Filed as `7.20`, fix in flight.

## What E2E-6 needs before it can run again

1. `7.21`: a Voyage payment method (owner). Without it, retrieval is throttled and every
   downstream number - calibration, eval, red team - is measuring the throttle.
2. `7.20`: the Ragas judge's live round trip.
3. Then re-capture all 20 scenarios from empty, and only then score `human_scores.csv`.

## Also observed, not filed

- The `7.14` fix held: `run_agent_turn` retried on TimeoutError and the terminal path emitted
  rather than dying silently. No repeat of the E2E-3b silent death.
- `pii_firewall.response_deflected` fired correctly on the PII scenario (S-005's 138-char refusal
  is the product working, not a failure).
- `total_cost_usd=0.0597` on a 3-turn scenario: `7.13`'s Anthropic-priced telemetry again, now
  visible at scale. A 20-scenario capture would report several dollars of fictional spend.
