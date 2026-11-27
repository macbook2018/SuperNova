# SuperNova staging deployment on JD Cloud

This is a temporary, low-concurrency staging deployment. It is isolated from the existing Docker projects and must not stop or modify Activation Manager, Website Watch, EmbedEaseAI, OpenClaw or other sites.

## Target topology

- Host: the original JD Cloud staging host.
- Existing host Nginx (Baota) remains the only public HTTP/HTTPS entry point.
- Console: 127.0.0.1:18083, served by a small Nginx container.
- API: 127.0.0.1:18082.
- PostgreSQL/pgvector and Redis are private to the Compose network.
- Dedicated Silo (S3-compatible) object storage: standalone container `supernova-staging-s3` on `supernova-staging_default`; endpoint `http://supernova-staging-s3:9000`. Keep its named volume `supernova_staging_s3_data` and bucket-only credentials; do not publish ports 9000/9001.
- No server-side source checkout or image build. GitHub Actions builds and publishes immutable SHA-tagged images to Docker Hub (`macbook201808/supernova`).

The core Compose memory limits total about 976 MiB, plus the separately managed 512 MiB Silo ceiling. They are ceilings, not guaranteed reservations. Monitor host memory, Swap, container OOM events and disk during testing. If memory pressure rises, stop staging workloads and review before changing existing services.

## Domains (confirmed)

- Console: bot.wet001.com
- API: api.wet001.com

Verify the A records for these two names point to the JD Cloud staging host. Check any AAAA or CNAME records for the same names before cutover.

In Baota:
1. Use the bot.wet001.com site with a valid HTTPS certificate and proxy / to http://127.0.0.1:18083.
2. Use the api.wet001.com site with a valid HTTPS certificate and proxy / to http://127.0.0.1:18082.
3. For the API site, set client_max_body_size 45m and proxy read/send timeouts to at least 300 seconds.
4. Preserve all existing vhost files. Do not replace the global Nginx configuration.

The staging Console image uses the API origin checked into its Dockerfile. Set the GitHub repository variable `STAGING_API_BASE_URL` to the same origin, `https://api.wet001.com`, to enable SSH deployment. A mismatched origin skips SSH and the verified SHA-tagged images remain available for deployment from the destination console. Set `SUPERNOVA_CORS_ORIGINS` in the destination .env to include `https://bot.wet001.com`. Verify the public API readiness response, a CORS preflight from the Console origin and the API origin in the deployed Console bundle.

## Object storage preparation

Use the dedicated Silo container (the deployed version is pinned to `pgsty/silo:RELEASE.2026-11-12T00-00-00Z`).
Start PostgreSQL and Redis first to create the project network, then attach the storage container. Create bucket `supernova-staging`, a dedicated application account, and attach `deploy/minio/supernova-staging-policy.json`. Verify object put/get/delete as that account, not the storage root user. Set the server-only .env endpoint to `http://supernova-staging-s3:9000`.

Keep root credentials under /opt/supernova-storage with mode 600. The application must not use root credentials. The standalone storage container is not a Compose-managed service and must remain connected to the project network. Do not tear down the project network during normal updates. Back up the S3 volume as well as PostgreSQL before destructive maintenance.

## One-time server setup

Create a dedicated deploy account. Docker-group membership is effectively root-equivalent; protect its SSH key accordingly.

As root on the JD Cloud staging host:

~~~bash
useradd --create-home --shell /bin/bash supernova-deploy 2>/dev/null || true
usermod -aG docker supernova-deploy
install -d -o supernova-deploy -g supernova-deploy -m 750 /opt/supernova-staging
install -d -o supernova-deploy -g supernova-deploy -m 700 /home/supernova-deploy/.ssh
~~~

Add the public half of a dedicated Ed25519 deployment key to:
 /home/supernova-deploy/.ssh/authorized_keys
and set owner supernova-deploy:supernova-deploy, mode 600. The private key goes only into the GitHub Actions secret STAGING_SSH_PRIVATE_KEY.

The deployment account must be able to run Docker commands and write to /opt/supernova-staging.

Download the deployment files from the stable branch or use the first workflow upload. Once the dedicated S3 credentials and DNS/domain decisions are ready, run:

~~~bash
cd /opt/supernova-staging
chmod 700 bootstrap-staging.sh deploy-staging.sh rollback-staging.sh
./bootstrap-staging.sh
~~~

The bootstrap script generates PostgreSQL, Redis, JWT and Fernet secrets locally and writes a mode-600 .env. It prompts for the S3 bucket-only access key/secret and the platform-admin email. Do not share the resulting .env.

### Configure platform model credentials

