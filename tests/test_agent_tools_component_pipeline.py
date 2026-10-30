"""A cross-old-seam paired domain must survive real source-bound color export."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.color_reuse import (
    bake_color_reuse,
    export_color_reuse,
    freeze_target,
    validate_color_reuse,
)
from reseam.agent_tools.geometry import apply_reuse, check_pair, inspect_uv
from reseam.agent_tools.regional_cut import cut_region
from reseam.agent_tools.regions import define_region
from reseam.agent_tools.reparameterize import boundary_loop
from reseam.agent_tools.state import load_snapshot, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState, fingerprint
from reseam.mesh import make_mesh, state_from_corner_uv


@pytest.mark.parametrize("refinement_failure", [False, True])
def test_component_regions_to_paired_domain_color_and_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refinement_failure: bool,
) -> None:
    vertices = np.array([[-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
                         [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 7, 5], [5, 7, 6]], dtype=np.int64)
    corners = .1 + .2 * vertices[faces, 1:]
    corners[2:, :, 0] += .5
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners,
                                   corner_ids=np.arange(12).reshape(4, 3))
    colors = np.full((32, 32, 4), [.7, .1, .1, 1.])
    colors[:, 16:] = [.1, .1, .7, 1.]
    atlas = atlas_from_arrays(colors, np.full((32, 32, 3), [0., 0, 1]),
                              np.full((32, 32, 3), [1., 1., 0]))
    reference = tmp_path / "reference.glb"
    export_material_glb(surface, atlas, reference)
    source = save_snapshot(BudgetState(surface), tmp_path / "source",
                           {"reference_glb": str(reference)})
    original_fingerprint = fingerprint(source.state)

    def folder(name: str) -> Path:
        path = tmp_path / name
        path.mkdir()
        return path

    def deadline() -> float:
        return time.monotonic() + 90

    left = define_region({"snapshot": str(source.root), "name": "left_panel", "faces": [0, 1]},
                         folder("left"), deadline())
    right = define_region({"snapshot": left["snapshot"], "name": "right_panel", "faces": [2, 3]},
                          folder("right"), deadline())
    profile: dict[str, object] = {"resolution": 128, "gap_pixels": 8, "border_pixels": 8,
                                  "normal_angle_degrees": 180, "correspondence_fraction": .1,
                                  "quality_constraints": False}
    if refinement_failure:
        def failed_refinement(*args: object, **kwargs: object) -> None:
            raise ValueError("NUMERICAL_REFINEMENT_REJECTED")
        monkeypatch.setattr("reseam.agent_tools.paired_domain.solve_budget_pair", failed_refinement)
    checked = check_pair({"snapshot": right["snapshot"], "mode": "paired_domain",
                          "refine_iterations": 1 if refinement_failure else 0,
                          "first_region_id": left["region_id"], "second_region_id": right["region_id"],
                          "first_seam_edges": [], "second_seam_edges": [], "domain": "convex",
                          "boundary_anchors": [
                              {"first": [0, 0], "second": [2, 0]},
                              {"first": [0, 1], "second": [2, 2]},
                              {"first": [0, 2], "second": [3, 2]},
                              {"first": [1, 2], "second": [2, 1]}],
                          "profile": profile}, folder("check"), deadline())
    assert checked["status"] == "OK", checked
    applied = apply_reuse({"snapshot": right["snapshot"], "proposal": checked["proposal"]},
                          folder("apply"), deadline())
    child = load_snapshot(Path(str(applied["snapshot"])))
    assert np.array_equal(child.state.surface.mesh.faces, faces)
    assert np.array_equal(child.state.surface.mesh.vertices, vertices)
    assert fingerprint(load_snapshot(source.root).state) == original_fingerprint
    inspection = inspect_uv({"snapshot": applied["snapshot"], "profile": profile},
                             folder("inspect"), deadline())
    assert inspection["status"] == "OK", inspection
    frozen = freeze_target({"snapshot": applied["snapshot"], "geometry_profile": profile},
                           folder("target"), deadline())
    baked = bake_color_reuse({"snapshot": applied["snapshot"], "target": frozen["target"],
                              "resolution": 128, "padding_pixels": 8}, folder("bake"), deadline())
    assert baked["status"] == "OK", baked
    validated = validate_color_reuse({"snapshot": applied["snapshot"], "bake": baked["bake"],
                                      "render_size": 16, "mip_levels": [0, 1]},
                                     folder("validate"), deadline())
    assert validated["quality_accepted"] is True, validated
    exported = export_color_reuse({"snapshot": applied["snapshot"], "bake": baked["bake"],
                                   "validation": validated["validation"], "mode": "accepted"},
                                  folder("export"), deadline())
    assert exported["status"] == "OK", exported
    assert (tmp_path / "export/asset.glb").is_file()
    assert (tmp_path / "bake/texture/BaseColor_mip0.png").is_file()


def test_paired_cylinder_cut_distinguishes_both_sides_of_one_original_vertex(tmp_path: Path) -> None:
    ring = np.arange(4) * np.pi / 2
    first = np.array([[-2 + np.cos(angle), np.sin(angle), z] for z in (0., 1.) for angle in ring])
    second = first.copy()
    second[:, 0] *= -1
    vertices = np.concatenate((first, second))
    first_faces = np.array([[a, (a + 1) % 4, a + 4] if part == 0 else
                            [(a + 1) % 4, (a + 1) % 4 + 4, a + 4]
                            for a in range(4) for part in range(2)], dtype=np.int64)
    faces = np.concatenate((first_faces, first_faces[:, [0, 2, 1]] + 8))
    corners = np.tile(np.array([[0., 0.], [.01, 0.], [0., .01]]), (16, 1, 1))
    corners[:, :, 0] += np.arange(16)[:, None] * .04
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners,
                                   corner_ids=np.arange(48).reshape(16, 3))
    cut = cut_region(surface, tuple(range(8)), [[0, 4]])
    loop = boundary_loop(cut.surface.mesh.faces)
    anchors: list[dict[str, list[int]]] = []
    for vertex in loop:
        row, corner = np.argwhere(cut.surface.mesh.faces == vertex)[0]
        anchors.append({"first": [int(row), int(corner)],
                        "second": [int(row) + 8, [0, 2, 1][int(corner)]]})
    assert len(loop) > len(set(cut.original_vertex_ids[loop]))
    original = save_snapshot(BudgetState(surface), tmp_path / "source", {})
    result = check_pair({"snapshot": str(original.root), "mode": "paired_domain",
                         "first_faces": list(range(8)), "second_faces": list(range(8, 16)),
                         "first_seam_edges": [[0, 4]], "second_seam_edges": [[8, 12]],
                         "boundary_anchors": anchors, "domain": "convex",
                         "profile": {"resolution": 128, "gap_pixels": 4, "border_pixels": 4,
                                     "normal_angle_degrees": 180,
                                     "quality_constraints": False}},
                        tmp_path / "check", time.monotonic() + 30)
    assert result["status"] == "OK", result
    applied = apply_reuse({"snapshot": str(original.root), "proposal": result["proposal"]},
                          tmp_path / "apply", time.monotonic() + 30)
    child = load_snapshot(Path(str(applied["snapshot"])))
    assert np.array_equal(child.state.surface.mesh.faces, faces)
    assert np.array_equal(child.state.surface.mesh.vertices, vertices)
