# Forge on AWS (Terraform)

> **This configuration has never been applied.** The environment Forge was
> developed in has no Terraform binary, no AWS account and no credentials. CI runs
> `terraform fmt -check` and `terraform validate`, which verify syntax and provider
> schemas — **not** that applying it produces a working deployment. Treat it as a
> reviewed design, not a tested artefact.
>
> `terraform fmt` could not be run locally either (no Terraform binary). The HCL
> was checked against fmt's alignment rules with a script, but the first CI run is
> the first real verification; a small `terraform fmt` commit may be needed.

## Architecture

```
Internet ──▶ ALB (public subnets, HTTPS)
                │
                ▼
          ECS Fargate (private subnets)
          ├── api      ×N   behind the ALB target group
          ├── worker   ×N   no inbound listener
          └── router   ×1   weighted traffic splitting
                │
                ├──▶ RDS PostgreSQL  (private, encrypted, Multi-AZ in production)
                ├──▶ ElastiCache Redis (private, TLS in transit)
                └──▶ ECR (immutable tags, scan on push)
```

Only the load balancer sits in a public subnet. Nothing that runs application
code or stores data has an inbound route from the internet.

## Design decisions

| Decision | Why |
| --- | --- |
| Images pinned by digest, validated by a regex | A mutable tag makes a deployment irreproducible. The variable validation rejects one outright. |
| Secrets in Secrets Manager, injected as `secrets` | Values never appear in the task definition, in an image, or in `docker inspect`. |
| One NAT gateway per AZ | A shared gateway is a cross-AZ dependency and a single point of failure for all egress. |
| Security groups reference each other | Rules stay correct as addresses change; no CIDR drift. |
| `/ready` as the ALB health check | `/ready` checks the database, so a task that cannot serve is drained instead of left in rotation. |
| Deployment circuit breaker with rollback | ECS protects the *control plane*. This is separate from Forge's own canary rollbacks, which protect the applications Forge deploys. |
| `deletion_protection` in production | Prevents an accidental `terraform destroy` from taking the database with it. |
| `ignore_changes` on task definitions | The release pipeline updates them; Terraform should not revert a deploy. |

## Usage

```bash
cd infra/terraform

# State must be remote, encrypted and locked.
terraform init \
  -backend-config="bucket=your-tfstate-bucket" \
  -backend-config="key=forge/dev.tfstate" \
  -backend-config="region=eu-west-1" \
  -backend-config="encrypt=true" \
  -backend-config="dynamodb_table=your-lock-table"

cp environments/dev.tfvars.example environments/dev.tfvars
# edit: pin real image digests, set allowed_ingress_cidrs and certificate_arn

terraform plan  -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars   # review the plan first
```

## After the first apply

1. Set the webhook secret, which starts empty so unsigned deliveries are refused:
   ```bash
   aws secretsmanager put-secret-value --secret-id <arn> \
     --secret-string "$(aws secretsmanager get-secret-value --secret-id <arn> \
       --query SecretString --output text \
       | jq --arg s "$(openssl rand -hex 32)" '.FORGE_GITHUB_WEBHOOK_SECRET = $s')"
   ```
2. Run migrations once, as a one-off ECS task using the same image and secrets:
   `python -m alembic -c packages/forge-core/alembic.ini upgrade head`
3. Create the first administrator with `python -m forge_api.cli create-user`.
   There is no bootstrap administrator outside development — `Settings` refuses it.

## Known gaps

These are design limitations, not oversights. They are listed because the
configuration should not be mistaken for production-complete:

- **Never applied.** Resource interactions, IAM sufficiency and startup ordering
  are all unverified.
- **No WAF** in front of the ALB.
- **Broad egress** from tasks (`0.0.0.0/0`). Forge's SSRF guard is a userspace
  control; a network-level egress policy is the proper defence and is not
  configured here. See `docs/security.md`.
- **No automated secret rotation.** Rotation is manual.
- **No DNS or certificate automation.** `certificate_arn` must be supplied.
- **The `router` service has no ECS service definition yet.** Traffic splitting in
  AWS would need either an ALB weighted target group or the router running as its
  own service; the local implementation uses the router process.
