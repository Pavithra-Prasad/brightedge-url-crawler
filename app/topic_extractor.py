"""
Topic extraction using YAKE — unsupervised, no training/models required.
YAKE scores are inverted (lower = better); we normalize to 0-1 where
higher = more relevant for API consumers.
"""

import yake
from typing import Optional

from app.config import settings
from app.models import TopicItem


def extract_topics(
    text: str,
    max_topics: int = settings.DEFAULT_NUM_TOPICS,
    language: Optional[str] = None,
) -> list[TopicItem]:
    """
    Extract keywords from text using YAKE. Returns TopicItems sorted by relevance.
    """
    if not text or len(text.strip()) < 50:
        # Not enough text to extract meaningful topics.
        return []

    # Normalize language code. YAKE expects 2-letter codes.
    lang = _normalize_language(language) if language else "en"

    # YAKE config: n-grams up to MAX_NGRAM_SIZE, dedup near-duplicates.
    kw_extractor = yake.KeywordExtractor(
        lan=lang,
        n=settings.MAX_NGRAM_SIZE,
        dedupLim=settings.DEDUP_THRESHOLD,
        top=max_topics,
        features=None,  # Use all default features
    )

    # YAKE returns list of (keyword, score) tuples.
    # Lower score = more relevant in YAKE's scoring.
    raw_keywords = kw_extractor.extract_keywords(text)

    if not raw_keywords:
        return []

    # Normalize to 0-1 (higher = better). 1/(1+score) is robust to single or identical scores, unlike min-max normalization.
    topics = []
    for keyword, score in raw_keywords:
        normalized_score = round(1.0 / (1.0 + score), 4)
        topics.append(TopicItem(
            keyword=keyword.strip(),
            score=normalized_score,
        ))

    # Sort by score descending (most relevant first).
    topics.sort(key=lambda t: t.score, reverse=True)

    return topics


def _normalize_language(lang: str) -> str:
    """
    Convert locale codes (e.g., 'en-US') to YAKE's 2-letter ISO 639-1 format.
    """
    if not lang:
        return "en"

    lang = lang.strip().lower()[:2]

    # Fall back to English for unsupported languages — YAKE still works.
    supported = {"en", "pt", "fr", "de", "es", "it", "nl", "fi", "ar", "tr"}
    return lang if lang in supported else "en"
