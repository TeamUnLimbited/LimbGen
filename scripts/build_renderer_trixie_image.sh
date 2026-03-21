#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-eu-west-2}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-236209347845}"
IMAGE_VERSION="${IMAGE_VERSION:-$(date -u +%Y%m%d-%H%M%S)-renderer-trixie}"
ECR_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/arminator-renderer"
IMAGE_URI="${ECR_REPO}:${IMAGE_VERSION}"

echo "Building ${IMAGE_URI}"
docker build -f Dockerfile.renderer-trixie -t "${IMAGE_URI}" .

echo "Smoke testing ${IMAGE_URI}"
docker run --rm "${IMAGE_URI}" openscad --version
docker run --rm "${IMAGE_URI}" /usr/local/bin/openscad-manifold --help | grep -- --backend
docker run --rm "${IMAGE_URI}" python -c "import boto3; print('python ok')"

if [[ "${PUSH_IMAGE:-0}" == "1" ]]; then
  aws ecr get-login-password --region "${AWS_REGION}" \
    | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  docker push "${IMAGE_URI}"
  echo "Pushed ${IMAGE_URI}"
fi

echo "IMAGE_URI=${IMAGE_URI}"
