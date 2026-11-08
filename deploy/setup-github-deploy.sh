#!/usr/bin/env bash
# One-time setup for deploy-on-push, from macOS, Linux or Git Bash.
# (On Windows PowerShell, use deploy/setup-github-deploy.ps1 instead.)
#
#   deploy/setup-github-deploy.sh <vps-host> [ssh-port] [user]
#   DRY_RUN=1 deploy/setup-github-deploy.sh <vps-host> [ssh-port]   # store nothing
#
# The host may be an alias from ~/.ssh/config; it is resolved to the real address,
# which is what GitHub Actions needs. Needs ssh, ssh-keygen, ssh-keyscan and an
# authenticated `gh`.
#
# It creates a fresh ed25519 deploy key, stores the private half and the server's
# host key as GitHub Actions secrets, deletes the private key locally, and prints
# the line to add to the server's ~/.ssh/authorized_keys. That line pins the key
# to deploy/update.sh, so it can deploy and do nothing else.
set -euo pipefail

host=${1:?usage: $0 <vps-host> [ssh-port] [user]}
user=${3:-django}
repo=${REPO:-mvuijlst/reader}
app_dir=${APP_DIR:-/home/$user/reader-yusupov/app}

# Resolve SSH config aliases to the real address and port, as `ssh` would.
config=$(ssh -G ${2:+-p "$2"} "$host" 2>/dev/null || true)
address=$(awk '$1 == "hostname" { print $2; exit }' <<<"$config")
port=$(awk '$1 == "port" { print $2; exit }' <<<"$config")
address=${address:-$host}
port=${port:-${2:-22}}
[ "$address" != "$host" ] && echo "$host is $address in your SSH config."

echo "Fetching the server's host key from $address:$port ..."
known=$(ssh-keyscan -p "$port" -t ed25519 "$address" 2>/dev/null || true)
if [ -z "$known" ]; then
  echo "Couldn't get the host key of $address:$port. Check the host and port (try: ssh -p $port $address)." >&2
  exit 1
fi

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT  # the private key ends up only in GitHub
ssh-keygen -q -t ed25519 -N "" -C "reader-deploy@github-actions" -f "$workdir/key"

if [ -n "${DRY_RUN:-}" ]; then
  echo "Dry run: would set DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS, DEPLOY_HOST, DEPLOY_PORT, DEPLOY_USER in $repo."
else
  gh secret set DEPLOY_SSH_KEY --repo "$repo" < "$workdir/key"
  gh secret set DEPLOY_KNOWN_HOSTS --repo "$repo" --body "$known"
  gh secret set DEPLOY_HOST --repo "$repo" --body "$address"
  gh secret set DEPLOY_PORT --repo "$repo" --body "$port"
  gh secret set DEPLOY_USER --repo "$repo" --body "$user"
  echo "Stored the five DEPLOY_* secrets in $repo."
fi

echo
echo "On the server, as $user, append this line to ~/.ssh/authorized_keys:"
echo
echo "command=\"$app_dir/deploy/update.sh\",restrict $(cat "$workdir/key.pub")"
echo
echo "Then push a commit, or use Actions > 'Test and deploy' > Run workflow."
