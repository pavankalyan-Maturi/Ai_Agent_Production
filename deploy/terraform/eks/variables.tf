variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "pap-burst"
}

variable "kubernetes_version" {
  description = "Must be in EKS standard support on the burst day (extended support is 6x the control-plane price). Check: aws eks describe-cluster-versions."
  type        = string
  default     = "1.34"
}

variable "node_instance_type" {
  type    = string
  default = "t3.large"
}

variable "node_capacity_type" {
  description = "SPOT is ~60-70% cheaper; ON_DEMAND if spot capacity is short on the day."
  type        = string
  default     = "SPOT"
  validation {
    condition     = contains(["SPOT", "ON_DEMAND"], var.node_capacity_type)
    error_message = "SPOT or ON_DEMAND."
  }
}

variable "node_count" {
  type    = number
  default = 2
  validation {
    condition     = var.node_count >= 1 && var.node_count <= 4
    error_message = "Cost guard: 1-4 nodes."
  }
}

variable "allowed_cidr" {
  description = "Your IP/32. Limits the EKS public endpoint and the ALB."
  type        = string
  validation {
    condition     = can(cidrnetmask(var.allowed_cidr)) && var.allowed_cidr != "0.0.0.0/0"
    error_message = "A CIDR other than 0.0.0.0/0."
  }
}

variable "budget_usd" {
  description = "Monthly AWS Budget; alerts at 50% and 100% of actual spend."
  type        = number
  default     = 10
}

variable "alert_email" {
  description = "Budget alert recipient. Passed at apply time, never committed."
  type        = string
}

variable "expires_on" {
  description = "Tag only: the date this stack must be gone (YYYY-MM-DD)."
  type        = string
}
