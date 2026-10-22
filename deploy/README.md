# Deployment configuration

This directory holds environment templates. **No real credentials belong here** —
`.env` is gitignored; `.env.example` contains placeholders only.

| File | Purpose |
| --- | --- |
| `.env.example` | Every supported setting, documented, with placeholder values |

## Local

`scripts/run_stack.sh` reads the environment directly and generates a development
secret key into `.forge/secret_key` on first run (gitignored, mode 600). No `.env`
file is required to run locally.

## Containers

```bash
cp deploy/.env.example deploy/.env
# fill in POSTGRES_PASSWORD and FORGE_SECRET_KEY
docker compose -f infra/compose/docker-compose.yml --env-file deploy/.env up -d
```

The container stack has **not been executed** — the environment Forge was built in
has no container runtime. See `docs/deployment.md`.

## AWS

Secrets are supplied by AWS Secrets Manager and injected into the ECS task
definition; they are never baked into an image or stored in Terraform state as
plaintext. See `infra/terraform/README.md`.
