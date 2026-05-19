"""
HTML parser and metadata extractor.
Uses BeautifulSoup4 with a priority cascade.
"""

import json
import re
from dataclasses import dataclass
from typing import Optional
from bs4 import BeautifulSoup, Comment

from app.config import settings


# Non-content tags stripped before text extraction.
NOISE_TAGS = {
    "script", "style", "nav", "footer", "header", "aside",
    "iframe", "noscript", "svg", "form", "button",
}

# Heuristic class/id patterns for non-content elements.
NOISE_PATTERNS = re.compile(
    r"(sidebar|menu|nav|footer|header|advertisement|ad-|social|share|"
    r"comment|cookie|popup|modal|overlay|breadcrumb|pagination)",
    re.IGNORECASE,
)


@dataclass
class ParseResult:
    """Structured metadata extracted from an HTML page."""

    title: Optional[str] = None
    description: Optional[str] = None
    body_content: Optional[str] = None
    full_text: Optional[str] = None  # Full body text (for topic extraction)
    og_image: Optional[str] = None
    og_type: Optional[str] = None
    canonical_url: Optional[str] = None
    language: Optional[str] = None


def parse_html(html: str) -> ParseResult:
    """
    Parse raw HTML and extract structured metadata.
    Uses lxml parser (C-based, ~10x faster than html.parser).
    """
    soup = BeautifulSoup(html, "lxml")

    # JSON-LD: richest metadata source on modern sites.
    json_ld = _extract_json_ld(soup)

    # Extract body content (needed as fallback for description).
    full_text = _extract_body_content(soup)

    # Prefer JSON-LD articleBody for topics (cleaner than HTML body).
    topic_text = None
    if json_ld:
        ld_body = json_ld.get("articleBody") or json_ld.get("description", "")
        if ld_body and len(ld_body) > 100:
            topic_text = ld_body

    # If HTML body extraction was thin, use JSON-LD body for display too.
    if json_ld and (not full_text or len(full_text) < 100):
        ld_body = json_ld.get("articleBody") or json_ld.get("description", "")
        if ld_body and len(ld_body) > (len(full_text) if full_text else 0):
            full_text = ld_body

    # Fall back to HTML body text for topics if no JSON-LD.
    if not topic_text:
        topic_text = full_text

    # Cap topic input — YAKE doesn't need more than ~20k chars.
    max_topic_text = 20000
    topic_text = topic_text[:max_topic_text] if topic_text else None

    title = _extract_title(soup)
    description = _extract_description(soup, full_text)

    # Use JSON-LD as fallback for title/description if HTML extraction failed.
    if json_ld:
        if not title:
            title = json_ld.get("headline") or json_ld.get("name")
        if not description:
            description = json_ld.get("description")

    result = ParseResult(
        title=title,
        description=description,
        og_image=_extract_meta_content(soup, property="og:image"),
        og_type=_extract_meta_content(soup, property="og:type"),
        canonical_url=_extract_canonical(soup),
        language=_extract_language(soup),
        full_text=topic_text,
        body_content=full_text[:settings.MAX_BODY_LENGTH] if full_text else None,
    )

    return result


# --- Title Extraction ---

