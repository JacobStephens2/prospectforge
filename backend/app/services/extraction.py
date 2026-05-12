"""LLM-driven extraction (spec §7).

Inputs a SourcePage's content + the deterministic regex pre-pass; outputs
validated, evidence-verified ExtractedOrganization + ExtractedContact rows.

Design choices:

- **Tool-use with forced output** — `tool_choice={"type": "tool", "name": "submit_extraction"}`
  + `disable_parallel_tool_use=True` gets one tool_use block per call,
  validated against a Pydantic schema. We don't use the SDK's tool runner
  because we need to gate every call on the run's budget before it goes
  out, retry on validation errors, and update cost atomically — control
  the runner doesn't expose.

- **Prompt caching** — the long system prompt (extraction rules, deny-list,
  segment vocabulary, JSON shape) is the same across thousands of calls in
  a run. We mark it `cache_control: ephemeral` so Anthropic caches the
  prefix and we pay ~0.1× per repeated call instead of 1×. Verify with
  `usage.cache_read_input_tokens` > 0 after the second call onward.

- **Sonnet 4.5 today, Sonnet 4.6 ready** — the model is `settings.anthropic_model_default`,
  defaulting to claude-sonnet-4-5 per spec §13. Sonnet 4.5 doesn't support
  the structured-outputs API (`output_config.format`) so we go through tool
  use. The same code works on Sonnet 4.6 if you flip the env var — Sonnet
  4.6 just becomes a faster/cheaper path that also happens to support
  structured outputs natively. We'd consider migrating to `messages.parse()`
  then; for now, the tool path is portable across both.

- **Retry on validation failure** — invalid JSON or schema mismatch gets one
  retry with the validation error appended to the user prompt, per spec §7.4.
  Second failure → extraction_status='failed', flag for human review.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import anthropic
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import settings
from app.services.budget import (
    OverBudget,
    UsageCost,
    check_budget_before_call,
    cost_from_usage,
    record_call_cost,
)
from app.services.evidence import EvidenceCheck, verify_excerpt
from app.services.regex_extract import PrePass, extract_signals

log = logging.getLogger(__name__)

# Per spec §5.3: any title containing one of these phrases is rejected
# outright. Case-insensitive whole-substring match.
_TITLE_DENYLIST = (
    "student",
    "parent",
    "guardian",
    "pto",
    "pta",
    "class rep",
    "class representative",
    "senior rep",
    "junior rep",
)


# ---------- Pydantic models (the tool's input schema) ----------


class ExtractedContact(BaseModel):
    """One person extracted from the page."""

    name: str = Field(..., min_length=2, description="Person's full name as it appears on the page.")
    title: str | None = Field(
        None,
        description=(
            "Job title or role (e.g. 'Band Director'). Omit if not stated. "
            "MUST NOT contain Student, Parent, Guardian, PTO, PTA, or Class Rep — "
            "those rows belong to deny-listed roles and should never be returned."
        ),
    )
    email: str | None = Field(
        None,
        description=(
            "Email address — ONLY if it literally appears on the page, attributed to "
            "this person. Never invent. Prefer addresses from the pre-extracted list."
        ),
    )
    phone: str | None = Field(
        None,
        description="Phone number — ONLY if it literally appears on the page.",
    )
    segment_tag: str | None = Field(
        None,
        description=(
            "Educational segment label, e.g. 'K-12 band', 'K-12 choir', 'K-12 theater', "
            "'K-12 global studies', 'university travel'. Omit if unclear."
        ),
    )
    evidence_excerpt: str = Field(
        ...,
        min_length=20,
        description=(
            "EXACT quoted text from the page (≥20 chars) that supports this extraction. "
            "Will be verified against source — fabricating this rejects the row."
        ),
    )

    @field_validator("title")
    @classmethod
    def _no_denylisted_title(cls, v: str | None) -> str | None:
        if v is None:
            return v
        lower = v.lower()
        for bad in _TITLE_DENYLIST:
            if bad in lower:
                raise ValueError(f"title contains deny-listed token: {bad!r}")
        return v


class ExtractedOrganization(BaseModel):
    """One organization (school, district, agency) with its contacts."""

    name: str = Field(..., min_length=2, description="Organization name as on page.")
    domain: str | None = Field(None, description="Web domain (e.g. 'springisd.org').")
    state: str | None = Field(
        None,
        max_length=2,
        description="US state two-letter code (e.g. 'TX'). Omit if not stated.",
    )
    school_type: str | None = Field(
        None, description="Public / Private / Charter / Independent / etc."
    )
    contacts: list[ExtractedContact] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """Top-level tool input."""

    organizations: list[ExtractedOrganization] = Field(default_factory=list)


# ---------- post-processing types ----------


@dataclass
class VerifiedContact:
    """An ExtractedContact after evidence + deny-list checks."""

    contact: ExtractedContact
    evidence: EvidenceCheck
    organization: ExtractedOrganization
    keep: bool                # False if denylisted or evidence failed
    keep_reason: str          # human-readable disposition


@dataclass
class ExtractionRunResult:
    """Everything the extraction worker writes per page."""

    organizations: list[ExtractedOrganization]
    verified_contacts: list[VerifiedContact]
    cost: UsageCost
    prompt_version: tuple[str, str]   # (name, version)
    retried: bool
    error: str | None = None


# ---------- prompt construction ----------


_SYSTEM_PROMPT = """\
You are an extraction agent for Acme Travel (Acme), a US tour operator
that serves educational travel for schools and group leaders. Your job is to read one
public web page and extract organizations (schools, districts, agencies) and the people
inside them who might lead an educational trip.

