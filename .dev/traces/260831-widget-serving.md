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
