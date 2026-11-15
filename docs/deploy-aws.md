# Deploying scim-target to AWS

This guide deploys scim-target to AWS with the Terraform module in [`deploy/terraform`](../deploy/terraform). It is a demonstration-grade deployment: small, cheap and easy to tear down.

> **Read this first: the admin API and UI are unauthenticated.**
> scim-target is a test double. Only the SCIM endpoints (`/t/<tenant>/scim/v2`) require the bearer token. The UI, the admin API under `/api` (including reset, fault injection and the request log) and `GET /api/config` (which returns the SCIM token) need no credentials. Anyone who can reach the load balancer can read and change everything.
> For that reason the Terraform module has **no default for `allowed_cidrs`** and the load balancer accepts traffic only from the CIDRs you list. Keep the list as small as possible and never use `0.0.0.0/0`.

## Architecture

```
        allowed_cidrs only (your IP, MockHRIS egress IP)
   You / MockHRIS --------------------+
                                      v
                          +-----------------------+
                          |  Application Load     |  :80  (or :443 + redirect
                          |  Balancer (public)    |        when certificate_arn set)
                          +-----------+-----------+
                                      | :8100, from ALB security group only
        VPC (2 AZs)                   v
   +----------------------------------------------------------+
   |  ECS Fargate service "scim-target" (exactly 1 task)       |
   |  image: ghcr.io/dddaaammmooo/scim-mock-target                  |
   |  env SCIM_TOKEN  <--- Secrets Manager                     |
   |  /data  <--- EFS access point (uid/gid 10001, TLS + IAM)  |
   +-----------------+---------------------------+------------+
                     | NFS 2049 (tasks SG only)  | CloudWatch Logs
                     v                           v /ecs/scim-target
              +--------------+
              | EFS          |  encrypted at rest, SQLite file
              | (2 AZ mount  |  survives task restarts and redeploys
              |  targets)    |
              +--------------+
```

Exactly one task runs because SQLite has a single writer. Deployments stop the old task before starting the new one (brief downtime on each release), and the ECS circuit breaker rolls back a failing release.

Task placement is a cost choice (variable `use_nat_gateway`):

| Mode | Tasks run in | Egress | Extra cost | Trade-off |
|---|---|---|---|---|
| Default (`false`) | public subnets, with a public IP | internet gateway | none | Tasks have a public IP, but their security group accepts traffic only from the ALB. Simple and cheapest. |
| `true` | private subnets | one NAT gateway | about 32 USD/month plus data | No public IP on tasks. A single NAT is a single-AZ dependency, fine for a demo. |

## Prerequisites

