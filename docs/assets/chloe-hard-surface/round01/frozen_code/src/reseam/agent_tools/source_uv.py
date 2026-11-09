"""Explicit preservation of authored UV conflicts without cutting original faces."""

from pathlib import Path

import numpy as np

from ..budget_state import BudgetState
from .state import load_snapshot


def source_uv_policy(value: object) -> dict[str, str]:
    """Require a hash-bound immutable reference, rather than a blanket waiver."""
    if (not isinstance(value, dict) or set(value) != {"snapshot", "state_hash"}
            or any(not isinstance(item, str) or not item for item in value.values())):
        raise ValueError("SOURCE_UV_POLICY_REQUIRES_SNAPSHOT_AND_STATE_HASH")
    return {key: str(item) for key, item in value.items()}


def _pair(error: object) -> tuple[int, int] | None:
    if not isinstance(error, str):
        return None
    parts = error.split(":")
    if len(parts) != 3 or parts[0] not in {"UNAUTHORIZED_OVERLAP", "UNAUTHORIZED_CONTACT"}:
        return None
    first, second = sorted((int(parts[1]), int(parts[2])))
    return first, second


def _same_layout(original: np.ndarray, candidate: np.ndarray) -> bool:
    """Allow a common rotation/scale/translation, including packing roundoff."""
    a = original[:, 0] + 1j * original[:, 1]
    b = candidate[:, 0] + 1j * candidate[:, 1]
    a, b = a - a.mean(), b - b.mean()
    denominator = float(np.vdot(a, a).real)
    if denominator <= 0 or not np.isfinite(b).all():
        return False
    multiplier = np.vdot(a, b) / denominator
    scale = float(np.max(np.abs(b)))
    return bool(abs(multiplier) > 0 and scale > 0
                and np.max(np.abs(b - multiplier * a)) <= 1e-9 * scale)


def preserve_source_conflicts(
    state: BudgetState, report: dict[str, object], profile: dict[str, object],
) -> dict[str, object]:
    """Retain strict evidence; accept only unchanged, already existing conflicts."""
    from .geometry import geometry_report

    policy = source_uv_policy(profile["preserve_source_uv"])
    source = load_snapshot(Path(policy["snapshot"]))
    if source.state_hash != policy["state_hash"]:
        raise ValueError("SOURCE_UV_STATE_HASH_MISMATCH")
    mesh, reference = state.surface.mesh, source.state.surface.mesh
    if (not np.array_equal(mesh.faces, reference.faces)
            or not np.array_equal(mesh.vertices, reference.vertices)):
        raise ValueError("SOURCE_UV_GEOMETRY_MISMATCH")
    original_profile = {key: item for key, item in profile.items() if key != "preserve_source_uv"}
    baseline = geometry_report(source.state, original_profile)
    baseline_errors = baseline["hard_errors"]
    assert isinstance(baseline_errors, list)
    pairs = {_pair(error) for error in baseline_errors} - {None}
    errors = report["errors"]
    hard_errors = report["hard_errors"]
    assert isinstance(errors, list) and isinstance(hard_errors, list)
    old_uv = source.state.surface.uv[source.state.surface.uv_faces]
    new_uv = state.surface.uv[state.surface.uv_faces]
    retained: list[str] = []
    for error in hard_errors:
        pair = _pair(error)
        if (pair is not None and pair in pairs
                and _same_layout(old_uv[list(pair)].reshape(-1, 2), new_uv[list(pair)].reshape(-1, 2))):
            retained.append(str(error))
    hard = [error for error in hard_errors if error not in retained]
    remaining = [error for error in errors if error not in retained]
    return {**report, "strict_valid": report["valid"], "strict_hard_valid": report["hard_valid"],
            "strict_errors": errors, "errors": remaining, "hard_errors": hard,
            "valid": not remaining, "hard_valid": not hard,
            "geometry_acceptance_scope": "preserve_source_uv_conflicts_appearance_first",
            "preserved_source_conflicts": retained,
            "source_uv_preservation": {**policy, "source_conflict_count": len(pairs),
                "retained_conflict_count": len(retained), "packing_similarity_relative_tolerance": 1e-9,
                "original_faces_split": False,
                "note": "Existing local layout only; new pairs or changed pair layouts remain rejected. "
                        "Source conflicts are not counted as deliberate reuse or strict validation success."}}
