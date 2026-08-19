#!/usr/bin/env bash
set -Eeuo pipefail

dockerfile="${1:-Dockerfile}"

mapfile -t versions < <(
  awk '
    $1 == "FROM" && $2 ~ /^ghcr\.io\/schweizerischebundesbahnen\/weasyprint-service:/ {
      sub(/^ghcr\.io\/schweizerischebundesbahnen\/weasyprint-service:/, "", $2)
      weasyprint = $2
    }

    $1 == "FROM" && $2 ~ /^public\.ecr\.aws\/awsguru\/aws-lambda-adapter:/ {
      sub(/^public\.ecr\.aws\/awsguru\/aws-lambda-adapter:/, "", $2)
      adapter = $2
    }

    END {
      print weasyprint
      print adapter
    }
  ' "${dockerfile}"
)

weasyprint_version="${versions[0]:-}"
adapter_version="${versions[1]:-}"

if [[ -z "${weasyprint_version}" || -z "${adapter_version}" ]]; then
  printf 'Could not find both pinned base-image versions in %s.\n' "${dockerfile}" >&2
  exit 1
fi

if [[ "${weasyprint_version}" == "latest" || "${adapter_version}" == "latest" ]]; then
  printf 'Base images must use explicit versions, not latest.\n' >&2
  exit 1
fi

if [[ ! "${weasyprint_version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'Unexpected WeasyPrint service version: %s\n' "${weasyprint_version}" >&2
  exit 1
fi

if [[ ! "${adapter_version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'Unexpected Lambda Web Adapter version: %s\n' "${adapter_version}" >&2
  exit 1
fi

printf 'v%s-lwa%s-arm64\n' "${weasyprint_version}" "${adapter_version}"
