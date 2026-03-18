# Normalizer Consolidation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate 7 normalizer functions down to 3 (generic, cisa_news, cisa_advisory), harden generic to handle all feed types, add field validation, and integrate 12 new feed sources.

**Architecture:** Enhance `normalize_generic` with content:encoded/Atom body priority, image extraction from media fields, WordPress footer stripping, and CVE extraction. Delete 4 redundant normalizers, alias their registry keys. Add `_ALLOWED_FIELDS` validation in the ingester to prevent unknown fields from crashing OpenSearch.

**Tech Stack:** Python 3.12, feedparser, OpenSearch (strict mapping), pytest (new)

**Spec:** `docs/superpowers/specs/2026-03-19-normalizer-consolidation-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/ingestion/normalizer.py` | Modify | Add helpers, enhance generic, delete 4 normalizers, update registry |
| `app/db/opensearch.py` | Modify | Rename `_NEWS_MAPPING` → `NEWS_MAPPING` |
| `app/ingestion/ingester.py` | Modify | Add `_ALLOWED_FIELDS` validation |
| `app/ingestion/sources.py` | Modify | Add 12 new feed source entries |
| `tests/test_normalizer.py` | Create | Tests for helpers + generic normalizer |
| `tests/test_ingester.py` | Create | Tests for field validation |

---

### Task 1: Set Up Test Infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_normalizer.py` (placeholder)

- [ ] **Step 1: Install pytest and add to requirements.txt**

```bash
cd c:/Users/xb_admin/Desktop/Omar/Projects/kiber.info/kiber
.venv/Scripts/pip.exe install pytest
```

Then add `pytest>=7.0.0` to `requirements.txt` under a new `# Testing` section at the end.

- [ ] **Step 2: Create test directory and init file**

```bash
mkdir tests
```

Create `tests/__init__.py` — empty file.

- [ ] **Step 3: Create placeholder test to verify pytest works**

Create `tests/test_normalizer.py`:

```python
def test_placeholder():
    assert True
```

- [ ] **Step 4: Run pytest to verify setup**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add tests/ requirements.txt
git commit -m "Add pytest infrastructure"
```

---

### Task 2: Add `_strip_wp_footer` Helper + Tests

**Files:**
- Modify: `app/ingestion/normalizer.py` (add helper after `_extract_first_image`, ~line 98)
- Modify: `tests/test_normalizer.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_normalizer.py`:

```python
from app.ingestion.normalizer import _strip_wp_footer


class TestStripWpFooter:
    def test_strips_standard_footer(self):
        text = "Article summary here. The post My Title appeared first on SecurityWeek."
        assert _strip_wp_footer(text) == "Article summary here."

    def test_strips_unit42_footer(self):
        text = "Some content. The post Open, Closed and Broken appeared first on Unit 42."
        assert _strip_wp_footer(text) == "Some content."

    def test_no_match_returns_unchanged(self):
        text = "This is normal text with no footer."
        assert _strip_wp_footer(text) == "This is normal text with no footer."

    def test_empty_string(self):
        assert _strip_wp_footer("") == ""

    def test_only_footer(self):
        text = "The post Title appeared first on Site."
        assert _strip_wp_footer(text) == ""

    def test_does_not_match_mid_text(self):
        text = "The post appeared first on stage. Then more text follows here."
        assert _strip_wp_footer(text) == "The post appeared first on stage. Then more text follows here."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalizer.py::TestStripWpFooter -v`
Expected: FAIL (ImportError — `_strip_wp_footer` doesn't exist yet)

- [ ] **Step 3: Implement `_strip_wp_footer`**

Add to `app/ingestion/normalizer.py` after the `_extract_first_image` function (~line 98):

```python
def _strip_wp_footer(text: str) -> str:
    """Remove WordPress 'The post X appeared first on Y.' syndication footer."""
    if not text:
        return ""
    return re.sub(r"\s*The post .+? appeared first on .+?\.\s*\Z", "", text).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalizer.py::TestStripWpFooter -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/normalizer.py tests/test_normalizer.py
