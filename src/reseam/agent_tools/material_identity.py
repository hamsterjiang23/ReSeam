"""Explicit same-surface face/corner contract with bounded float32 roundoff."""

import hashlib

import numpy as np

from ..types import FloatArray


def face_corner_identity(target: FloatArray, source: FloatArray) -> dict[str, object]:
    """Allow attribute splits, but never infer correspondence from face counts."""
    if (target.shape != source.shape or not np.isfinite(target).all()
            or not np.isfinite(source).all()):
        raise ValueError("PBR_SAME_SURFACE_FACE_CORNER_IDENTITY_MISMATCH")
    first = np.asarray(target, dtype="<f4")
    second = np.asarray(source, dtype="<f4")
    scale = max(float(np.max(np.abs(source), initial=0)),
                float(np.max(np.ptp(source.reshape(-1, 3), axis=0), initial=0)))
    tolerance = float(np.finfo(np.float32).eps) * scale
    difference = float(np.max(np.abs(target - source), initial=0))
    if difference > tolerance:
        raise ValueError("PBR_SAME_SURFACE_FACE_CORNER_IDENTITY_MISMATCH")
    return {
        "contract": "ordered face-corner positions agree within one scale-relative float32 epsilon; no nearest lookup",
        "faces": len(first),
        "target_float32_sha256": hashlib.sha256(first.tobytes()).hexdigest(),
        "source_float32_sha256": hashlib.sha256(second.tobytes()).hexdigest(),
        "position_max_abs_difference": difference,
        "position_absolute_tolerance": tolerance,
        "uv_and_shading_normals_required_equal": False,
    }
