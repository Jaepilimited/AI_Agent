"""Trust-state helpers for retrieved knowledge-wiki facts.

The wiki stores facts mined from earlier assistant answers.  They are useful
memory, but they are not all equally safe to assert.  This module converts the
existing review, validation, conflict and freshness metadata into a compact
trust state that every retrieval path can handle consistently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


TRUSTED = "trusted"
PARTLY_TRUSTED = "partly_trusted"
DISPUTED = "disputed"
STALE_RISK = "stale_risk"

TRUST_LABELS = {
    TRUSTED: "검증됨",
    PARTLY_TRUSTED: "검증 대기",
    DISPUTED: "충돌/검토 필요",
    STALE_RISK: "최신성 주의",
}

TRUST_ORDER = (TRUSTED, PARTLY_TRUSTED, DISPUTED, STALE_RISK)

# A validated permanent rule can silently become outdated.  Historical facts
# with an explicit period are snapshots, so age alone does not make them stale.
PERMANENT_STALE_DAYS = 180
MIN_TRUSTED_CONFIDENCE = 0.6


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def classify_fact_trust(
    fact: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    """Classify one fact without inventing provenance that is not stored.

    Priority matters: an unresolved conflict is disputed even if the row was
    validated previously.  A resolved conflict may become trusted again.
    """
    review_status = str(fact.get("review_status") or "none")
    conflict_id = fact.get("conflict_with_id")
    if review_status == "needs_review" or (conflict_id and review_status != "resolved"):
        return DISPUTED

    if str(fact.get("status") or "pending") != "active":
        return PARTLY_TRUSTED

    validated_at = _utc(fact.get("validated_at"))
    if validated_at is None or float(fact.get("confidence") or 0) < MIN_TRUSTED_CONFIDENCE:
        return PARTLY_TRUSTED

    period = str(fact.get("period") or "").strip().lower()
    is_permanent = not period or period == "permanent"
    if is_permanent:
        current = _utc(now) or datetime.now(timezone.utc)
        if (current - validated_at).days > PERMANENT_STALE_DAYS:
            return STALE_RISK

    return TRUSTED


def annotate_fact_trust(fact: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(fact)
    row["trust_state"] = classify_fact_trust(row)
    return row


def trust_label(fact: Mapping[str, Any]) -> str:
    state = str(fact.get("trust_state") or classify_fact_trust(fact))
    return TRUST_LABELS.get(state, TRUST_LABELS[PARTLY_TRUSTED])

