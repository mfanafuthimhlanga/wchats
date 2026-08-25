# W Chats

A multi-tenant platform where a business owner provisions a customer service agent that is
grounded in their documents, evaluated, and red-teamed before it goes live. This glossary fixes
the words the code, the issues and the conversations use.

## Language

### The thing being shipped

**Agent**:
The deployed customer-facing assistant a Tenant owns. Only this.
_Avoid_: bot, assistant, chatbot, and "agent" for anything below.

**Tenant**:
The business that owns an Agent, with its own Neon project and its own documents.
_Avoid_: customer (that word is the Tenant's end user), account, org.

**Customer**:
A person talking to an Agent on the Tenant's site.
_Avoid_: user, visitor, end user.

**Corpus**:
The Tenant's ingested documents, chunked and embedded, that the Agent is grounded in.
_Avoid_: knowledge base, docs, data.

### Proving it

**Harness**:
The code that runs Scenarios against an Agent and produces a Verdict.
_Avoid_: eval framework, test suite, pipeline.

**Scenario**:
One question a Customer might ask, with what a correct answer must contain.
_Avoid_: test case, prompt, sample.

**Judge**:
A single typed model call that scores one Agent turn on one dimension.
_Avoid_: evaluator, grader, LLM-as-judge, and never "agent".

**Calibration**:
Measuring a Judge against a human who labelled the same turns twice, so the Judge's agreement
is read against the human's agreement with themself.
_Avoid_: alignment, validation, tuning.

**Attacker**:
The red-team probe that tries to make an Agent misbehave.
_Avoid_: red-team agent, adversary, probe.

**Verdict**:
The Harness's output for one Agent: `ship` or `block`, with the evidence that produced it.
_Avoid_: result, score, pass/fail, grade.

**Ship**:
The Verdict that lets an Agent go live. It has never been produced.
_Avoid_: approve, pass, green.

### Running it

**Orchestrator**:
The code that runs the deployment checklist and acts on a Verdict.
_Avoid_: deployer, deployment agent.

**Actor gate**:
The check that runs before an Agent moves money.
_Avoid_: payment gate, transaction guard.

**Transactional**:
An Agent with typed skills that can complete an action, such as a purchase, through the Actor
gate and a provider adapter.
_Avoid_: commerce agent, checkout bot.

**Provisioning**:
Creating and managing an Agent from outside the admin console, over the REST API through the
MCP server.
_Avoid_: A2A (a runtime protocol, out of scope), CLI (dropped).

**Ledger**:
The record of every model call a Tenant's Agent or the platform makes, with tokens, the
requested and served model, and when. Money is derived from it at read time and never stored
on a call.
_Avoid_: usage log, billing table, telemetry.

**Production ready**:
Two Agents live on their deployed Vercel URLs: the Mellow Earth Elements Transactional Agent
completing a real action through the Actor gate, and the Bantuson portfolio support Agent
answering grounded questions, each provisioned the way its finish-line test specifies.
_Avoid_: done, shipped, MVP, validated.
