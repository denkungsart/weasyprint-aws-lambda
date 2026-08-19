# WeasyPrint for AWS Lambda (ARM64)

This repository packages the
[SBB WeasyPrint service](https://github.com/SchweizerischeBundesbahnen/weasyprint-service)
with the [AWS Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter).
The result is a non-root, ARM64 container image that can run behind an
IAM-protected Lambda Function URL without adding Lambda-specific code to the
upstream application.

The first delivery stage is deliberately limited to the image pipeline. It does
not provision a Lambda function, change `prestage`, migrate Prawn documents, or
remove wkhtmltopdf. It gives those changes a tested artifact to adopt gradually.

## Pinned components

- SBB WeasyPrint service: `69.0.0`
- AWS Lambda Web Adapter: `1.0.1`
- Application port: `9080`
- Lambda invoke mode: `response_stream`

Both upstream versions are pinned in the [Dockerfile](Dockerfile). Dependabot
opens updates for the container bases and GitHub Actions. A release tag encodes
both versions, for example `v69.0.0-lwa1.0.1-arm64`, so an existing artifact is
never silently replaced by a different adapter or renderer.

## Local build and smoke test

The smoke test needs Docker, curl, `qpdf`, `pdfinfo`, and `pdftotext`. On
Debian/Ubuntu, the last three are provided by `qpdf` and `poppler-utils`.

```sh
docker build --tag weasyprint-aws-lambda:test .
test/smoke.sh weasyprint-aws-lambda:test
```

The test starts the service with a read-only root filesystem, a writable `/tmp`,
a 2 GiB memory limit, and a random loopback port. It then requests a `pdf/ua-1`
document and verifies:

- the image keeps the upstream non-root `appuser`;
- the Lambda Web Adapter executable is present;
- the service becomes healthy;
- the response is a structurally valid, tagged, one-page A4 PDF;
- the HTML title and expected text survive conversion.

The service can also be exercised manually:

```sh
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,exec,mode=1777,size=1024m \
  --memory 2g \
  --publish 9080:9080 \
  weasyprint-aws-lambda:test

curl --fail \
  --header 'Content-Type: text/html; charset=utf-8' \
  --data-binary @test/fixtures/accessible.html \
  --output smoke.pdf \
  'http://127.0.0.1:9080/convert/html?pdf_variant=pdf%2Fua-1&file_name=smoke.pdf'
```

## CI and publishing

Pull requests and pushes to `main` build and smoke-test the image on a native
GitHub-hosted ARM64 runner. When either pinned image version changes on `main`,
the tag workflow creates the combined immutable Git tag and dispatches the ECR
workflow. The ECR workflow checks out that exact tag, rebuilds and smoke-tests it
on ARM64, then pushes the already-tested local image.

Publishing expects:

- an ECR repository named `weasyprint-aws-lambda` in `eu-west-1`, configured
  with immutable tags;
- an `AWS_IAM_ROLE` GitHub Actions secret containing the role ARN;
- GitHub OIDC trust allowing this repository to assume that role;
- ECR image lookup and upload permissions on the role.

The workflow can also be dispatched manually with an existing combined-version
tag. It refuses a tag that does not match the versions pinned in its Dockerfile.

## Lambda configuration for the first workload

For the initial wkhtmltopdf replacement trial, start with:

| Setting | Initial value |
| --- | --- |
| Architecture | `arm64` |
| Memory | 2048 MiB |
| Timeout | 120 seconds |
| Ephemeral storage | 1024 MiB |
| Function URL authorization | `AWS_IAM` |
| Function URL invoke mode | `RESPONSE_STREAM` |

The image already configures the adapter for port `9080`, the `/health`
readiness check, and response streaming. It redirects the upstream application's
home, cache, and logs to `/tmp`, disables the unused metrics listener, and asks
the service to reclaim memory after each conversion.

Version 69.0.0 of the upstream service does not provide application-level
authentication. Do not expose it through a Function URL with `NONE`
authorization. Use `AWS_IAM` and grant `lambda:InvokeFunctionUrl` only to the
callers that generate PDFs. Also constrain outbound networking if untrusted HTML
could cause the renderer to fetch external resources.

Lambda's documented payload limits still apply: buffered request payloads are
limited to 6 MB, while a streamed response can be up to 200 MB. See the
[Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html)
and [Function URL invoke modes](https://docs.aws.amazon.com/lambda/latest/dg/config-rs-invoke-furls.html).

## Accessibility and migration notes

Requesting `pdf/ua-1` enables the PDF/UA output path; it does not make arbitrary
HTML accessible. Source templates still need meaningful document structure,
language, titles, heading order, link text, table semantics, and image
alternatives. Validate representative outputs with the accessibility tooling
used by the product, not only this smoke test. The
[WeasyPrint PDF/UA guidance](https://doc.courtbouillon.org/weasyprint/stable/common_use_cases.html#pdf-ua-universal-access)
describes the renderer's requirements and limitations.

For the first migration slice, translate wkhtmltopdf-specific switches into
print CSS and service inputs one document type at a time. Keep the current path
available until visual regression, content extraction, accessibility, latency,
and failure handling are acceptable. Prawn migration remains a separate later
stage.

## Licensing

This wrapper is licensed under Apache License 2.0. The bundled upstream projects
are also Apache-2.0 licensed; their attribution is preserved in [NOTICE](NOTICE).
