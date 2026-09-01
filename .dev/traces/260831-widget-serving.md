# 260831 · widget-serving (#135)

Branch `fix/widget-serving` off `474bb7e`. One logical change: the api service serves the
widget bundle, and the embed snippet derives that host, so nothing can emit a URL nothing
serves.

## What changed

- `apps/widget/scripts/sync-embed.mjs`: a third SHA-gated target,
  `apps/api/static/wchats/` (beside `app/`, not in it: the complexity gate runs lizard
  over `app/` and a minified bundle is not code it should measure). The script now
  creates missing target directories.
- `apps/api/static/wchats/`: the four synced files (loader, index.html, iife bundle,
  css), checked in like the other two targets, inside the api Docker context.
- `apps/api/app/main.py`: `app.mount("/wchats", StaticFiles(...))` over that folder.
- `apps/api/app/core/config.py`: `WIDGET_CDN_BASE` defaults to empty. The old default,
  `https://widget.wchats.app`, named a host nothing served once the CloudFront origin
  went with ADR 0005, so every emitted snippet was dead on arrival, silently.
- `apps/api/app/services/deployment_service.py`: an empty `WIDGET_CDN_BASE` derives the
  bundle host as `PUBLIC_API_BASE + "/wchats"`; a configured CDN still wins. The
  production refusal on loopback API bases now guards the derived host too.
- `apps/api/.env.example`: the variable's guidance says leave it unset.

## Decisions

- Serve from the api service, not an R2 public bucket. The loader resolves index.html,
  css and the bundle relative to `script.src`, so one mounted folder serves the whole
  set; R2 would add an owner console journey, a public-bucket policy and a fourth host
  for zero finish-line benefit. A real CDN later is one env var.
- Derive rather than configure. Two hosts that must agree and are set independently is
  the drift that produced #135; deriving one from the other removes the failure mode
  instead of documenting it.

## Observed

- `sync-embed.mjs --check`: PASS, 10 files across three targets, SHA-matched.
- Mount tests: 3 passed (all four files serve, foreign path 404, served loader
  byte-identical to the synced folder).
- `test_deployment_routes.py` after the assertion update and two new derivation pins:
  43 passed in 153.0s.
- Mutation proof: the derivation fallback removed, the derivation test failed; restored,
  the suite passed (outputs in the PR).
- `gates.py static` passed in 15.6s (after relocating the bundle out of lizard's tree,
  which had failed the gate on the minified functions). ruff clean.

## The tier-1 review

15 findings. Applied: the no-store middleware now exempts `/wchats` (public,
max-age=300 with ETag revalidation; CloudFront's caching was its unreplicated second
job), the integration e2e assertion that still demanded the dead host, the root
`.env.example` that still instructed setting it (an explicit value beats the
derivation), a CI drift gate (`git diff --exit-code` over the three sync targets,
because postbuild's write-mode sync would silently repair drift in the CI checkout),
production refusal extended to the configured CDN and to non-https schemes on both
bases (`_refuse_unservable_base`), the fail-loud import note on the mount, the 404
test's in-test control, honest docstrings on the byte-identity test and the loader's
iframe claim, and the CORS caveat wherever a CDN is suggested.

Declined, recorded in PRODUCTION-READINESS as accepted costs with the CDN env var as
the lever: no compression (gzip middleware risks SSE buffering; ~25KB raw once per
visitor per cache window), widget delivery sharing the API's fate and capacity, and
widget rebuilds redeploying all four Railway services via `apps/api/**` watch
patterns.

After the fixes: 46 passed across the deployment and mount suites; ruff clean;
`gates.py static` 34.3s green; sync check PASS over 10 files.
