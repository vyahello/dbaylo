# Deploy

Дбайло runs on a small VPS as a **systemd service**. CI (`.github/workflows/ci-cd.yml`)
runs the test suite on every push/PR and, on a green `main`, SSHes in, **pulls `main`,
and restarts the bot**.

## One-time VPS setup

The first CI deploy creates the git checkout itself. You only need to provide `.env` and
install the service once. On the VPS, as the deploy user:

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv git
cd <VPS_APP_DIR>                            # where CI checked the repo out
cp .env.example .env && nano .env           # fill BOT_TOKEN (at least)
bash deploy/setup-vps.sh                     # venv + migrate + install & enable the systemd unit
```

`setup-vps.sh` installs `dbaylo-bot.service` (long-polling — works **without** a TLS
cert) and prints the one sudoers line CI needs to restart it passwordlessly. The VPS also
needs SSH access to the GitHub repo (a deploy key, or your existing key — same as any
other bot on this box) so CI's `git fetch` works.

## GitHub secrets

Configured under **Settings → Secrets and variables → Actions** — never hard-coded here.

| Secret | Meaning |
|---|---|
| `VPS_HOST` | the VPS hostname or IP |
| `VPS_USER` | the deploy user on the VPS |
| `VPS_SSH_KEY` | private key the runner uses to SSH into the VPS |
| `VPS_APP_DIR` | absolute path of the app dir on the VPS |
| `REPO_SSH_URL` | *(optional)* the repo's SSH clone URL; CI defaults to it if unset |

## How CI deploys

1. `test` job: `ruff check` + `ruff format --check` + `mypy` + `pytest --cov`.
2. `deploy` job (only on green `main`, via `appleboy/ssh-action`): on the VPS it ensures a
   git checkout in `$VPS_APP_DIR`, runs `git fetch` + `git reset --hard origin/main`, then
   `deploy/deploy.sh` (install → `alembic upgrade head` → restart `dbaylo-bot`).

`.env`, the SQLite DB, stored lab files, and `venv/` are git-ignored, so `git reset --hard`
never touches them — your data and config survive every deploy.

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
