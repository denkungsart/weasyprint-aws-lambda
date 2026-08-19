FROM public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 AS lambda-web-adapter

FROM ghcr.io/schweizerischebundesbahnen/weasyprint-service:69.0.0

COPY --from=lambda-web-adapter /lambda-adapter /opt/extensions/lambda-adapter

ENV AWS_LWA_PORT=9080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_READINESS_CHECK_HEALTHY_STATUS=200-399 \
    AWS_LWA_INVOKE_MODE=response_stream \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    LOG_DIR=/tmp/weasyprint-logs \
    METRICS_SERVER_ENABLED=false \
    ENABLE_METRICS=false \
    RECLAIM_MEMORY_AFTER_CONVERSION=true

LABEL org.opencontainers.image.source="https://github.com/denkungsart/weasyprint-aws-lambda" \
      org.opencontainers.image.description="ARM64 WeasyPrint service for AWS Lambda"
