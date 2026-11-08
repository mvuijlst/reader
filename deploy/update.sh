#!/usr/bin/env bash
# Deploy the latest main on the VPS. Run as the django user from anywhere:
#   ~/reader-yusupov/app/deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
venv/bin/pip install -q -r requirements.txt
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py collectstatic --noinput -v0
sudo systemctl restart reader
echo "$(date -Is) deployed $(git rev-parse --short HEAD)"
