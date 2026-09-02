FROM public.ecr.aws/awsguru/aws-lambda-adapter:1.0.1 AS lambda-web-adapter

FROM ghcr.io/schweizerischebundesbahnen/weasyprint-service:69.0.2

ARG LAMBDA_WRAPPER_REVISION=1

COPY --from=lambda-web-adapter /lambda-adapter /opt/extensions/lambda-adapter
COPY --chown=appuser:appuser lambda_app.py /opt/weasyprint/lambda_app.py
COPY --chown=appuser:appuser --chmod=755 lambda-entrypoint.sh /opt/weasyprint/lambda-entrypoint.sh

ENV AWS_LWA_PORT=9080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_READINESS_CHECK_HEALTHY_STATUS=200-399 \
    AWS_LWA_ASYNC_INIT=true \
    AWS_LWA_INVOKE_MODE=response_stream \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    LOG_DIR=/tmp/weasyprint-logs \
    METRICS_SERVER_ENABLED=false \
    ENABLE_METRICS=false \
    RECLAIM_MEMORY_AFTER_CONVERSION=true

ENTRYPOINT ["./lambda-entrypoint.sh"]

LABEL org.opencontainers.image.source="https://github.com/denkungsart/weasyprint-aws-lambda" \
      org.opencontainers.image.description="ARM64 WeasyPrint service for AWS Lambda" \
      io.github.weasyprint-aws-lambda.wrapper-revision="${LAMBDA_WRAPPER_REVISION}"
