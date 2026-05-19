"""
FastAPI entry point. Wires the crawler → parser → topic_extractor
pipeline into HTTP endpoints.
"""

import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.models import (
    ExtractionRequest,
    ExtractionResponse,
    ErrorResponse,
    PageMetadata,
)
from app.crawler import fetch_url
from app.parser import parse_html
from app.topic_extractor import extract_topics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brightedge")


# --- App Lifecycle ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    logger.info("BrightEdge URL Crawler service starting up")
    yield
    logger.info("BrightEdge URL Crawler service shutting down")


# --- App Setup ---

app = FastAPI(
    title="BrightEdge URL Metadata & Topic Extractor",
    description=(
        "A service that takes a URL as input, fetches the page, extracts "
        "metadata (title, description, body content), and returns a list "
        "of relevant topics/keywords the page is about."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Permissive CORS for demo; lock to specific origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health Check ---

@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "ok"}


# --- Main Extraction Pipeline ---

async def _run_extraction(url: str, max_topics: int, include_body: bool) -> JSONResponse:
    """
    Core extraction pipeline shared by POST and GET endpoints.
    Pipeline: fetch → parse → extract topics → build response.
    Each stage degrades gracefully; partial results preferred over 500s.
    """
    url_str = str(url)
    logger.info(f"Processing URL: {url_str}")

    # Step 1: Fetch the page
    crawl_result = await fetch_url(url_str)

    if not crawl_result.is_success:
        # Map error types to HTTP status codes
        status_code_map = {
            "timeout": 408,
            "content_type": 400,
            "content_length": 400,
            "fetch": 502,
        }
        http_status = status_code_map.get(crawl_result.error_type, 502)

        error_response = ErrorResponse(
            url=url_str,
            error=crawl_result.error or "Unknown fetch error",
            error_type=crawl_result.error_type or "fetch",
        )
        return JSONResponse(
            status_code=http_status,
            content=error_response.model_dump(),
        )

    logger.info(
        f"Fetched {url_str} — HTTP {crawl_result.status_code}, "
        f"{crawl_result.fetch_time_ms:.0f}ms"
    )

    # Step 2: Parse HTML and extract metadata
    try:
        parse_result = parse_html(crawl_result.html)
    except Exception as e:
        logger.error(f"Parse error for {url_str}: {e}\n{traceback.format_exc()}")
        error_response = ErrorResponse(
            url=url_str,
            error=f"Failed to parse HTML: {str(e)}",
            error_type="parse",
        )
        return JSONResponse(
            status_code=500,
            content=error_response.model_dump(),
        )

    # Step 2b: Detect upstream errors with no useful content.
    # If upstream returned 4xx/5xx, check whether we extracted anything
    # meaningful. Paywalled pages (NYT, Medium) often return 401/403 with
    # valid og:title — keep those. But bot walls (Cloudflare, Akamai)
    # return generic challenge pages we shouldn't surface as "success".
    if crawl_result.status_code >= 400:
        title = parse_result.title
        has_useful_metadata = bool(title or parse_result.description)

        # Known challenge/wall page titles that look like metadata but aren't.
        challenge_phrases = (
            "just a moment", "access denied", "please wait",
            "checking your browser", "attention required",
            "one more step", "bot verification", "verify you are human",
            "403 forbidden", "404 not found", "error",
        )
        if title:
            title_lower = title.strip().lower()
            if any(title_lower.startswith(p) for p in challenge_phrases):
                has_useful_metadata = False

        if not has_useful_metadata:
            error_response = ErrorResponse(
                url=url_str,
                error=f"Upstream returned HTTP {crawl_result.status_code} with no extractable metadata",
                error_type="upstream_error",
            )
            return JSONResponse(
                status_code=502,
                content=error_response.model_dump(),
            )

    # Step 3: Extract topics from body text
    topics = []
    if parse_result.full_text:
        try:
            topics = extract_topics(
                text=parse_result.full_text,
                max_topics=max_topics,
                language=parse_result.language,
            )
        except Exception as e:
            # Topic extraction failure is non-fatal — return metadata without topics.
            logger.warning(f"Topic extraction failed for {url_str}: {e}")

    # Step 4: Build response
    metadata = PageMetadata(
        title=parse_result.title,
        description=parse_result.description,
        body_content=parse_result.body_content if include_body else None,
        og_image=parse_result.og_image,
        og_type=parse_result.og_type,
        canonical_url=parse_result.canonical_url,
        language=parse_result.language,
    )

    # Determine overall status. "partial" if we got the page but some
    # extraction had issues (e.g., no body text, no topics).
    status = "success"
    if not parse_result.title and not parse_result.description:
        status = "partial"

    response = ExtractionResponse(
        url=url_str,
        final_url=crawl_result.final_url if crawl_result.final_url != url_str else None,
        status=status,
        http_status_code=crawl_result.status_code,
        metadata=metadata,
        topics=topics,
        fetch_time_ms=round(crawl_result.fetch_time_ms, 1),
    )

    return JSONResponse(content=response.model_dump())


# --- POST Endpoint (Primary) ---

@app.post(
    "/extract",
    response_model=ExtractionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request (invalid URL, non-HTML, etc.)"},
        408: {"model": ErrorResponse, "description": "Request timeout"},
        502: {"model": ErrorResponse, "description": "Failed to fetch URL"},
    },
    tags=["Extraction"],
    summary="Extract metadata and topics from a URL",
)
async def extract_post(request: ExtractionRequest):
    """
    Extract metadata and topics from a URL via POST with JSON body.
    """
    return await _run_extraction(
        url=str(request.url),
        max_topics=request.max_topics,
        include_body=request.include_body,
    )


# --- GET Endpoint (Convenience) ---

@app.get(
    "/extract",
    response_model=ExtractionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        408: {"model": ErrorResponse, "description": "Request timeout"},
        502: {"model": ErrorResponse, "description": "Failed to fetch URL"},
    },
    tags=["Extraction"],
    summary="Extract metadata and topics from a URL (GET convenience)",
)
async def extract_get(
    url: str = Query(
        ...,
        description="The URL to fetch and extract metadata from.",
        examples=["https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"],
    ),
    max_topics: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of topics to return.",
    ),
    include_body: bool = Query(
        default=True,
        description="Whether to include body content in response.",
    ),
):
    """
    Convenience GET endpoint — same as POST /extract with query params.
    """
    # Basic URL validation for the GET endpoint (POST uses Pydantic HttpUrl)
    if not url.startswith(("http://", "https://")):
        error_response = ErrorResponse(
            url=url,
            error="URL must start with http:// or https://",
            error_type="validation",
        )
        return JSONResponse(status_code=400, content=error_response.model_dump())

    return await _run_extraction(
        url=url,
        max_topics=max_topics,
        include_body=include_body,
    )


# --- Run with uvicorn ---

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,  # Auto-reload during development
        log_level="info",
    )
