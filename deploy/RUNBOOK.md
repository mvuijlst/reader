# Deploy runbook — a Debian/Ubuntu VPS with nginx

The setup: an unprivileged user **`django`**, gunicorn on a localhost port, nginx in
front, certbot for TLS, a systemd timer for polling. The paths and hostname below are
the defaults baked into `deploy/`; adjust them to taste. Everything lives in
**`/home/django/reader-yusupov`**:

```
/home/django/reader-yusupov/
└── app/                 # clone of this repo
    ├── venv/
    ├── db.sqlite3       # all your subscriptions, items, stars
    └── staticfiles/     # collectstatic output (nginx serves /static/ from here)
```

Before you start:
- Point an A (and AAAA) record for **reader.yusupov.cloud** at the VPS — or swap the
  hostname for another one in `deploy/nginx-reader.conf` and `.env`.
- Port **8027** is assumed free. Check with `ss -ltnp | grep 8027`; if it's taken,
  change it in `deploy/reader.service` and `deploy/nginx-reader.conf`.

## 1. Clone + venv

```bash
mkdir -p /home/django/reader-yusupov && cd /home/django/reader-yusupov
git clone https://github.com/mvuijlst/reader.git app
cd app
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## 2. Configure

```bash
cp .env.example .env   # then edit:
#   DJANGO_SECRET_KEY   → python3 -c "import secrets; print(secrets.token_urlsafe(50))"
#   DJANGO_DEBUG=false
#   DJANGO_ALLOWED_HOSTS=reader.yusupov.cloud

venv/bin/python manage.py migrate
venv/bin/python manage.py createsuperuser        # the single login account
venv/bin/python manage.py collectstatic --noinput
```

Bringing subscriptions along? Copy your OPML export over and:

```bash
venv/bin/python manage.py import_opml ~/subscriptions.opml
venv/bin/python manage.py fetch_feeds
```

(Or upload it on the Manage page after logging in.)

## 3. Services, timer, nginx, TLS

```bash
sudo cp deploy/reader.service deploy/reader-fetch.service deploy/reader-fetch.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now reader reader-fetch.timer

sudo cp deploy/nginx-reader.conf /etc/nginx/sites-available/reader-yusupov
sudo ln -s /etc/nginx/sites-available/reader-yusupov /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d reader.yusupov.cloud
```

Check:
- `curl -I https://reader.yusupov.cloud/login/` → 200
- `systemctl list-timers reader-fetch.timer` → next run within 10 minutes
- `journalctl -u reader-fetch -n 20` → lines like `42 feeds, 17 new items, … in 6.3s`

## 4. Deploy on every push

`.github/workflows/deploy.yml` runs the tests on every push to `main` and, if they pass,
SSHes into the VPS and runs `deploy/update.sh` for that commit: reset to it, install
deps, migrate, collectstatic, gracefully reload gunicorn (HUP via its pidfile, no sudo),
then health-check `/login/`. A failed deploy shows as a red run in GitHub Actions.

The deploy key is pinned to that script on the server (`command="…",restrict`), so even
if it leaked, all it could do is deploy `main`.

**One-time setup**, on your own machine, from this repo (needs `gh` logged in):

```bash
deploy/setup-github-deploy.sh <vps-host-or-ip> <ssh-port>
```

That creates the key, stores five Actions secrets in the repo (`DEPLOY_SSH_KEY`,
`DEPLOY_KNOWN_HOSTS`, `DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER`), deletes the private
key locally, and prints one line. On the VPS, as `django`, append that line to
`~/.ssh/authorized_keys`:

```bash
nano ~/.ssh/authorized_keys      # paste the command="…",restrict ssh-ed25519 … line
```

Then trigger a deploy: push any commit, or **Actions → Test and deploy → Run workflow**.
The run log shows `Code: abc1234 -> def5678 …` and `deployed def5678, site is up`.

Notes:
- Until the secrets exist, the deploy job is skipped with a notice, not failed (forks too).
- To redeploy, use **Run workflow**. **Re-run** replays the old commit's workflow file.
- With a non-standard SSH port, `DEPLOY_KNOWN_HOSTS` must be in bracketed form
  (`[host]:port ssh-ed25519 …`). `ssh-keyscan -p` produces that, and the setup script uses it.
- Server-side edits to tracked files are discarded on deploy. `.env`, `db.sqlite3`,
  `venv/` and `staticfiles/` are untracked and never touched.
- Services were installed from `deploy/` once (step 3). If a later commit changes
  `reader.service` or the timer, copy them again and `sudo systemctl daemon-reload`.
  The deploy doesn't do that, because it runs without root.

## Ongoing

- **Deploy:** push to `main`. By hand: `~/reader-yusupov/app/deploy/update.sh` (optionally
  with a commit hash).
- **Logs:** `journalctl -u reader -e` (web), `journalctl -u reader-fetch -e` (polling).
- **Fetch now:** `cd ~/reader-yusupov/app && venv/bin/python manage.py fetch_feeds --all`
- **Backups:** everything is in `db.sqlite3`. A safe hot copy, e.g. nightly from cron:
  `sqlite3 ~/reader-yusupov/app/db.sqlite3 ".backup '/home/django/backups/reader-$(date +\%F).sqlite3'"`
  — and keep an OPML export somewhere (Manage → Export OPML) for the subscriptions alone.