def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract page title with cascade: og:title > twitter:title > <title>.
    OG is preferred — cleaner than <title> tags with site-name suffixes.
    """
    og_title = _extract_meta_content(soup, property="og:title")
    if og_title:
        return og_title.strip()

    twitter_title = _extract_meta_content(soup, attrs={"name": "twitter:title"})
    if twitter_title:
        return twitter_title.strip()

    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        return title_tag.string.strip()

    return None


# --- Description Extraction ---

def _extract_description(
    soup: BeautifulSoup,
    body_text: Optional[str] = None,
) -> Optional[str]:
    """
    Extract description with cascade: og:description > meta description >
    twitter:description > first 200 chars of body.
    """
    og_desc = _extract_meta_content(soup, property="og:description")
    if og_desc:
        return og_desc.strip()

    meta_desc = _extract_meta_content(soup, attrs={"name": "description"})
    if meta_desc:
        return meta_desc.strip()

    twitter_desc = _extract_meta_content(soup, attrs={"name": "twitter:description"})
    if twitter_desc:
        return twitter_desc.strip()
    # Last resort: first 200 chars of body text (reuse already-extracted text)
    if body_text:
        return body_text[:200].strip()

    return None


# --- Body Content Extraction ---

def _extract_body_content(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract main body text.
    Strategy: clone soup → strip noise tags/classes → drop comments →
    extract text with space separators → collapse whitespace.
    """
    body = soup.find("body")
    if not body:
        return None

    # Work on a copy to avoid mutating the original soup.
    body_copy = BeautifulSoup(str(body), "lxml").find("body")
    if not body_copy:
        return None

    # Collect tags before decomposing — mutation during iteration breaks find_all results.
    tags_to_remove = list(body_copy.find_all(NOISE_TAGS))
    for tag in tags_to_remove:
        tag.decompose()

    # Remove elements with noise class/id patterns.
    noise_elements = []
    for tag in body_copy.find_all(True):
        classes = tag.get("class", [])
        # Defensive: class attribute can sometimes be a string instead of list
        if isinstance(classes, str):
            classes = [classes]
        class_str = " ".join(classes) if classes else ""
        tag_id = tag.get("id", "") or ""
        if NOISE_PATTERNS.search(class_str) or NOISE_PATTERNS.search(tag_id):
            noise_elements.append(tag)
    for tag in noise_elements:
        tag.decompose()

    for comment in body_copy.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # separator=' ' prevents words running together when tags are stripped.
    text = body_copy.get_text(separator=" ", strip=True)

    # Collapse multiple whitespace characters into single spaces.
    text = re.sub(r"\s+", " ", text).strip()

    return text if text else None


# --- Helper Functions ---

def _extract_meta_content(
    soup: BeautifulSoup,
    property: Optional[str] = None,
    attrs: Optional[dict] = None,
) -> Optional[str]:
    """
    Get 'content' attr from a <meta> tag by property (OG) or name (standard).
    """
    if property:
        tag = soup.find("meta", attrs={"property": property})
        if tag and tag.get("content"):
            return tag["content"]
    if attrs:
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"]
    return None


def _extract_canonical(soup: BeautifulSoup) -> Optional[str]:
    """Extract the canonical URL from <link rel="canonical">."""
    link = soup.find("link", attrs={"rel": "canonical"})
    if link and link.get("href"):
        return link["href"]
    return None


def _extract_language(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract page language: <html lang> > meta content-language > og:locale.
    """
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        return html_tag["lang"].strip()

    content_lang = soup.find("meta", attrs={"http-equiv": "content-language"})
    if content_lang and content_lang.get("content"):
        return content_lang["content"].strip()

    og_locale = _extract_meta_content(soup, property="og:locale")
    if og_locale:
        return og_locale.strip()

    return None


def _extract_json_ld(soup: BeautifulSoup) -> Optional[dict]:
    """
    Extract structured data from <script type="application/ld+json"> tags.
    Returns highest-priority schema (Article > Product > WebPage), or None.
    """
    # Priority order for schema types — most specific to least.
    priority_types = {
        "NewsArticle", "Article", "BlogPosting", "Product",
        "WebPage", "ItemPage", "CollectionPage",
    }

    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})

    candidates = []
    for script in scripts:
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        # Handle both single objects and arrays of objects.
        items = data if isinstance(data, list) else [data]

        for item in items:
            if not isinstance(item, dict):
                continue

            # Handle @graph containers (used by some sites).
            if "@graph" in item:
                items.extend(item["@graph"])
                continue

            schema_type = item.get("@type", "")
            # @type can be a string or list
            if isinstance(schema_type, list):
                schema_type = schema_type[0] if schema_type else ""

            if schema_type in priority_types:
                candidates.append((schema_type, item))

    if not candidates:
        return None

    # Return the highest-priority match.
    for ptype in priority_types:
        for schema_type, item in candidates:
            if schema_type == ptype:
                return item

    return candidates[0][1] if candidates else None
