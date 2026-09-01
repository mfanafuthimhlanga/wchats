# The two control DSNs take mutually exclusive ssl parameters

W Chats runs two engines against the same control database: an async one on asyncpg for
FastAPI, and a sync one on psycopg2 for Celery and Alembic. Their connection strings look
almost identical and their ssl parameters are mutually exclusive, so a value that works in
one variable silently destroys the other. Read this before editing `CONTROL_DB_URL` or
`CONTROL_DB_SYNC_URL` anywhere, local or deployed.

## Measured, 2026-09-01, asyncpg 0.31.0 and psycopg2 2.9.12

| query param | asyncpg | psycopg2 |
|---|---|---|
| `ssl=require` | accepted | `ProgrammingError: invalid dsn: invalid connection option "ssl"` |
| `sslmode=require` | `TypeError: connect() got an unexpected keyword argument 'sslmode'` | accepted |
| `channel_binding=require` | `TypeError: connect() got an unexpected keyword argument 'channel_binding'` | accepted |

`asyncpg.connect` takes no `**kwargs`, so an unknown parameter is a hard `TypeError`.
SQLAlchemy's `create_connect_args` hands query parameters to the driver untranslated, for
both dialects, so SQLAlchemy never corrects the mismatch.

Neon issues `?sslmode=require&channel_binding=require`. That string is correct for the
sync variable and fatal for the async one. Deriving the async value means swapping the
scheme AND replacing the whole query string, not copying it across.

The shape both live environments use:

```
CONTROL_DB_URL      = postgresql+asyncpg://USER:PW@ep-...-pooler...neon.tech/neondb?ssl=require
CONTROL_DB_SYNC_URL = postgresql://USER:PW@ep-...-pooler...neon.tech/neondb?sslmode=require
```

Pooler host, database `neondb`, one ssl-flavoured parameter each, `channel_binding` on
neither.

## A parse error and a connect error mean different things

`sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL from given URL string`
narrows the fault to the paste, never to the DSN's contents. Wrong driver and wrong ssl
parameter both parse.

Values that fail to parse:

```
CONTROL_DB_URL=postgresql+asyncpg://...     an env-file line pasted as the value
psql 'postgresql://...'                     the Neon psql tab snippet
"postgresql+asyncpg://..."                  surrounding quotes, either kind
${{Postgres.DATABASE_URL}}                  a reference that did not resolve
 postgresql+asyncpg://...                   leading or trailing whitespace
```

Values that parse and then break:

```
postgresql+asyncpg://...?sslmode=require    boots, then every query raises TypeError
postgresql+asyncpg://user:your-password@host/db\nEXTRA=1      database becomes 'db\nEXTRA=1'
postgresql+asyncpg://                       host is None
postgresql://postgresql+asyncpg://user:your-password@host/db  driver reads as plain postgresql
```

`make_url` raises `ValueError`, not `ArgumentError`, on a non-numeric port, so a guard
catching only `ArgumentError` lets that one through.

## The guard

`app/core/config.py` validates both fields at `Settings` construction, refusing a line
break, surrounding whitespace, an unparseable value, a missing host, the other engine's
driver, and a query parameter the field's driver rejects. Each message names the field.

`model_config` sets `hide_input_in_errors=True`, so a validator message carries its own
diagnosis. Messages echo only `value.split("://", 1)[0]`, which cannot contain userinfo,
and fall back to a character count when there is no `://`. A test pins that a password
never reaches the message.

## A cold Neon compute reads as a connection failure

Neon computes suspend when idle and take roughly 8 to 20 seconds to wake. The first
probe after a redeploy can therefore fail against a correct DSN. On 2026-09-01 staging
reported `{"redis":"ok","db":"error"}` immediately after a redeploy whose credentials
had already been verified from a workstation, and three probes a few minutes later all
returned `db: ok` with nothing changed in between.

Probe more than once before diagnosing a database failure on a freshly deployed
service. `/health` currently swallows the exception (#142), so a waking compute and a
dead credential are the same four characters.