The platform model pool uses server-side credentials. The Console stores only a credential reference; it never stores or returns the upstream API Key.

1. Confirm `SUPERNOVA_PLATFORM_ADMIN_EMAILS` contains the exact email used to sign in to SuperNova.
2. Edit `/opt/supernova-staging/.env` and set the credential map. The keys on the left are credential references that you will enter in the platform model admin page:

   ```dotenv
   SUPERNOVA_PLATFORM_CREDENTIALS_JSON='{"deepseek-primary":"REPLACE_WITH_REAL_API_KEY"}'
   ```

   Keep the real value only on the server. Do not paste it into chat, GitHub, issue comments or shell commands that may be saved in history.
3. Ensure the file remains private: `chmod 600 /opt/supernova-staging/.env`.
4. Recreate only the API and application workers so they receive the updated environment:

   ```bash
   cd /opt/supernova-staging
   docker compose --project-name supernova-staging --env-file .env -f compose.staging.yaml up -d --no-deps --force-recreate api knowledge-worker chat-import-worker
   ```

5. In the platform admin page, create a model using the same `credential_ref` (for example, `deepseek-primary`), publish a current price version, and only then enable it.

The staging Compose file passes this map to the API and workers. If the map is empty or a reference does not exist, the platform model is intentionally omitted from tenant-visible model lists.

## GitHub configuration

Repository: Settings -> Secrets and variables -> Actions.

Variables:
- STAGING_DEPLOY_ENABLED = true (set this only after one-time server setup and GitHub Actions runner checks).
- STAGING_DEPLOY_PATH = /opt/supernova-staging.
- STAGING_API_BASE_URL = https://api.wet001.com (change if another API domain is selected).

For the JD Cloud SSH connection, set the following five secrets on the `jd-staging` GitHub environment. The Hong Kong candidate workflows have separate deployment access.

- STAGING_HOST = the JD Cloud staging host public IP.
- STAGING_USER = supernova-deploy.
- STAGING_SSH_PORT = SSH port (usually 22).
- STAGING_SSH_PRIVATE_KEY = private half of the dedicated deployment key.
- STAGING_SSH_KNOWN_HOSTS = trusted host-key line(s) for the target SSH server, obtained via an already authenticated server console; for port 22 use IP followed by the contents of /etc/ssh/ssh_host_ed25519_key.pub (key type and public key only).

The image publisher reads the repository secrets below.

- DOCKERHUB_USERNAME = macbook201808.
- DOCKERHUB_TOKEN = Docker Hub publishing token.

The workflow publishes SHA-tagged images with the existing Docker Hub credentials. Keep STAGING_DEPLOY_ENABLED=false until the new host, its deployment key, pinned host key, object storage and .env are verified. Do not reuse an old SHA tag built with a different Console API origin. After the new host is ready, manually dispatch CI on the current Phase 6 branch. Do not merge or deploy the Phase 7 draft as part of this infrastructure cutover.

## CI and automatic deployment

On feat/phase-6-knowledge-base-rag with STAGING_DEPLOY_ENABLED=true, image publication follows the Console build, API tests and Compose smoke checks. The SSH job then checks the configured origin and confirms that the SSH address belongs to the API domain before connecting with the pinned SSH host key.

Before enabling deployment, verify GitHub Actions can actually allocate hosted runners and execute steps. A recent run for commit f67759b4eff273d891ef4f8487638c4d6a90f1b2 ended in failure with zero steps and no runner name; that is a runner/platform issue to resolve, not evidence of a code test failure.

## Deploy and rollback behavior

- Deployment pulls immutable SHA-tagged images; the server never builds source code.
- PostgreSQL and Redis data use dedicated named volumes.
- Before every Alembic upgrade, the script writes a PostgreSQL custom-format backup under /opt/supernova-staging/backups and keeps the latest three.
- API, Console, Knowledge Worker and Chat Import Worker health checks must pass before the new SHA is recorded as the last successful deployment.
- On failure, inspect logs and the backup. The script does not automatically restore database data.
- Application image rollback is available with ./rollback-staging.sh (or pass an explicit SHA). It does not reverse Alembic migrations or restore database contents. Use it only when the previous application version is compatible with the current schema.
- Never run docker compose down -v, docker system prune -a, or global volume/image cleanup on the JD Cloud staging host.

## Resource monitoring

During the first tests, check:

~~~bash
free -h
swapon --show
df -h /
docker stats --no-stream
docker compose --project-name supernova-staging --env-file .env -f compose.staging.yaml ps
~~~

If host available memory falls below 500 MiB, Swap grows rapidly, or any staging container is OOM-killed, stop and review the resource plan before continuing.
