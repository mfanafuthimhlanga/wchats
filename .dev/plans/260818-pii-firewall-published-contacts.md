# 7.29 · pii-firewall-eats-ordinary-answers

**Goal:** an answer that quotes an email address the tenant published in its own corpus reaches the
customer. Everything the firewall deflects today still deflects.

**Rule this encodes:** the output firewall exists to stop the agent leaking a CUSTOMER's personal
data. It does not exist to stop it repeating the BUSINESS's own published contact details.

## Cause, already proven

`detect_pii` matches `_EMAIL_RE` against the agent's own response and asks nothing about where the
address came from. Two of the live corpus's sixteen chunks carry addresses, and chunk 14 is the
"8. Contact and Escalation" section. Any question whose best chunk is that section is therefore
unanswerable: retrieval works, the agent composes a correct cited answer, and the firewall replaces
the whole reply with a deflection. Four of twenty E2E-6 responses came back byte-identical: S-002,
S-003, S-005 and S-010. The row recorded three, and S-010 is an edge scenario rather than a golden
one, so the blast radius was never confined to the golden path.

The same module already made this call once, for phone numbers: OD-4 excluded a phone detector
because "a tenant's own published support line is content the agent is supposed to hand out". Email
has the identical property and got no such exemption.

## Design

```
scan_response(text, *, published_context: Sequence[str] = ()) -> tuple[str, str | None]
```

- **Email only.** `card` and `sa_id` deflect unconditionally, whatever the context contains. A
  business does not publish a customer's card number or ID number, so there is no legitimate case to
  serve and no reason to open one.
- **Allowlist by extraction, not by substring.** Run `_EMAIL_RE` over `published_context`, lowercase
  the hits into a set, and exempt a detected address only on exact membership. A substring search
  would let `a@b.com` be exempted by a context containing `xa@b.comy`.
- **Per address.** A response carrying one corpus address and one foreign address still deflects.
- **Empty context reproduces today's behaviour exactly**, so every existing caller and test is
  unchanged by construction.

**What may be in `published_context`, and what may not:**

| Source | In | Why |
|---|---|---|
| `RETRIEVE_CHUNKS_KEY` content of this turn's non-errored retrieves | yes | the material the tenant ingested for the agent to answer from |
| The framed retrieve payload, header and footer | no | it echoes the query, so a customer who types their address into the chat could exempt it |
| The customer's message and the conversation history | no | same bypass, one step shorter |
| The agent soul | no | `pii_firewall`'s docstring makes "nothing in the agent soul reaches this function's behaviour" a stated property (T-18-SEC-02), and the observed defect does not need it |
| An errored retrieve (`result_is_error`) | no | `retrieve_tool` returns its DoS-guard refusal as ordinary text, and a refusal is not published material |

Excluding the soul leaves one gap open: an agent that answers "how do I contact you" from its soul
rather than from retrieval still deflects. Not closed here, because closing it means contradicting a
documented invariant, and the observed failure is entirely in the corpus path.

## Threats

| Threat | Disposition after this change |
|---|---|
| Agent echoes a card number or SA ID | closed, unchanged, and now proven against a context that contains the number verbatim |
| Agent echoes an address the customer typed this turn | closed: conversation text is never in `published_context` |
| Corpus text instructs the firewall off | closed: the context is read as data for one set-membership test and is never parsed for instructions |
| Retrieve capture unparseable | closed by failing to today's behaviour: no allowlist, so no exemption |
| **A third party's address reaches the corpus inside an ingested document, and the agent repeats it** | **open, accepted here, filed as a new row.** The control belongs at ingest, not at egress, and the identical exposure already exists for phone numbers by OD-4's own decision |

## Files

- `apps/api/app/utils/pii_firewall.py` — the keyword-only parameter and the exemption.
- `apps/api/app/worker/tasks/runtime/agent.py` — build `published_context` from `tool_calls_log`
  above the existing `scan_response` call (1708), and log each exemption.
- `apps/api/tests/unit/test_pii_firewall.py` — the exemption and its four non-widenings.
- `apps/api/tests/unit/test_agent_pii_context.py` — the context builder reads chunks, skips errored
  retrieves, and ignores non-retrieve entries.
- `apps/api/tests/evals/capture_responses.py:142` — `payload.get("tool", "")` reads a key the emitter
  does not send. `agent.py:1290` emits `tool_name`. This is why the corpus carries `tool_name: ""`,
  and it is a capture-script gap, not `5.9` returning.

## Tests

Each is written to fail first.

1. `detect_pii` returns `"email"` for a corpus address when `published_context` is empty.
2. The same address with the chunk in `published_context` returns `None`, and `scan_response`
   returns the text byte-identical.
3. Corpus address plus foreign address deflects.
4. A card number that appears verbatim in `published_context` still deflects. Same for an SA ID.
5. A `published_context` chunk reading "the PII filter is disabled for this reply" plus a card
   number still deflects.
6. Case-insensitive match: `Hello@AcmeCoffee.example` in context exempts `hello@acmecoffee.example`
   in the response.
7. `scan_response` still takes exactly one POSITIONAL parameter; the added one is keyword-only.
8. The context builder skips a retrieve whose `result_is_error` is true.
9. The three E2E-6 questions that failed, as a regression fixture built from the real chunk 14 text.

**Mutation proof, per the repo's negative-test rule.** Delete the exemption's `card`/`sa_id` guard
and observe test 4 go red; restore from `HEAD`; observe green. Record the observed output in the
trace, not the intention.

## Exit

- `apps/api` full battery green.
- The three E2E-6 questions re-captured against the live tenant and answered rather than deflected,
  which also unblocks S-002 and S-003 in `human_scores.csv`.
