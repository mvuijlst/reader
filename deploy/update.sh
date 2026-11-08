#!/usr/bin/env bash
# Deploy on the VPS: update the code, install deps, migrate, collect static,
# gracefully reload gunicorn, then check the site answers.
#
# Runs two ways, as the django user:
#   - by hand:            ~/reader-yusupov/app/deploy/update.sh [commit]
#   - from GitHub Actions: the deploy key in authorized_keys is pinned to this
#     script (command="…/update.sh",restrict), and the workflow passes the commit
#     it tested, which arrives here as $SSH_ORIGINAL_COMMAND.
#
# The working tree is reset to the target commit, so local edits to tracked files
# on the server are discarded. Untracked files (.env, db.sqlite3, venv/,
# staticfiles/) are never touched.
set -euo pipefail

APP_DIR=${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
PIDFILE=${PIDFILE:-/run/reader/gunicorn.pid}
PORT=${READER_PORT:-8027}
cd "$APP_DIR"

# One deploy at a time.
exec 9>"${TMPDIR:-/tmp}/reader-deploy.lock"
flock 9

target=${1:-${SSH_ORIGINAL_COMMAND:-}}
git fetch --quiet origin main
if [[ $target =~ ^[0-9a-f]{40}$ ]]; then
  if ! git cat-file -e "${target}^{commit}" 2>/dev/null; then
    echo "Unknown commit $target (not on origin/main yet?)" >&2
    exit 1
  fi
  if ! git merge-base --is-ancestor "$target" origin/main; then
    echo "Refusing to deploy $target: it is not on origin/main" >&2
    exit 1
  fi
  if [[ $target != "$(git rev-parse HEAD)" ]] && git merge-base --is-ancestor "$target" HEAD; then
    echo "Already running $(git rev-parse --short HEAD), which is newer than ${target:0:7}; nothing to do."
    exit 0
  fi
elif [[ -n $target ]]; then
  echo "Ignoring unexpected argument: $target" >&2
  target=origin/main
else
  target=origin/main
fi

before=$(git rev-parse --short HEAD)
git reset --hard --quiet "$target"
after=$(git rev-parse --short HEAD)
echo "Code: $before -> $after ($(git log -1 --format=%s))"

venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
venv/bin/python manage.py check --fail-level ERROR
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py collectstatic --noinput -v0

# HUP = graceful reload: new workers load the new code, old ones finish their
# requests. The django user owns gunicorn, so no sudo is needed.
if [[ -s $PIDFILE ]]; then
  kill -HUP "$(cat "$PIDFILE")"
  echo "Reloaded gunicorn (pid $(cat "$PIDFILE"))"
else
  echo "No gunicorn pidfile at $PIDFILE: is the reader service running?" >&2
  exit 1
fi

# Health check through the app itself, with a Host it accepts.
host=$(sed -n 's/^DJANGO_ALLOWED_HOSTS=\([^,#[:space:]]*\).*/\1/p' .env 2>/dev/null | head -n1)
host=${host:-localhost}
for _ in $(seq 1 30); do
  sleep 1
  if curl -fsS -o /dev/null -H "Host: $host" "http://127.0.0.1:$PORT/login/"; then
    echo "$(date -Is) deployed $after, site is up"
    exit 0
  fi
done
echo "Site did not answer on 127.0.0.1:$PORT after the reload. Check: journalctl -u reader -e" >&2
exit 1