Acme's sales team uses your output to decide who to contact. Wrong information embarrasses
sales. Invented information is worse than no information.

Rules — non-negotiable:

1. **Only extract what the page literally says.** Never infer, never paraphrase.
   If the page says "Band Director Jane Smith — jsmith@school.org", that's an extraction.
   If the page lists "Jane Smith" and separately mentions a band program, you may NOT
   call her the band director.

2. **Every contact MUST carry an `evidence_excerpt`** that is an EXACT quoted snippet
   from the page (≥ 20 characters) supporting your extraction. The excerpt will be
   verified character-by-character against the source. Excerpts that do not appear in
   the source are rejected — never make them up to satisfy the schema.

3. **Email and phone are optional and must literally appear on the page.** Prefer the
   pre-extracted addresses passed in the user prompt — those came from a deterministic
   regex pass over the same source text, so they are guaranteed present. NEVER invent
   an email pattern (e.g. `firstname.lastname@school.org`).

4. **Deny-listed roles** — reject any person whose title contains any of:
   Student, Parent, Guardian, PTO, PTA, Class Rep, Class Representative, Senior Rep,
   Junior Rep. These are families and student leaders, not decision-makers.

5. **Segment tags** must come from this vocabulary or you omit them:
   `K-12 band`, `K-12 choir`, `K-12 theater`, `K-12 global studies`,
   `K-12 social studies`, `K-12 music`, `K-12 performing arts`, `K-12 world language`,
   `university travel`, `independent school travel`. Omit if unclear.

6. **State** is the US 2-letter code (TX, NY, CA). Omit if the page doesn't say.

7. **Return zero organizations or contacts if the page has nothing extractable.** It is
   correct and expected to return `{"organizations": []}`. Empty output is a fine result.

Output discipline:

- Call the `submit_extraction` tool exactly once with your full result.
- Do not narrate, do not apologize, do not return text in addition to the tool call.
- If the page is in another language, transliterate names but only extract if the
  person is associated with US educational travel.
- Group contacts under the organization they belong to. A contact with no clear
  organization can be placed under an organization named after the source.
"""


def _build_user_prompt(
    *,
    source_url: str,
    page_text: str,
    pre_pass: PrePass,
    validation_error: str | None,
) -> str:
    """User-side prompt — varies per call, NOT cached."""
    pre_emails = (
        "(none found)" if not pre_pass.emails else "\n".join(f"  - {e}" for e in pre_pass.emails)
    )
    pre_phones = (
        "(none found)" if not pre_pass.phones else "\n".join(f"  - {p}" for p in pre_pass.phones)
    )
    retry_note = ""
    if validation_error:
        retry_note = (
            "\n\n[RETRY] Your previous response failed validation:\n"
            f"{validation_error}\n"
            "Fix the issue and submit again. Do NOT invent fields to satisfy the schema."
        )
    return (
        f"Source URL: {source_url}\n\n"
        f"Pre-extracted emails on this page (from deterministic regex):\n{pre_emails}\n\n"
        f"Pre-extracted phones on this page:\n{pre_phones}\n\n"
        "--- BEGIN PAGE CONTENT ---\n"
        f"{page_text}\n"
        "--- END PAGE CONTENT ---\n\n"
        "Extract organizations and contacts as instructed in the system prompt. "
        "Call `submit_extraction` exactly once."
        f"{retry_note}"
    )


# ---------- the call ----------


def _get_client() -> anthropic.Anthropic:
    """One client per process. The SDK is thread-safe and httpx-pooled."""
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _extract_tool_input(message: anthropic.types.Message) -> dict[str, Any] | None:
    """Pull the single forced tool_use block's `input`."""
    for block in message.content:
        if block.type == "tool_use":
            return block.input  # already a dict, parsed by SDK
    return None


