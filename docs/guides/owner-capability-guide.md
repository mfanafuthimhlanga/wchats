# Owner Guide: Configuring What Your Agent Is Allowed To Do

**Audience:** Business owners configuring what their agent is allowed to do
**Phase:** 19 (narrates the Phase 18 admin UI shipped by plan 18-10). Corrected and extended by
Phase 22: the Enabled section below now describes the control as it actually ships today — Phase
22 removed the platform-default lock this guide used to correctly describe — and a new section
covers the approval queue Phase 22 shipped alongside that change.
**Scope:** What each capability control means, what tighten-only implies, and what the approval
queue does and does not do — not a click-by-click tour of the screen.

---

## What a capability envelope is

Your agent can talk to customers freely, but it cannot move money or change a
booking unless you say so. Every action that touches money or a booking —
placing an order, cancelling one, issuing a refund, changing a subscription,
booking a slot, or updating a customer record — has its own **capability
envelope**: a small set of rules that says whether the action is allowed at
all, and if so, how far it can go.

There are seven envelopes in total. Six of them (`place_order`,
`cancel_order`, `issue_refund`, `update_subscription`, `book_slot`,
`update_customer_record`) move money or change something real in the world,
and the deploy screen shows one panel for each. The seventh, `confirm_action`,
is different on purpose: it moves no money at all, so the screen does not show
it a currency ceiling. It exists only so the agent can ask a customer to
confirm something before proceeding — there is nothing for a rand figure to
mean there.

---

## The six controls, one subsection each

Every one of the six money-moving skills is governed by the same six
controls. Each is explained here in plain terms first, then in the exact
shipped behaviour.

### Enabled

**Plain meaning:** whether the agent is allowed to attempt this action at
all.

**Shipped behaviour:** every skill ships **off**. Nothing acts on a customer's
behalf until a skill is turned on. There is no skill that starts active by
default — this is true for every tenant, on day one, with no exception.

You can now turn a skill on yourself, from this screen, with no request to
anybody. The checkbox carries one of two resting captions, depending on
whether the skill is currently on or off:

> Off. Turn this on to let the agent use this skill.

> On. The agent can use this skill.

**If your agent is already live** — it has been through the checklist and
approved for deployment — ticking the box does not save immediately. It
stages a confirmation first, and asks you a direct question before anything
changes:

> Let this live agent use {skill} now? Customers can trigger it on their next turn.

The primary button reads "Turn on {skill}"; the secondary button reads "Keep it off." Nothing is
written to your configuration until you choose one of the two. The reason for this extra step is
specific, not decorative: for a live agent, turning a skill on takes effect on the agent's very
next customer turn, with nothing else standing in between — no checklist, no approval, no further
gate downstream. Confirming is the one moment this screen gives you to say "yes, right now, for
real" before that happens.

**If your agent has not been approved for deployment yet,** the box saves immediately, with no
staged confirmation at all — the checklist and the approval step still stand fully between this
change and any customer, so there is nothing live yet for a confirmation to protect against.

**Turning a skill off is unchanged in both cases:** it always saves immediately, whether or not
the agent is live. Disabling is always safe, so it never needs a confirmation.

(See "Tighten-only, and what happens at the edge," below, for what turning a skill on does — and
does not — change about every other setting on that skill.)

### Rate limit

**Plain meaning:** how many times per hour (or minute, or day) this action is
allowed to fire before the agent refuses to attempt it again, no matter what
a customer asks for.

**Shipped behaviour:** every skill starts at `5/hour`. If you try to raise the
number of allowed calls, the change is refused outright — the server will
never let you widen a rate limit from this screen. If you try to set a rate
limit of zero calls, the screen refuses to send the request at all and shows
you this sentence:

> A rate limit has to allow at least one call.

That sentence is the deploy screen's own safeguard, checked before any API
call is made — it is not something the server enforces, and nothing was
written when you see it. The server itself has **no lower bound on a rate
limit at all**: a proposal of `0/hour` passes the server's tighten-only check
unconditionally, because zero is mathematically the tightest value a rate
limit can take, so nothing on the server side ever refuses it. This matters
because it means a raw API call bypassing the screen — something this guide
cannot prevent — could write a `0/hour` rate limit today, and once written,
the tighten-only rule makes it permanent: every future attempt to raise it
back above zero is a loosening, and is refused forever after. This is a real
gap in the shipped platform, not a documented safety feature; the "at least
one call" floor exists only in the screen you are looking at right now.

Lowering a rate limit (fewer calls, or a narrower window) is always allowed
and takes effect the moment you confirm it — the screen shows you the old and
new value side by side and asks you to confirm before anything is saved.

### Ceiling (maximum amount)

**Plain meaning:** the largest single amount, in South African rand, that this
skill may move in one action.