git commit -m "Add _strip_wp_footer helper with tests"
```

---

### Task 3: Add `_extract_image_url` Helper + Tests

**Files:**
- Modify: `app/ingestion/normalizer.py` (add helper after `_strip_wp_footer`)
- Modify: `tests/test_normalizer.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_normalizer.py`:

```python
from app.ingestion.normalizer import _extract_image_url


class TestExtractImageUrl:
    def test_media_thumbnail(self):
        """feedparser stores media:thumbnail as entry.media_thumbnail list."""
        entry = {"media_thumbnail": [{"url": "https://example.com/thumb.jpg"}]}
        assert _extract_image_url(entry, None) == "https://example.com/thumb.jpg"

    def test_media_content_image(self):
        """feedparser stores media:content as entry.media_content list."""
        entry = {"media_content": [{"url": "https://example.com/img.png", "type": "image/png"}]}
        assert _extract_image_url(entry, None) == "https://example.com/img.png"

    def test_media_content_skips_non_image(self):
        """media:content with video type should be skipped."""
        entry = {"media_content": [{"url": "https://example.com/video.mp4", "type": "video/mp4"}]}
        assert _extract_image_url(entry, None) is None

    def test_enclosure_image(self):
        """Enclosure links with image type."""
        entry = {"links": [{"rel": "enclosure", "type": "image/jpeg", "href": "https://example.com/photo.jpg"}]}
        assert _extract_image_url(entry, None) == "https://example.com/photo.jpg"

    def test_enclosure_skips_audio(self):
        entry = {"links": [{"rel": "enclosure", "type": "audio/mpeg", "href": "https://example.com/podcast.mp3"}]}
        assert _extract_image_url(entry, None) is None

    def test_featured_image_custom_field(self):
        """Unit 42 style featuredImage custom field."""
        entry = {"featuredimage": "https://example.com/featured.jpg"}
        assert _extract_image_url(entry, None) == "https://example.com/featured.jpg"

    def test_img_tag_fallback(self):
        entry = {}
        html = '<p>Text <img src="https://example.com/inline.jpg" /> more</p>'
        assert _extract_image_url(entry, html) == "https://example.com/inline.jpg"

    def test_priority_media_thumbnail_over_img_tag(self):
        """media:thumbnail wins over img tag in content."""
        entry = {"media_thumbnail": [{"url": "https://example.com/thumb.jpg"}]}
        html = '<img src="https://example.com/inline.jpg" />'
        assert _extract_image_url(entry, html) == "https://example.com/thumb.jpg"

    def test_no_image_anywhere(self):
        entry = {}
        assert _extract_image_url(entry, None) is None
        assert _extract_image_url(entry, "<p>No images here</p>") is None

    def test_truncates_long_url(self):
        entry = {"media_thumbnail": [{"url": "https://example.com/" + "a" * 3000}]}
        result = _extract_image_url(entry, None)
        assert result is not None
        assert len(result) <= 2048
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalizer.py::TestExtractImageUrl -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `_extract_image_url`**

Add to `app/ingestion/normalizer.py` after `_strip_wp_footer`:

```python
def _extract_image_url(
    entry: feedparser.FeedParserDict, content_html: Optional[str]
) -> Optional[str]:
    """Extract article image URL from feed entry metadata or HTML content.

    Priority: media:thumbnail > media:content (image) > enclosure (image) > <img> tag.
    """
    # 1. media:thumbnail
    thumbs = entry.get("media_thumbnail") or []
    if thumbs and thumbs[0].get("url"):
        return thumbs[0]["url"][:2048]

    # 2. media:content (image types only)
    media = entry.get("media_content") or []
    for m in media:
        mtype = (m.get("type") or "").lower()
        if mtype.startswith("image/") and m.get("url"):
            return m["url"][:2048]

    # 3. enclosure links with image type
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure":
            ltype = (link.get("type") or "").lower()
            if ltype.startswith("image/") and link.get("href"):
                return link["href"][:2048]

    # 4. Custom fields (e.g. Unit 42 featuredImage) — feedparser lowercases element names
    featured = entry.get("featuredimage") or entry.get("featuredImage")
    if featured:
        return (featured if isinstance(featured, str) else str(featured))[:2048]

    # 5. <img> tag from HTML content
    if content_html:
        img = _extract_first_image(content_html)
        if img:
            return img[:2048]

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalizer.py::TestExtractImageUrl -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/normalizer.py tests/test_normalizer.py
git commit -m "Add _extract_image_url helper with tests"
```

