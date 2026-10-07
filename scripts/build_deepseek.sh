#!/usr/bin/env bash
set -euo pipefail

# Export tracked source at the inspected commit; never copy local credentials or node_modules.
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
harness_root="${1:-$HOME/deepseek-harness}"
image_tag="${2:-nl2repobench-deepseek:639ed01539}"
revision="$(git -C "$harness_root" rev-parse --verify '639ed01539^{commit}')"
build_context="$(mktemp -d)"
trap 'rm -rf -- "$build_context"' EXIT
git -C "$harness_root" archive "$revision" | tar -x -C "$build_context"
cp "$repo_root/docker/deepseek.Dockerfile" "$build_context/Dockerfile"
docker build --tag "$image_tag" --build-arg "HARNESS_REVISION=$revision" "$build_context"
