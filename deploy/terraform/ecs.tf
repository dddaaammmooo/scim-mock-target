locals {
  container_name = "scim-target"
  container_port = 8100
  health_path    = "/api/health"

  # Public URL: custom domain when given, otherwise the ALB DNS name.
  public_host = coalesce(var.domain_name, aws_lb.this.dns_name)
  public_url  = "${local.https_enabled ? "https" : "http"}://${local.public_host}"

  environment = merge(
    {
      DATA_DIR = "/data"
    },
    var.extra_environment,
  )

  secret_env  = { SCIM_TOKEN = aws_secretsmanager_secret.scim_token.arn }
  secret_arns = values(local.secret_env)
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = var.enable_container_insights ? "enabled" : "disabled"
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  volume {
    name = "data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.data.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.data.id
        iam             = "ENABLED"
      }
    }
  }

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = local.container_name
      image     = var.image
      essential = true

      portMappings = [{ containerPort = local.container_port, protocol = "tcp" }]

      environment = [for k, v in local.environment : { name = k, value = v }]
      secrets     = [for k, arn in local.secret_env : { name = k, valueFrom = arn }]

      mountPoints = [{ sourceVolume = "data", containerPath = "/data", readOnly = false }]

      # Mirrors the Dockerfile HEALTHCHECK (the image has no curl).
      healthCheck = {
        command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${local.container_port}${local.health_path}', timeout=2)"]
        interval    = 10
        timeout     = 3
        retries     = 5
        startPeriod = 20
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.app.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "app"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "app" {
  name             = var.name
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.app.arn
  desired_count    = 1 # SQLite: exactly one task
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  enable_execute_command            = var.enable_execute_command
  health_check_grace_period_seconds = 60
  # SQLite allows a single writer: stop the old task before starting the new one.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = local.task_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = local.container_name
    container_port   = local.container_port
  }

  # Listener must exist before the service registers targets.
  depends_on = [aws_lb_listener.http, aws_lb_listener.https, aws_efs_mount_target.data, aws_efs_file_system_policy.data]
}
