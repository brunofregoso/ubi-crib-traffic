# Self-hosted CI/CD (deploy off `main` on your own machine)

`.github/workflows/deploy.yml` runs on a **GitHub Actions self-hosted
runner** on your Mac. Every push to `main` (i.e. a merge from `develop`)
triggers one pipeline:

1. **Sanity checks** — fresh venv, `pip install -r requirements.txt`,
   `compileall`, and an import smoke test on both entry-point scripts.
   A broken import fails the run before anything is deployed.
2. **Build image** — `docker build -f collector/Dockerfile`, tagged with
   the commit SHA and `latest`.
3. **Deploy collector** — `scripts/deploy.sh` recreates the
   `eta-collector` container with `--restart unless-stopped`.
4. **Run regression** — `regression/train_and_evaluate.py` against the
   live DB; output lands in `data/regression-output/`. "No readings yet"
   is a skip, not a failure.

Nothing here needs inbound network access — the runner makes an
outbound long-poll to GitHub.

## One-time setup

### 1. Canonical clone + `.env`

The workflow reads `.env` and persists data from a fixed clone on the
host, **separate** from the runner's own working directory. Default:
`~/ubi-crib-traffic`.

```bash
git clone https://github.com/brunofregoso/ubi-crib-traffic.git ~/ubi-crib-traffic
cd ~/ubi-crib-traffic
cp .env.example .env      # fill in GOOGLE_MAPS_API_KEY, coords, etc.
```

If your clone is elsewhere, set a repo Variable `ETA_REPO_DIR` to its
absolute path (GitHub → repo → Settings → Secrets and variables →
Actions → Variables).

### 2. Docker Desktop

Install Docker Desktop and set it to **start at login** (Settings →
General → "Start Docker Desktop when you sign in"). The runner shells out
to `docker`, so the daemon must be up whenever a deploy might fire.

### 3. Register the runner

GitHub → repo → Settings → Actions → Runners → **New self-hosted
runner** → macOS. Follow the shown commands; when configuring, add the
labels the workflow expects:

```bash
mkdir ~/actions-runner && cd ~/actions-runner
# curl the runner tarball per the page, then:
./config.sh --url https://github.com/brunofregoso/ubi-crib-traffic \
  --token <TOKEN-FROM-THAT-PAGE> \
  --labels self-hosted,macOS \
  --name "$(hostname -s)" --unattended
```

Run it as a login-scoped service (launchd) so it survives logout/reboot
and inherits your user's Docker access:

```bash
./svc.sh install
./svc.sh start
./svc.sh status
```

The runner executes as your macOS user. It has full access to your
`$HOME` and your Docker socket — only enable this on a machine you
control.

### 4. Get the workflow onto `main`

The runner only acts on `push` events to `main` once `deploy.yml` exists
on `main`:

```bash
git checkout develop && git push
# open a PR develop -> main and merge, or:
git checkout main && git merge develop && git push
```

That merge is itself the first deploy.

## Day-to-day

- **Trigger a deploy:** merge to `main`. Or run it by hand from the
  repo's Actions tab (`deploy (self-hosted)` → Run workflow).
- **Watch a run:** repo → Actions. Full step logs, history, and
  pass/fail live there.
- **Collector logs:** `docker logs -f eta-collector`.
- **Regression output:** `~/ubi-crib-traffic/data/regression-output/`
  (`summary.json` + per-segment PNGs).
- **Manual redeploy without a push:**
  ```bash
  cd ~/ubi-crib-traffic
  scripts/deploy.sh --build
  ```

## Data persistence note

The stock `docker run` in the README writes the SQLite file **inside**
the container, so a rebuild wipes collected readings. `scripts/deploy.sh`
fixes this: when `DATABASE_URL` is a relative `sqlite://` URL (the
default), it bind-mounts `data/` and overrides the container's
`DATABASE_URL` to `sqlite:////data/eta.db`, so readings survive every
redeploy at `~/ubi-crib-traffic/data/eta.db`. An absolute SQLite path or
a Postgres/MySQL URL is passed through from `.env` untouched — for a
long-running setup, Postgres is still the better call.

## Tunables (repo Variables)

| Variable       | Default                     | Purpose                        |
| -------------- | --------------------------- | ------------------------------ |
| `ETA_REPO_DIR` | `$HOME/ubi-crib-traffic`    | Clone holding `.env` + `data/` |

`scripts/deploy.sh` also honors `IMAGE`, `TAG`, `CONTAINER`,
`ETA_ENV_FILE`, `ETA_DATA_DIR` as environment overrides.
