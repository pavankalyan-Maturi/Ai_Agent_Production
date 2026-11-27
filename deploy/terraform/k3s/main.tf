# Cheaper alternative to ../eks (P8.1): one k3s node on EC2 in the default VPC. No EKS control
# plane, no ALB (k3s ships Traefik), no IRSA; pgBackRest uses an instance-profile role instead.
# Same chart and drills; KEDA and Chaos Mesh work as on EKS.
terraform {
  required_version = "~> 1.13.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.20.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "pap", burst = "true", expires_on = var.expires_on }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "instance_type" {
  type    = string
  default = "t3.xlarge"
}

variable "k3s_version" {
  type    = string
  default = "v1.34.1+k3s1"
}

variable "allowed_cidr" {
  description = "Your IP/32 for 443 and 6443. No SSH: use SSM Session Manager."
  type        = string
  validation {
    condition     = can(cidrnetmask(var.allowed_cidr)) && var.allowed_cidr != "0.0.0.0/0"
    error_message = "A CIDR other than 0.0.0.0/0."
  }
}

variable "expires_on" {
  type = string
}

data "aws_vpc" "default" {
  default = true
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_security_group" "k3s" {
  name   = "pap-k3s"
  vpc_id = data.aws_vpc.default.id
  dynamic "ingress" {
    for_each = [443, 6443]
    content {
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = [var.allowed_cidr]
    }
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "k3s" {
  name = "pap-k3s"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.k3s.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "k3s" {
  name = "pap-k3s"
  role = aws_iam_role.k3s.name
}

resource "aws_instance" "k3s" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.k3s.name
  vpc_security_group_ids = [aws_security_group.k3s.id]

  # Spot, and the instance terminates itself if the OS shuts down (cost guard).
  instance_initiated_shutdown_behavior = "terminate"
  instance_market_options {
    market_type = "spot"
  }

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = 40
    volume_type = "gp3"
  }

  # Dead man's switch: the node powers off (and so terminates) after 10 hours.
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    shutdown -h +600
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION='${var.k3s_version}' sh -s - \
      --write-kubeconfig-mode 600 --tls-san "$(curl -s -H "X-aws-ec2-metadata-token: $(curl -s -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token)" http://169.254.169.254/latest/meta-data/public-ipv4)"
  EOT

  tags = { Name = "pap-k3s" }
}

output "instance_id" {
  value = aws_instance.k3s.id
}

output "public_ip" {
  value = aws_instance.k3s.public_ip
}