---

### Task 4: Enhance `normalize_generic` + Tests

**Files:**
- Modify: `app/ingestion/normalizer.py` (rewrite `normalize_generic`, lines 182-222)
- Modify: `tests/test_normalizer.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_normalizer.py`:

```python
from app.ingestion.normalizer import normalize_generic
from app.ingestion.sources import FeedSource


def _make_source(**overrides) -> FeedSource:
    defaults = {
        "name": "TestFeed",
        "url": "https://example.com/feed",
        "default_type": "news",
        "default_category": "breaking",
        "default_severity": None,
        "normalizer": "generic",
    }
    defaults.update(overrides)
    return FeedSource(**defaults)


class TestNormalizeGeneric:
    def test_prefers_content_encoded_over_summary(self):
        """content:encoded (full body) should be content_html, summary from desc."""
        entry = {
            "title": "Test Article",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "Short teaser text",
            "content": [{"value": "<p>Full article body with <b>HTML</b> content here.</p>"}],
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["content_html"] == "<p>Full article body with <b>HTML</b> content here.</p>"
        assert "Full article body" in result["summary"]
        assert result["desc"] == "Short teaser text"
        assert result["content_source"] == "rss"

    def test_falls_back_to_summary_when_no_content(self):
        """When no content:encoded, use summary for everything."""
        entry = {
            "title": "Test Article",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "<p>Only a summary here</p>",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["content_html"] == "<p>Only a summary here</p>"
        assert result["summary"] == "Only a summary here"
        assert result["desc"] == "Only a summary here"

    def test_strips_wp_footer_from_desc_and_summary(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "Content here. The post Test appeared first on MySite.",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert "appeared first on" not in result["desc"]
        assert "appeared first on" not in result["summary"]

    def test_extracts_image_url(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
            "media_thumbnail": [{"url": "https://example.com/thumb.jpg"}],
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["image_url"] == "https://example.com/thumb.jpg"

    def test_extracts_cve_ids_from_content(self):
        entry = {
            "title": "Patch for CVE-2026-1234",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "content": [{"value": "<p>Fixes CVE-2026-1234 and CVE-2026-5678</p>"}],
            "summary": "teaser",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert "CVE-2026-1234" in result["cve_ids"]
        assert "CVE-2026-5678" in result["cve_ids"]

    def test_extracts_cve_ids_from_tags(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
            "tags": [{"term": "CVE-2026-9999"}, {"term": "security"}],
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["cve_ids"] == ["CVE-2026-9999"]

    def test_no_cve_ids_returns_empty_list(self):
        entry = {
            "title": "No CVEs here",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["cve_ids"] == []

    def test_returns_none_for_missing_title(self):
        entry = {"link": "https://example.com/article"}
        assert normalize_generic(entry, _make_source()) is None

    def test_returns_none_for_missing_link(self):
        entry = {"title": "Test"}
        assert normalize_generic(entry, _make_source()) is None

    def test_tags_extracted(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
            "tags": [{"term": "ransomware"}, {"term": "malware"}],
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert "ransomware" in result["tags"]
        assert "malware" in result["tags"]

    def test_content_source_set_when_content_exists(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
        }
        result = normalize_generic(entry, _make_source())
        assert result["content_source"] == "rss"

    def test_content_source_none_when_no_content(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["content_source"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalizer.py::TestNormalizeGeneric -v`
Expected: Some tests FAIL (content priority logic, CVE extraction, image extraction not yet in generic)

