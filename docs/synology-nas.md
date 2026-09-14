# Deploying on a Synology NAS (with DSM's own HTTPS)

This guide covers running the hosted server on a Synology NAS where DSM's
**Reverse Proxy** already terminates HTTPS on a custom port — for example
`https://your-nas.example.com:4043/` — so you don't need Caddy or any
certificate management inside the container. Placeholders below
(`your-nas.example.com`, port `4043`) stand in for your own DDNS hostname and
chosen port; substitute your real values throughout.

This is a variant of the ["Hosted mode" section of the main
README](../README.md#hosted-mode) — read that first for what hosted mode is
and its current verification status (unit-tested, not yet proven against a
live Claude/ChatGPT connector).

## Prerequisites

- A Synology NAS running Docker (Container Manager) with DSM's own HTTPS
  already configured and working — e.g. via DSM's Let's Encrypt integration
  and a DDNS hostname like `your-nas.example.com`.
- A free port on the NAS to dedicate to this service (e.g. `4043`).

## 1. Configure users and secrets

Copy `.env.example` to `.env` and fill in the hosted-mode section, same as
the standard hosted-mode setup:

```bash
cp .env.example .env
```

```
FAMILYWALL_MODE=hosted
FAMILYWALL_PUBLIC_URL=https://your-nas.example.com:4043
FAMILYWALL_AUTH_SECRET_KEY=<random-32+-character-string>
FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS=claude.ai,chatgpt.com

FAMILYWALL_USER_1_MCP_USERNAME=alice
FAMILYWALL_USER_1_MCP_PASSWORD=<mcp-password-alice-will-log-in-with>
FAMILYWALL_USER_1_FW_EMAIL=alice@familywall.account
FAMILYWALL_USER_1_FW_PASSWORD=<alice's real FamilyWall password>
```

`FAMILYWALL_PUBLIC_URL` must include the custom port — the OAuth provider
uses this value to build redirect URIs, and it has to match exactly what
clients will hit from the outside.

Generate the secret key with:

```bash
openssl rand -base64 32
```

Add `FAMILYWALL_USER_2_*`, `FAMILYWALL_USER_3_*`, etc. for additional family
members. See `.env.example` for the full list of variables.

## 2. Deploy the container

This setup skips Caddy entirely — DSM is already your reverse proxy and
certificate manager. Use [`docker-compose.nas.yml`](../docker-compose.nas.yml)
instead of `docker-compose.prod.yml`:

```bash
docker compose -f docker-compose.nas.yml up -d --build
```

This starts a single `familywall-mcp` container, publishing its internal
port 8000 to the same port on the NAS's host network. State (SQLite database,
OAuth tokens) persists in the `familywall_data` named volume.

If you'd rather map to a different host port than 8000 (e.g. to avoid a
collision with something else on the NAS), edit the `ports:` line in
`docker-compose.nas.yml`:

```yaml
ports:
  - "18000:8000"   # host:container — point DSM's reverse proxy at 18000
```

## 3. Point DSM's reverse proxy at the container

In **DSM Control Panel → Login Portal → Advanced → Reverse Proxy**, create a
rule:

- **Source**: Protocol `HTTPS`, Hostname `your-nas.example.com`, Port `4043`
  (use your existing DDNS hostname and certificate)
- **Destination**: Protocol `HTTP`, Hostname `localhost`, Port `8000` (or
  whatever host port you mapped in step 2)

Save, then verify:

```bash
curl https://your-nas.example.com:4043/health
```

## 4. Add it to Claude or ChatGPT

In Claude or ChatGPT's custom-connector / MCP settings, add
`https://your-nas.example.com:4043/mcp`. The client redirects to this
server's login page; sign in with one of the `FAMILYWALL_USER_<N>_MCP_USERNAME`
/ `MCP_PASSWORD` pairs from `.env` and authorize the connector.

## Notes

- Keep `.env` off the NAS's shared folders that sync to cloud backup or are
  otherwise exposed — it holds real FamilyWall credentials and the OAuth
  signing key.
- `docker-compose.prod.yml` (with Caddy) and `docker-compose.yml` (loopback
  dev) are still the right choices if you don't already have your own
  reverse proxy / TLS — see the main [README](../README.md#hosted-mode).