**Shipped behaviour:** if you try to raise a ceiling above what it is
currently set to, the change is refused and nothing is written. On this
screen, you see this sentence:

> That amount is higher than the current ceiling. Nothing was changed.

That sentence is the deploy screen's own copy, shown by a pre-flight check
before the API is even called. The refusal itself is genuinely enforced by
the server too — unlike the rate-limit floor above, there is no gap here —
but a request that reaches the server directly (a raw API call instead of a
click) receives a different, machine-oriented message instead of this
sentence: a 422 response reading "Capability envelope change rejected:
loosen_max_amount_cents". The outcome is identical either way — refused,
nothing written — only the wording differs depending on whether you're
looking at the screen or a direct API response.

Every amount in the system — the ceiling you set, the amount the agent
proposes to move, everything — is held internally as a whole number of
**cents**. There is no rounding, no fraction of a cent, and no currency
conversion anywhere in this path. When a ceiling says R500.00, it means
exactly 50 000 cents, not "about R500."

If a ceiling has never been set for a skill, the screen shows the label
**No ceiling**, and if a rate limit has never been set, it shows the label
**No rate limit**. These two fallback labels only ever appear for a control
that has genuinely never been configured. **No ceiling** is deliberately shown as a
**fail-state** indicator, not a neutral grey one — an enabled skill that can
move money with no upper bound at all is a finding you should look at, not a
blank field to ignore.

### Requires confirmation

**Plain meaning:** whether the agent must get an explicit "yes, do it" from
the customer before this action runs, on top of everything else.

**Shipped behaviour:** off by default, and this is a **one-way switch**. Once
you turn confirmation on for a skill, it cannot be turned back off from this
screen — the server refuses that change unconditionally, the same way it
refuses a raised ceiling.

### Requires identity verification

**Plain meaning:** whether the customer must prove who they are — for
example with a one-time code — before this action is allowed to run.

**Shipped behaviour:** also off by default, and also **one-way on**. Exactly
like confirmation, once identity verification is switched on for a skill it
cannot be switched back off.

### Actor review mode

**Plain meaning:** an independent security check that reviews every proposed
action against what the customer actually asked for, before the action is
allowed to touch money or a booking. Think of it as a second pair of eyes
that never gets tired.

**Shipped behaviour:** every money-moving skill starts at **always-on** — the
strictest possible setting, and the setting every ceiling and rate limit is
tightened against. Turning Actor review off is **not a legal setting for any
skill that moves money**. This is a hard constraint enforced by the server
itself, not merely something the screen declines to offer: even a request
that reached the server by some other route than the screen would be refused
in exactly the same way.

---

## Tighten-only, and what happens at the edge

The rule, in one sentence: **every change you make must be at least as
strict as what is there now**, and the very first change you ever make to a
skill is compared against the platform default — so you can never start
looser than the platform allows, even on day one.

That sentence is true of five of the six controls, not all six. The ceiling, the rate limit, and
the Actor review mode are still compared against the platform default on a first write, exactly as
described, and can never start looser than that default allows. The **Enabled** control is the one
field the platform default no longer bounds, in either direction: turning a skill on is a
permission you hold, not a limit the platform sets, so tighten-only simply does not apply to it the
way it applies to the other five. Turning a skill on does not reset, raise, or unlock any other
setting on that skill — every ceiling and rate limit you configured before disabling it is still
exactly where you left it, completely unaffected by the Enabled checkbox in either direction.

At the boundary, this is exactly what happens:

- Propose **exactly** the current value — accepted, and nothing changes. This
  is treated as a no-op, not an error.
- Propose **one cent above** the current ceiling — refused. The stored
  setting is left completely untouched, and on this screen you see the
  sentence quoted above: "That amount is higher than the current ceiling.
  Nothing was changed."
- Propose **one cent below** the current ceiling — accepted immediately.

**This refusal happens on the server, before anything is written to the
database.** Even a request that somehow bypassed the screen entirely — a
raw API call instead of a click — would be refused with the same outcome
(rejected, nothing written), for exactly the same reason, though not with the
same wording: as noted under "Ceiling (maximum amount)" above, the screen
shows its own copy while a direct API call gets the server's own message
instead. The screen is a convenience that shows you this rule clearly; it is
never the thing actually enforcing it. The enforcement lives in the server
function `validate_tighten_only`, and it runs before any database write is
made — a rejected change leaves the stored row byte-for-byte untouched. (This
guarantee is specific to the ceiling and does not hold for every control on
this screen — see the rate-limit floor described above, which has no
server-side equivalent at all.)

---

## When an action needs your approval

Some skills are marked **Requires confirmation** (see above). When a live agent tries to use one
of those, the action does not run immediately — it is held back as a **pending confirmation**,
waiting for a person to decide. The queue for these requests lives on this same screen, under the
**Pending confirmations** heading, below the capability panel for all six skills.

