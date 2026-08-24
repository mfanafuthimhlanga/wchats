# W Chats embed

The deployable widget delivery layer. This folder is **self-contained** — drop it on any
static host and the one-line snippet works.

```
embed/
├── widget.js        ← loader the customer pastes (reads data-agent / data-api, injects launcher + iframe)
├── index.html       ← iframe host page (loads the bundle, reads ?agent_id=&api=)
├── widget.iife.js   ← built Preact widget, written here by the build (24.7 KB raw, 9.4 KB gzipped
│                      against a 20,480-byte gzipped budget)
└── widget.css       ← built widget styles, written here by the build (6.6 KB raw)
```

`widget.iife.js` and `widget.css` are build output: `npm run build` copies them from `dist/`
and `npm run check:embed-sync` fails if the two folders ever differ. Do not edit them here.

`apps/admin/public/wchats/` is the second copy of these four files, and it is the one the
production deploy actually uploads (`deploy/README.md` step 4, `deploy/terraform` widget
bucket). The same build step and the same gate keep it in step with this folder, so a change
here cannot ship without reaching there. Edit the loaders here; never there.

## Paste-in snippet

```html
<script src="https://<WIDGET_HOST>/wchats/widget.js"
        data-agent="fe230a9d-09f0-4043-b2f1-4506a2ef0059"
        data-api="https://<API_HOST>"
        async></script>
```

- `data-agent` — the deployed agent id (required).
- `data-api` — the public base URL of the W Chats API (required for the agent to answer).
  Swappable at any time with no rebuild — this is the "flip to cloud-native" seam: point it
  at a managed container host today, at AWS Fargate/ALB tomorrow.
- `data-color` / `data-label` — optional launcher styling.

## Deploy

1. Rebuild if the widget source changed: `npm run build` in `apps/widget`. It refreshes
   `widget.iife.js` and `widget.css` here, so no copy step is needed.
2. Publish this folder under a stable path on a static host (e.g. Vercel `public/wchats/`,
   or S3 + CloudFront). All four files must sit in the same folder.
3. Ensure the API host allows the embedding origin — widget routes already send
   `Access-Control-Allow-Origin: *`, so any site works.
4. Paste the snippet into the target site.

## How it works

`widget.js` finds its own `<script>` tag, reads `data-agent`/`data-api`, injects a floating
launcher, and on first click mounts `index.html?agent_id=…&api=…` in a sandboxed `<iframe>`.
The iframe app (`src/index.jsx`) calls `GET /widget/{id}/config` for a JWT + theming, then
`POST /widget/{id}/chat` + SSE `GET /widget/jobs/{job}/events`. Nothing about the embed is
host-specific, so the same files work behind Vercel, CloudFront, or a tunnel.
