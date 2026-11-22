#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [[ -e .env ]]; then
  echo "Refusing to overwrite existing .env. Back it up and review it manually first."
  exit 1
fi

read -r -p "Console origin [https://bot.ai-supernova.com]: " CONSOLE_ORIGIN
CONSOLE_ORIGIN="${CONSOLE_ORIGIN:-https://bot.ai-supernova.com}"
read -r -p "Platform admin email: " ADMIN_EMAIL
read -r -p "MinIO bucket [supernova-staging]: " S3_BUCKET
S3_BUCKET="${S3_BUCKET:-supernova-staging}"
read -r -p "MinIO access key: " S3_ACCESS_KEY
read -r -s -p "MinIO secret key: " S3_SECRET_KEY
printf '\n'

if [[ "$CONSOLE_ORIGIN" != https://* ]]; then
  echo "Console origin must start with https://"
  exit 1
fi
if [[ -z "$ADMIN_EMAIL" || "$ADMIN_EMAIL" == *" "* ]]; then
  echo "A valid platform admin email is required."
  exit 1
fi
if [[ ! "$S3_BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]]; then
  echo "Invalid S3 bucket name."
  exit 1
fi
if [[ ! "$S3_ACCESS_KEY" =~ ^[A-Za-z0-9_-]{8,128}$ ]]; then
  echo "S3 access key must use 8-128 letters, numbers, underscores or hyphens."
  exit 1
fi
if [[ ${#S3_SECRET_KEY} -lt 8 || ${#S3_SECRET_KEY} -gt 128 || "$S3_SECRET_KEY" =~ [[:cntrl:]] ]]; then
  echo "S3 secret key must be 8-128 printable characters without line breaks."
  exit 1
fi

# Store the S3 secret as a single-quoted Compose .env value so punctuation
# such as #, $, !, and quotes is preserved literally.
S3_SECRET_KEY_DOTENV="$(S3_SECRET_KEY="$S3_SECRET_KEY" python3 -c 'import os; q=chr(39); b=chr(92); s=os.environ["S3_SECRET_KEY"]; print(s.replace(b,b+b).replace(q,b+q), end="")')"

POSTGRES_PASSWORD="$(openssl rand -hex 24)"
REDIS_PASSWORD="$(openssl rand -hex 24)"
SUPERNOVA_JWT_SECRET="$(openssl rand -hex 32)"
SUPERNOVA_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"

umask 077
cat > .env <<EOF
POSTGRES_DB=supernova
POSTGRES_USER=supernova
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
REDIS_PASSWORD=${REDIS_PASSWORD}
SUPERNOVA_IMAGE_TAG=staging-latest
SUPERNOVA_LOG_LEVEL=INFO
SUPERNOVA_JWT_SECRET=${SUPERNOVA_JWT_SECRET}
SUPERNOVA_ENCRYPTION_KEY=${SUPERNOVA_ENCRYPTION_KEY}
SUPERNOVA_ACCESS_TOKEN_TTL_MINUTES=15
SUPERNOVA_REFRESH_TOKEN_TTL_DAYS=30
SUPERNOVA_PLATFORM_ADMIN_EMAILS=${ADMIN_EMAIL}
SUPERNOVA_PLATFORM_CREDENTIALS_JSON='{}'
SUPERNOVA_CORS_ORIGINS=${CONSOLE_ORIGIN}
SUPERNOVA_S3_ENDPOINT_URL=http://host.docker.internal:9000
SUPERNOVA_S3_ACCESS_KEY=${S3_ACCESS_KEY}
SUPERNOVA_S3_SECRET_KEY='${S3_SECRET_KEY_DOTENV}'
SUPERNOVA_S3_BUCKET=${S3_BUCKET}
SUPERNOVA_S3_REGION=us-east-1
SUPERNOVA_KNOWLEDGE_MAX_UPLOAD_BYTES=10485760
SUPERNOVA_KNOWLEDGE_MAX_URL_BYTES=10485760
SUPERNOVA_KNOWLEDGE_WORKER_POLL_SECONDS=2
SUPERNOVA_KNOWLEDGE_WORKER_LEASE_SECONDS=900
SUPERNOVA_EMBEDDING_TIMEOUT_SECONDS=60
SUPERNOVA_CHAT_IMPORT_MAX_FILE_BYTES=20971520
SUPERNOVA_CHAT_IMPORT_MAX_BATCH_BYTES=41943040
SUPERNOVA_CHAT_IMPORT_MAX_FILES=10
SUPERNOVA_CHAT_IMPORT_WORKER_POLL_SECONDS=2
SUPERNOVA_CHAT_IMPORT_WORKER_LEASE_SECONDS=180
SUPERNOVA_CHAT_IMPORT_BATCH_SIZE=500
SUPERNOVA_CHAT_IMPORT_TEMP_TTL_HOURS=24
EOF

chmod 600 .env
echo ".env created with mode 600. Keep it on the server and never commit or send it."