When nothing is waiting, the queue says so plainly:

> No confirmations are waiting.
>
> When a mutating action needs your approval, it will appear here with what it wants to do, when it was requested, and when it expires.

### What a row tells you

Each row states, in plain business language before any identifier, what the agent wants to do and
for how much, when it was asked, and when the request expires. A refund row names the order and the
amount; a booking row names the customer, the service, and the time — never a raw dump of the
underlying data.

### Approving

Click Approve, and the screen asks you to confirm before anything runs:

> This executes immediately once approved.

That sentence is accurate, not a simplification: once you confirm, the action runs once, in the
background, shortly after you approve it. There is no further step for you to take.

There is one narrow edge case worth naming plainly, the same way the rate-limit section above
does. If the background worker running this action crashes at the exact wrong moment — after it
has told the provider (Stripe, your store, etc.) to do something, but before it has recorded that
this succeeded — the system does not guess what happened. It will never run the action a second
time to find out, because that could mean, for example, a second refund going out. Instead it stops
and marks the request as needing a human to check what actually happened before anything is retried.
This is rare, and it trades a small chance of needing you to double-check one request for a real,
load-bearing guarantee: this system will never knowingly take the same money-moving action twice.

### Rejecting

Click Reject, and the screen tells you plainly what that means:

> The agent will not be able to complete this action for the customer.

Rejecting is final. The action never runs, under any circumstance, once you reject it.

### Expiry

Every request carries a deadline. A request that passes that deadline before anyone acts on it
cannot be approved afterwards, and it never runs:

> This request expired before anyone acted on it. It cannot execute.

Nothing tidies an expired row away automatically today — no background sweep ships in this phase to
clear the queue for you. The screen simply shows an expired request as expired for as long as it
sits there, and refuses to act on it either way.

### The honest outcome, in two steps

Approving and executing are two different moments, and this screen never conflates them. The
instant after you approve, the row reads as approved and waiting:

> Awaiting execution.

Only once the action has actually been attempted does the row become either executed or not
executed — never before, and never assumed on your behalf.

### Why an approved request can still be refused

If you tighten the ceiling, lower the rate limit, or turn a skill off **after** a request was made
but **before** anyone approves it, the checks run again against your *current* settings at the
exact moment the action tries to run — never against whatever was true when the request was first
made. If your current settings would refuse it, it is refused, even though it was already approved:

> This skill was turned off after the request was made, so it could not run.

> The maximum amount allowed for this skill was lowered after the request was made. This amount is now over that limit.

> This skill had already reached its rate limit when this request tried to run.

This is the intended behaviour, not a fault. An approval granted under looser settings never
executes under them — a limit you tighten always wins, even against a request that was approved
before you tightened it.

### What approving does not do

Say this plainly, because it matters: **approving a request authorises the action. It does not verify the customer's identity.** If a skill requires identity verification, the customer was
verified when they originally made the request — potentially hours before you ever see the row.
Approving it later does not check that again. Approval and identity verification are different
acts, done at different times by different people, and this screen never lets you mistake one for
the other.

### Two people, one request

If two people — or the same person in two browser tabs — try to resolve the same request at the
same moment, the first one wins. The second is told plainly what happened:

> Someone already resolved this request.

This is expected behaviour, not an error. Nothing was lost; the request was simply already decided
by the time the second attempt reached it.

---

## The shipped starting point

Every skill begins here, on day one, before you have configured anything:

| Skill | Enabled | Rate limit | Ceiling | Confirmation | Identity verification | Actor mode |
|---|---|---|---|---|---|---|
| `place_order` | off | `5/hour` | `R1 000.00 (100 000 cents)` | off | off | `always-on` |
| `cancel_order` | off | `5/hour` | `R500.00 (50 000 cents)` | off | off | `always-on` |
| `issue_refund` | off | `5/hour` | `R500.00 (50 000 cents)` | off | off | `always-on` |
| `update_subscription` | off | `5/hour` | `R500.00 (50 000 cents)` | off | off | `always-on` |
| `book_slot` | off | `5/hour` | `R500.00 (50 000 cents)` | off | off | `always-on` |
| `update_customer_record` | off | `5/hour` | `R500.00 (50 000 cents)` | off | off | `always-on` |

`place_order` is the one skill with a larger starting ceiling
(`R1 000.00 (100 000 cents)`) because a completed order is the single largest
legitimate amount the platform expects to see moving in one action. The
other five all start at `R500.00 (50 000 cents)`.

---

## Blast radius: what the numbers mean

