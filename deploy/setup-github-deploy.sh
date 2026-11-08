#!/usr/bin/env bash
# One-time setup for deploy-on-push. Run on your own machine (needs ssh-keygen,
# ssh-keyscan and an authenticated `gh`):
#
#   deploy/setup-github-deploy.sh <vps-host> [ssh-port] [user]
#
# It creates a fresh ed25519 deploy key, stores the private half and the server's
# host key as GitHub Actions secrets, deletes the private key locally, and prints
# the line to add to the server's ~/.ssh/authorized_keys. That line pins the key
# to deploy/update.sh, so it can deploy and do nothing else.
set -euo pipefail

host=${1:?usage: $0 <vps-host> [ssh-port] [user]}
port=${2:-22}
user=${3:-django}
repo=${REPO:-mvuijlst/reader}
app_dir=${APP_DIR:-/home/$user/reader-yusupov/app}

known=$(ssh-keyscan -p "$port" -t ed25519 "$host" 2>/dev/null || true)
if [ -z "$known" ]; then
  echo "ssh-keyscan got no ed25519 host key from $host:$port. Check the host and port." >&2
  exit 1
fi

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
ssh-keygen -q -t ed25519 -N "" -C "reader-deploy@github-actions" -f "$workdir/key"

gh secret set DEPLOY_SSH_KEY --repo "$repo" < "$workdir/key"
gh secret set DEPLOY_KNOWN_HOSTS --repo "$repo" --body "$known"
gh secret set DEPLOY_HOST --repo "$repo" --body "$host"
gh secret set DEPLOY_PORT --repo "$repo" --body "$port"
gh secret set DEPLOY_USER --repo "$repo" --body "$user"

echo
echo "Secrets stored in $repo. Now, on the server as $user, append this line to ~/.ssh/authorized_keys:"
echo
echo "command=\"$app_dir/deploy/update.sh\",restrict $(cat "$workdir/key.pub")"
echo
echo "Then push a commit (or Actions → Test and deploy → Run workflow) to deploy."
