"""Closed native payload domains, independent of route implementation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from swinglab.api.contracts import (
    AnalysisFailure,
    CapabilitiesResponse,
    NativeEventRequest,
    ProgressResponse,
    ProofCycleTargetResponse,
)


def test_native_contracts_reject_unknown_failure_event_and_nested_payload_values():
    """Catches a client payload domain expanding without a version change."""
    with pytest.raises(ValidationError):
        AnalysisFailure(code="unbounded_failure", retryable=False, message="no")
    with pytest.raises(ValidationError):
        NativeEventRequest(event="unbounded_event")
    with pytest.raises(ValidationError):
        CapabilitiesResponse(capabilities={"unbounded_capability": True})
    with pytest.raises(ValidationError):
        ProgressResponse(progress={"unbounded_progress": 1})
    with pytest.raises(ValidationError):
        ProofCycleTargetResponse(target={"unbounded_target": True})
