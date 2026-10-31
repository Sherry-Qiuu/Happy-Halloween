#!/usr/bin/env bash
#
# One-shot deployment for a fresh Ubuntu/Debian server.
#
#   sudo mkdir -p /opt/xingjian
#   # upload this project folder to /opt/xingjian, then:
#   sudo bash /opt/xingjian/deploy/bootstrap.sh calhub.asia
#
# It installs Docker and Caddy, writes .env (asks for the DeepSeek key),
# builds and starts the container, gets a TLS certificate, then checks
# https://DOMAIN/api/health. Safe to re-run: it only fills in what is
# missing and leaves an existing .env alone except for three keys.
set -euo pipefail

DOMAIN="${1:-${DOMAIN:-}}"
EMAIL="${2:-${LETSENCRYPT_EMAIL:-}}"
APP_DIR="${APP_DIR:-/opt/xingjian}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: sudo bash $0 $DOMAIN"
[ -n "$DOMAIN" ] || die "usage: sudo bash deploy/bootstrap.sh <domain> [email]"
[ -f "$APP_DIR/docker-compose.yml" ] || die "$APP_DIR/docker-compose.yml not found - upload the project there first"
[ -f /etc/debian_version ] || die "this script supports Ubuntu/Debian only"

export DEBIAN_FRONTEND=noninteractive
log "installing base packages"
apt-get update -y >/dev/null
apt-get install -y --no-install-recommends \
  curl ca-certificates gnupg openssl >/dev/null

# --- Docker -----------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "installing docker"
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  apt-get install -y docker-compose-plugin >/dev/null
  COMPOSE="docker compose"
fi

cd "$APP_DIR"

# --- .env -------------------------------------------------------------
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  log "creating $ENV_FILE"
  KEY="${DEEPSEEK_API_KEY:-}"
  if [ -z "$KEY" ] && [ -t 0 ]; then
    read -r -s -p "DeepSeek API key (sk-..., leave empty for offline mode): " KEY || true
    printf '\n'
  fi
  umask 077
  cat > "$ENV_FILE" <<EOF
DEEPSEEK_API_KEY=$KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
HOST=0.0.0.0
PORT=8000
XINGJIAN_DATA_DIR=/data
COOKIE_SECURE=1
XINGJIAN_TRUST_PROXY=1
XINGJIAN_ALLOW_REGISTRATION=1
AGENT_MOCK=0
AGENT_CORS_ORIGINS=https://$DOMAIN,http://localhost,null
EOF
  chmod 600 "$ENV_FILE"
fi

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

log "pointing .env at https://$DOMAIN"
set_env HOST 0.0.0.0
set_env COOKIE_SECURE 1
set_env XINGJIAN_TRUST_PROXY 1
set_env AGENT_CORS_ORIGINS "https://$DOMAIN,http://localhost,null"

if ! grep -q '^DEEPSEEK_API_KEY=.\+' "$ENV_FILE"; then
  warn "DEEPSEEK_API_KEY is empty: the AI panel will run in offline demo mode"
fi

# --- Container --------------------------------------------------------
mkdir -p "$APP_DIR/data"
chown -R 10001:10001 "$APP_DIR/data"

log "building and starting the app container"
$COMPOSE up -d --build

ready=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  $COMPOSE logs --tail 60
  die "the app did not become healthy on 127.0.0.1:8000"
fi
log "app is up on 127.0.0.1:8000"

# --- Caddy ------------------------------------------------------------
if ! command -v caddy >/dev/null 2>&1; then
  log "installing caddy"
  apt-get install -y --no-install-recommends \
    debian-keyring debian-archive-keyring apt-transport-https >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y >/dev/null
  apt-get install -y caddy >/dev/null
fi

log "writing /etc/caddy/Caddyfile for $DOMAIN"
if [ -n "$EMAIL" ]; then
  GLOBAL_BLOCK="{
	email $EMAIL
}"
else
  GLOBAL_BLOCK="# no email configured; Caddy uses the ACME default"
fi

cat > /etc/caddy/Caddyfile <<EOF
$GLOBAL_BLOCK

$DOMAIN {
	encode zstd gzip

	reverse_proxy 127.0.0.1:8000 {
		# Never buffer the AI panel's server-sent events.
		flush_interval -1
	}
}

# Add the www record in DNS first, then uncomment:
# www.$DOMAIN {
# 	redir https://$DOMAIN{uri} permanent
# }
EOF

caddy validate --config /etc/caddy/Caddyfile >/dev/null
systemctl enable caddy >/dev/null 2>&1 || true
systemctl reload caddy 2>/dev/null || systemctl restart caddy

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null || true
  ufw allow 443/tcp >/dev/null || true
fi

# --- Smoke test -------------------------------------------------------
log "waiting for the certificate on https://$DOMAIN"
live=0
for _ in $(seq 1 20); do
  if curl -fsS "https://$DOMAIN/api/health" >/dev/null 2>&1; then
    live=1
    break
  fi
  sleep 3
done

printf '\n'
if [ "$live" -eq 1 ]; then
  printf '\033[1;32mOK\033[0m https://%s is serving\n\n' "$DOMAIN"
  curl -s "https://$DOMAIN/api/health"; printf '\n'
else
  warn "https://$DOMAIN is not answering yet"
  warn "most likely the DNS record is missing or the domain is still on registry hold"
  warn "see deploy/DNS.md, then check: journalctl -u caddy -n 50 --no-pager"
fi

cat <<EOF

Next:
  1. In DNSPod add an A record:  @  ->  $(curl -fsS https://api.ipify.org 2>/dev/null || echo '<this server IP>')
  2. Open ports 80 and 443 in the cloud console security group.
  3. Verify from anywhere:  python scripts/verify_deploy.py $DOMAIN
EOF
