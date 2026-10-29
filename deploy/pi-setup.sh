#!/usr/bin/env bash
# Install or update polish-legal-mcp as a hardened systemd service (no Docker) and publish it
# with Tailscale Funnel. Run on the host as root:  sudo bash pi-setup.sh
# Prerequisites: Tailscale installed and logged in (`sudo tailscale up`), MagicDNS + HTTPS
# certificates enabled in the tailnet, and a GitHub OAuth App whose callback URL is
# https://<host>.<tailnet>.ts.net/auth/callback. Re-running updates the code and restarts.
set -euo pipefail

APP_USER=plmcp
APP_DIR=/opt/polish-legal-mcp
ENV_FILE=/etc/polish-legal-mcp.env
UNIT=/etc/systemd/system/polish-legal-mcp.service
REPO=https://github.com/Emenowicz/polish-legal-mcp.git
UV_VERSION=0.13.6
PORT=8000

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo bash $0" >&2; exit 1; }
command -v tailscale >/dev/null || { echo "Install Tailscale first (https://tailscale.com/download/linux)." >&2; exit 1; }

case "$(uname -m)" in
  aarch64) UV_TARGET=aarch64-unknown-linux-gnu ;;
  x86_64) UV_TARGET=x86_64-unknown-linux-gnu ;;
  *) echo "Unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

echo "==> Service user"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_DIR"

echo "==> uv $UV_VERSION (checksum-verified)"
if ! /usr/local/bin/uv --version 2>/dev/null | grep -q " $UV_VERSION"; then
  tmp=$(mktemp -d)
  base="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz"
  curl -fsSL -o "$tmp/uv.tar.gz" "$base"
  expected=$(curl -fsSL "$base.sha256" | cut -d' ' -f1)
  echo "$expected  $tmp/uv.tar.gz" | sha256sum -c -
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  install -m 755 "$tmp/uv-$UV_TARGET/uv" /usr/local/bin/uv
  rm -rf "$tmp"
fi

echo "==> Code"
as_app() { runuser -u "$APP_USER" -- env HOME="$APP_DIR" UV_CACHE_DIR="$APP_DIR/.cache/uv" UV_PYTHON_INSTALL_DIR="$APP_DIR/python" "$@"; }
if [ -d "$APP_DIR/app/.git" ]; then
  as_app git -C "$APP_DIR/app" pull --ff-only
else
  as_app git clone --depth 1 "$REPO" "$APP_DIR/app"
fi
as_app /usr/local/bin/uv sync --locked --no-dev --no-editable --directory "$APP_DIR/app"

echo "==> Configuration"
if [ ! -f "$ENV_FILE" ]; then
  dns=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
  default_url="https://$dns"
  read -rp "Public base URL [$default_url]: " base_url; base_url=${base_url:-$default_url}
  read -rp "GitHub OAuth App client ID: " gh_id
  read -rsp "GitHub OAuth App client secret (hidden): " gh_secret; echo
  read -rp "Allowed GitHub logins, comma-separated [Emenowicz]: " logins; logins=${logins:-Emenowicz}
  jwt=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  umask 077
  # No inline comments: systemd EnvironmentFile would keep them as part of the value.
  cat > "$ENV_FILE" <<CONF
PLMCP_ENV=production
PLMCP_AUTH_MODE=github
PLMCP_BASE_URL=$base_url
PLMCP_GITHUB_CLIENT_ID=$gh_id
PLMCP_GITHUB_CLIENT_SECRET=$gh_secret
PLMCP_JWT_SIGNING_KEY=$jwt
PLMCP_ALLOWED_GITHUB_LOGINS=$logins
CONF
  chown root:root "$ENV_FILE"; chmod 600 "$ENV_FILE"
  echo "Wrote $ENV_FILE (callback URL to register in GitHub: $base_url/auth/callback)"
else
  echo "Keeping existing $ENV_FILE"
fi

echo "==> systemd"
install -m 644 "$APP_DIR/app/deploy/polish-legal-mcp.service" "$UNIT"
systemctl daemon-reload
systemctl enable polish-legal-mcp >/dev/null
systemctl restart polish-legal-mcp
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/health" && echo || { journalctl -u polish-legal-mcp -n 30 --no-pager; exit 1; }

echo "==> Tailscale Funnel"
tailscale funnel --bg "$PORT"
tailscale funnel status
echo "Done. MCP endpoint: $(grep '^PLMCP_BASE_URL=' "$ENV_FILE" | cut -d= -f2-)/mcp"
