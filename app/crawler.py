"""
Async HTTP crawler. Handles fetch, timeouts, content-type validation,
and size limits. Returns raw HTML to the parser layer.
"""

import time
import httpx
from dataclasses import dataclass
from typing import Optional

from app.config import settings


@dataclass
class CrawlResult:
    """Result of a URL fetch attempt."""

    html: Optional[str] = None
    status_code: int = 0
    final_url: Optional[str] = None
    content_type: Optional[str] = None
    fetch_time_ms: float = 0.0
    error: Optional[str] = None
    error_type: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.error is None and self.html is not None


async def fetch_url(url: str) -> CrawlResult:
    """
    Fetch a URL and return raw HTML or error details in a CrawlResult.
    """
    start_time = time.monotonic()
    headers = {
        "User-Agent": settings.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # Omit Accept-Encoding — letting httpx negotiate avoids Brotli decode issues.
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.REQUEST_TIMEOUT),
            follow_redirects=True,
            max_redirects=settings.MAX_REDIRECTS,
            headers=headers,
        ) as client:
            response = await client.get(str(url))

        elapsed_ms = (time.monotonic() - start_time) * 1000

        # Check content type BEFORE reading body — reject non-HTML early.
        content_type = response.headers.get("content-type", "")
        if not _is_html_content(content_type):
            return CrawlResult(
                status_code=response.status_code,
                final_url=str(response.url),
                content_type=content_type,
                fetch_time_ms=elapsed_ms,
                error=f"Not an HTML page. Content-Type: {content_type}",
                error_type="content_type",
            )

        # Enforce max content length to prevent OOM on huge pages.
        content_length = len(response.content)
        if content_length > settings.MAX_CONTENT_LENGTH:
            return CrawlResult(
                status_code=response.status_code,
                final_url=str(response.url),
                content_type=content_type,
                fetch_time_ms=elapsed_ms,
                error=f"Page too large: {content_length / (1024*1024):.1f} MB (max: {settings.MAX_CONTENT_LENGTH / (1024*1024):.1f} MB)",
                error_type="content_length",
            )

        # httpx auto-detects charset from headers or content
        html = response.text

        return CrawlResult(
            html=html,
            status_code=response.status_code,
            final_url=str(response.url),
            content_type=content_type,
            fetch_time_ms=elapsed_ms,
        )

    except httpx.TimeoutException:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        return CrawlResult(
            fetch_time_ms=elapsed_ms,
            error=f"Request timed out after {settings.REQUEST_TIMEOUT}s",
            error_type="timeout",
        )

    except httpx.TooManyRedirects:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        return CrawlResult(
            fetch_time_ms=elapsed_ms,
            error=f"Too many redirects (max: {settings.MAX_REDIRECTS})",
            error_type="fetch",
        )

    except httpx.ConnectError as e:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        return CrawlResult(
            fetch_time_ms=elapsed_ms,
            error=f"Could not connect to host: {str(e)}",
            error_type="fetch",
        )

    except httpx.HTTPError as e:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        return CrawlResult(
            fetch_time_ms=elapsed_ms,
            error=f"HTTP error: {str(e)}",
            error_type="fetch",
        )

    except Exception as e:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        return CrawlResult(
            fetch_time_ms=elapsed_ms,
            error=f"Unexpected error: {str(e)}",
            error_type="fetch",
        )


def _is_html_content(content_type: str) -> bool:
    """
    Check if Content-Type indicates HTML.
    Missing Content-Type is allowed through — parser handles non-HTML gracefully.
    """
    if not content_type:
        return True
    ct_lower = content_type.lower()
    return "text/html" in ct_lower or "application/xhtml" in ct_lower
