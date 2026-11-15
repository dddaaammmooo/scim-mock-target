resource "random_password" "scim_token" {
  count = var.scim_token == null ? 1 : 0

  length  = 40
  special = false
}

resource "aws_secretsmanager_secret" "scim_token" {
  name                    = "${var.name}/scim-token"
  description             = "Bearer token required by the scim-target SCIM endpoints (SCIM_TOKEN)"
  recovery_window_in_days = var.secret_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "scim_token" {
  secret_id     = aws_secretsmanager_secret.scim_token.id
  secret_string = var.scim_token != null ? var.scim_token : random_password.scim_token[0].result
}
