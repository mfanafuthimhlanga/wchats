# Railway runs the service instance's start command, not the toml's

Each `apps/api/railway.*.toml` names a `startCommand`, and none of the four staging services
runs it. The command a deployment runs is the service instance setting, readable as
`serviceInstances[].node.startCommand` in `railway status --json` and written through the
`serviceInstanceUpdate` mutation. The toml is what `railway up` reads when a service has no
setting of its own; every staging service has one, set when the wizard created it.

Found 2026-09-13: #252 added `--without-gossip --without-mingle --without-heartbeat` to both
worker tomls, CI passed, the merge deployed, and the `worker-runtime` log still read
`mingle: searching for neighbors`. The deployment's `meta.serviceManifest.deploy.startCommand`
was the old command.

## Change a start command

```bash
S=/path/to/scratch
printf '%s' 'mutation($s: String!, $e: String!, $i: ServiceInstanceUpdateInput!) { serviceInstanceUpdate(serviceId: $s, environmentId: $e, input: $i) }' > $S/siu.graphql
# variables: {"s": "<serviceId>", "e": "<environmentId>", "i": {"startCommand": "..."}}
railway api -f $S/siu.graphql --variables @$S/vars.json
```

Service and environment ids come from `railway status --json`. `railway api` refuses a
query as a positional argument and a JSON envelope on stdin; it takes the document from
`-f` or bare stdin and the variables from `--variables`. The change takes effect on the
next deployment, so a parked service picks it up when it is next brought up.

Keep the toml and the instance setting identical. `test_railway_config.py` pins the toml,
and nothing pins the instance; until something does, read both after any change.

## Staging as of 2026-09-13 12:41 SAST

Both workers carry the three flags in their instance setting, `polling_interval` is 10 in
code (#252, an integer, because BRPOP refuses `10.0`), and all four services are parked.

## Unparking, and the deploy that is SKIPPED

`railway up --detach -y -s <service> -e staging` on a parked service can come back
`SKIPPED` (observed twice on `worker-runtime` at 20:00 SAST, and `railway redeploy` then
says "No deployment found for service"). Railway skips an upload whose content matches the
last build even when nothing is running. What forces a real deployment is any variable
change on the service: `railway variables --set WCHATS_UNPARK_STAMP=$(date +%s) -s
worker-runtime -e staging` produced `BUILDING` within seconds and `ready.` a minute later.
The stamp is harmless and stays; bump it to unpark again.

A chat test needs `api-service` and `worker-runtime` only. `beat` and `worker-pipeline`
stay parked and cost nothing.
