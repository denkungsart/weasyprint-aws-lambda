#!/usr/bin/env bash
set -Eeuo pipefail

image="${1:-weasyprint-aws-lambda:test}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fixture="${script_dir}/fixtures/accessible.html"
external_image_fixture="${script_dir}/fixtures/external-image.html"
container="weasyprint-smoke-$$"
tmp_dir="$(mktemp -d)"

cleanup() {
  status=$?

  if (( status != 0 )) && docker container inspect "${container}" >/dev/null 2>&1; then
    printf '\nContainer logs:\n' >&2
    docker logs "${container}" >&2 || true
  fi

  docker rm --force "${container}" >/dev/null 2>&1 || true
  rm -rf -- "${tmp_dir}"
  exit "${status}"
}
trap cleanup EXIT INT TERM

for command in docker curl pdfimages pdfinfo pdftotext qpdf; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    printf 'Required command is missing: %s\n' "${command}" >&2
    exit 1
  fi
done

if [[ ! -f "${fixture}" ]]; then
  printf 'Fixture not found: %s\n' "${fixture}" >&2
  exit 1
fi

# Resolve the effective identity so named and numeric USER declarations both work.
image_uid="$(docker run --rm --entrypoint /usr/bin/id "${image}" -u)"
appuser_uid="$(docker run --rm --entrypoint /usr/bin/id "${image}" -u appuser)"
if [[ "${image_uid}" == "0" || "${image_uid}" != "${appuser_uid}" ]]; then
  printf 'Expected non-root appuser UID %s, got: %s\n' "${appuser_uid}" "${image_uid}" >&2
  exit 1
fi

if [[ -n "${EXPECTED_ARCHITECTURE:-}" ]]; then
  image_architecture="$(docker image inspect --format '{{.Architecture}}' "${image}")"
  if [[ "${image_architecture}" != "${EXPECTED_ARCHITECTURE}" ]]; then
    printf 'Expected architecture %s, got: %s\n' \
      "${EXPECTED_ARCHITECTURE}" "${image_architecture}" >&2
    exit 1
  fi
fi

docker run --rm --entrypoint /bin/sh "${image}" \
  -c 'test -x /opt/extensions/lambda-adapter'

api_key="weasyprint-smoke-test-key-not-for-deployment"

docker run --detach \
  --env "API_KEY=${api_key}" \
  --name "${container}" \
  --read-only \
  --tmpfs /tmp:rw,exec,mode=1777,size=1024m \
  --memory 2g \
  --publish 127.0.0.1::9080 \
  "${image}" >/dev/null

port_mapping="$(docker port "${container}" 9080/tcp | sed -n '1p')"
port="${port_mapping##*:}"
if [[ -z "${port}" || "${port}" == "${port_mapping}" ]]; then
  printf 'Could not determine the published service port from: %s\n' "${port_mapping}" >&2
  exit 1
fi

base_url="http://127.0.0.1:${port}"
healthy=false
for ((attempt = 1; attempt <= 90; attempt++)); do
  if curl --fail --silent --show-error "${base_url}/health" >/dev/null 2>&1; then
    healthy=true
    break
  fi

  if [[ "$(docker inspect --format '{{.State.Running}}' "${container}")" != "true" ]]; then
    printf 'Container exited before becoming healthy.\n' >&2
    exit 1
  fi

  sleep 1
done

if [[ "${healthy}" != "true" ]]; then
  printf 'Service did not become healthy within 90 seconds.\n' >&2
  exit 1
fi

if docker exec "${container}" pgrep -f 'chrome.*--headless' >/dev/null 2>&1; then
  printf 'Chromium must not run in the Lambda-oriented image.\n' >&2
  exit 1
fi

curl --fail --silent --show-error \
  --output "${tmp_dir}/health.json" \
  "${base_url}/health?detailed=true"

grep -Fq '"status":"healthy"' "${tmp_dir}/health.json"
grep -Fq '"chromium_running":false' "${tmp_dir}/health.json"
grep -Fq '"health_monitoring_enabled":false' "${tmp_dir}/health.json"

# Both conversion routes must reject missing or incorrect credentials.
for route in html html-with-attachments; do
  for supplied_key in "" "incorrect-key"; do
    status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
      --request POST --header "X-API-Key: ${supplied_key}" \
      --header 'Content-Type: text/html; charset=utf-8' \
      --data-binary "@${fixture}" "${base_url}/convert/${route}")"
    if [[ "${status}" != "401" ]]; then
      printf 'Expected 401 for rejected credentials on %s, got: %s\n' "${route}" "${status}" >&2
      exit 1
    fi
  done
done

curl --fail --silent --show-error \
  --request POST \
  --header "X-API-Key: ${api_key}" \
  --header 'Content-Type: text/html; charset=utf-8' \
  --data-binary "@${fixture}" \
  --dump-header "${tmp_dir}/headers.txt" \
  --output "${tmp_dir}/smoke.pdf" \
  "${base_url}/convert/html?pdf_variant=pdf%2Fua-1&file_name=smoke.pdf"

if ! grep -Eiq '^content-type:[[:space:]]*application/pdf' "${tmp_dir}/headers.txt"; then
  printf 'Response did not have an application/pdf content type.\n' >&2
  exit 1
fi

if ! grep -Eiq '^content-disposition:.*filename="?smoke\.pdf"?' "${tmp_dir}/headers.txt"; then
  printf 'Response did not include the expected PDF filename.\n' >&2
  exit 1
fi

if [[ "$(head -c 5 "${tmp_dir}/smoke.pdf")" != '%PDF-' ]]; then
  printf 'Response body does not begin with a PDF signature.\n' >&2
  exit 1
fi

qpdf --check "${tmp_dir}/smoke.pdf" >/dev/null
pdfinfo "${tmp_dir}/smoke.pdf" >"${tmp_dir}/pdfinfo.txt"
pdftotext "${tmp_dir}/smoke.pdf" "${tmp_dir}/smoke.txt"

grep -Eq '^Title:[[:space:]]+Accessible smoke test$' "${tmp_dir}/pdfinfo.txt"
grep -Eq '^Tagged:[[:space:]]+yes$' "${tmp_dir}/pdfinfo.txt"
grep -Eq '^Pages:[[:space:]]+1$' "${tmp_dir}/pdfinfo.txt"
grep -Eq '^Page size:.*A4' "${tmp_dir}/pdfinfo.txt"
grep -Fq 'This text must survive conversion and remain extractable.' "${tmp_dir}/smoke.txt"
grep -Fq 'Native SVG rendering check' "${tmp_dir}/smoke.txt"

# An image on a public HTTPS host must be fetched over verified TLS and embedded,
# not replaced by its alt text.
curl --fail --silent --show-error \
  --request POST \
  --header "X-API-Key: ${api_key}" \
  --header 'Content-Type: text/html; charset=utf-8' \
  --data-binary "@${external_image_fixture}" \
  --output "${tmp_dir}/external-image.pdf" \
  "${base_url}/convert/html?file_name=external-image.pdf"

if [[ "$(pdfimages -list "${tmp_dir}/external-image.pdf" | tail -n +3 | wc -l)" -lt 1 ]]; then
  printf 'The external HTTPS image was not embedded in the PDF.\n' >&2
  exit 1
fi

printf 'Smoke test passed for %s (%s).\n' \
  "${image}" "$(docker image inspect --format '{{.Architecture}}' "${image}")"
