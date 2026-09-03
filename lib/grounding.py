"""Deterministic source and claim-grounding validation.

Research and script agents make the factual decisions; this module only
checks the durable contract they leave behind.  It deliberately performs no
web requests and never tries to infer whether a sentence is true.  A claim is
supported only when the artifact explicitly gives it a source reference that
resolves to a source in the research brief (or to a fully described source
record carried by the script).

The validator accepts the older ``source_ref`` spelling for compatibility,
but production artifacts should use stable claim IDs and ``source_refs``.
Creative, opinion, instruction, and CTA text is reported separately from
factual claims so it cannot accidentally be treated as evidence.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse


GROUNDING_CONTRACT_VERSION = "1.0"


class ClaimStatus(str, Enum):
    """Deterministic status values used in the grounding report."""

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNCERTAIN = "uncertain"
    UNSUPPORTED = "unsupported"
    MISSING_SOURCE = "missing_source"
    CREATIVE = "creative"
    OPINION = "opinion"
    INSTRUCTION = "instruction"


class ClaimType(str, Enum):
    FACTUAL = "factual"
    CREATIVE = "creative"
    OPINION = "opinion"
    INSTRUCTION = "instruction"
    CTA = "cta"
    RHETORICAL = "rhetorical"
    UNKNOWN = "unknown"


class GroundingDecision(str, Enum):
    PASS = "pass"
    REVISE = "revise"
    BLOCK = "block"


HIGH_RISK_TOKENS = frozenset(
    {
        "health",
        "medical",
        "medicine",
        "clinical",
        "disease",
        "diagnosis",
        "treatment",
        "drug",
        "dosage",
        "safety",
        "legal",
        "law",
        "regulation",
        "financial",
        "finance",
        "investment",
        "insurance",
        "tax",
        "security",
    }
)


@dataclass(frozen=True)
class SourceRecord:
    """Normalised source metadata used by a claim."""

    id: str
    title: str
    canonical_locator: str
    accessed_at: str | None
    excerpt_or_note: str | None
    license: str | None
    usage_constraints: str | None
    claim_ids: tuple[str, ...] = ()
    reliability: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "canonical_locator": self.canonical_locator,
            "accessed_at": self.accessed_at,
            "excerpt_or_note": self.excerpt_or_note,
            "license": self.license,
            "usage_constraints": self.usage_constraints,
            "claim_ids": list(self.claim_ids),
            "reliability": self.reliability,
        }


@dataclass(frozen=True)
class ClaimRecord:
    """Normalised claim plus its resolved evidence and risk level."""

    id: str
    text: str
    claim_type: str
    status: str
    source_refs: tuple[str, ...] = ()
    resolved_source_ids: tuple[str, ...] = ()
    risk_level: str = "low"
    section_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "claim_type": self.claim_type,
            "status": self.status,
            "source_refs": list(self.source_refs),
            "resolved_source_ids": list(self.resolved_source_ids),
            "risk_level": self.risk_level,
            "section_id": self.section_id,
        }


@dataclass(frozen=True)
class GroundingReport:
    """Serializable result of :func:`validate_grounding`."""

    valid: bool
    decision: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    sources: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        for claim in self.claims:
            status = str(claim.get("status") or "unknown")
            statuses[status] = statuses.get(status, 0) + 1
        return {
            "version": GROUNDING_CONTRACT_VERSION,
            "valid": self.valid,
            "decision": self.decision,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "source_count": len(self.sources),
            "claim_count": len(self.claims),
            "status_counts": statuses,
            "sources": list(self.sources),
            "claims": list(self.claims),
        }


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _stable_source_id(title: str, locator: str, index: int) -> str:
    if title or locator:
        digest = hashlib.sha256(f"{title}\n{locator}".encode("utf-8")).hexdigest()[:12]
        return f"source_{digest}"
    return f"source_{index + 1}"


def _stable_claim_id(text: str, index: int, section_id: str | None = None) -> str:
    seed = f"{section_id or ''}\n{text}".strip()
    if seed:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"claim_{digest}"
    return f"claim_{index + 1}"


def _valid_locator(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "s3", "gs", "file", "urn"}:
        return bool(parsed.netloc or parsed.path)
    # A project-relative source path is valid provenance for a user-provided
    # or licensed local source, even though it is not a web URL.
    return not any(char.isspace() for char in value)


def _valid_accessed_at(value: str | None) -> bool:
    if not value:
        return False
    try:
        date.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
        except (TypeError, ValueError):
            return False


def _source_locator(source: Mapping[str, Any]) -> str:
    return _string(
        source.get("canonical_locator")
        or source.get("url")
        or source.get("source_url")
        or source.get("locator")
        or source.get("path")
    )


def _source_accessed_at(source: Mapping[str, Any]) -> str | None:
    value = source.get("accessed_at") or source.get("retrieved_at") or source.get("access_date")
    return _string(value) or None


def _source_note(source: Mapping[str, Any]) -> str | None:
    value = (
        source.get("excerpt_or_note")
        or source.get("excerpt")
        or source.get("structured_note")
        or source.get("note")
        or source.get("used_for")
    )
    return _string(value) or None


def _source_records(brief: Mapping[str, Any], script: Mapping[str, Any]) -> tuple[list[SourceRecord], dict[str, str], list[str]]:
    """Build source records and deterministic aliases.

    Aliases include explicit IDs, URL/locator, title, and legacy
    ``data_point_N`` references.  The aliases are internal and never replace
    the canonical source ID in the report.
    """

    raw_sources: list[Mapping[str, Any]] = []
    for container in (brief, script):
        values = container.get("sources") if isinstance(container, Mapping) else None
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            raw_sources.extend(value for value in values if isinstance(value, Mapping))

    records: list[SourceRecord] = []
    aliases: dict[str, str] = {}
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, source in enumerate(raw_sources):
        title = _string(source.get("title") or source.get("name"))
        locator = _source_locator(source)
        explicit_id = _string(source.get("id") or source.get("source_id"))
        source_id = explicit_id or _stable_source_id(title, locator, index)
        if source_id in seen_ids:
            errors.append(f"duplicate source id {source_id!r}")
            source_id = f"{source_id}_{index + 1}"
        seen_ids.add(source_id)
        claim_ids = tuple(
            _string(value)
            for value in (source.get("claim_ids") or source.get("claims") or [])
            if _string(value)
        )
        record = SourceRecord(
            id=source_id,
            title=title,
            canonical_locator=locator,
            accessed_at=_source_accessed_at(source),
            excerpt_or_note=_source_note(source),
            license=_string(source.get("license") or source.get("license_name")) or None,
            usage_constraints=(
                _string(source.get("usage_constraints") or source.get("usage")) or None
            ),
            claim_ids=claim_ids,
            reliability=_string(source.get("reliability") or source.get("credibility")) or None,
        )
        records.append(record)
        aliases[source_id.lower()] = source_id
        for value in (locator, title, source.get("url"), source.get("source_url")):
            alias = _string(value).lower()
            if alias:
                aliases[alias] = source_id

    # A data point is not itself a source, but its stable ID is a common
    # legacy reference. Resolve it to a matching source URL when possible.
    data_points = brief.get("data_points") if isinstance(brief, Mapping) else None
    if isinstance(data_points, Sequence) and not isinstance(data_points, (str, bytes)):
        for index, point in enumerate(data_points):
            if not isinstance(point, Mapping):
                continue
            data_id = _string(point.get("id") or point.get("data_point_id")) or f"data_point_{index + 1}"
            point_locator = _string(point.get("source_url") or point.get("url"))
            resolved = aliases.get(point_locator.lower()) if point_locator else None
            if resolved:
                aliases[data_id.lower()] = resolved
            else:
                # Keep the alias so the claim gets a useful missing-source
                # diagnostic instead of an opaque unknown-reference message.
                aliases[data_id.lower()] = ""

    return records, aliases, errors


def _normalise_refs(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_string(item) for item in value if _string(item))
    return ()


_CREATIVE_TYPES = {
    ClaimType.CREATIVE.value,
    ClaimType.OPINION.value,
    ClaimType.INSTRUCTION.value,
    ClaimType.CTA.value,
    ClaimType.RHETORICAL.value,
    "metaphor",
    "narrative",
}


def classify_claim(claim: Mapping[str, Any]) -> str:
    """Return a stable claim type without judging factual truth."""

    raw = _string(
        claim.get("claim_type")
        or claim.get("type")
        or claim.get("content_type")
        or claim.get("factuality")
    ).lower()
    if raw in _CREATIVE_TYPES:
        if raw in {ClaimType.OPINION.value}:
            return ClaimType.OPINION.value
        if raw in {ClaimType.INSTRUCTION.value, ClaimType.CTA.value}:
            return ClaimType.INSTRUCTION.value
        return ClaimType.CREATIVE.value
    if raw in {ClaimType.FACTUAL.value, "fact", "evidence", "data"}:
        return ClaimType.FACTUAL.value
    # Explicit source references are a strong signal that the author intends
    # a factual claim. Otherwise the production contract remains conservative.
    if _normalise_refs(claim.get("source_refs") or claim.get("source_ref")):
        return ClaimType.FACTUAL.value
    return ClaimType.FACTUAL.value


def _claim_items(script: Mapping[str, Any]) -> list[dict[str, Any]]:
    explicit = script.get("claims")
    items: list[dict[str, Any]] = []
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        for value in explicit:
            if isinstance(value, Mapping):
                items.append(dict(value))
        if items:
            return items

    sections = script.get("sections") or []
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
        return items
    for section_index, section in enumerate(sections):
        if not isinstance(section, Mapping):
            continue
        section_id = _string(section.get("id")) or None
        nested = section.get("claims")
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            for value in nested:
                if isinstance(value, Mapping):
                    item = dict(value)
                    item.setdefault("section_id", section_id)
                    items.append(item)
            continue
        text = _string(section.get("claim") or section.get("text") or section.get("content"))
        if not text:
            continue
        item = dict(section)
        item["text"] = text
        item.setdefault("section_id", section_id)
        item.setdefault("id", section_id or f"section_{section_index + 1}")
        items.append(item)
    return items


def _status(raw: Any) -> str | None:
    value = _string(raw).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "verified": ClaimStatus.SUPPORTED.value,
        "supported_by_source": ClaimStatus.SUPPORTED.value,
        "not_supported": ClaimStatus.UNSUPPORTED.value,
        "no_source": ClaimStatus.MISSING_SOURCE.value,
        "missing": ClaimStatus.MISSING_SOURCE.value,
        "creative": ClaimStatus.CREATIVE.value,
    }
    return aliases.get(value, value or None)


def _risk_level(claim: Mapping[str, Any], topic: str) -> str:
    raw = _string(claim.get("risk_level") or claim.get("risk") or claim.get("criticality")).lower()
    if raw in {"critical", "high"} or claim.get("critical") is True:
        return "high"
    if raw in {"medium", "moderate"}:
        return "medium"
    if raw == "low":
        return "low"
    text = f"{topic} {_string(claim.get('text') or claim.get('claim'))}".lower()
    tokens = set(re.findall(r"[a-z]+", text))
    return "high" if tokens & HIGH_RISK_TOKENS else "low"


def _claim_text(claim: Mapping[str, Any]) -> str:
    return _string(claim.get("text") or claim.get("claim") or claim.get("statement") or claim.get("content"))


def _source_contract_errors(source: SourceRecord) -> list[str]:
    errors: list[str] = []
    if not source.title:
        errors.append(f"source {source.id!r} is missing title")
    if not _valid_locator(source.canonical_locator):
        errors.append(f"source {source.id!r} is missing a valid canonical_locator")
    if not _valid_accessed_at(source.accessed_at):
        errors.append(f"source {source.id!r} is missing a valid accessed_at date")
    if not source.excerpt_or_note:
        errors.append(f"source {source.id!r} is missing excerpt_or_note")
    if not source.license:
        errors.append(f"source {source.id!r} is missing license")
    if not source.usage_constraints:
        errors.append(f"source {source.id!r} is missing usage_constraints")
    return errors


def validate_grounding(
    research_brief: Mapping[str, Any] | None,
    script: Mapping[str, Any] | None,
    *,
    strict: bool = True,
    high_risk_categories: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate source metadata and claim traceability deterministically.

    ``strict`` is the production mode.  In compatibility mode, missing
    source metadata is retained as warnings so older exploratory artifacts can
    still be inspected; unsupported high-risk claims are still visibly
    classified and become blocking when the artifact sets
    ``grounding_contract.required``.
    """

    brief = research_brief if isinstance(research_brief, Mapping) else {}
    script_value = script if isinstance(script, Mapping) else {}
    contract = script_value.get("grounding_contract") or brief.get("grounding_contract") or {}
    enforce = bool(strict or (isinstance(contract, Mapping) and contract.get("required") is True))
    topic = _string(brief.get("topic") or script_value.get("topic"))
    configured_high_risk = {
        _slug(value)
        for value in (high_risk_categories or ())
        if _slug(_string(value))
    }

    records, aliases, source_errors = _source_records(brief, script_value)
    errors: list[str] = []
    warnings: list[str] = []
    if enforce:
        errors.extend(source_errors)
        for record in records:
            errors.extend(_source_contract_errors(record))
    else:
        warnings.extend(source_errors)
        for record in records:
            warnings.extend(_source_contract_errors(record))

    claims: list[ClaimRecord] = []
    seen_claim_ids: set[str] = set()
    raw_claims = _claim_items(script_value)
    for index, raw_claim in enumerate(raw_claims):
        text = _claim_text(raw_claim)
        section_id = _string(raw_claim.get("section_id") or raw_claim.get("section")) or None
        claim_id = _string(raw_claim.get("id") or raw_claim.get("claim_id")) or _stable_claim_id(text, index, section_id)
        if claim_id in seen_claim_ids:
            errors.append(f"duplicate claim id {claim_id!r}")
            claim_id = f"{claim_id}_{index + 1}"
        seen_claim_ids.add(claim_id)
        claim_type = classify_claim(raw_claim)
        refs = _normalise_refs(raw_claim.get("source_refs") or raw_claim.get("source_ref"))
        resolved: list[str] = []
        unresolved: list[str] = []
        for ref in refs:
            source_id = aliases.get(ref.lower())
            if source_id:
                resolved.append(source_id)
            else:
                unresolved.append(ref)

        explicit_status = _status(
            raw_claim.get("status")
            or raw_claim.get("verification_status")
            or raw_claim.get("factuality_status")
        )
        if claim_type == ClaimType.CREATIVE.value:
            status = ClaimStatus.CREATIVE.value
        elif claim_type == ClaimType.OPINION.value:
            status = ClaimStatus.OPINION.value
        elif claim_type == ClaimType.INSTRUCTION.value:
            status = ClaimStatus.INSTRUCTION.value
        elif explicit_status in {
            ClaimStatus.CONTRADICTED.value,
            ClaimStatus.UNCERTAIN.value,
            ClaimStatus.UNSUPPORTED.value,
            ClaimStatus.MISSING_SOURCE.value,
        }:
            status = explicit_status
        elif not refs or unresolved:
            status = ClaimStatus.MISSING_SOURCE.value
        else:
            status = ClaimStatus.SUPPORTED.value

        risk_level = _risk_level(raw_claim, topic)
        if configured_high_risk:
            categories = {
                _slug(value)
                for value in (
                    raw_claim.get("category"),
                    raw_claim.get("risk_category"),
                    raw_claim.get("domain"),
                )
                if _slug(_string(value))
            }
            if categories & configured_high_risk:
                risk_level = "high"

        record = ClaimRecord(
            id=claim_id,
            text=text,
            claim_type=claim_type,
            status=status,
            source_refs=refs,
            resolved_source_ids=tuple(dict.fromkeys(resolved)),
            risk_level=risk_level,
            section_id=section_id,
        )
        claims.append(record)

        if status in {ClaimStatus.CREATIVE.value, ClaimStatus.OPINION.value, ClaimStatus.INSTRUCTION.value}:
            continue
        if not text:
            message = f"claim {claim_id!r} is missing text"
            (errors if enforce else warnings).append(message)
        if unresolved:
            message = f"claim {claim_id!r} references unknown source(s): {', '.join(unresolved)}"
            (errors if enforce else warnings).append(message)
        if status in {
            ClaimStatus.CONTRADICTED.value,
            ClaimStatus.UNCERTAIN.value,
            ClaimStatus.UNSUPPORTED.value,
            ClaimStatus.MISSING_SOURCE.value,
        }:
            message = f"claim {claim_id!r} is {status}"
            blocking = risk_level == "high" and status != ClaimStatus.SUPPORTED.value
            if enforce or blocking:
                errors.append(message)
            else:
                warnings.append(message)

    high_risk_failures = [
        claim.id
        for claim in claims
        if claim.risk_level == "high"
        and claim.status
        in {
            ClaimStatus.CONTRADICTED.value,
            ClaimStatus.UNCERTAIN.value,
            ClaimStatus.UNSUPPORTED.value,
            ClaimStatus.MISSING_SOURCE.value,
        }
    ]
    if high_risk_failures:
        decision = GroundingDecision.BLOCK.value
    elif errors:
        decision = GroundingDecision.REVISE.value
    else:
        decision = GroundingDecision.PASS.value

    report = GroundingReport(
        valid=not errors,
        decision=decision,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        sources=tuple(record.as_dict() for record in records),
        claims=tuple(record.as_dict() for record in claims),
    )
    return report.as_dict()


