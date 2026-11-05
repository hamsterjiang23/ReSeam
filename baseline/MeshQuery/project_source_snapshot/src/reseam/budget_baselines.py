"""Budget-comparison adapters with common immutable geometry and ROI starts.

No result from this module is material acceptance.  Native xatlas is isolated in
a killable child process; historical paired optimization is retained verbatim.
"""

import argparse
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .budget_pair import BudgetPairProblem, solve_budget_pair
from .joint_regions import RegionPair, paired_problem
from .joint_types import Correspondence
from .mesh import state_from_corner_uv
from .paired_solver import solve_pair
from .types import IntArray, Mesh, MeshState

ROI_METHODS = ("independent", "old_pair", "geometry_only")


@dataclass
class ROIBaselineResult:
    """An unapproved UV proposal; original face identities are explicit."""

    state: MeshState
    initial: MeshState
    original_face_ids: tuple[int, ...]
    member_ids: IntArray
    orientations: IntArray
    contact_classes: tuple[int, ...] | None
    constraints: tuple[Correspondence, ...]
    status: str
    wall_seconds: float
    metadata: dict[str, object]


def _remaining(deadline: float) -> float:
    if not math.isfinite(deadline):
        raise ValueError("Finite absolute monotonic deadline required")
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("BASELINE_BUDGET_EXHAUSTED")
    return seconds


def prepare_baseline(
    mesh: Mesh, method: str, seed: int, deadline: float
) -> MeshState:
    """Independently unwrap a complete fixed mesh with explicit xatlas identity.

    xatlas 0.0.11 is deterministic here.  Seed identifies the protocol but does
    not create an independent stochastic sample, and is never passed as a fake
    unsupported library option.  The returned UVs retain xatlas packing.
    """
    if method not in ("independent", "xatlas"):
        raise ValueError(f"Unsupported complete-mesh baseline: {method}")
    if type(seed) is not int or seed < 0:
        raise ValueError("Nonnegative integer protocol seed required")
    _remaining(deadline)
    with tempfile.TemporaryDirectory(prefix="reseam-budget-baseline-") as temporary:
        folder = Path(temporary)
        source, output = folder / "input.npz", folder / "output.npz"
        np.savez(source, vertices=mesh.vertices, faces=mesh.faces)
        environment = os.environ.copy()
        environment.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "PYTHONUNBUFFERED": "1"})
        command = [sys.executable, "-m", "reseam.budget_baselines", str(source), str(output)]
        try:
            process = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=_remaining(deadline),
            )
        except subprocess.TimeoutExpired as error:
            # subprocess.run kills and reaps this direct native-library worker.
            raise TimeoutError("XATLAS_INITIALIZATION_TIMEOUT") from error
        if process.returncode:
            raise RuntimeError(f"XATLAS_INITIALIZATION_FAILED:{process.returncode}:{process.stderr[-4000:]}")
        _remaining(deadline)
        if not output.exists():
            raise RuntimeError("XATLAS_OUTPUT_MISSING")
        with np.load(output, allow_pickle=False) as data:
            mapping = np.asarray(data["mapping"], dtype=np.int64)
            faces = np.asarray(data["faces"], dtype=np.int64)
            uv = np.asarray(data["uv"], dtype=np.float64)
        if (
            faces.shape != mesh.faces.shape
            or mapping.ndim != 1
            or faces.min() < 0
            or faces.max() >= len(mapping)
            or mapping.min() < 0
            or mapping.max() >= len(mesh.vertices)
            or uv.shape != (len(mapping), 2)
            or not np.isfinite(uv).all()
            or not np.array_equal(mapping[faces], mesh.faces)
        ):
            raise ValueError("XATLAS_CHANGED_ORIGINAL_FACE_CORNER_IDENTITY")
        return state_from_corner_uv(mesh, uv[faces], corner_ids=faces)