- [ ] **Step 3: Rewrite `normalize_generic`**

Replace the current `normalize_generic` function (lines 182-222 of `normalizer.py`) with:

```python
def normalize_generic(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Universal normalizer for RSS 2.0 and Atom feeds.

    Handles content:encoded (RSS), <content> (Atom), summary-only feeds,
    WordPress footers, image extraction, and CVE ID extraction.
    """
    title = (entry.get("title") or "").strip()
    link = (entry.get("link") or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()

    # --- Content body: prefer content:encoded / Atom <content> ---
    content_list = entry.get("content") or []
    content_value = (content_list[0].get("value") if content_list else "") or ""
    raw_desc = entry.get("summary") or entry.get("description") or ""

    # If content:encoded / Atom content exists, use it as the full body
    if content_value:
        content_html = content_value or None
        desc_text = _strip_wp_footer(strip_html(raw_desc).strip()) or strip_html(content_value).strip() or title
        summary_text = _strip_wp_footer(strip_html(content_value).strip())[:2000] or None
    elif raw_desc:
        content_html = raw_desc or None
        desc_text = _strip_wp_footer(strip_html(raw_desc).strip()) or title
        summary_text = _strip_wp_footer(strip_html(raw_desc).strip())[:2000] or None
    else:
        content_html = None
        desc_text = title
        summary_text = None

    content_source = "rss" if content_html else None

    # --- Image extraction ---
    image_url = _extract_image_url(entry, content_html)

    # --- Tags ---
    tags = _extract_tags(entry)

    # --- CVE extraction from content + tags ---
    tag_text = " ".join(tags)
    cve_source = f"{title} {content_html or ''} {tag_text}"
    cve_ids = _extract_cve_ids(cve_source)

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author=(entry.get("author") or "").strip() or None,
        desc=desc_text,
        content_html=content_html,
        summary=summary_text,
        content_source=content_source,
        image_url=image_url[:2048] if image_url else None,
        tags=tags,
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
        cve_ids=cve_ids if cve_ids else [],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalizer.py -v`
Expected: All tests pass (placeholder + wp footer + image + generic tests)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/normalizer.py tests/test_normalizer.py
git commit -m "Enhance normalize_generic with content priority, images, WP footer, CVEs"
```

---

### Task 5: Delete Old Normalizers + Update Registry

**Files:**
- Modify: `app/ingestion/normalizer.py` (delete functions, update registry)

- [ ] **Step 1: Write a test verifying registry aliases resolve**

Add to `tests/test_normalizer.py`:

```python
from app.ingestion.normalizer import NORMALIZER_REGISTRY, normalize_generic, normalize_cisa_news, normalize_cisa_advisory


class TestNormalizerRegistry:
    def test_all_keys_resolve(self):
        expected_keys = {"generic", "thn", "bleepingcomputer", "securityweek", "krebs", "cisa_news", "cisa_advisory"}
        assert set(NORMALIZER_REGISTRY.keys()) == expected_keys

    def test_aliases_point_to_generic(self):
        for key in ("generic", "thn", "bleepingcomputer", "securityweek", "krebs"):
            assert NORMALIZER_REGISTRY[key] is normalize_generic

    def test_cisa_normalizers_are_distinct(self):
        assert NORMALIZER_REGISTRY["cisa_news"] is normalize_cisa_news
        assert NORMALIZER_REGISTRY["cisa_advisory"] is normalize_cisa_advisory
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalizer.py::TestNormalizerRegistry -v`
Expected: FAIL (registry still has separate functions)

- [ ] **Step 3: Delete old normalizer functions and update registry**

In `app/ingestion/normalizer.py`:

1. Delete the `normalize_thn` function entirely (lines 139-179)
2. Delete the `normalize_bleepingcomputer` function entirely (lines 225-261)
3. Delete the `normalize_securityweek` function entirely (lines 264-305)
4. Delete the `normalize_krebs` function entirely (lines 402-471)
   - **Note:** This intentionally drops Krebs comment metadata extraction (`slash_comments`, `wfw_commentrss`, `comments` URL stored in `raw_metadata`). This data is not used by any downstream feature. CVE extraction and image extraction from Krebs articles are preserved by the enhanced generic normalizer.
5. Replace the `NORMALIZER_REGISTRY` dict with:

```python
NORMALIZER_REGISTRY: dict[str, Callable] = {
    "generic":          normalize_generic,
    "thn":              normalize_generic,
    "bleepingcomputer": normalize_generic,
    "securityweek":     normalize_generic,
    "krebs":            normalize_generic,
    "cisa_news":        normalize_cisa_news,
    "cisa_advisory":    normalize_cisa_advisory,
}
```

- [ ] **Step 4: Run all tests to verify nothing broke**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalizer.py -v`
Expected: All tests pass

