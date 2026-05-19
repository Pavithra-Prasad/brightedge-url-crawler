"""
Pydantic models for API request/response validation.
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional


# --- Request Models ---

class ExtractionRequest(BaseModel):
    """Request body for the POST /extract endpoint."""

    url: HttpUrl = Field(
        ...,
        description="The URL to fetch and extract metadata from.",
        json_schema_extra={"examples": ["https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"]}
    )
    max_topics: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of topics/keywords to return."
    )
    include_body: bool = Field(
        default=True,
        description="Whether to include the extracted body content in the response."
    )


# --- Response Models ---

class TopicItem(BaseModel):
    """A single extracted topic/keyword with its relevance score."""

    keyword: str = Field(..., description="The extracted keyword or phrase.")
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Relevance score (0-1, higher = more relevant)."
    )


class PageMetadata(BaseModel):
    """Extracted metadata from the HTML page."""

    title: Optional[str] = Field(None, description="Page title.")
    description: Optional[str] = Field(None, description="Page description/summary.")
    body_content: Optional[str] = Field(
        None,
        description="Extracted main body text (noise elements removed)."
    )
    og_image: Optional[str] = Field(None, description="Open Graph image URL.")
    og_type: Optional[str] = Field(None, description="Open Graph type (article, product, etc.).")
    canonical_url: Optional[str] = Field(None, description="Canonical URL of the page.")
    language: Optional[str] = Field(None, description="Page language (e.g., 'en').")


class ExtractionResponse(BaseModel):
    """Successful extraction response."""

    url: str = Field(..., description="The URL that was processed.")
    final_url: Optional[str] = Field(
        None,
        description="Final URL after redirects (if different from input)."
    )
    status: str = Field(
        default="success",
        description="Processing status: 'success' or 'partial'."
    )
    http_status_code: int = Field(..., description="HTTP status code from the fetch.")
    metadata: PageMetadata
    topics: list[TopicItem] = Field(
        default_factory=list,
        description="Extracted topics/keywords ranked by relevance."
    )
    fetch_time_ms: float = Field(
        ...,
        description="Time taken to fetch the URL in milliseconds."
    )


class ErrorResponse(BaseModel):
    """Error response for failed extractions."""

    url: str = Field(..., description="The URL that was attempted.")
    status: str = Field(default="error")
    error: str = Field(..., description="Human-readable error message.")
    error_type: str = Field(
        ...,
        description="Error category: 'validation', 'fetch', 'parse', 'timeout', 'content_type'."
    )
