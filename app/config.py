"""
Application settings. All values overridable via environment variables.
"""

import os


class Settings:
    """Service configuration with sensible defaults."""

    # --- HTTP Fetching ---
    # Total fetch timeout (connect + read). 15s prevents hangs on slow servers.
    REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", "15.0"))

    # Max redirects to follow. 10 handles most redirect chains (shorteners, etc.)
    # without looping forever.
    MAX_REDIRECTS: int = int(os.getenv("MAX_REDIRECTS", "10"))

    # 10 MB cap to prevent OOM on huge pages.
    MAX_CONTENT_LENGTH: int = int(os.getenv("MAX_CONTENT_LENGTH", str(10 * 1024 * 1024)))

    # Real Chrome UA — many sites block or obfuscate for non-browser agents.
    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    # --- Content Extraction ---
    # Body length in API response. Full text still used internally for topics.
    MAX_BODY_LENGTH: int = int(os.getenv("MAX_BODY_LENGTH", "5000"))

    # --- Topic Extraction (YAKE) ---
    # Max n-gram size for keywords. 3 captures phrases like "compact toaster oven".
    MAX_NGRAM_SIZE: int = int(os.getenv("MAX_NGRAM_SIZE", "3"))

    DEFAULT_NUM_TOPICS: int = int(os.getenv("DEFAULT_NUM_TOPICS", "10"))

    # Dedup threshold (0-1). 0.5 removes near-dups like "toaster"/"toasters".
    DEDUP_THRESHOLD: float = float(os.getenv("DEDUP_THRESHOLD", "0.5"))

    # --- Server ---
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))


settings = Settings()
