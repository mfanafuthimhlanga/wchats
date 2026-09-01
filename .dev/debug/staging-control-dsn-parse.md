# staging-control-dsn-parse
Symptom      Railway api staging crash-loops at import. Deploy log 2026-09-01T10:21:25Z:
             sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL from given URL string
             raised from create_async_engine(settings.CONTROL_DB_URL) at app/core/database.py:23,
             after the owner updated redis + control DB vars and synced.
Reproduce    Every container start (8+ identical tracebacks in one log window), so 5/5 on Railway.
             Locally, .venv/Scripts/python.exe make_url() matrix reproduces the exact exception:
               unresolved railway ref ${{X.Y}}   -> ArgumentError
               value with surrounding quotes     -> ArgumentError
               empty string                      -> ArgumentError
               leading whitespace                -> ArgumentError
               postgresql+asyncpg://user:your-password@host/db?ssl=require -> parses
Ruled out    - Missing var: pydantic Settings requires CONTROL_DB_URL (str, no default,
               config.py:73); Settings constructed fine, so the var IS set. It is set to garbage.
             - Wrong scheme / ssl param: postgresql+asyncpg://...?ssl=require parses (observed
               above), and even plain postgresql:// parses; a scheme mistake fails LATER with a
               driver error, not this parse error.
             - Code path: database.py passes settings.CONTROL_DB_URL verbatim, no transformation.
Current      CONTROL_DB_URL in the Railway api service resolves to a non-URL string. Candidates,
             most likely first: (a) surrounding quotes kept by the raw editor, (b) an unresolved
             ${{...}} reference (typo'd service/var name passes through literally), (c) pasted
             Neon "psql '...'" command instead of the bare DSN, (d) leading whitespace/newline.
             Falsifier: owner reveals the raw value in Railway Variables; if it begins exactly
             with postgresql+asyncpg:// the hypothesis is dead.
Next         Owner reads the raw CONTROL_DB_URL value in Railway (api service > Variables),
             fixes it to a bare one-line postgresql+asyncpg://...?ssl=require DSN, checks
             CONTROL_DB_SYNC_URL the same way (database.py:45 parses it next and will raise the
             same way), redeploys. Then probe: curl https://api-service-staging-09dc.up.railway.app/health
             and read the body (redis/db keys), never the dashboard.

## Observed 2026-09-01, this session

Probe of the live service (not the dashboard):
  curl https://api-service-staging-09dc.up.railway.app/health
  -> HTTP 502 {"status":"error","code":502,"message":"Application failed to respond"}
  The var update moved staging BACKWARDS: the handoff recorded /health HTTP 200
  (body redis:error, db:error). It is now down at import, not degraded.

make_url() differential, run against the repo venv:
  postgresql+asyncpg://user:your-password@host/db?ssl=require                        PARSES
  postgresql://user:your-password@host/db?sslmode=require&channel_binding=require    PARSES
  postgresql+asyncpg://... KEEPING sslmode+channel_binding          PARSES
  CONTROL_DB_URL=postgresql+asyncpg://...                           FAILS ArgumentError
  psql 'postgresql://...'                                           FAILS ArgumentError
  "postgresql+asyncpg://..."  (double quotes)                       FAILS ArgumentError
  'postgresql+asyncpg://...'  (single quotes)                       FAILS ArgumentError
  ${{Postgres.DATABASE_URL}}  (unresolved reference)                FAILS ArgumentError
  " postgresql+asyncpg://... \n"  (whitespace)                      FAILS ArgumentError
  -d postgresql://...                                               FAILS ArgumentError

  => A parse error means the VALUE IS NOT A BARE URL. Wrong driver and wrong ssl
     param both PARSE and fail later with a different exception, so the scheme and
     the ssl flavour are ruled out as the cause of THIS error.

Exact reproduction of the container traceback, locally:
  CONTROL_DB_URL='<bad shape>' .venv/Scripts/python.exe -c "import app.core.database"
  reproduces sqlalchemy/engine/url.py:922 ArgumentError identically for all three of
  the env-line, psql-wrapper and quoted shapes. 3/3.

  The three are INDISTINGUISHABLE in the traceback, and it never names CONTROL_DB_URL
  or CONTROL_DB_SYNC_URL. That is the real defect: a config paste error costs a
  crash-loop plus a dashboard hunt, because nothing fails fast and names the field.

Gate state: apps/api scripts/gates.py static -> EXIT 0 in 46.7s (CLAUDE.md says 8.4s;
that figure has drifted).

## The two driver dialects are mirror images (measured, not quoted)

asyncpg 0.31.0 vs psycopg2 2.9.12, same box, same venv:

  param                     asyncpg                     psycopg2
  ssl=require               accepted                    ProgrammingError: invalid
                                                        connection option "ssl"
  sslmode=require           TypeError: unexpected       accepted
                            keyword argument
  channel_binding=require   TypeError: unexpected       accepted
                            keyword argument

SQLAlchemy's create_connect_args passes query params through UNTRANSLATED (observed
for both dialects), so the wrong param parses, boots, and then kills every connection
at connect time rather than at import. Neon hands out
?sslmode=require&channel_binding=require, which is right for the SYNC url and fatal
for the ASYNC one. Swapping the two DSNs breaks in both directions.

## Known-good target shape, read from the production .env (shape only, no secrets)

  CONTROL_DB_URL       driver postgresql+asyncpg  host neon -pooler  query ['ssl']
  CONTROL_DB_SYNC_URL  driver postgresql          host neon -pooler  query ['sslmode']
  database neondb in both; neither value is quoted in the file; neither carries
  channel_binding.

So staging mirrors production against the SEPARATE staging Neon project:
  CONTROL_DB_URL      = postgresql+asyncpg://USER:PW@ep-...-pooler...neon.tech/neondb?ssl=require
  CONTROL_DB_SYNC_URL = postgresql://USER:PW@ep-...-pooler...neon.tech/neondb?sslmode=require
Take Neon's copied string for the SYNC one; for the ASYNC one swap the scheme AND
replace the whole query string. Do not keep channel_binding on either.

## Fix landing in this session

A field_validator on both fields in app/core/config.py, so a mispaste is refused at
Settings construction with a message that NAMES the field and the fault, instead of
an ArgumentError 60 frames deep that names neither. Covers: whitespace, unparseable
value, swapped driver, and a query param the field's driver refuses.

## Edge cases make_url does NOT refuse (probed, and they shaped the guard)

  postgresql+asyncpg://u:p@h:notaport/db   ValueError, NOT ArgumentError
  postgresql://postgresql+asyncpg://user:your-password@host/db   PARSES as driver 'postgresql'
  postgresql+asyncpg://                    PARSES, host=None
  postgresql+asyncpg://user:your-password@host/db\nEXTRA=1   PARSES, host='h', database 'db\nEXTRA=1'
  s3cr3t-do-not-leak                       ArgumentError
  postgres ql://user:your-password@host/db                   ArgumentError

The newline one is the nastiest: a multi-line paste into Railway's raw editor is
accepted silently and corrupts the database name rather than failing, so the guard
refuses a line break ANYWHERE, not only at the ends. The non-numeric port raises
ValueError, so the guard catches (ArgumentError, ValueError) or it escapes.

Credential safety: the guard echoes only value.split("://", 1)[0], which by URL
construction cannot contain userinfo, and falls back to a character count when there
is no "://" at all. model_config sets hide_input_in_errors=True, so pydantic will not
echo the value itself; a test pins that a password never reaches the message.
