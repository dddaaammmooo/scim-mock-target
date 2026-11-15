# scim-target on AWS (Terraform)

Self-contained root module that runs scim-target on ECS Fargate behind an Application Load Balancer, with SQLite on an encrypted EFS file system. The full walkthrough is in [docs/deploy-aws.md](../../docs/deploy-aws.md).

> **Security warning.** The scim-target admin API and UI have **no authentication by design** (only the SCIM endpoints require the bearer token, and `GET /api/config` even returns that token). `allowed_cidrs` has no default and must be set. Never use `0.0.0.0/0` unless you accept that anyone on the internet can read, change and reset the data.

## Quick start

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # set allowed_cidrs at minimum
terraform init
terraform plan
terraform apply
```

## What it creates

| File | Resources |
|---|---|
| `network.tf` | VPC, 2 public subnets (2 AZs), optional 2 private subnets + 1 NAT gateway, security groups (ALB, tasks, EFS). Or reuse an existing VPC (`create_vpc = false`). |
| `alb.tf` | ALB, target group (health check `/api/health`), HTTP listener, optional HTTPS listener (HTTP then redirects). |
| `efs.tf` | Encrypted EFS, mount targets, access point (uid/gid 10001, matching the image's `app` user), TLS-only file system policy, optional automatic backups. |
| `secrets.tf` | Secrets Manager secret for `SCIM_TOKEN` (generated unless supplied). |
| `iam.tf` | Execution role (logs, read the secret) and task role (EFS client access through the access point only). |
| `ecs.tf` | Log group, cluster, task definition (EFS mounted at `/data`), service with circuit breaker and rollback. Always exactly 1 task, stop-before-start deployments. |

## Inputs you will usually set

| Variable | Required | Notes |
|---|---|---|
| `allowed_cidrs` | **yes** | No default. Keep it as narrow as possible. Include MockHRIS's egress IP if it must call this target. |
| `aws_region` | no | Default `us-east-1`. |
| `image` | no | Default `ghcr.io/dddaaammmooo/scim-target:latest`; pin a tag for repeatable deploys. |
| `certificate_arn`, `domain_name` | no | Enable HTTPS and a custom host name. |
| `use_nat_gateway` | no | Default `false` (tasks in public subnets, reachable only from the ALB). |
| `scim_token` | no | Generated if null. |

See `variables.tf` for the complete list. Outputs include `url`, `scim_base_url`, `get_scim_token_command`, `efs_file_system_id`, `cluster_name`, `service_name` and `log_group`.

## Remote state (optional)

No backend is committed. To keep state in S3 add a block like this to `versions.tf` (bucket and table must already exist):

```hcl
# terraform {
#   backend "s3" {
#     bucket         = "my-terraform-state-bucket"
#     key            = "scim-target/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "my-terraform-locks"
#     encrypt        = true
#   }
# }
```

Terraform state contains the generated SCIM token. Treat the state as a secret.

## Validate locally

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```
