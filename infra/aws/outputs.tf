output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "region" {
  value = var.region
}

output "default_vpc_id" {
  value = data.aws_vpc.default.id
}

output "default_subnet_ids" {
  value = data.aws_subnets.default.ids
}

output "artifacts_bucket_name" {
  value = aws_s3_bucket.artifacts.bucket
}

output "site_bucket_name" {
  value = aws_s3_bucket.site.bucket
}

output "jobs_table_name" {
  value = aws_dynamodb_table.jobs.name
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  value = aws_ecs_cluster.main.arn
}

output "renderer_ecr_repository_url" {
  value = aws_ecr_repository.renderer.repository_url
}

output "renderer_task_role_arn" {
  value = aws_iam_role.renderer_task.arn
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "lambda_role_arn" {
  value = aws_iam_role.lambda.arn
}

output "renderer_security_group_id" {
  value = aws_security_group.renderer.id
}

output "renderer_task_definition_arn" {
  value = aws_ecs_task_definition.renderer.arn
}

output "lambda_function_name" {
  value = aws_lambda_function.api.function_name
}

output "lambda_function_url" {
  value = aws_lambda_function_url.api.function_url
}

output "cloudfront_domain_name" {
  value = aws_cloudfront_distribution.site.domain_name
}

output "public_hostname" {
  value = local.enable_https ? var.domain_name : null
}

output "certificate_arn" {
  value = local.create_certificate ? aws_acm_certificate.site[0].arn : null
}

output "certificate_dns_validation_name" {
  value = local.create_certificate ? one(aws_acm_certificate.site[0].domain_validation_options).resource_record_name : null
}

output "certificate_dns_validation_type" {
  value = local.create_certificate ? one(aws_acm_certificate.site[0].domain_validation_options).resource_record_type : null
}

output "certificate_dns_validation_value" {
  value = local.create_certificate ? one(aws_acm_certificate.site[0].domain_validation_options).resource_record_value : null
}