def validate_claim_grounding(
    research_brief: Mapping[str, Any] | None,
    script: Mapping[str, Any] | None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility alias for callers that use the longer name."""

    return validate_grounding(research_brief, script, **kwargs)


def validate_source_record(source: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one source record without requiring a script claim ledger."""

    value = source if isinstance(source, Mapping) else {}
    records, _aliases, source_errors = _source_records({"sources": [value]}, {})
    if not records:
        source_errors.append("source record must be an object")
    elif records:
        source_errors.extend(_source_contract_errors(records[0]))
    return {
        "valid": not source_errors,
        "errors": list(dict.fromkeys(source_errors)),
        "source": records[0].as_dict() if records else None,
    }


class GroundingValidator:
    """Reusable validator object for stage handlers and integration tests."""

    def __init__(
        self,
        *,
        strict: bool = True,
        high_risk_categories: Iterable[str] | None = None,
    ) -> None:
        self.strict = bool(strict)
        self.high_risk_categories = tuple(high_risk_categories or ())

    def validate(
        self,
        research_brief: Mapping[str, Any] | None,
        script: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return validate_grounding(
            research_brief,
            script,
            strict=self.strict,
            high_risk_categories=self.high_risk_categories,
        )


def build_grounding_report(
    research_brief: Mapping[str, Any] | None,
    script: Mapping[str, Any] | None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build the same serialisable report as :func:`validate_grounding`."""

    return validate_grounding(research_brief, script, **kwargs)


__all__ = [
    "ClaimRecord",
    "ClaimStatus",
    "ClaimType",
    "GROUNDING_CONTRACT_VERSION",
    "GroundingDecision",
    "GroundingReport",
    "GroundingValidator",
    "SourceRecord",
    "build_grounding_report",
    "classify_claim",
    "validate_claim_grounding",
    "validate_grounding",
    "validate_source_record",
]
