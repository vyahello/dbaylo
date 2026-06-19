# Deploy

Дбайло runs on a small VPS. CI (`.github/workflows/ci-cd.yml`) runs the test suite on
every push/PR and, on a green `main`, **rsyncs the code to the VPS and restarts the bot**.

## One-time VPS setup

On the VPS, as the deploy user:

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv git rsync
mkdir -p ~/dbaylo && cd ~/dbaylo          # this is your VPS_APP_DIR
git clone <your-repo-url> .                # or let CI rsync the first push
cp .env.example .env && nano .env          # fill BOT_TOKEN (at least)
bash deploy/setup-vps.sh                    # venv + migrate + install & enable the systemd unit
```

`setup-vps.sh` installs `dbaylo-bot.service` (long-polling — works **without** a TLS
cert) and prints the one sudoers line CI needs to restart it passwordlessly.

## GitHub secrets (already set)

Configured under **Settings → Secrets and variables → Actions** — never hard-coded here.

| Secret | Meaning |
|---|---|
| `VPS_HOST` | the VPS hostname or IP |
| `VPS_USER` | the deploy user on the VPS |
| `VPS_SSH_KEY` | private key authorized on the VPS |
| `VPS_APP_DIR` | absolute path of the app dir on the VPS |

## How CI deploys

1. `test` job: `ruff` + `ruff format --check` + `mypy` + `pytest --cov`.
2. `deploy` job (only on green `main`): writes the SSH key, `ssh-keyscan`s the host,
   **rsyncs** the working tree (excluding `.env`, `data/`, `venv/`, `*.db`, caches),
   then runs `deploy/deploy.sh` on the VPS (install → `alembic upgrade head` → restart).

`.env`, the SQLite DB, and stored lab files on the VPS are never overwritten (excluded
from rsync and protected from `--delete`).

## Webhook + TLS (optional, later)

The bot runs fine via **long polling** with no public URL or certificate. To switch to
the webhook, point a domain you control at the VPS, then:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example    # issue + auto-renew the cert
```

Point nginx at `127.0.0.1:8000` (the `dbaylo-web` bind), set `WEBHOOK_BASE_URL=https://your-domain.example`
in `.env`, add a `dbaylo-web.service` (uvicorn via `venv/bin/dbaylo-web`), and disable
`dbaylo-bot` (don't run both). Telegram requires HTTPS for webhooks, so the cert is mandatory there.
