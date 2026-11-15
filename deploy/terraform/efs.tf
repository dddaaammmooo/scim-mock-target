# Persistent storage for the SQLite database (DATA_DIR=/data).
resource "aws_efs_file_system" "data" {
  creation_token   = "${var.name}-data"
  encrypted        = true
  throughput_mode  = var.efs_throughput_mode
  performance_mode = "generalPurpose"

  tags = { Name = "${var.name}-data" }
}

resource "aws_efs_backup_policy" "data" {
  file_system_id = aws_efs_file_system.data.id

  backup_policy {
    status = var.enable_efs_backup ? "ENABLED" : "DISABLED"
  }
}

# One mount target per task subnet (keys are list indexes, so they are known at plan time).
resource "aws_efs_mount_target" "data" {
  for_each = { for i, id in local.task_subnet_ids : tostring(i) => id }

  file_system_id  = aws_efs_file_system.data.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}

# Access point: forces uid/gid 10001 (the non-root "app" user in the image) and a private directory.
resource "aws_efs_access_point" "data" {
  file_system_id = aws_efs_file_system.data.id

  posix_user {
    uid = 10001
    gid = 10001
  }

  root_directory {
    path = "/scim-target"

    creation_info {
      owner_uid   = 10001
      owner_gid   = 10001
      permissions = "0750"
    }
  }

  tags = { Name = "${var.name}-data" }
}

# Refuse unencrypted (non-TLS) NFS connections. Access itself is granted by the task role policy.
data "aws_iam_policy_document" "efs" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["*"]
    resources = [aws_efs_file_system.data.arn]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid    = "AllowTaskRoleViaAccessPoint"
    effect = "Allow"
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
    ]
    resources = [aws_efs_file_system.data.arn]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.task.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.data.arn]
    }
  }
}

resource "aws_efs_file_system_policy" "data" {
  file_system_id = aws_efs_file_system.data.id
  policy         = data.aws_iam_policy_document.efs.json
}