def solve_roi_baseline(
    mesh: Mesh,
    pair: RegionPair,
    method: str,
    deadline: float,
    iterations: int = 240,
    maximum_anisotropy: float = 2.0,
    coupling_weight: float = 10.0,
) -> ROIBaselineResult:
    """Run independent/old/new-geometry proposals from the same planar start.

    Initializer and correspondence cost count in the supplied deadline.  Any
    exception propagates so the experiment can retain an explicit failed row;
    this adapter never silently chooses a smaller ROI or another initializer.
    """
    started = time.monotonic()
    if method not in ROI_METHODS:
        raise ValueError(f"Unsupported ROI baseline: {method}")
    if type(iterations) is not int or iterations < 0:
        raise ValueError("Nonnegative integer iteration limit required")
    _remaining(deadline)
    prepared, identities = paired_problem(mesh, pair, deadline, planar=True)
    initial = prepared.initial.copy()
    members = np.r_[
        np.zeros(len(pair.first_faces), dtype=np.int64),
        np.ones(len(pair.second_faces), dtype=np.int64),
    ]
    metadata: dict[str, object] = {
        "method": method,
        "initialization": "common_planar_geometry",
        "original_face_ids": list(identities),
        "constraint_count": len(prepared.constraints),
        "material_status": "NOT_EVALUATED",
        "geometry_acceptance": "CALLER_VALIDATOR_REQUIRED",
        "optcuts_used": False,
        "shared_proposal": method != "independent",
        "initializer_seconds": time.monotonic() - started,
        "solver_iteration_limit": iterations,
    }
    if method == "independent" or iterations == 0:
        _remaining(deadline)
        state = initial.copy()
        status = "INITIALIZED" if method == "independent" else "ITERATION_LIMIT"
        metadata["iterations"] = 0
    elif method == "old_pair":
        solved = solve_pair(prepared, _remaining(deadline), iterations=iterations)
        state, status = solved.state, solved.status
        metadata.update(
            {
                "iterations": len(solved.history),
                "history": solved.history,
                "coupling_rms": solved.coupling_rms,
                "solver_seconds": solved.wall_seconds,
                "objective": "historical_symmetric_dirichlet_and_coupling_with_area_restore",
            }
        )
    else:
        problem = BudgetPairProblem(
            initial,
            prepared.orientations,
            prepared.constraints,
            member_ids=members,
            contact_classes=prepared.contact_classes,
            maximum_anisotropy=maximum_anisotropy,
            coupling_weight=coupling_weight,
        )
        solved_geometry = solve_budget_pair(problem, _remaining(deadline), iterations=iterations)
        state, status = solved_geometry.state, solved_geometry.status
        metadata.update(
            {
                "iterations": len(solved_geometry.history),
                "history": solved_geometry.history,
                "coupling_rms": solved_geometry.coupling_rms,
                "solver_seconds": solved_geometry.wall_seconds,
                "geometry_valid_internal": solved_geometry.geometry_valid,
                "objective": "scale_normalized_shape_and_coupling_fixed_common_gauge",
            }
        )
    if not np.array_equal(state.mesh.faces, mesh.faces[list(identities)]) or not np.array_equal(
        state.mesh.vertices, mesh.vertices
    ):
        raise ValueError("BASELINE_CHANGED_ORIGINAL_GEOMETRY")
    return ROIBaselineResult(
        state,
        initial,
        identities,
        members,
        prepared.orientations.copy(),
        prepared.contact_classes,
        prepared.constraints,
        status,
        time.monotonic() - started,
        metadata,
    )


def _xatlas_worker(source: Path, output: Path) -> None:
    """Private process entry; the parent handles timeout and identity checks."""
    import xatlas

    with np.load(source, allow_pickle=False) as data:
        vertices = np.asarray(data["vertices"], dtype=np.float32)
        original = np.asarray(data["faces"], dtype=np.uint32)
    mapping, faces, uv = xatlas.parametrize(vertices, original)
    if not np.array_equal(mapping[faces], original):
        raise ValueError("xatlas changed face order or corner identity")
    np.savez(output, mapping=mapping, faces=faces, uv=uv)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Private xatlas worker for budget baselines")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    _xatlas_worker(args.source, args.output)


if __name__ == "__main__":
    _main()
