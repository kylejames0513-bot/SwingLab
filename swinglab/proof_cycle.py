"""Evidence-first comparison primitives for a CaddieInsight Proof Cycle.

This module intentionally has no database, web, report, Shopify, or pipeline
dependencies.  It turns an already-selected Caddie Brief issue into a durable
comparison target, then evaluates a matched re-film without inventing a result
when the evidence is weak or the capture conditions do not match.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

from .caddie_brief import warning_requires_refilm
from .coaching import IssueCard
from .metrics import (
    ANGLE_DTL,
    ANGLE_FACE_ON,
    FACE_ON_ONLY_FIELDS,
    NUMERIC_FIELDS,
    SwingMetrics,
    finite_float,
)


Aggregation = Literal["mean", "std", "worst"]
ProofVerdict = Literal[
    "no_baseline",
    "not_comparable",
    "inconclusive",
    "early_signal",
    "improved",
    "holding",
    "needs_attention",
]


def _normalise_context(value: str | None) -> str:
    """Return a stable comparison key without accepting a missing value."""

    return value.strip().casefold() if isinstance(value, str) else ""


def _finite_mean(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    try:
        mean = math.fsum(value / len(values) for value in values)
    except (OverflowError, TypeError, ValueError):
        return None
    return mean if math.isfinite(mean) else None


def _finite_std(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    try:
        std = statistics.pstdev(values)
    except (OverflowError, TypeError, ValueError):
        return None
    return std if math.isfinite(std) else None


def _coerce_values(values: Iterable[object] | object) -> tuple[float | None, ...]:
    """Keep one value per swing while making malformed legacy data unreadable."""

    if isinstance(values, (str, bytes)):
        return (None,)
    try:
        raw_values = tuple(values)  # type: ignore[arg-type]
    except TypeError:
        raw_values = (values,)
    return tuple(finite_float(value) for value in raw_values)


@dataclass(frozen=True)
class SessionContext:
    """The capture facts that must match before a mechanical comparison."""

    session_id: str
    user_id: str
    club: str | None
    hand: str | None
    angle: str | None


@dataclass(frozen=True)
class ProofMeasurement:
    """A compact, durable measurement snapshot for one target metric."""

    metric: str
    aggregation: Aggregation
    value: float | None
    mean: float | None
    std: float | None
    readable_swings: int

    def __post_init__(self) -> None:
        if not isinstance(self.metric, str) or not self.metric:
            raise ValueError("metric must be a non-empty string")
        if self.aggregation not in ("mean", "std", "worst"):
            raise ValueError(f"Unsupported aggregation: {self.aggregation}")
        if (
            not isinstance(self.readable_swings, int)
            or isinstance(self.readable_swings, bool)
            or self.readable_swings < 0
        ):
            raise ValueError("readable_swings must be a non-negative integer")
        object.__setattr__(self, "value", finite_float(self.value))
        object.__setattr__(self, "mean", finite_float(self.mean))
        object.__setattr__(self, "std", finite_float(self.std))
        if not self.readable_swings:
            object.__setattr__(self, "value", None)
            object.__setattr__(self, "mean", None)
            object.__setattr__(self, "std", None)

    @classmethod
    def from_values(
        cls,
        metric: str,
        values: Iterable[object] | object,
        *,
        aggregation: Aggregation,
        worse_direction: str,
    ) -> "ProofMeasurement":
        """Summarise one metric without trusting NaN, infinity, or bad JSON."""

        if aggregation not in ("mean", "std", "worst"):
            raise ValueError(f"Unsupported aggregation: {aggregation}")
        if worse_direction not in ("higher", "lower"):
            raise ValueError(f"Unsupported worse direction: {worse_direction}")

        readable = tuple(
            value for value in _coerce_values(values) if value is not None
        )
        mean = _finite_mean(readable)
        std = _finite_std(readable)
        if aggregation == "mean":
            value = mean
        elif aggregation == "std":
            value = std
        elif not readable:
            value = None
        elif worse_direction == "higher":
            value = max(readable)
        else:
            value = min(readable)
        return cls(
            metric=metric,
            aggregation=aggregation,
            value=value,
            mean=mean,
            std=std,
            readable_swings=len(readable),
        )


@dataclass(frozen=True)
class ProofSession:
    """The smallest immutable session snapshot needed to test a re-film.

    ``metrics`` holds one tuple per numeric metric, with one entry per swing.
    Invalid values remain ``None`` so a comparison can fail closed on too few
    readable swings instead of silently dropping an entire session.
    """

    session_id: str
    user_id: str
    club: str | None
    hand: str | None
    angle: str | None
    metrics: Mapping[str, Iterable[object] | object]
    completed: bool = True
    coaching_eligible: bool = True
    warning: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, Mapping):
            raise TypeError("metrics must be a mapping of metric names to swing values")
        cleaned = {
            metric: _coerce_values(values)
            for metric, values in self.metrics.items()
            if isinstance(metric, str)
        }
        object.__setattr__(self, "metrics", MappingProxyType(cleaned))
        if self.warning is not None and not isinstance(self.warning, str):
            raise TypeError("warning must be a string or None")

    @property
    def context(self) -> SessionContext:
        return SessionContext(
            session_id=self.session_id,
            user_id=self.user_id,
            club=self.club,
            hand=self.hand,
            angle=self.angle,
        )

    @classmethod
    def from_swing_metrics(
        cls,
        *,
        session_id: str,
        user_id: str,
        club: str | None,
        hand: str | None,
        angle: str | None,
        swings: Iterable[SwingMetrics],
        completed: bool = True,
        coaching_eligible: bool = True,
        warning: str | None = None,
    ) -> "ProofSession":
        """Adapt in-memory pipeline metrics without adding a pipeline dependency."""

        rows = tuple(swings)
        return cls(
            session_id=session_id,
            user_id=user_id,
            club=club,
            hand=hand,
            angle=angle,
            metrics={
                metric: tuple(getattr(row, metric, None) for row in rows)
                for metric in NUMERIC_FIELDS
            },
            completed=completed,
            coaching_eligible=coaching_eligible,
            warning=warning,
        )

    def measurement_for(
        self, metric: str, *, aggregation: Aggregation, worse_direction: str
    ) -> ProofMeasurement:
        return ProofMeasurement.from_values(
            metric,
            self.metrics.get(metric, ()),
            aggregation=aggregation,
            worse_direction=worse_direction,
        )


@dataclass(frozen=True)
class ProofRefilm:
    """A durable, provenance-preserving snapshot of one candidate re-film.

    Later PRs can persist this small object beside a report without retaining
    every raw swing value.  Its context and quality fields are deliberately
    retained so a prior re-film cannot be counted just because its numeric
    shape happens to match the target.
    """

    context: SessionContext
    measurement: ProofMeasurement
    completed: bool
    coaching_eligible: bool
    warning: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.context, SessionContext):
            raise TypeError("context must be a SessionContext")
        if not isinstance(self.measurement, ProofMeasurement):
            raise TypeError("measurement must be a ProofMeasurement")
        if self.warning is not None and not isinstance(self.warning, str):
            raise TypeError("warning must be a string or None")

    @classmethod
    def from_session(
        cls, target: "ProofTarget", session: ProofSession
    ) -> "ProofRefilm":
        return cls(
            context=session.context,
            measurement=session.measurement_for(
                target.metric,
                aggregation=target.aggregation,
                worse_direction=target.worse_direction,
            ),
            completed=session.completed,
            coaching_eligible=session.coaching_eligible,
            warning=session.warning,
        )


@dataclass(frozen=True)
class ProofTarget:
    """Immutable baseline issue snapshot; never reconstruct it from prose."""

    source_flag: str
    metric: str
    display_name: str
    unit: str
    worse_direction: str
    aggregation: Aggregation
    benchmark_value: float | None
    benchmark_text: str
    drill_ids: tuple[str, ...]
    drill_names: tuple[str, ...]
    baseline_context: SessionContext
    baseline: ProofMeasurement
    baseline_completed: bool
    baseline_coaching_eligible: bool
    baseline_warning: str | None
    rule_version: int = 1

    @classmethod
    def from_issue_card(
        cls, baseline_session: ProofSession, card: IssueCard
    ) -> "ProofTarget":
        """Snapshot the exact metric Caddie Brief selected for the baseline."""

        if card.metric not in NUMERIC_FIELDS:
            raise ValueError(f"Unsupported Proof Cycle metric: {card.metric}")
        if card.worse_direction not in ("higher", "lower"):
            raise ValueError(
                f"Unsupported Proof Cycle direction: {card.worse_direction}"
            )
        aggregation = _aggregation_for_label(card.session_label)
        card_values = _coerce_values(card.per_swing)
        session_values = baseline_session.metrics.get(card.metric, ())
        if card_values != session_values:
            raise ValueError(
                "IssueCard evidence does not match the supplied baseline session"
            )
        baseline = baseline_session.measurement_for(
            card.metric,
            aggregation=aggregation,
            worse_direction=card.worse_direction,
        )
        return cls(
            source_flag=card.flag,
            metric=card.metric,
            display_name=card.display_name,
            unit=card.unit,
            worse_direction=card.worse_direction,
            aggregation=aggregation,
            benchmark_value=card.benchmark_value,
            benchmark_text=card.benchmark_text,
            drill_ids=card.drill_ids,
            drill_names=card.drill_names,
            baseline_context=baseline_session.context,
            baseline=baseline,
            baseline_completed=baseline_session.completed,
            baseline_coaching_eligible=baseline_session.coaching_eligible,
            baseline_warning=baseline_session.warning,
        )


def _aggregation_for_label(label: str) -> Aggregation:
    if label == "session mean":
        return "mean"
    if label == "std dev across swings":
        return "std"
    if label == "worst swing":
        return "worst"
    raise ValueError(f"Unsupported IssueCard session label: {label}")


@dataclass(frozen=True)
class ComparisonConfidence:
    """Machine-readable reasons why a result is or is not trustworthy."""

    hard_failures: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return not self.hard_failures


@dataclass(frozen=True)
class ProofComparison:
    """One evidence verdict, ready for a future report or persisted sidecar."""

    verdict: ProofVerdict
    confidence: ComparisonConfidence
    current: ProofRefilm | None
    prior_refilms: tuple[ProofRefilm, ...]
    minimum_detectable_effect: float | None
    maximum_refilm_spread: float | None
    directional_change: float | None

    @property
    def accepted_refilm_count(self) -> int:
        if self.verdict in ("no_baseline", "not_comparable"):
            return 0
        return len(self.prior_refilms) + (1 if self.current is not None else 0)


def comparison_confidence(
    target: ProofTarget,
    current_session: ProofSession,
    *,
    minimum_readable_swings: int = 3,
) -> tuple[ComparisonConfidence, ProofRefilm | None]:
    """Validate the current session before it can alter a Proof Cycle verdict."""

    _validate_minimum_readable_swings(minimum_readable_swings)

    baseline_failures = _baseline_failures(target, minimum_readable_swings)
    if baseline_failures:
        return ComparisonConfidence(hard_failures=baseline_failures), None

    refilm = ProofRefilm.from_session(target, current_session)
    failures = _refilm_failures(
        target,
        refilm,
        minimum_readable_swings=minimum_readable_swings,
        prefix="session",
    )
    return ComparisonConfidence(hard_failures=failures), refilm


def compare_refilm(
    target: ProofTarget,
    current_session: ProofSession,
    *,
    prior_refilms: Iterable[ProofRefilm] = (),
    noise_floor: float,
    minimum_readable_swings: int = 3,
    minimum_refilms_for_improved: int = 2,
    maximum_refilm_spread: float | None = None,
) -> ProofComparison:
    """Return a conservative, explainable verdict for one matched re-film.

    ``prior_refilms`` must retain the accepted session's context, quality, and
    measurement snapshot.  The function validates every one again before it
    can turn an early signal into an improvement.
    ``noise_floor`` is deliberately required: product policy, not this domain
    model, chooses the metric-specific minimum useful movement.
    ``maximum_refilm_spread`` bounds the full range of confirming effects;
    when omitted, the MDE itself is the conservative stability limit.  PR 1
    intentionally makes this order-independent until later persistence can
    provide a trusted capture sequence.
    """

    _validate_minimum_readable_swings(minimum_readable_swings)
    if (
        not isinstance(minimum_refilms_for_improved, int)
        or isinstance(minimum_refilms_for_improved, bool)
        or minimum_refilms_for_improved < 2
    ):
        raise ValueError("minimum_refilms_for_improved must be an integer at least 2")
    floor = finite_float(noise_floor)
    if floor is None or floor < 0:
        raise ValueError("noise_floor must be a finite non-negative number")
    configured_stability_limit = None
    if maximum_refilm_spread is not None:
        configured_stability_limit = finite_float(maximum_refilm_spread)
        if configured_stability_limit is None or configured_stability_limit < 0:
            raise ValueError(
                "maximum_refilm_spread must be a finite non-negative number"
            )

    confidence, current = comparison_confidence(
        target,
        current_session,
        minimum_readable_swings=minimum_readable_swings,
    )
    if confidence.hard_failures:
        verdict: ProofVerdict = (
            "no_baseline"
            if any(failure.startswith("baseline_") for failure in confidence.hard_failures)
            else "not_comparable"
        )
        return ProofComparison(
            verdict=verdict,
            confidence=confidence,
            current=current,
            prior_refilms=(),
            minimum_detectable_effect=None,
            maximum_refilm_spread=None,
            directional_change=None,
        )

    assert current is not None  # guaranteed by the confidence checks above
    assert target.baseline.value is not None
    assert target.baseline.std is not None
    mde = max(floor, target.baseline.std)
    stability_limit = (
        mde if configured_stability_limit is None else configured_stability_limit
    )
    previous_input = tuple(prior_refilms)
    history_failures, previous = _validated_history(
        target,
        previous_input,
        current_session_id=current.context.session_id,
        minimum_readable_swings=minimum_readable_swings,
    )
    if history_failures:
        return ProofComparison(
            verdict="inconclusive",
            confidence=ComparisonConfidence(
                hard_failures=(), notes=history_failures
            ),
            current=current,
            prior_refilms=(),
            minimum_detectable_effect=mde,
            maximum_refilm_spread=stability_limit,
            directional_change=_directional_change(target, current.measurement),
        )

    evidence = previous + (current,)
    changes = tuple(
        _directional_change(target, refilm.measurement) for refilm in evidence
    )
    assert all(change is not None for change in changes)
    meaningful = tuple(change for change in changes if change is not None)
    latest_change = meaningful[-1]

    if latest_change < -mde:
        return _comparison(
            "needs_attention",
            current,
            previous,
            mde,
            stability_limit,
            latest_change,
            notes=("latest_refilm_moved_away_from_target",),
        )
    if any(change < -mde for change in meaningful):
        return _comparison(
            "inconclusive",
            current,
            previous,
            mde,
            stability_limit,
            latest_change,
            notes=("refilm_direction_is_contradictory",),
        )
    if any(change <= mde for change in meaningful):
        return _comparison(
            "inconclusive",
            current,
            previous,
            mde,
            stability_limit,
            latest_change,
            notes=("change_is_below_minimum_detectable_effect",),
        )

    if len(meaningful) >= 2 and _materially_exceeds(
        max(meaningful) - min(meaningful), stability_limit
    ):
        return _comparison(
            "inconclusive",
            current,
            previous,
            mde,
            stability_limit,
            latest_change,
            notes=("refilm_effect_is_not_consistent",),
        )

    if len(evidence) < minimum_refilms_for_improved:
        return _comparison(
            "early_signal", current, previous, mde, stability_limit, latest_change
        )
    if len(evidence) == minimum_refilms_for_improved:
        return _comparison(
            "improved", current, previous, mde, stability_limit, latest_change
        )
    return _comparison(
        "holding", current, previous, mde, stability_limit, latest_change
    )


def _comparison(
    verdict: ProofVerdict,
    current: ProofRefilm,
    previous: tuple[ProofRefilm, ...],
    mde: float,
    stability_limit: float,
    directional_change: float,
    *,
    notes: tuple[str, ...] = (),
) -> ProofComparison:
    return ProofComparison(
        verdict=verdict,
        confidence=ComparisonConfidence(notes=notes),
        current=current,
        prior_refilms=previous,
        minimum_detectable_effect=mde,
        maximum_refilm_spread=stability_limit,
        directional_change=directional_change,
    )


def _baseline_failures(
    target: ProofTarget, minimum_readable_swings: int
) -> tuple[str, ...]:
    failures = list(
        _snapshot_quality_failures(
            completed=target.baseline_completed,
            coaching_eligible=target.baseline_coaching_eligible,
            warning=target.baseline_warning,
            prefix="baseline",
        )
    )
    context = target.baseline_context
    if not _normalise_context(context.session_id):
        failures.append("baseline_id_missing")
    if not _normalise_context(context.user_id):
        failures.append("baseline_owner_missing")
    if not _normalise_context(context.club):
        failures.append("baseline_club_missing")
    if not _normalise_context(context.hand):
        failures.append("baseline_hand_missing")
    angle = _normalise_context(context.angle)
    if angle not in (ANGLE_FACE_ON, ANGLE_DTL):
        failures.append("baseline_angle_unsupported")
    failures.extend(_angle_metric_failures(target, context.angle, prefix="baseline"))
    if target.baseline.readable_swings < minimum_readable_swings:
        failures.append("baseline_insufficient_readable_swings")
    if target.baseline.value is None:
        failures.append("baseline_target_metric_unreadable")
    if target.baseline.mean is None:
        failures.append("baseline_target_metric_summary_unreadable")
    if target.baseline.std is None:
        failures.append("baseline_variance_unavailable")
    if target.baseline.metric != target.metric:
        failures.append("baseline_metric_mismatch")
    if target.baseline.aggregation != target.aggregation:
        failures.append("baseline_aggregation_mismatch")
    if target.worse_direction not in ("higher", "lower"):
        failures.append("baseline_target_direction_unsupported")
    return tuple(dict.fromkeys(failures))


def _snapshot_quality_failures(
    *,
    completed: bool,
    coaching_eligible: bool,
    warning: str | None,
    prefix: str,
) -> tuple[str, ...]:
    failures: list[str] = []
    if completed is not True:
        failures.append(f"{prefix}_not_complete")
    if coaching_eligible is not True:
        failures.append(f"{prefix}_not_coaching_eligible")
    if warning is not None and not isinstance(warning, str):
        failures.append(f"{prefix}_warning_malformed")
    elif warning_requires_refilm(warning):
        failures.append(f"{prefix}_requires_refilm")
    return tuple(failures)


def _validate_minimum_readable_swings(minimum_readable_swings: int) -> None:
    if (
        not isinstance(minimum_readable_swings, int)
        or isinstance(minimum_readable_swings, bool)
        or minimum_readable_swings < 3
    ):
        raise ValueError("minimum_readable_swings must be an integer at least 3")


def _refilm_failures(
    target: ProofTarget,
    refilm: ProofRefilm,
    *,
    minimum_readable_swings: int,
    prefix: str,
) -> tuple[str, ...]:
    failures = list(
        _snapshot_quality_failures(
            completed=refilm.completed,
            coaching_eligible=refilm.coaching_eligible,
            warning=refilm.warning,
            prefix=prefix,
        )
    )
    refilm_id = _normalise_context(refilm.context.session_id)
    baseline_id = _normalise_context(target.baseline_context.session_id)
    if not refilm_id:
        failures.append(f"{prefix}_id_missing")
    elif refilm_id == baseline_id:
        failures.append(f"{prefix}_is_baseline")
    failures.extend(
        _context_failures(
            target.baseline_context, refilm.context, prefix=prefix
        )
    )
    failures.extend(_angle_metric_failures(target, refilm.context.angle, prefix=prefix))
    failures.extend(
        _measurement_failures(
            target,
            refilm.measurement,
            minimum_readable_swings=minimum_readable_swings,
            prefix=prefix,
        )
    )
    return tuple(dict.fromkeys(failures))


def _context_failures(
    baseline: SessionContext, current: SessionContext, *, prefix: str
) -> tuple[str, ...]:
    failures: list[str] = []
    baseline_user = _normalise_context(baseline.user_id)
    current_user = _normalise_context(current.user_id)
    if not current_user:
        failures.append(_context_failure(prefix, "owner", "missing"))
    elif baseline_user != current_user:
        failures.append(_context_failure(prefix, "owner", "mismatch"))

    for name in ("club", "hand", "angle"):
        baseline_value = _normalise_context(getattr(baseline, name))
        current_value = _normalise_context(getattr(current, name))
        if not current_value:
            failures.append(_context_failure(prefix, name, "missing"))
        elif baseline_value != current_value:
            failures.append(_context_failure(prefix, name, "mismatch"))
    return tuple(failures)


def _context_failure(prefix: str, field: str, kind: str) -> str:
    if prefix == "session":
        return f"session_{field}_missing" if kind == "missing" else f"{field}_mismatch"
    return f"{prefix}_{field}_{kind}"


def _angle_metric_failures(
    target: ProofTarget, angle: str | None, *, prefix: str = ""
) -> tuple[str, ...]:
    label = f"{prefix}_" if prefix else ""
    resolved_angle = _normalise_context(angle)
    if resolved_angle not in (ANGLE_FACE_ON, ANGLE_DTL):
        return (f"{label}angle_unsupported",)
    if target.metric not in NUMERIC_FIELDS:
        return (f"{label}target_metric_unsupported",)
    if resolved_angle == ANGLE_DTL:
        # DTL can validate rhythm, but never a face-on mechanics claim.
        if target.metric != "tempo_ratio":
            return (f"{label}target_metric_not_supported_by_angle",)
    elif target.metric in FACE_ON_ONLY_FIELDS:
        return ()
    return ()


def _measurement_failures(
    target: ProofTarget,
    measurement: ProofMeasurement,
    *,
    minimum_readable_swings: int,
    prefix: str,
) -> tuple[str, ...]:
    failures: list[str] = []
    if measurement.metric != target.metric:
        failures.append(f"{prefix}_metric_mismatch")
    if measurement.aggregation != target.aggregation:
        failures.append(f"{prefix}_aggregation_mismatch")
    if measurement.readable_swings < minimum_readable_swings:
        failures.append(
            "insufficient_readable_swings"
            if prefix == "session"
            else f"{prefix}_insufficient_readable_swings"
        )
    if measurement.value is None:
        failures.append(
            "target_metric_unreadable"
            if prefix == "session"
            else f"{prefix}_target_metric_unreadable"
        )
    if measurement.mean is None or measurement.std is None:
        failures.append(
            "target_metric_summary_unreadable"
            if prefix == "session"
            else f"{prefix}_target_metric_summary_unreadable"
        )
    return tuple(failures)


def _validated_history(
    target: ProofTarget,
    history: tuple[object, ...],
    *,
    current_session_id: str,
    minimum_readable_swings: int,
) -> tuple[tuple[str, ...], tuple[ProofRefilm, ...]]:
    failures: list[str] = []
    accepted: list[ProofRefilm] = []
    seen_ids = {
        _normalise_context(target.baseline_context.session_id),
        _normalise_context(current_session_id),
    }
    for refilm in history:
        if not isinstance(refilm, ProofRefilm):
            failures.append("prior_refilm_provenance_missing")
            continue
        refilm_id = _normalise_context(refilm.context.session_id)
        if refilm_id and refilm_id in seen_ids:
            failures.append("duplicate_refilm_session_id")
        elif refilm_id:
            seen_ids.add(refilm_id)
        item_failures = _refilm_failures(
            target,
            refilm,
            minimum_readable_swings=minimum_readable_swings,
            prefix="prior_refilm",
        )
        if item_failures:
            failures.extend(item_failures)
            continue
        accepted.append(refilm)
    return tuple(dict.fromkeys(failures)), tuple(accepted)


def _directional_change(
    target: ProofTarget, measurement: ProofMeasurement
) -> float | None:
    if target.baseline.value is None or measurement.value is None:
        return None
    raw_change = measurement.value - target.baseline.value
    return -raw_change if target.worse_direction == "higher" else raw_change


def _materially_exceeds(value: float, threshold: float) -> bool:
    """Avoid classifying a binary-float rounding artifact as inconsistency."""

    return value > threshold and not math.isclose(
        value, threshold, rel_tol=1e-9, abs_tol=1e-12
    )
