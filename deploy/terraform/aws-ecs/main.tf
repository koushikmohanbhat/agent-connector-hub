# ECS Fargate service behind an internal ALB, for the connector-hub image. Agents reach it from inside the VPC (or a
# peered one); set internal = false only if agents run elsewhere. Everything environment-specific is a variable
# (see terraform.tfvars.example); secrets are Secrets Manager ARNs injected by ECS, never plain values.
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = ">= 5.40" } }
}

provider "aws" { region = var.region }

variable "region" { type = string }
variable "name" {
  type    = string
  default = "connector-hub"
}
variable "image" { type = string } # ECR image URI with tag
variable "vpc_id" { type = string }
variable "alb_subnet_ids" { type = list(string) }     # private subnets for an internal ALB
variable "private_subnet_ids" { type = list(string) } # tasks
variable "certificate_arn" { type = string }          # ACM certificate for the HTTPS listener
variable "cpu" {
  type    = number
  default = 512
}
variable "memory" {
  type    = number
  default = 1024
}
variable "desired_count" {
  type    = number
  default = 2
}
variable "internal" {
  type    = bool
  default = true
}
variable "allowed_cidrs" { # where agents call from (the agent VPC / subnets)
  type = list(string)
}
variable "environment" { # plain settings: HUB_AUTH, HUB_ENV=prod, SALESFORCE_BACKEND, SALESFORCE_INSTANCE_URL, ...
  type    = map(string)
  default = {}
}
variable "secrets" { # NAME => Secrets Manager ARN (HUB_API_KEYS, HUB_STORE, HUB_ASSERTION_KEY, *_CLIENT_SECRET, ...)
  type    = map(string)
  default = {}
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = 30
}

resource "aws_ecs_cluster" "this" { name = var.name }

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "secrets" {
  count  = length(var.secrets) > 0 ? 1 : 0
  role   = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = values(var.secrets) }] })
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name             = var.name
    image            = var.image
    essential        = true
    portMappings     = [{ containerPort = 8080, protocol = "tcp" }]
    environment      = [for k, v in merge({ PORT = "8080", AWS_REGION = var.region, HUB_ENV = "prod" }, var.environment) : { name = k, value = v }]
    secrets          = [for k, v in var.secrets : { name = k, valueFrom = v }]
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.this.name, awslogs-region = var.region, awslogs-stream-prefix = "hub" } }
  }])
}

resource "aws_security_group" "alb" {
  name   = "${var.name}-alb"
  vpc_id = var.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "task" {
  name   = "${var.name}-task"
  vpc_id = var.vpc_id
  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "this" {
  name               = var.name
  load_balancer_type = "application"
  internal           = var.internal
  subnets            = var.alb_subnet_ids
  security_groups    = [aws_security_group.alb.id]
  idle_timeout       = 120 # streaming replies
}

resource "aws_lb_target_group" "this" {
  name        = var.name
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  health_check { path = "/readyz" }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }
}

resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"
  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [aws_security_group.task.id]
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = var.name
    container_port   = 8080
  }
  depends_on = [aws_lb_listener.https]
}

output "url" { value = "https://${aws_lb.this.dns_name}" }
