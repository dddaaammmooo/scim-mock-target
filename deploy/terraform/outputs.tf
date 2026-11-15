output "url" {
  description = "Public URL of scim-target (UI at /, admin API under /api)."
  value       = local.public_url
}

output "scim_base_url" {
  description = "SCIM base URL for the tenant \"hris\" (use this as the SCIM base URL in MockHRIS; any tenant id works)."
  value       = "${local.public_url}/t/hris/scim/v2"
}

output "alb_dns_name" {
  description = "DNS name of the load balancer (create a CNAME/alias to it for domain_name)."
  value       = aws_lb.this.dns_name
}

output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.app.name
}

output "log_group" {
  description = "CloudWatch log group."
  value       = aws_cloudwatch_log_group.app.name
}

output "efs_file_system_id" {
  description = "EFS file system ID holding the SQLite database."
  value       = aws_efs_file_system.data.id
}

output "scim_token_secret_arn" {
  description = "Secrets Manager ARN holding the SCIM bearer token."
  value       = aws_secretsmanager_secret.scim_token.arn
}

output "get_scim_token_command" {
  description = "AWS CLI command that prints the SCIM bearer token."
  value       = "aws secretsmanager get-secret-value --region ${var.aws_region} --secret-id ${aws_secretsmanager_secret.scim_token.arn} --query SecretString --output text"
}
