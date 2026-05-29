"""Contact-form detection.

Many K-12 sites publish no individual email — they route everything
through a "Contact Us" form. When the extractor finds no email on a page,
it's useful to record whether the page at least HAS a contact form, so
the operator knows "reachable via form" rather than "dead end."

Detection is regex-based (not a full DOM parse) to stay cheap on the hot
crawl path. Heuristic: a <form> element whose attributes or fields look
like a contact form — an email input, a message textarea, or a
contact-y action/id/class. We accept some false positives (a newsletter
signup is also a "form with an email field"); the signal is "there's a
way to reach someone here," which a newsletter box weakly satisfies too.
"""

from __future__ import annotations

import re

_FORM_BLOCK_RE = re.compile(r"<form\b[^>]*>.*?</form>", re.IGNORECASE | re.DOTALL)
_EMAIL_INPUT_RE = re.compile(
    r'<input\b[^>]*\btype\s*=\s*["\']?email["\']?', re.IGNORECASE
)
_NAMED_EMAIL_INPUT_RE = re.compile(
    r'<input\b[^>]*\bname\s*=\s*["\'][^"\']*e-?mail[^"\']*["\']', re.IGNORECASE
)
_TEXTAREA_RE = re.compile(r"<textarea\b", re.IGNORECASE)
_CONTACT_HINT_RE = re.compile(
    r"(contact|get[\s_-]?in[\s_-]?touch|reach[\s_-]?us|message[\s_-]?us|inquir|enquir)",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(r'\baction\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def has_contact_form(html: str | None) -> bool:
    """True if the HTML contains at least one form that looks like a way to
    contact the org (contact form, inquiry form, or message form)."""
    if not html or "<form" not in html.lower():
        return False
    for block in _FORM_BLOCK_RE.findall(html):
        if _form_is_contactish(block):
            return True
    return False


def _form_is_contactish(form_html: str) -> bool:
    has_email = bool(
        _EMAIL_INPUT_RE.search(form_html) or _NAMED_EMAIL_INPUT_RE.search(form_html)
    )
    has_message = bool(_TEXTAREA_RE.search(form_html))
    has_hint = bool(_CONTACT_HINT_RE.search(form_html))
    # A message box is the strongest single signal (newsletter signups
    # rarely have a textarea). Email-input + a contact hint also counts.
    if has_message and (has_email or has_hint):
        return True
    if has_message and not has_email:
        # A bare textarea form is still likely a contact/feedback form.
        return True
    if has_email and has_hint:
        return True
    return False


def detect_contact_form_actions(html: str | None) -> list[str]:
    """Return the `action` targets of contact-ish forms (deduped, order-
    preserving). Useful for telling the operator where the form submits —
    a mailto: action even reveals an address. Relative actions are returned
    as-is; the caller can resolve against the page URL if needed."""
    if not html:
        return []
    out: dict[str, None] = {}
    for block in _FORM_BLOCK_RE.findall(html):
        if not _form_is_contactish(block):
            continue
        m = _ACTION_RE.search(block)
        if m:
            out.setdefault(m.group(1).strip(), None)
    return list(out.keys())
