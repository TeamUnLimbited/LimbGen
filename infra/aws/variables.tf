variable "region" {
  description = "AWS region for all regional resources."
  type        = string
  default     = "eu-west-2"
}

variable "project_name" {
  description = "Prefix for named resources."
  type        = string
  default     = "arminator"
}

variable "domain_name" {
  description = "Public DNS name to serve through CloudFront."
  type        = string
  default     = ""
}

variable "enable_https" {
  description = "Whether to wait for ACM validation and attach the certificate to CloudFront."
  type        = bool
  default     = false
}

variable "renderer_image" {
  description = "Full image URI for the on-demand renderer task."
  type        = string
  default     = ""
}

variable "deployment_version" {
  description = "Opaque deployment version used to force new task and function revisions."
  type        = string
  default     = ""
}

variable "public_base_url" {
  description = "Public HTTPS base URL used in emails and magic links."
  type        = string
  default     = ""
}

variable "email_from_address" {
  description = "SES verified From address for transactional emails."
  type        = string
  default     = "limbgen@teamunlimbited.org"
}

variable "email_reply_to" {
  description = "Reply-To address for transactional emails."
  type        = string
  default     = "hello@teamunlimbited.org"
}

variable "report_email_to" {
  description = "Internal mailbox that receives structured generation reports."
  type        = string
  default     = "drew@teamunlimbited.org"
}

variable "renderer_task_cpu" {
  description = "CPU units for the on-demand renderer task."
  type        = number
  default     = 2048
}

variable "renderer_task_memory" {
  description = "Memory in MiB for the on-demand renderer task."
  type        = number
  default     = 4096
}

variable "lambda_memory_size" {
  description = "Memory in MiB for the API Lambda."
  type        = number
  default     = 512
}

variable "lambda_timeout" {
  description = "Timeout in seconds for the API Lambda."
  type        = number
  default     = 30
}

variable "artifact_retention_days" {
  description = "How long rendered artifacts should stay in S3 before lifecycle expiration."
  type        = number
  default     = 3
}
