# The provider routing has two seams, and only one obeys the route table

`PURPOSE_ROUTES` in `app/core/model_client.py` looks like the single place a model call's
provider and model are decided. It is not. Two seams read it differently, and the split
decides which calls actually reach OpenAI.

Measured 2026-08-27 on `feat/sdk-removal`. Anything below that carries a number was run.

## The two seams

| Seam | Reads the route's provider | Builds |
|---|---|---|
| `make_instructor_client` | yes | `openai.OpenAI`, and raises `UnsupportedProvider` for any other route |
| `make_client` / `LedgerContext.client` | **no** | whatever `provider_for_base_url` derives |
| `make_async_client` | passes `OPENAI_PROVIDER` literally | `openai.AsyncOpenAI` |

`make_client` resolves in this order, in `_hooked_sdk_client`:

```python
credentials = credentials or resolve_credentials(provider)      # provider is None
provider = provider or provider_for_base_url(credentials.base_url)
```

`provider_for_base_url(None)` returns `"anthropic"` by its first branch. `_check_raw_purpose`
does read `PURPOSE_ROUTES`, but only to refuse an unknown purpose and one carrying a
reasoning effort. The route's `provider` field is never consulted on this path.

## What that produces

Every raw direct-API purpose, checked one by one:

```
scenario_generation      route=openai/gpt-5.6-luna   built=Anthropic
metadata_enrichment      route=openai/gpt-5.6-luna   built=Anthropic
actor_gate               route=openai/gpt-5.6-luna   built=Anthropic
red_team_prompt          route=openai/gpt-5.6-luna   built=Anthropic
red_team_probe           route=openai/gpt-5.6-luna   built=Anthropic
red_team_severity        route=openai/gpt-5.6-luna   built=Anthropic
deployment_orchestrator  route=openai/gpt-5.6-luna   built=Anthropic
query_expansion          route=openai/gpt-5.6-luna   built=Anthropic
retrieval_strategist     route=openai/gpt-5.6-luna   built=Anthropic
strategist               route=openai/gpt-5.6-luna   built=Anthropic
gatekeeper               route=openai/gpt-5.6-luna   built=Anthropic
auditor                  route=openai/gpt-5.6-luna   built=Anthropic
```

The call sites match the client rather than the route, which is why nothing raised sooner:
`retrieval_service.py:472` and `strategy_service.py:228` both call `.messages.create(...)`
with a Claude model id, and `openai.OpenAI` has no `.messages` attribute at all.

Issue #88 carries the decision. **Do not read a green route-table test as evidence about a
client.** Every existing test asserts `route_for(purpose).provider == "openai"`, which is a
claim about the table. None asserts what the factory hands back.

## The empty-key asymmetry, and why it hides this

```
openai.OpenAI(api_key="")     -> raises OpenAIError, message names api_key
anthropic.Anthropic(api_key="") -> constructs; fails later, resolving auth at the call
```

Pinned by `tests/unit/test_model_client.py::TestWhatEachProviderSdkDoesWithAnEmptyKey`.
Offline, no socket.

This is why the twelve above are worth treating as urgent rather than untidy. They are on the
branch that fails quietly, and `ANTHROPIC_API_KEY` has defaulted to empty since commit
`82d5db9` retired the credential on 2026-08-26. A misconfigured worker on the OpenAI branch
dies at the factory naming the setting; on the Anthropic branch it reaches the call site and
surfaces as whatever the caller's `except` decides to say.

## Where a key is read from

Settings, not `os.environ`, since #49 removed the Agent SDK. `resolve_credentials` reads
`settings.OPENAI_API_KEY`, and an absent `OPENAI_BASE_URL` is the correct production
endpoint. Defect `1.28` was the opposite: pydantic filled Settings while the SDK's client read
the environment, so an unexported base url sent a DeepSeek key to `api.anthropic.com`.

**The failure mode inverted. An unset variable is now the safe case and a set one is what to
look at.** `scripts/probe_environment.py` reports the shell it is run from.

## Checking this yourself

```bash
cd apps/api
.venv/Scripts/python.exe -c "
from app.core.model_client import PURPOSE_ROUTES, make_client
raw = [p for p, r in PURPOSE_ROUTES.items() if r.reasoning_effort is None]
for p in raw:
    c = make_client(p, tenant_id='t', recorder=lambda call: None)
    print(f'{p:24} route={PURPOSE_ROUTES[p].provider} built={type(c).__name__}')
"
```

Builds clients, opens no socket, spends nothing.