- An AWS account and credentials in your shell (`aws sts get-caller-identity` works) with permission to create VPC, ECS, ELB, EFS, IAM, Secrets Manager and CloudWatch resources.
- [Terraform](https://developer.hashicorp.com/terraform/install) 1.6 or newer.
- AWS CLI v2 (to read the SCIM token).
- The image must exist in GitHub Container Registry as a public package (`ghcr.io/dddaaammmooo/scim-mock-target`).
- Optional: an ACM certificate (same region) and a DNS name you control for HTTPS.

## Deploy

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`. The only required value is `allowed_cidrs` (find your IP with `curl https://checkip.amazonaws.com`):

```hcl
allowed_cidrs = ["203.0.113.10/32"]
```

Then:

```bash
terraform init
terraform plan -out tfplan
terraform apply tfplan
```

Useful outputs:

```bash
terraform output url
terraform output scim_base_url
terraform output get_scim_token_command
terraform output efs_file_system_id
```

### Optional settings

| Goal | Variables |
|---|---|
| HTTPS | `certificate_arn` (HTTP then redirects to HTTPS). With a custom host name also set `domain_name` and create a CNAME or alias record to `terraform output alb_dns_name`. |
| Pin the image | `image = "ghcr.io/dddaaammmooo/scim-mock-target:<tag>"` |
| Private tasks | `use_nat_gateway = true` |
| Existing network | `create_vpc = false`, `vpc_id`, `alb_subnet_ids`, `task_subnet_ids`, `assign_public_ip` |
| Supply your own token | `scim_token = "..."` (otherwise generated) |
| No EFS backups | `enable_efs_backup = false` |

## First use

1. Get the SCIM token:

   ```bash
   aws secretsmanager get-secret-value --region us-east-1 \
     --secret-id "$(terraform output -raw scim_token_secret_arn)" \
     --query SecretString --output text
   ```

2. Open `terraform output -raw url` for the UI. Base URLs for SCIM clients:

   | Style | URL |
   |---|---|
   | Generic | `<url>/t/<tenant>/scim/v2` (any tenant id, for example `hris`) |
   | GitHub Enterprise style | `<url>/scim/v2/enterprises/<tenant>` |

3. Quick test:

   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "$(terraform output -raw url)/t/hris/scim/v2/Users"
   ```

### Pointing MockHRIS at the deployed scim-target

With [MockHRIS](https://github.com/dddaaammmooo/scim-mock-hris) running locally or in AWS, create an integration with:

- SCIM base URL: `terraform output -raw scim_base_url`, which is `http(s)://<scim-target-alb>/t/hris/scim/v2`
- Bearer token: the SCIM token from step 1.

Make sure the machine or task running MockHRIS is inside `allowed_cidrs`. If MockHRIS runs on your laptop, your own IP is enough. If MockHRIS runs in AWS, add its egress IP: with the default placement that is the task public IP, which changes on every deployment, so for a stable address deploy MockHRIS with `use_nat_gateway = true` and allow the NAT Elastic IP.

For an AWS-hosted MockHRIS you can store the token without using the UI by setting `integration_tokens = { HRIS_TARGET = "<token>" }` in the MockHRIS Terraform stack.

See [End-to-end testing with scim-mock-hris](end-to-end-with-mockhris.md) for the provisioning scenarios.

## Cost notes

Approximate us-east-1 on-demand prices; check the AWS pricing pages.

| Item | Approx. monthly |
|---|---|
| Fargate, 0.25 vCPU / 0.5 GiB, 1 task 24x7 | about 9 USD |
| Application Load Balancer | about 17 USD plus tiny LCU usage |
| Public IPv4 addresses (ALB uses 2, the task 1 in default mode) | about 0.005 USD per address-hour each |
| NAT gateway (only with `use_nat_gateway`) | about 32 USD plus 0.045 USD/GB |
| EFS (bursting, standard storage) | about 0.30 USD per GB-month; the database is tiny, so cents |
| AWS Backup for EFS | per GB-month of backup storage; disable with `enable_efs_backup = false` |
| CloudWatch Logs | per GB ingested; retention defaults to 14 days |
| Secrets Manager | about 0.40 USD per secret |

## Security notes

- Unauthenticated admin API and UI: see the warning at the top. Restrict `allowed_cidrs`, and destroy the stack when you are not using it.
- Without `certificate_arn` traffic, including the SCIM bearer token, is plain HTTP. Use HTTPS beyond a quick test from a trusted network.
- The SCIM token is generated with `random_password`, stored in Secrets Manager and injected by ECS. It is also in Terraform state, so protect the state (an example encrypted S3 backend is in the module README).
- The EFS file system is encrypted at rest, only reachable over TLS from the task security group, and the task role can mount only through the dedicated access point.
- Only put fictitious data in scim-target.
- SQLite on NFS is fine for a single writer at test volumes; do not scale beyond one task.

## Operations

```bash
aws logs tail "$(terraform output -raw log_group)" --follow
terraform apply -var 'image=ghcr.io/dddaaammmooo/scim-mock-target:<tag>'
```

## Teardown

```bash
terraform destroy
```

This deletes the EFS file system and all stored data. EFS has no deletion protection toggle in this module, so export anything you need first (for example through the admin API) or keep the AWS Backup recovery points. If you set `alb_deletion_protection = true`, set it back to `false` and apply before destroying. Secrets Manager keeps the deleted secret for `secret_recovery_window_days` (default 7); set it to `0` for throwaway stacks so the name can be reused immediately.
