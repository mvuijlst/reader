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

`deploy/update.sh` calls `sudo systemctl restart reader`. To run it without a password
prompt, allow just that command (optional):

```bash
echo 'django ALL=NOPASSWD: /usr/bin/systemctl restart reader' | sudo tee /etc/sudoers.d/reader
```

## Ongoing

- **Update:** `~/reader-yusupov/app/deploy/update.sh` (pull, deps, migrate, collectstatic, restart).
- **Logs:** `journalctl -u reader -e` (web), `journalctl -u reader-fetch -e` (polling).
- **Fetch now:** `cd ~/reader-yusupov/app && venv/bin/python manage.py fetch_feeds --all`
- **Backups:** everything is in `db.sqlite3`. A safe hot copy, e.g. nightly from cron:
  `sqlite3 ~/reader-yusupov/app/db.sqlite3 ".backup '/home/django/backups/reader-$(date +\%F).sqlite3'"`
  — and keep an OPML export somewhere (Manage → Export OPML) for the subscriptions alone.