- [ ] **Step 5: Verify Python syntax is valid**

Run: `.venv/Scripts/python.exe -c "import app.ingestion.normalizer; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/normalizer.py tests/test_normalizer.py
git commit -m "Delete redundant normalizers, alias registry keys to generic"
```

---

### Task 6: Rename `_NEWS_MAPPING` → `NEWS_MAPPING` in opensearch.py

**Files:**
- Modify: `app/db/opensearch.py`

- [ ] **Step 1: Rename the variable**

In `app/db/opensearch.py`:
- Rename `_NEWS_MAPPING` to `NEWS_MAPPING` (line 12)
- Update the reference in `ensure_indexes()` (line 201) from `_NEWS_MAPPING` to `NEWS_MAPPING`

This is a find-and-replace: `_NEWS_MAPPING` → `NEWS_MAPPING` (3 occurrences: declaration, and in the `ensure_indexes` loop tuple).

- [ ] **Step 2: Verify import works**

Run: `.venv/Scripts/python.exe -c "from app.db.opensearch import NEWS_MAPPING; print(len(NEWS_MAPPING['mappings']['properties']), 'fields')"`
Expected: `23 fields`

- [ ] **Step 3: Commit**

```bash
git add app/db/opensearch.py
git commit -m "Rename _NEWS_MAPPING to NEWS_MAPPING for external import"
```

---

### Task 7: Add Field Validation to `_prepare_article_doc()` + Tests

**Files:**
- Modify: `app/ingestion/ingester.py` (lines 71-88)
- Create: `tests/test_ingester.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ingester.py`:

```python
from datetime import datetime, timezone
from app.ingestion.ingester import _prepare_article_doc


class TestPrepareArticleDoc:
    def test_known_fields_pass_through(self):
        article = {
            "slug": "test-article-abc12345",
            "guid": "https://example.com/1",
            "source_name": "TestFeed",
            "title": "Test Article",
            "desc": "Description",
            "published_at": datetime(2026, 3, 19, tzinfo=timezone.utc),
            "tags": ["security"],
            "keywords": [],
            "cve_ids": [],
        }
        slug, doc = _prepare_article_doc(article)
        assert slug == "test-article-abc12345"
        assert doc["title"] == "Test Article"
        assert doc["tags"] == ["security"]

    def test_unknown_fields_stripped(self):
        article = {
            "slug": "test-article-abc12345",
            "guid": "https://example.com/1",
            "source_name": "TestFeed",
            "title": "Test Article",
            "desc": "Description",
            "published_at": datetime(2026, 3, 19, tzinfo=timezone.utc),
            "tags": [],
            "keywords": [],
            "cve_ids": [],
            "bogus_field": "should be removed",
            "another_unknown": 42,
        }
        slug, doc = _prepare_article_doc(article)
        assert "bogus_field" not in doc
        assert "another_unknown" not in doc

    def test_datetime_serialized_to_iso(self):
        article = {
            "slug": "test-abc12345",
            "guid": "guid",
            "source_name": "Feed",
            "title": "T",
            "published_at": datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
            "tags": [],
            "keywords": [],
            "cve_ids": [],
        }
        _, doc = _prepare_article_doc(article)
        assert doc["published_at"] == "2026-03-19T12:00:00+00:00"

    def test_defaults_set_for_missing_fields(self):
        article = {
            "slug": "test-abc12345",
            "guid": "guid",
            "source_name": "Feed",
            "title": "T",
            "published_at": "2026-03-19T12:00:00+00:00",
        }
        _, doc = _prepare_article_doc(article)
        assert doc["tags"] == []
        assert doc["keywords"] == []
        assert doc["cve_ids"] == []
        assert doc["content_html"] is None
        assert doc["summary"] is None
        assert doc["content_source"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingester.py -v`
