# Working on Hexfield

Read CONTRIBUTING.md first: no runtime dependencies, seeds stay valid, bot strength is
measured. The rules below are for CI, the image, the deploy and dependencies; README's
"Deploying hex.vaari.dev" says how they fit together.

## Deploy

- The deploy is two files, `compose.yaml` and `.env`, and one command
  (`docker compose up --wait`). CI renders `.env` from the `oracle-cloud` environment, with
  `APP_IMAGE=ghcr.io/vaari-dev/hexfield:<full sha>` first. All branching, including the
  rollback, runs in CI; each command sent to the server is one line.
- Keep logic off the server: no scripts, polling loops, downloads, `mkdir` or `sudo`, and no
  registry login (the package is public). If the server needs to know something, it goes
  in one of the two files.
- Deploy one compose file that publishes no host port: the server is shared, and Caddy
  reaches `hexfield:8765` over the edge network. `compose.override.yaml` only adds the
  loopback port for local use and is never copied to the server.
- The app's compose healthcheck is the deploy gate. Never add a curl loop to CI.
- `backup` runs before the server on every `up`. Keep it: updates merge and deploy
  themselves, so a manual backup step would be skipped.
- A change to what is stored (accounts.db's schema, the saved-game format) must not break
  the release running now: the old server keeps serving while the new one starts, and a
  rolled-back image runs on whatever the new one wrote. Stop reading something in one
  release; remove it in a later one.
- A new setting is one line in each of `.env.example`, `compose.yaml` and the deploy job's
  `Write .env` step. Secrets use `${VAR:?}` in compose, never a default.

## CI

- The ruleset on `main` requires `test`, `browser` and `image`. Keep those job names, and
  put new checks inside them rather than in a new job nobody requires.
- `image` is the only build. It runs on every event; login, push and the attestation run
  only on `main`. One tag: the full commit sha, also passed in as `HEXFIELD_BUILD`.
- `permissions: contents: read` at the top, more only per job; `persist-credentials: false`
  on every checkout; `timeout-minutes` on every job.

## Dependencies

- Every action is pinned to a commit sha with `# vX.Y.Z`, every image to a digest, every
  Python tool to an exact version in `pyproject.toml`. A tool installed in a `run:` line
  gets a `# renovate:` comment (see `PLAYWRIGHT_VERSION`).
- Renovate (renovate.json) is the only updater. Don't bump by hand what it would bump; if
  it's wrong, fix renovate.json.
- A new Python (3.15 to 3.16) also needs a classifier in pyproject.toml; Renovate opens it
  as its own pull request for that. The 3.11 pin in `test` moves only with
  `requires-python`.

## Code and docs

- Lint and format with ruff (`ruff check . && ruff format .`); the pre-commit hook in
  `.githooks` runs it and the tests. `# fmt: off` only where the layout means something
  (the beginner board's rows).
- Prefer one atomic SQL statement to read-then-write (see `Accounts.used`).
- Comment only what the code can't say: why, or what not to change back. No
  "keep in step with X": make it one source instead.
- Update README, CONTRIBUTING, `.env.example` and this file in the same pull request as
  the change they describe. A doc that no longer matches the code is a bug.
- Pull request titles state the end state; commit bodies give the why.
