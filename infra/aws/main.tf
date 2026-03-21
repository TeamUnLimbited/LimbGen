provider "aws" {
  region = var.region
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

resource "random_string" "bucket_suffix" {
  length  = 8
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  name_prefix          = var.project_name
  jobs_table_name      = "${var.project_name}-jobs"
  artifacts_bucket     = "${var.project_name}-artifacts-${data.aws_caller_identity.current.account_id}-${random_string.bucket_suffix.result}"
  site_bucket          = "${var.project_name}-site-${data.aws_caller_identity.current.account_id}-${random_string.bucket_suffix.result}"
  renderer_log_group   = "/ecs/${var.project_name}-renderer"
  lambda_log_group     = "/aws/lambda/${var.project_name}-api"
  lambda_function_name = "${var.project_name}-api"
  create_certificate   = trimspace(var.domain_name) != ""
  enable_https         = local.create_certificate && var.enable_https
  cloudfront_aliases   = local.enable_https ? [var.domain_name] : []
  api_origin_domain    = trimsuffix(trimprefix(aws_lambda_function_url.api.function_url, "https://"), "/")
  site_files           = fileset("${path.module}/../../site", "**")
  progress_files       = fileset("${path.module}/../../progressimages", "**")
  content_types = {
    css  = "text/css; charset=utf-8"
    html = "text/html; charset=utf-8"
    ico  = "image/x-icon"
    js   = "application/javascript; charset=utf-8"
    json = "application/json; charset=utf-8"
    jpg  = "image/jpeg"
    jpeg = "image/jpeg"
    png  = "image/png"
    svg  = "image/svg+xml"
    webp = "image/webp"
  }
}

resource "aws_ecr_repository" "renderer" {
  name                 = "${var.project_name}-renderer"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = local.artifacts_bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-artifacts"
    status = "Enabled"

    filter {}

    expiration {
      days = var.artifact_retention_days
    }
  }
}

resource "aws_s3_bucket" "site" {
  bucket        = local.site_bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_dynamodb_table" "jobs" {
  name         = local.jobs_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_cloudwatch_log_group" "renderer" {
  name              = local.renderer_log_group
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = local.lambda_log_group
  retention_in_days = 14
}

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"
}

