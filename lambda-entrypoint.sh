#!/bin/bash
set -Eeuo pipefail

build_timestamp="$(cat /opt/weasyprint/.build_timestamp)"
export WEASYPRINT_SERVICE_BUILD_TIMESTAMP="${build_timestamp}"

# Refresh the cache so fonts mounted by a consuming deployment are available.
fc-cache -f

exec uv run --no-sync python -m lambda_app
