"""SPA-shell detection for Tier-2 auto-escalation (yield workstream step 6).

K-12 staff directories are increasingly built on JavaScript frameworks
(Finalsite, Edlio, Blackboard, generic React/Vue SPAs). The Tier-1
httpx+trafilatura crawler returns near-empty markdown for these because
the meaningful DOM is built after page load. The Perplexity Model Council
called out that the escalation triggers should be explicit and measurable
rather than opt-in per source.

Heuristic, intentionally conservative:

  - Tier-1 produced little or no text content (under EMPTY_MARKDOWN_THRESHOLD).
  - AND the raw HTML contains one or more SPA framework markers.
  - AND the URL itself scored positive (i.e. looks like a contact-bearing
    path — we don't want to burn Firecrawl credits re-fetching /privacy).
  - AND the per-run escalation budget isn't exhausted.

When all four hold, the orchestrator re-fetches via the self-hosted
Firecrawl/Playwright path so the rendered DOM is what extraction sees.
"""

from __future__ import annotations

from app.services.url_scoring import score_url


# A page that yields fewer than this many characters of extracted text is
# treated as "essentially empty" for escalation purposes. The number is a
# soft heuristic: a real contact page yields hundreds-to-thousands of chars;
# an SPA shell yields a sentence or two of <noscript> content.
EMPTY_MARKDOWN_THRESHOLD = 500

# Per-run cap on how many Tier-1→Tier-2 escalations we'll do automatically.
# Caps Firecrawl credit consumption; tuned upward in config if pilot sources
# turn out to need more headroom.
DEFAULT_MAX_AUTO_ESCALATIONS = 25


# Substrings that indicate the response is a SPA shell. Lower-cased
# comparison; the source HTML is also lower-cased before matching. Most
# of these are CMS-specific markers we've seen on K-12 sites; the generic
# ones (id="root", __NEXT_DATA__, ng-app, etc.) catch the rest.
_SPA_MARKERS = (
    # Generic SPA mount points
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    'id="__next"',
    "__next_data__",
    "window.__initial_state__",
    "window.__nuxt__",
    "ng-app",
    "ng-controller",
    "data-react-helmet",
    "data-reactroot",
    "data-vue-meta",
    # K-12 CMS-specific markers (Finalsite, Edlio, Blackboard Cloud)
    "finalsite",
    "fl-page",
    "edlio",
    "edlio-cms",
    "blackboardcdn",
    "blackboardconnect",
    "schoolwires",
    "campussuite",
    # Squarespace + Webflow + generic builders we see on small private schools
    "squarespace-cdn",
    "static.squarespace.com",
    "webflow.com",
    "wf-form",
)


def looks_like_spa_shell(
    raw_body: bytes | None,
    markdown: str | None,
    *,
    threshold: int = EMPTY_MARKDOWN_THRESHOLD,
) -> bool:
    """True iff the raw HTML smells like an SPA shell AND trafilatura
    produced near-empty text."""
    if not raw_body:
        return False
    if markdown and len(markdown) >= threshold:
        return False
    try:
        body_lower = raw_body.decode("utf-8", errors="replace").lower()
    except Exception:  # noqa: BLE001 - decoding shouldn't raise but be safe
        return False
    return any(marker in body_lower for marker in _SPA_MARKERS)


def should_auto_escalate(
    *,
    url: str,
    raw_body: bytes | None,
    markdown: str | None,
    escalations_used: int,
    max_escalations: int = DEFAULT_MAX_AUTO_ESCALATIONS,
) -> bool:
    """Bundle the escalation decision: SPA shell + high-score URL +
    budget remaining. Returns True only when all conditions hold; callers
    that want to bypass URL-score gating can call looks_like_spa_shell
    directly."""
    if escalations_used >= max_escalations:
        return False
    if score_url(url) <= 0:
        return False
    return looks_like_spa_shell(raw_body, markdown)