resource "aws_security_group" "renderer" {
  name        = "${var.project_name}-renderer-sg"
  description = "Renderer task security group"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_egress_rule" "renderer_outbound" {
  security_group_id = aws_security_group.renderer.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_acm_certificate" "site" {
  count             = local.create_certificate ? 1 : 0
  provider          = aws.us_east_1
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "site" {
  count                   = local.enable_https ? 1 : 0
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.site[0].arn
  validation_record_fqdns = [one(aws_acm_certificate.site[0].domain_validation_options).resource_record_name]
}

resource "aws_iam_role" "execution" {
  name = "${var.project_name}-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "renderer_task" {
  name = "${var.project_name}-renderer-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "renderer_task" {
  name = "${var.project_name}-renderer-task-policy"
  role = aws_iam_role.renderer_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Effect   = "Allow"
        Resource = aws_dynamodb_table.jobs.arn
      },
      {
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Effect   = "Allow"
        Resource = "${aws_s3_bucket.artifacts.arn}/*"
      },
      {
        Action   = ["s3:ListBucket"]
        Effect   = "Allow"
        Resource = aws_s3_bucket.artifacts.arn
      },
      {
        Action   = ["ecs:RunTask"]
        Effect   = "Allow"
        Resource = "*"
      },
      {
        Action = ["iam:PassRole"]
        Effect = "Allow"
        Resource = [
          aws_iam_role.execution.arn,
          aws_iam_role.renderer_task.arn
        ]
      },
      {
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

resource "aws_ecs_task_definition" "renderer" {
  family                   = "${var.project_name}-renderer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.renderer_task_cpu)
  memory                   = tostring(var.renderer_task_memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.renderer_task.arn

  container_definitions = jsonencode([
    {
      name      = "renderer"
      image     = var.renderer_image
      essential = true
      command   = ["python", "renderer_job.py"]
      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "ARMINATOR_JOBS_TABLE", value = aws_dynamodb_table.jobs.name },
        { name = "ARMINATOR_ARTIFACTS_BUCKET", value = aws_s3_bucket.artifacts.bucket },
        { name = "ARMINATOR_ECS_CLUSTER_ARN", value = aws_ecs_cluster.main.arn },
        { name = "ARMINATOR_RENDERER_SUBNETS", value = join(",", data.aws_subnets.default.ids) },
        { name = "ARMINATOR_RENDERER_SECURITY_GROUP", value = aws_security_group.renderer.id },
        { name = "ARMINATOR_PUBLIC_BASE_URL", value = var.public_base_url },
        { name = "ARMINATOR_EMAIL_FROM", value = var.email_from_address },
        { name = "ARMINATOR_EMAIL_REPLY_TO", value = var.email_reply_to },
        { name = "ARMINATOR_REPORT_EMAIL_TO", value = var.report_email_to },
        { name = "ARMINATOR_DEPLOYMENT_VERSION", value = var.deployment_version }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.renderer.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}

resource "aws_iam_role" "lambda" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Effect   = "Allow"
        Resource = aws_dynamodb_table.jobs.arn
      },
      {
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Effect   = "Allow"
        Resource = "${aws_s3_bucket.artifacts.arn}/*"
      },
      {
        Action   = ["s3:ListBucket"]
        Effect   = "Allow"
        Resource = aws_s3_bucket.artifacts.arn
      },
      {
        Action = [
          "ecs:RunTask",
          "ecs:StopTask",
          "ecs:DescribeTasks"
        ]
        Effect   = "Allow"
        Resource = "*"
      },
      {
        Action = ["iam:PassRole"]
        Effect = "Allow"
        Resource = [
          aws_iam_role.execution.arn,
          aws_iam_role.renderer_task.arn
        ]
      },
      {
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

data "archive_file" "lambda" {
  type        = "zip"
  output_path = "${path.module}/build/lambda-api.zip"

  source {
    content  = file("${path.module}/../../lambda_api.py")
    filename = "lambda_api.py"
  }

  source {
    content  = file("${path.module}/../../arminator_aws_backend.py")
    filename = "arminator_aws_backend.py"
  }

  source {
    content  = file("${path.module}/../../arminator_common.py")
    filename = "arminator_common.py"
  }

  source {
    content  = file("${path.module}/../../UnLimbited Arm V3.00.scad")
    filename = "UnLimbited Arm V3.00.scad"
  }

  source {
    content  = file("${path.module}/../../UnLimbited_Arm_V2.2.scad")
    filename = "UnLimbited_Arm_V2.2.scad"
  }

  source {
    content  = file("${path.module}/../../UnLimbitedPhoenix.scad")
    filename = "UnLimbitedPhoenix.scad"
  }
}

resource "aws_lambda_function" "api" {
  function_name    = local.lambda_function_name
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "lambda_api.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory_size

  environment {
    variables = {
      ARMINATOR_JOBS_TABLE                   = aws_dynamodb_table.jobs.name
      ARMINATOR_ARTIFACTS_BUCKET             = aws_s3_bucket.artifacts.bucket
      ARMINATOR_ECS_CLUSTER_ARN              = aws_ecs_cluster.main.arn
      ARMINATOR_RENDERER_TASK_DEFINITION_ARN = aws_ecs_task_definition.renderer.arn
      ARMINATOR_RENDERER_SUBNETS             = join(",", data.aws_subnets.default.ids)
      ARMINATOR_RENDERER_SECURITY_GROUP      = aws_security_group.renderer.id
      ARMINATOR_PUBLIC_BASE_URL              = var.public_base_url
      ARMINATOR_EMAIL_FROM                   = var.email_from_address
      ARMINATOR_EMAIL_REPLY_TO               = var.email_reply_to
      ARMINATOR_REPORT_EMAIL_TO              = var.report_email_to
      ARMINATOR_DEPLOYMENT_VERSION           = var.deployment_version
    }
  }
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "api_function_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.project_name}-site-oac"
  description                       = "Origin access control for the site bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_origin_request_policy" "api" {
  name = "${var.project_name}-api-origin"

  cookies_config {
    cookie_behavior = "whitelist"

    cookies {
      items = ["arminator_client_id"]
    }
  }

  headers_config {
    header_behavior = "whitelist"

    headers {
      items = ["CloudFront-Viewer-Country"]
    }
  }

  query_strings_config {
    query_string_behavior = "all"
  }
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  aliases             = local.cloudfront_aliases

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "site-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  origin {
    domain_name = local.api_origin_domain
    origin_id   = "api-origin"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "site-origin"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_disabled.id
  }

  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "api-origin"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = !local.enable_https
    acm_certificate_arn            = local.enable_https ? aws_acm_certificate.site[0].arn : null
    minimum_protocol_version       = local.enable_https ? "TLSv1.2_2021" : null
    ssl_support_method             = local.enable_https ? "sni-only" : null
  }

  depends_on = [aws_acm_certificate_validation.site]
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontRead"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.site.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.site.arn
          }
        }
      }
    ]
  })
}

resource "aws_s3_object" "site_files" {
  for_each = { for file in local.site_files : file => file }

  bucket        = aws_s3_bucket.site.id
  key           = each.value
  source        = "${path.module}/../../site/${each.value}"
  etag          = filemd5("${path.module}/../../site/${each.value}")
  content_type  = lookup(local.content_types, regex("[^.]+$", each.value), "application/octet-stream")
  cache_control = each.value == "index.html" ? "no-cache, no-store, must-revalidate" : "public, max-age=300"
}

resource "aws_s3_object" "progress_files" {
  for_each = { for file in local.progress_files : file => file }

  bucket        = aws_s3_bucket.site.id
  key           = "progressimages/${each.value}"
  source        = "${path.module}/../../progressimages/${each.value}"
  etag          = filemd5("${path.module}/../../progressimages/${each.value}")
  content_type  = lookup(local.content_types, regex("[^.]+$", each.value), "application/octet-stream")
  cache_control = "public, max-age=300"
}