Expected: `test_unknown_fields_stripped` FAILS (unknown fields currently pass through)

- [ ] **Step 3: Add field validation to `_prepare_article_doc`**

In `app/ingestion/ingester.py`:

Add import at top:
```python
from app.db.opensearch import NEWS_MAPPING
```

Add module-level constant after imports:
```python
_ALLOWED_FIELDS = frozenset(NEWS_MAPPING["mappings"]["properties"].keys())
```

Add field stripping at the end of `_prepare_article_doc()`, before the return statement:

```python
    # Strip unknown fields to prevent dynamic:strict indexing errors
    unexpected = set(doc.keys()) - _ALLOWED_FIELDS
    for key in unexpected:
        logger.warning("Dropping unknown field '%s' from article '%s'", key, doc.get("slug"))
        doc.pop(key)
    return doc["slug"], doc
```

(Replace the existing `return doc["slug"], doc` line.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingester.py -v`
Expected: All 4 tests pass

- [ ] **Step 5: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/ingester.py tests/test_ingester.py
git commit -m "Add field validation in _prepare_article_doc to strip unknown keys"
```

---

### Task 8: Add 12 New Feed Sources

**Files:**
- Modify: `app/ingestion/sources.py`

- [ ] **Step 1: Add new entries to `SEED_SOURCES`**

Append to the `SEED_SOURCES` list in `app/ingestion/sources.py`:

```python
    FeedSource(
        name="Schneier on Security",
        url="https://www.schneier.com/feed/atom/",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Unit 42",
        url="https://unit42.paloaltonetworks.com/feed/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="The DFIR Report",
        url="https://thedfirreport.com/feed/",
        default_type="report",
        default_category="deep-dives",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="SANS ISC",
        url="https://isc.sans.edu/rssfeed_full.xml",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Troy Hunt",
        url="https://feeds.feedburner.com/TroyHunt",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Didier Stevens",
        url="https://blog.didierstevens.com/feed/atom/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Dark Reading",
        url="https://www.darkreading.com/rss.xml",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Google Threat Intelligence",
        url="https://cloudblog.withgoogle.com/topics/threat-intelligence/rss/",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="PortSwigger Research",
        url="https://portswigger.net/research/rss",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Recorded Future",
        url="https://www.recordedfuture.com/feed",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Red Canary",
        url="https://redcanary.com/blog/feed/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="CyberScoop",
        url="https://cyberscoop.com/feed/atom/",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
    ),
```

- [ ] **Step 2: Verify the module loads and count is correct**

Run: `.venv/Scripts/python.exe -c "from app.ingestion.sources import SEED_SOURCES; print(f'{len(SEED_SOURCES)} sources'); [print(f'  {s[\"name\"]}') for s in SEED_SOURCES]"`
Expected: `18 sources` followed by all names

- [ ] **Step 3: Run all tests to make sure nothing broke**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/sources.py
git commit -m "Add 12 new feed sources (Schneier, Unit42, DFIR, SANS, etc.)"
```

---

### Task 9: Integration Verification (Docker)

This task verifies the full pipeline works end-to-end inside Docker.

- [ ] **Step 1: Build and start Docker containers**

```bash
docker compose build backend
docker compose up -d
```

- [ ] **Step 2: Verify backend starts cleanly**

```bash
docker compose logs backend --tail 50
```

Expected: No errors. Look for `ensure_indexes` log lines showing mapping updates.

- [ ] **Step 3: Seed the new sources into PostgreSQL**

```bash
docker compose exec backend python scripts/seed_sources.py
```

Expected: `12 inserted` (the 12 new feeds), `6 already existed` (existing feeds).

- [ ] **Step 4: Run ingestion**

```bash
docker compose exec backend python -c "
import asyncio
from app.ingestion.ingester import ingest_all_feeds
asyncio.run(ingest_all_feeds())
"
```

Expected: Log lines for all 18 feeds. No `Dropping unknown field` warnings (if there are, investigate). No indexing errors. Some feeds may have 0 new articles (if previously ingested).

- [ ] **Step 5: Spot-check articles in OpenSearch**

```bash
docker compose exec backend python -c "
import asyncio
from app.db.opensearch import get_os_client, INDEX_NEWS

async def check():
    client = get_os_client()
    # Check a Krebs article
    r = await client.search(index=INDEX_NEWS, body={'query': {'term': {'source_name': 'Krebs on Security'}}, 'size': 1, '_source': ['title', 'content_html', 'summary', 'image_url', 'cve_ids', 'content_source']})
    hits = r['hits']['hits']
    if hits:
        src = hits[0]['_source']
        print('=== Krebs ===')
        print(f'title: {src.get(\"title\", \"\")[:80]}')
        print(f'content_html: {\"YES\" if src.get(\"content_html\") else \"NO\"} ({len(src.get(\"content_html\", \"\"))} chars)')
        print(f'summary: {\"YES\" if src.get(\"summary\") else \"NO\"}')
        print(f'image_url: {src.get(\"image_url\", \"NONE\")}')
        print(f'cve_ids: {src.get(\"cve_ids\", [])}')
        print(f'content_source: {src.get(\"content_source\")}')

    # Check a Dark Reading article (image from media:thumbnail)
    r = await client.search(index=INDEX_NEWS, body={'query': {'term': {'source_name': 'Dark Reading'}}, 'size': 1, '_source': ['title', 'image_url', 'desc']})
    hits = r['hits']['hits']
    if hits:
        src = hits[0]['_source']
        print('\\n=== Dark Reading ===')
        print(f'title: {src.get(\"title\", \"\")[:80]}')
        print(f'image_url: {src.get(\"image_url\", \"NONE\")}')

    # Check a Schneier article (full body from Atom content)
    r = await client.search(index=INDEX_NEWS, body={'query': {'term': {'source_name': 'Schneier on Security'}}, 'size': 1, '_source': ['title', 'content_html', 'summary']})
    hits = r['hits']['hits']
    if hits:
        src = hits[0]['_source']
        print('\\n=== Schneier ===')
        print(f'title: {src.get(\"title\", \"\")[:80]}')
        print(f'content_html: {\"YES\" if src.get(\"content_html\") else \"NO\"} ({len(src.get(\"content_html\", \"\"))} chars)')

    # Check a Unit 42 article (WP footer should be stripped)
    r = await client.search(index=INDEX_NEWS, body={'query': {'term': {'source_name': 'Unit 42'}}, 'size': 1, '_source': ['title', 'desc', 'summary']})
    hits = r['hits']['hits']
    if hits:
        src = hits[0]['_source']
        print('\\n=== Unit 42 ===')
        print(f'title: {src.get(\"title\", \"\")[:80]}')
        print(f'desc: {src.get(\"desc\", \"\")[:120]}')
        print(f'WP footer in desc: {\"appeared first on\" in (src.get(\"desc\") or \"\")}')

    await client.close()

asyncio.run(check())
"
```

Expected:
- Krebs: `content_html: YES`, `summary: YES`, `image_url` populated, `content_source: rss`
- Dark Reading: `image_url` populated (from media:thumbnail)
- Schneier: `content_html: YES` with substantial length
- Unit 42: `WP footer in desc: False`

- [ ] **Step 6: Final commit (if any fixes were needed)**

If any adjustments were made during verification, commit them.

```bash
git add -u
git commit -m "Fix issues found during integration verification"
```
