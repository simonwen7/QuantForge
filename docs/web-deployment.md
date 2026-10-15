# Web terminal deployment

QuantForge's interactive terminal is two Vercel projects from this one
repository. The Python API and the Next.js app deploy separately. This
document describes the expected project settings. It does not record a
deployment. No public URL exists yet.

Placeholders, not addresses:

- API project: not deployed
- Web project: not deployed

## API project

| Setting | Value |
| --- | --- |
| Root directory | repository root (`.`) |
| Framework | Python, detected from `api/index.py` |
| Python | 3.12 |
| Install command | `pip install -r requirements.txt` when the project root has no `package.json` |
| Entrypoint | `api/index.py` exporting `app` |
| Function duration | `vercel.json` sets `maxDuration` to 30 seconds |

`requirements.txt` is generated from `uv.lock`. It pins FastAPI, Pydantic,
Starlette, DuckDB 1.5.6, and their transitive packages. A default install of
this repository from `pyproject.toml` and `uv.lock` resolves only DuckDB,
because FastAPI is in the `api` dependency group rather than the library's
runtime dependencies.

`.vercelignore` excludes `pyproject.toml` and `uv.lock` from the API project
upload. That leaves `requirements.txt` as the dependency manifest the Python
build installs. `.python-version` still says `3.12`. Do not remove those two
exclusions unless FastAPI is added to the library's runtime dependencies.

`api/index.py` inserts `src/` on `sys.path` when that directory is present, so
the function does not need the developer virtual environment or a preset
`PYTHONPATH`. The library is not installed as a wheel inside the function.
The source tree is the import root.

Routes are registered both with and without the `/api` prefix:

- `/api/health` and `/health`
- `/api/config` and `/config`
- `/api/backtest` and `/backtest`
- `/api/compare` and `/compare`
- `/api/research-block` and `/research-block`

The duplicate mount exists because a hosted Python function may or may not
include `/api` in the path it passes to FastAPI. Which of the two the live
runtime uses is a hosted verification gate. Locally, the `/api` paths are the
ones the tests call.

`.vercelignore` keeps tests, virtual environments, the Next.js app, docs, and
build outputs out of the function upload. `src/` and `api/` stay included.

There is no database, no background worker, and no market-data download.
Request bodies are limited to 8 KB by the application. That limit is not a
public rate limit. A public function can still consume serverless compute
until the platform quota stops it. Stronger quota enforcement would need
infrastructure this repository does not add.

Hobby plans can reject a 30-second function duration. If the project plan
allows only 10 seconds, lower `maxDuration` in the API project's `vercel.json`.
A 168-bar synthetic run is a short CPU task, but the plan cap is a hosted
setting, not something this repository can observe.

DuckDB is imported because existing research modules import it at import time.
The synthetic terminal does not query it. Whether the Vercel Python image can
load the pinned DuckDB wheel is a hosted verification gate. Do not remove
DuckDB from the library to make a guess succeed.

## Web project

| Setting | Value |
| --- | --- |
| Root directory | `apps/web` |
| Framework | Next.js |
| Node.js | 22 |
| Install command | `npm ci`, selected because `apps/web` contains `package.json` and `package-lock.json` |
| Environment variable | `QUANTFORGE_API_ORIGIN` |

Set `QUANTFORGE_API_ORIGIN` to the API project's origin, with no path and no
trailing slash. Example shape, not a real host:
`https://quantforge-api.example.vercel.app`.

The variable is not prefixed with `NEXT_PUBLIC_`. The browser does not receive
it. `app/api/[...path]/route.ts` reads it when a request arrives, then fetches
`${QUANTFORGE_API_ORIGIN}/api/<route>`. `next build` does not need the
variable. A production process that is missing it returns 503 and does not
call `127.0.0.1`.

`next dev` uses `http://127.0.0.1:8000` only when the variable is unset and
`NODE_ENV` is `development`.

The proxy allows `health`, `config`, `backtest`, `compare`, and
`research-block`. It does not forward arbitrary paths.

## Local processes

From the repository root, with the API dependency group installed:

```bash
uv sync --locked --python 3.12 --group dev --group api
uv run --group api --no-sync uvicorn api.index:app --host 127.0.0.1 --port 8000
```

In another shell:

```bash
cd apps/web
npm ci
npm run dev
```

Open `http://127.0.0.1:3000`. The first screen requests
`POST /api/backtest` through the Next route, which calls the Python process.

## Deployment sequence

1. Deploy the API project first, from the repository root.
2. Open the API project's `/api/health`, then POST a small synthetic body to `/api/backtest`. The response must name `synthetic_unverified` and return bars from the Python engine.
3. Deploy the web project with root directory `apps/web`.
4. Set `QUANTFORGE_API_ORIGIN` to the API origin, with no path and no trailing slash.
5. Open the web project and run a backtest, the replay, and a comparison through the site.
6. Read both projects' runtime logs for import errors, missing packages, and 502 responses.
7. Confirm the public web URL loads the terminal. Record the real origins in place of the placeholders above.

## What this repository does not do

- It does not authenticate to Vercel.
- It does not create the projects.
- It does not store a deployment token.
- It does not publish a public URL.

After a human deploy, replace the placeholders above with the real origins and
confirm `GET /api/health` on the API project before pointing the web project
at it.
