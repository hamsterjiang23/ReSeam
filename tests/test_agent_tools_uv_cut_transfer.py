"""Exact correspondence transfers master's explicit cuts, independent of target cuts."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools import geometry
from reseam.agent_tools.geometry import apply_reuse, check_pair
from reseam.agent_tools.state import load_snapshot, read_object, save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv


def _different_cuts(other_target_cut: bool) -> BudgetState:
    first = np.asarray([[-1., y, z] for z in (0., 1.) for y in (0., 1., 2.)])
    vertices = np.concatenate((first, first*[-1., 1., 1.]))
    original_faces = np.asarray([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]], dtype=np.int64)
    faces = np.concatenate((original_faces, original_faces[:, [0, 2, 1]]+6))
    corners = .12*vertices[faces, 1:]+[.05, .1]
    corners[2:4, :, 0] += .2
    corners[4:] = vertices[faces[4:], 1:]*[-.12, .12]+[.8, .6]
    identities = faces.copy()
    identities[2:4] += 20  # Master has two connected two-triangle islands.
    if other_target_cut:
        identities[6] += 60  # Target split is one plus three, at a different edge.
        corners[6, :, 1] += .2
    return BudgetState(state_from_corner_uv(make_mesh(vertices, faces), corners, corner_ids=identities))


@pytest.mark.parametrize("mode", ["exact", "topology", "chart_aware"])
@pytest.mark.parametrize("other_target_cut", [False, True])
def test_master_uv_identity_and_cuts_transfer_without_face_fragmentation(
    tmp_path: Path, mode: str, other_target_cut: bool,
) -> None:
    state = _different_cuts(other_target_cut)
    old_labels = chart_labels(state.surface)
    assert len(np.unique(old_labels[:4])) == 2
    assert len(np.unique(old_labels[4:])) == (2 if other_target_cut else 1)
    source = save_snapshot(state, tmp_path / "source", {})
    result = check_pair({"snapshot": str(source.root), "first_faces": [0, 1, 2, 3],
                         "second_faces": [4, 5, 6, 7], "mode": mode,
                         "profile": {"resolution": 128, "gap_pixels": 2, "border_pixels": 2}},
                        tmp_path / "check", time.monotonic()+30)
    assert result["status"] == "OK" and result["geometry_passed"] is True, result
    proposal = read_object(Path(str(result["proposal"])))
    target = load_snapshot(Path(str(proposal["candidate_snapshot"])))
    labels = chart_labels(target.state.surface)
    assert len(np.unique(labels[:4])) == len(np.unique(labels[4:])) == 2
    assert labels[4] == labels[5] and labels[6] == labels[7] and labels[4] != labels[6]
    assert len(np.unique(labels)) == 4  # Four connected islands, not eight per-face islands.
    original_uv = target.state.surface.uv[target.state.surface.uv_faces[:4]]
    copied_uv = target.state.surface.uv[target.state.surface.uv_faces[4:]][:, [0, 2, 1]]
    assert np.array_equal(original_uv, copied_uv)
    # Source and member copies remain distinct identities even at exact UV equality.
    assert not set(target.state.surface.uv_faces[:4].ravel()) & set(target.state.surface.uv_faces[4:].ravel())
    assert np.array_equal(target.state.surface.mesh.vertices, state.surface.mesh.vertices)
    assert np.array_equal(target.state.surface.mesh.faces, state.surface.mesh.faces)
    if mode == "chart_aware":
        applied = apply_reuse({"snapshot": str(source.root), "proposal": result["proposal"]},
                              tmp_path/"applied", time.monotonic()+30)
        delivered = load_snapshot(Path(str(applied["snapshot"])))
        assert delivered.metadata["semantic_chart_groups"] == target.metadata["semantic_chart_groups"]


def test_rejected_chart_candidate_is_explicit_immutable_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _different_cuts(False)
    source = save_snapshot(state, tmp_path/"source", {})
    original_pack = geometry._packed

    def collide_packing_owners(
        candidate: BudgetState, profile: dict[str, object], density_normalize: bool = True,
        deadline: float | None = None,
    ) -> BudgetState:
        packed = original_pack(candidate, profile, density_normalize, deadline)
        a, b = packed.groups
        first = np.unique(packed.surface.uv_faces[list(a.first_faces+a.second_faces)])
        second = np.unique(packed.surface.uv_faces[list(b.first_faces+b.second_faces)])
        packed.surface.uv[second] += packed.surface.uv[first].mean(axis=0)-packed.surface.uv[second].mean(axis=0)
        packed.invalidate()
        return packed

    monkeypatch.setattr(geometry, "_packed", collide_packing_owners)
    request: dict[str, object] = {"snapshot": str(source.root), "first_faces": [0, 1, 2, 3],
                                  "second_faces": [4, 5, 6, 7], "mode": "chart_aware",
                                  "preserve_rejected_candidate": True}
    result = check_pair(request, tmp_path/"check", time.monotonic()+30)
    assert result["status"] == "REJECTED" and result["geometry_passed"] is False
    report = read_object(Path(str(result["report"])))
    candidates = report["diagnostic_candidates"]
    assert isinstance(candidates, list) and len(candidates) == 1
    candidate = load_snapshot(Path(candidates[0]["snapshot"]))
    metadata = candidate.metadata["diagnostic_candidate"]
    assert isinstance(metadata, dict) and metadata["accepted"] is False
    assert read_object(candidate.root/"snapshot.json")["parent_hash"] == source.state_hash
    assert np.array_equal(candidate.state.surface.mesh.faces, state.surface.mesh.faces)
    assert np.array_equal(candidate.state.surface.mesh.vertices, state.surface.mesh.vertices)
    assert load_snapshot(source.root).state_hash == source.state_hash
    assert not (tmp_path/"check"/"proposal.json").exists()
    request["mode"] = "exact"
    with pytest.raises(ValueError, match="DIAGNOSTIC_PRESERVATION_REQUIRES"):
        check_pair(request, tmp_path/"invalid_mode", time.monotonic()+30)