The deploy screen reports two different kinds of number for every
money-moving skill, and it is important to keep them apart: the
**configured ceiling** (what the agent is currently *authorized* to do) and
the **observed maximum** (what it has actually done, historically). These are
always shown as two separate labelled lines per figure — they are never
merged into a single number, because they answer different questions. A
warning on this screen is driven only by what is authorized, never by what
merely happened to occur; a quiet history says nothing about what a
misconfigured ceiling could still do tomorrow.

If no transactional skill is enabled for the agent at all, the screen shows
this exact sentence instead of any figures:

> No transactional skill is enabled for this agent. There is no blast radius to report.

That sentence means precisely what it says: with zero enabled skills, there
is no authorized action for the agent to take, so there is nothing to
measure a blast radius against. This is a perfectly valid, deliberate state
to deploy an agent in — an agent that only answers questions and moves
nothing is not a lesser deployment, it is a specific choice you are entitled
to make.

---

## Changing settings after you have approved a deployment

Approving a deployment is an attestation: you are telling the platform "I
have reviewed these exact limits, and I approve deploying the agent with
them." That attestation is tied to the specific configuration that existed
at the moment you approved it.

If you change **any** capability setting after approving a deployment — even
tightening one further — the acknowledgement you gave is no longer valid for
the new configuration. The screen will show you this exact instruction:

> Re-run the checklist to review and acknowledge the new configuration before deploying.

Re-running the checklist means exactly what it says: you re-read the
per-skill summary table the acknowledgement covers, and you tick the box
again, confirming you have reviewed the *new* posture — not the one you
approved before. The agent cannot be deployed with a changed configuration
until that fresh acknowledgement has been given.

---

## A note on friction

If a rate limit or a ceiling feels too restrictive for how you want your
agent to operate, the right response is either to raise it **within the
tighten-only rule** — for example, if you have never set a ceiling yet, your
first configured value can legally be up to the platform default — or to
accept the refusal as a signal that the platform's default is doing its job.
It is never the right response to lower Actor review, and it is never the
right response to try to turn identity verification off — beyond the fact
that both of those are simply impossible from this screen (they are one-way
switches, described above), presenting either as a way to "remove friction"
would be presenting a safety control as an obstacle. It is not an obstacle.
It is the reason a customer-facing agent is safe to give money-moving
authority to in the first place.

The same principle now covers two more surfaces this screen exposes. Enabling a skill is a
decision to grant an authority — the right question is never whether turning it on would make a
refusal go away, it is whether the agent should hold that authority at all. And approving a
pending request is a decision about that one action, and only that one action. It is never a way
to bypass a limit you have set elsewhere, and it is never a substitute for verifying who you are
actually dealing with — see "What approving does not do," above.

---

## How this is enforced

Everything described above traces to real, checkable behaviour in the
codebase, not merely to what the screen happens to display:

- `validate_tighten_only` in `apps/api/app/services/capability_service.py` —
  the pure comparator that refuses every loosening change, run before any
  database write.
- `PLATFORM_CAPABILITY_DEFAULTS` in the same file — the exact starting values
  for every skill listed in the table above.
- The routes in `apps/api/app/api/v1/capability_envelopes.py` — the read and
  tighten-only-write endpoints the deploy screen calls.
- The routes in `apps/api/app/api/v1/pending_confirmations.py` — the queue
  read and the atomic resolve claim the Approve/Reject buttons call.
- `execute_approved_confirmation` in
  `apps/api/app/services/transactional/confirmation_resolution.py` — the
  resolver that re-runs every check whose live answer can have changed
  since the request was made, and that never re-verifies a customer's
  identity.
- `resolve_approved_confirmation` in
  `apps/api/app/worker/tasks/runtime/confirmations.py` — the background task
  that actually executes an approved action, only after the resolve claim
  above has committed.
- `mark_reservation_in_flight` / `reserve_idempotency` in
  `apps/api/app/services/transactional/idempotency.py` — the durable
  "adapter call is about to happen" marker written before every provider
  call and the reclaim logic that refuses to auto-retry a call whose outcome
  is unknown, which is what makes "runs once" (never twice) true even if the
  background worker crashes mid-call, at the cost of occasionally needing a
  human to check a request the system could not safely retry on its own.
- The shipped screen at `apps/admin/app/agents/[id]/deploy/page.tsx` — every
  sentence quoted above is copied verbatim from this file, not paraphrased.

## References

- `apps/api/app/services/capability_service.py`
- `apps/api/app/api/v1/capability_envelopes.py`
- `apps/api/app/api/v1/pending_confirmations.py`
- `apps/api/app/services/transactional/confirmation_resolution.py`
- `apps/api/app/services/transactional/idempotency.py`
- `apps/api/app/worker/tasks/runtime/confirmations.py`
- `apps/admin/app/agents/[id]/deploy/page.tsx`
- `docs/runbooks/integration-credentials.md`
