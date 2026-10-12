"""Lambda-specific application wiring for the upstream WeasyPrint service."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from typing import Any
from urllib.parse import unquote, urlsplit

import uvicorn
import weasyprint  # type: ignore[import-untyped]
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, Response
from weasyprint import html as weasyprint_html  # type: ignore[import-untyped]
from weasyprint.urls import URLFetcherResponse  # type: ignore[import-untyped]

from app import weasyprint_controller
from app.chromium_manager import ChromiumManager, get_chromium_manager
from app.external_resources import PolicyUrlFetcher
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


PREFETCH_WORKERS_VAR = "EXTERNAL_RESOURCES_PREFETCH_WORKERS"
DEFAULT_PREFETCH_WORKERS = 16
GENERATE_PDF = "__generate_pdf_from_parsed_html"

# What the images of the current request loaded to: a response, or the error
# the load raised. Set around a conversion, read by the fetcher it creates.
prefetched_images: ContextVar[dict[str, URLFetcherResponse | Exception] | None] = ContextVar(
    "prefetched_images", default=None
)


def get_prefetch_workers() -> int:
    """Read how many images may load at the same time."""

    configured = os.environ.get(PREFETCH_WORKERS_VAR, "").strip()
    try:
        workers = int(configured) if configured else DEFAULT_PREFETCH_WORKERS
    except ValueError:
        return DEFAULT_PREFETCH_WORKERS
    return workers if workers > 0 else DEFAULT_PREFETCH_WORKERS


def remote_image_urls(parsed_html: BeautifulSoup, base_url: str | None) -> list[str]:
    """The distinct http(s) images of a document, as WeasyPrint will ask for them."""

    urls = (weasyprint_html.parse_url(img.get("src"), base_url) for img in parsed_html.find_all("img"))
    return list(dict.fromkeys(url for url in urls if url and urlsplit(url).scheme.lower() in ("http", "https")))


async def prefetch_images(urls: list[str]) -> dict[str, URLFetcherResponse | Exception]:
    """Load images in parallel, each through the upstream policy checks.

    WeasyPrint loads the images of a document one after another while it lays
    the document out, so a few hundred pictures spend most of a conversion
    waiting on the network.
    """

    if not urls:
        return {}

    fetcher = PolicyUrlFetcher()

    def load(url: str) -> URLFetcherResponse | Exception:
        try:
            return fetcher.fetch(url)
        except Exception as e:  # noqa: BLE001 - raised again when WeasyPrint asks for the image
            return e

    started = time.monotonic()
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=min(get_prefetch_workers(), len(urls))) as pool:
        results = await asyncio.gather(*(loop.run_in_executor(pool, load, url) for url in urls))
    loaded = dict(zip(urls, results, strict=True))

    failed = sum(isinstance(result, Exception) for result in results)
    logging.getLogger(__name__).info(
        "Prefetched %d external image(s) in %.1fs, %d failed", len(urls), time.monotonic() - started, failed
    )
    return loaded


class PrefetchedUrlFetcher(PolicyUrlFetcher):
    """Hand WeasyPrint the images loaded before the conversion, and load anything else itself."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.prefetched = prefetched_images.get() or {}

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> Any:
        # Each response is handed out once, as WeasyPrint closes it after reading
        # and keeps the image it made, so it does not ask for the same url again.
        prefetched = self.prefetched.pop(url, None)
        if prefetched is None:
            return super().fetch(url, headers)
        if isinstance(prefetched, Exception):
            raise prefetched
        return prefetched


async def generate_pdf_with_prefetched_images(parsed_html: BeautifulSoup, html_parser: Any, render: Any, *args: Any, **kwargs: Any) -> bytes:
    """Load the images of the document in parallel, then convert as upstream does."""

    # The same base URL the upstream conversion hands WeasyPrint.
    base_url = unquote(render.base_url, render.encoding) if render.base_url else None
    loaded = await prefetch_images(remote_image_urls(parsed_html, base_url))
    token = prefetched_images.set(loaded)
    try:
        return await upstream_generate_pdf(parsed_html, html_parser, render, *args, **kwargs)
    finally:
        prefetched_images.reset(token)


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
        weasyprint_controller: ("app", "SvgProcessor", "PolicyUrlFetcher", GENERATE_PDF),
        weasyprint_html: ("parse_url",),
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

# The conversion looks both names up when it runs, so it loads the images of a
# document in parallel first and then reads them through this fetcher.
upstream_generate_pdf = getattr(weasyprint_controller, GENERATE_PDF)
setattr(weasyprint_controller, GENERATE_PDF, generate_pdf_with_prefetched_images)
weasyprint_controller.PolicyUrlFetcher = PrefetchedUrlFetcher
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
