import numpy as np
import pytest

from reseam.joint_types import Correspondence, JointProfile, ReuseGroup, V2State
from reseam.joint_validation import (
    density_objective,
    uv_barycentric,
    validate_joint_geometry,
    validate_joint_quality,
)
from reseam.mesh import make_mesh
from reseam.paired_solver import PairProblem, solve_pair
from reseam.types import MeshState


def panels() -> tuple[MeshState, ReuseGroup]:
    vertices = np.asarray(
        [
            [-1, 0, 0],
            [-1, 1, 0],
            [-1, 1, 1],
            [-1, 0, 1],
            [1, 0, 0],
            [1, 1, 0],
            [1, 1, 1],
            [1, 0, 1],
            [1, 0.5, 0.5],
        ],
        dtype=float,
    )
    faces = np.asarray([[0, 2, 1], [0, 3, 2], [4, 5, 8], [5, 6, 8], [6, 7, 8], [7, 4, 8]], dtype=np.int64)
    uv = vertices[:, 1:] * 0.25 + 0.2
    state = MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())
    group = ReuseGroup("test", (0, 1), (2, 3, 4, 5), (1.0, 0, 0), 0, -1, 1)
    return state, group


def panel_correspondences(state: MeshState) -> tuple[Correspondence, ...]:
    result = []
    for fi, face in enumerate(state.uv_faces):
        point = state.uv[face].mean(axis=0)
        candidates = range(2, 6) if fi < 2 else range(2)
        for target in candidates:
            bary = uv_barycentric(state.uv[state.uv_faces[target]], point[None])[0]
            if min(bary) >= -1e-10:
                result.append(
                    Correspondence(
                        fi,
                        (1 / 3, 1 / 3, 1 / 3),
                        target,
                        (float(bary[0]), float(bary[1]), float(bary[2])),
                        1.0,
                    )
                )
                break
    return tuple(result)


def test_overlap_permission_checks_actual_surface_not_only_group() -> None:
    state, group = panels()
    checked = validate_joint_geometry(V2State(state, (group,)), JointProfile())
    assert checked["valid"], checked
    assert abs(checked["reuse_factor"] - 2) < 1e-10
    assert checked["overlap_cells"] == 4
    bad = ReuseGroup("test", (0, 1), (2, 3, 4, 5), (1.0, 0, 0), 0.2, -1, 1)
    result = validate_joint_geometry(V2State(state, (bad,)), JointProfile())
    assert not result["valid"] and any("OVERLAP_CORRESPONDENCE" in x for x in result["errors"])
    assert not validate_joint_geometry(V2State(state), JointProfile())["valid"]


def test_density_counts_reuse_separately_and_preserves_geometry() -> None:
    reused, group = panels()
    reference = reused.copy()
    reference.uv[:4, 0] = 0.7 - reference.uv[:4, 0]
    reference.uv[4:, 0] += 0.4
    correspondence = panel_correspondences(reused)
    assert validate_joint_quality(reference, V2State(reused, (group,), correspondence), JointProfile())[
        "valid"
    ]
    grown = reused.copy()
    grown.uv = (grown.uv - 0.2) * 1.2 + 0.2
    result = validate_joint_quality(reference, V2State(grown, (group,), correspondence), JointProfile())
    assert result["valid"], result
    assert abs(result["gain"] - 1.2) < 1e-10
    assert result["unique_coverage"] < result["summed_uv_area"]
    shrunk = reused.copy()
    shrunk.uv *= 0.9
    assert not validate_joint_quality(reference, V2State(shrunk, (group,)), JointProfile())["valid"]
    assert abs(density_objective(reference, reused)["gain"] - 1) < 1e-10


def test_coupling_requires_complete_evidence_and_one_pixel_limit() -> None:
    state, group = panels()
    assert not validate_joint_quality(state, V2State(state, (group,)), JointProfile())["valid"]
    correspondence = panel_correspondences(state)
    moved = state.copy()
    moved.uv[4:, 0] += 3 / 1024
    result = validate_joint_quality(state, V2State(moved, (group,), correspondence), JointProfile())
    assert "COUPLING_PIXEL_LIMIT:test" in result["errors"]


@pytest.mark.parametrize("density", [False, True])
def test_analytic_sd_coupling_gradient_including_mirror(density: bool) -> None:
    state, _ = panels()
    state.uv[8] += [0.01, -0.006]
    constraint = Correspondence(2, (0, 0, 1), 0, (0.5, 0.5, 0), 1.0)
    problem = PairProblem(state, np.asarray([-1, -1, 1, 1, 1, 1]), (constraint,))
    if density:
        target = np.asarray([2.0, 2.0, 1.0, 1.0, 1.0, 1.0]) * np.asarray([0.9, 1.1, 0.85, 1.15, 0.95, 1.05])
        problem.target_areas = target / target.sum() * problem.initial_area
    _, gradient = problem.energy_gradient(state.uv, 10)
    numeric = np.zeros_like(gradient)
    for index in np.ndindex(state.uv.shape):
        plus, minus = state.uv.copy(), state.uv.copy()
        plus[index] += 1e-6
        minus[index] -= 1e-6
        numeric[index] = (problem.energy_gradient(plus, 10)[0] - problem.energy_gradient(minus, 10)[0]) / 2e-6
    assert np.allclose(numeric, gradient, atol=2e-6, rtol=2e-5)


def test_pair_solve_improves_correspondence_without_fixing_scale() -> None:
    state, _ = panels()
    state.uv[4:] += [0.04, 0.015]
    constraint = Correspondence(2, (0, 0, 1), 0, (0.5, 0.5, 0), 1.0)
    problem = PairProblem(state, np.asarray([-1, -1, 1, 1, 1, 1]), (constraint,))
    before = np.linalg.norm((problem.coupling @ state.uv)[0])
    result = solve_pair(problem, seconds=4, iterations=120)
    assert result.coupling_rms < before * 0.5
    assert abs(problem.area(result.state.uv) - problem.initial_area) < 1e-10
    assert problem.positive(result.state.uv)
    fallback = solve_pair(problem, seconds=0)
    assert fallback.status == "TIMEOUT_WITH_BEST"
    assert np.array_equal(fallback.state.uv, state.uv)


def test_local_flip_and_duplicate_members_rejected() -> None:
    state, group = panels()
    bad = state.copy()
    bad.uv[8] = [0.7, 0.9]
    assert not validate_joint_geometry(V2State(bad, (group,)), JointProfile())["valid"]
    assert not validate_joint_geometry(V2State(state, (group, group)), JointProfile())["valid"]


def test_checkpoint_verified_after_deadline_is_not_archived(monkeypatch: pytest.MonkeyPatch) -> None:
    state, _ = panels()
    state.uv[4:] += [0.04, 0.015]
    problem = PairProblem(
        state, np.asarray([-1, -1, 1, 1, 1, 1]), (Correspondence(2, (0, 0, 1), 0, (0.5, 0.5, 0), 1.0),)
    )
    clock = [0.0]

    def crossing_check(
        candidate: MeshState, sheets: np.ndarray, provenance: tuple[int, ...] | None = None
    ) -> list[str]:
        clock[0] = 2.0
        return []

    monkeypatch.setattr("reseam.paired_solver.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("reseam.paired_solver.sheet_intersections", crossing_check)
    result = solve_pair(problem, seconds=1, iterations=120)
    assert result.status == "TIMEOUT_WITH_BEST"
    assert np.array_equal(result.state.uv, state.uv)
