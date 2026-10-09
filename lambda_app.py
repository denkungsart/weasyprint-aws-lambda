"""Lambda-specific application wiring for the upstream WeasyPrint service."""

from __future__ import annotations

import logging
import os

import uvicorn
import weasyprint  # type: ignore[import-untyped]
from fastapi import FastAPI, Query, Response

from app import weasyprint_controller
from app.chromium_manager import ChromiumManager, get_chromium_manager
from app.schemas import ChromiumMetricsSchema, HealthSchema
from app.svg_processor import SvgProcessor
from app.weasyprint_service_application import setup_logging


class NativeSvgProcessor(SvgProcessor):
    """Keep SVG input intact so WeasyPrint renders it without Chromium."""

    # Upstream passes further arguments (such as a render size) that only matter
    # for rasterizing, so accept and ignore whatever it adds.
    async def replace_svg_with_png(self, svg, *_args, **_kwargs):  # type: ignore[no-untyped-def, override]
        native_svg = self.ensure_mandatory_attributes(svg)
        self.log.debug("Preserving SVG for native WeasyPrint rendering")
        return self.IMAGE_SVG, self.svg_to_string(native_svg)


async def skip_chromium_start(_self: ChromiumManager) -> None:
    """Replace the Chromium start so the upstream lifespan runs without a browser."""

    logging.getLogger(__name__).info(
        "Lambda mode enabled: Chromium disabled; SVG rendered natively by WeasyPrint"
    )


async def skip_chromium_stop(_self: ChromiumManager) -> None:
    """Replace the Chromium stop, as no browser was started."""


def require_upstream_hooks() -> None:
    """Fail at startup, not on the first request, if an upstream hook was renamed."""

    hooks = {
        ChromiumManager: ("start", "stop"),
        weasyprint_controller: ("app", "SvgProcessor"),
        SvgProcessor: ("replace_svg_with_png", "ensure_mandatory_attributes", "svg_to_string"),
    }
    missing = [
        f"{getattr(owner, '__name__', owner)}.{name}"
        for owner, names in hooks.items()
        for name in names
        if not hasattr(owner, name)
    ]
    if missing:
        raise RuntimeError(f"Upstream hooks patched by lambda_app are missing: {', '.join(missing)}")


async def lambda_health(detailed: bool = Query(False)) -> Response:
    """Report readiness of the PDF service without requiring Chromium."""

    if not detailed:
        return Response("OK", media_type="text/plain", status_code=200)

    chromium_manager = get_chromium_manager()
    metrics_data = chromium_manager.get_metrics()
    metrics_data["last_health_status"] = True
    health_response = HealthSchema(
        status="healthy",
        version=os.environ.get("WEASYPRINT_SERVICE_VERSION", "unknown"),
        weasyprint_version=weasyprint.__version__,
        chromium_running=False,
        chromium_version=None,
        health_monitoring_enabled=False,
        metrics=ChromiumMetricsSchema(**metrics_data),
    )
    return Response(
        content=health_response.model_dump_json(),
        media_type="application/json",
        status_code=200,
    )


require_upstream_hooks()

# Keep the upstream lifespan, so the setup it adds in new releases still runs,
# and only leave out the browser.
ChromiumManager.start = skip_chromium_start  # type: ignore[method-assign]
ChromiumManager.stop = skip_chromium_stop  # type: ignore[method-assign]

app = weasyprint_controller.app
weasyprint_controller.SvgProcessor = NativeSvgProcessor
app.router.routes[:] = [
    route for route in app.router.routes if getattr(route, "path", None) != "/health"
]
app.add_api_route(
    "/health",
    lambda_health,
    methods=["GET"],
    response_model=None,
    include_in_schema=False,
)


def main() -> None:
    """Start the Lambda-oriented ASGI application."""

    setup_logging()
    port = int(os.environ.get("PORT", os.environ.get("AWS_LWA_PORT", "9080")))
    logging.info("WeasyPrint Lambda service listening on port: %d", port)
    uvicorn.run(app=app, host="", port=port)


if __name__ == "__main__":
    main()