def extract_from_page(
    *,
    db,
    run_id: uuid.UUID,
    source_url: str,
    page_text: str,
    prompt_version_name: str = "extract",
    prompt_version_value: str = "v0-stub",
    client: anthropic.Anthropic | None = None,
) -> ExtractionRunResult:
    """One Anthropic call per page. Validates, verifies evidence, returns rows.

    Budget cap is enforced before the call; cost is recorded after. Raises
    OverBudget if the run is already at/past its cap.
    """
    client = client or _get_client()
    pre_pass = extract_signals(page_text)

    # Schema: Pydantic → JSON Schema for the tool definition.
    tool_schema = ExtractionResult.model_json_schema()
    tool = {
        "name": "submit_extraction",
        "description": (
            "Submit the structured extraction result for this page. "
            "Call exactly once. The schema requires every Contact to have a name "
            "and an evidence_excerpt of at least 20 characters."
        ),
        "input_schema": tool_schema,
    }

    def _one_call(validation_error: str | None) -> tuple[ExtractionResult | None, UsageCost, str | None]:
        check_budget_before_call(db, run_id, estimated_cents=1)

        user_prompt = _build_user_prompt(
            source_url=source_url,
            page_text=page_text,
            pre_pass=pre_pass,
            validation_error=validation_error,
        )

        response = client.messages.create(
            model=settings.anthropic_model_default,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    # Cache the long, stable system prompt. ~0.1× cost on
                    # every subsequent call within the 5-min window — the
                    # entire economic justification for keeping a fat
                    # system prompt.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[tool],
            tool_choice={
                "type": "tool",
                "name": "submit_extraction",
                "disable_parallel_tool_use": True,
            },
            messages=[{"role": "user", "content": user_prompt}],
        )

        cost = cost_from_usage(response.usage)
        record_call_cost(db, run_id, cost)

        raw = _extract_tool_input(response)
        if raw is None:
            return None, cost, "model did not call the submit_extraction tool"

        try:
            parsed = ExtractionResult.model_validate(raw)
            return parsed, cost, None
        except ValidationError as exc:
            return None, cost, exc.json(indent=2)

    # First attempt
    parsed, cost1, err1 = _one_call(validation_error=None)
    retried = False
    total_cost = cost1

    if parsed is None:
        # One retry with the validation error in-band (spec §7.4).
        retried = True
        try:
            parsed, cost2, err2 = _one_call(validation_error=err1)
            total_cost = UsageCost(
                input_cents=cost1.input_cents + cost2.input_cents,
                cache_write_cents=cost1.cache_write_cents + cost2.cache_write_cents,
                cache_read_cents=cost1.cache_read_cents + cost2.cache_read_cents,
                output_cents=cost1.output_cents + cost2.output_cents,
                total_cents=cost1.total_cents + cost2.total_cents,
            )
            if parsed is None:
                return ExtractionRunResult(
                    organizations=[],
                    verified_contacts=[],
                    cost=total_cost,
                    prompt_version=(prompt_version_name, prompt_version_value),
                    retried=True,
                    error=err2,
                )
        except OverBudget as exc:
            return ExtractionRunResult(
                organizations=[],
                verified_contacts=[],
                cost=total_cost,
                prompt_version=(prompt_version_name, prompt_version_value),
                retried=True,
                error=f"retry blocked by budget: {exc}",
            )

    # Post-processing: evidence verification + deny-list (Pydantic caught most,
    # but we re-check in case a future schema is laxer).
    verified: list[VerifiedContact] = []
    for org in parsed.organizations:
        for contact in org.contacts:
            evidence = verify_excerpt(contact.evidence_excerpt, page_text)
            keep = evidence.status in ("verified", "fuzzy")
            reason = (
                f"evidence {evidence.status}"
                + (f" (score={evidence.score:.2f})" if evidence.score is not None else "")
            )
            if not keep:
                reason = f"rejected: {reason}"
            verified.append(
                VerifiedContact(
                    contact=contact,
                    evidence=evidence,
                    organization=org,
                    keep=keep,
                    keep_reason=reason,
                )
            )

    return ExtractionRunResult(
        organizations=list(parsed.organizations),
        verified_contacts=verified,
        cost=total_cost,
        prompt_version=(prompt_version_name, prompt_version_value),
        retried=retried,
        error=None,
    )
