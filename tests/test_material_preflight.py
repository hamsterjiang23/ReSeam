"""Normal/UV and ray coverage contracts, including incomplete execution."""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest
import trimesh

from reseam.agent_tools.assets import import_asset
from reseam.agent_tools.preflight import normal_uv_audit, preflight, uv_sampling_audit
from reseam.agent_tools.preflight_projection import projection_audit
from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import Snapshot, file_hash, read_object, save_snapshot, write_json
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv
from scripts.preflight_examples import cylinder, toolbox

BLENDER = Path("E:/Blender 4.5.11/blender.exe")
CONFIG = {"resolution": 128, "gap_pixels": 2, "border_pixels": 2,
          "padding_pixels": 2, "mip_levels": [0, 1, 2]}


def panel(root: Path, *, hard: bool = False, split: bool = False, evidence: bool = True,
          z: float = 0., x: float = 0.) -> Snapshot:
    vertices = np.array([[x, 0., z], [1+x, 0., z], [1+x, 1., z], [x, 1., z]])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    uv = vertices[faces, :2] * .8 + .1
    ids = np.arange(6).reshape(2, 3) if split else faces
    surface = state_from_corner_uv(make_mesh(vertices, faces), uv, corner_ids=ids)
    metadata: dict[str, object] = {"polygon_topology": {"triangle_to_polygon": [0, 0]}}
    if evidence:
        normals = np.tile([0., 0., 1.], (2, 3, 1))
        if hard:
            normals[1] = [0., 1., 0.]
        metadata["hard_surface_source"] = {"corner_normals": normals.tolist(), "sharp_vertex_edges": [],
            "objects": [{"face_start": 0, "face_count": 2, "has_custom_normals": True}]}
    return save_snapshot(BudgetState(surface), root, metadata)


def test_internal_diagonal_normal_discontinuity_not_suppressed(tmp_path: Path) -> None:
    source = panel(tmp_path / "source", hard=True)
    report = normal_uv_audit(source, None, .1)
    assert report["status"] == "fail"
    assert cast(dict, report["source"])["missing_uv_seam_edges"]
    assert cast(dict, report["target"])["status"] == "pass"
    assert cast(list[dict], report["edges"])[0]["original_polygon_diagonal"]


def test_split_coincident_is_distinct_from_missing_seam(tmp_path: Path) -> None:
    report = normal_uv_audit(panel(tmp_path / "source", hard=True, split=True), None, .1)
    assert report["status"] == "warn"
    assert cast(dict, report["source"])["missing_uv_seam_edges"] == []
    assert report["split_coincident_edges"]


def test_missing_normals_unknown_not_smooth_and_enrichment_checked(tmp_path: Path) -> None:
    old = panel(tmp_path / "old", evidence=False)
    assert cast(dict, normal_uv_audit(old, None, .1)["source"])["status"] == "not_checked"
    new = panel(tmp_path / "new")
    assert cast(dict, normal_uv_audit(old, new, .1)["source"])["status"] == "pass"
    with pytest.raises(ValueError, match="GEOMETRY_MISMATCH"):
        normal_uv_audit(old, panel(tmp_path / "wrong", z=1.), .1)


def test_evaluated_normals_override_smoothing_group_labels(tmp_path: Path) -> None:
    source = panel(tmp_path / "source")
    # Adjacent groups {1,2} / {2,3} share group 2. Importer-evaluated normals are authoritative.
    enriched = save_snapshot(source.state, tmp_path / "groups", {
        **source.metadata, "smoothing_groups": [[1, 2], [2, 3]]})
    assert cast(dict, normal_uv_audit(enriched, None, .1)["source"])["status"] == "pass"


def test_hard_cube_faces_and_smooth_cylinder_export_distinction(tmp_path: Path) -> None:
    box_report = normal_uv_audit(toolbox(tmp_path / "box"), None, .1)
    assert box_report["status"] == "pass"
    round_report = normal_uv_audit(cylinder(tmp_path / "round"), None, .1)
    assert cast(dict, round_report["source"])["status"] == "pass"
    assert cast(dict, round_report["target"])["status"] == "fail"
    assert any(row["uv_split"] and row["source_angle_degrees"] < .1
               for row in cast(list[dict], round_report["edges"]))


def test_uv_requires_settings_reports_subpixel_and_udim(tmp_path: Path) -> None:
    source = panel(tmp_path / "source", split=True)
    assert uv_sampling_audit(source, None)["status"] == "not_checked"
    report = uv_sampling_audit(source, CONFIG)
    assert report["status"] == "fail"
    assert cast(list[dict], report["mips"])[2]["effective_gap_pixels"] == 0
    outside = panel(tmp_path / "outside", x=2.)
    assert uv_sampling_audit(outside, CONFIG)["reason"] == "SINGLE_0_1_ATLAS_ONLY_UDIM_NOT_SUPPORTED"


@pytest.mark.parametrize("patch", [{"resolution": True}, {"padding_pixels": float("nan")},
                                   {"mip_levels": [0, 0]}, {"mip_levels": [99]}])
def test_invalid_bake_config_rejects(tmp_path: Path, patch: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        uv_sampling_audit(panel(tmp_path / "source"), {**CONFIG, **patch})


def test_read_only_cli_and_stale_request(tmp_path: Path) -> None:
    source = panel(tmp_path / "source")
    before = [file_hash(source.root / p) for p in ("snapshot.json", "arrays.npz")]
    request = tmp_path / "request.json"
    write_json(request, {"snapshot": str(source.root), "state_hash": source.state_hash,
                         "checks": ["geometry", "normal_uv", "uv_sampling"], "bake_config": CONFIG,
                         "views": False})
    result = run_tool("material.preflight", request, tmp_path / "run")
    assert result["execution_success"] is True
    assert result["final_quality_passed"] is None
    assert before == [file_hash(source.root / p) for p in ("snapshot.json", "arrays.npz")]
    write_json(request, {"snapshot": str(source.root), "state_hash": "stale"})
    assert run_tool("material.preflight", request, tmp_path / "stale")["reason"] == "STALE_SNAPSHOT_REQUEST"


def test_missing_blender_is_not_checked(tmp_path: Path) -> None:
    source = panel(tmp_path / "source")
    report = projection_audit(source, {"high_snapshot": str(source.root), "blender": str(tmp_path / "absent")},
                              tmp_path / "probe", time.monotonic()+30)
    assert report["status"] == "not_checked" and report["reason"] == "BLENDER_MISSING"


@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender integration dependency not installed")
@pytest.mark.parametrize(("z", "x", "expected"), [(0., 0., "hit"), (-.2, 0., "out_of_range"),
                                                  (0., 3., "miss")])
def test_actual_ray_hit_range_and_miss(tmp_path: Path, z: float, x: float, expected: str) -> None:
    low, high = panel(tmp_path / "low"), panel(tmp_path / "high", z=z, x=x)
    report = projection_audit(low, {"high_snapshot": str(high.root)}, tmp_path / "probe", time.monotonic()+60)
    assert report["complete"] is True, report
    assert cast(dict, report["counts"])[expected] == 32
    samples = [json.loads(line) for line in (tmp_path / "probe/samples.jsonl").read_text().splitlines()]
    assert len(samples) == 32 and samples[0]["low_face"] == 0
    if expected == "hit":
        assert samples[0]["hit"]["high_face"] == 0
        assert samples[0]["hit"]["distance"] == pytest.approx(.02 * 2**.5, abs=1e-6)
        assert samples[0]["nearest_surface_distance"] == pytest.approx(0, abs=1e-6)


@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender integration dependency not installed")
def test_part_filter_and_backface_hit(tmp_path: Path) -> None:
    low = panel(tmp_path / "low")
    vertices = np.concatenate((low.state.surface.mesh.vertices, low.state.surface.mesh.vertices+[0, 0, .01]))
    faces = np.concatenate((low.state.surface.mesh.faces, low.state.surface.mesh.faces[:, ::-1]+4))
    surface = state_from_corner_uv(make_mesh(vertices, faces), vertices[faces, :2], corner_ids=faces)
    high = save_snapshot(BudgetState(surface), tmp_path / "high", {"objects": [
        {"node": "correct", "face_start": 0, "face_count": 2},
        {"node": "neighbor", "face_start": 2, "face_count": 2}]})
    raw = {"high_snapshot": str(high.root)}
    report = projection_audit(low, raw, tmp_path / "global", time.monotonic()+60)
    assert cast(dict, report["counts"])["backface_hit"] == 32
    report = projection_audit(low, {**raw, "part_pairings": [{"low_faces": [0, 1], "high_objects": ["correct"]}]},
                              tmp_path / "paired", time.monotonic()+60)
    assert report["status"] == "pass"


def test_explicit_cage_requires_identity_and_exact_topology(tmp_path: Path) -> None:
    low, cage = panel(tmp_path / "low"), panel(tmp_path / "cage", z=.02)
    with pytest.raises(ValueError, match="CAGE_FOR_STATE_HASH"):
        projection_audit(low, {"high_snapshot": str(low.root), "cage_snapshot": str(cage.root)},
                         tmp_path / "bad", time.monotonic()+60)
    wrong_faces = cage.state.surface.mesh.faces[::-1]
    wrong = state_from_corner_uv(make_mesh(cage.state.surface.mesh.vertices, wrong_faces),
                                 cage.state.surface.uv[cage.state.surface.uv_faces[::-1]], corner_ids=wrong_faces)
    other = save_snapshot(BudgetState(wrong), tmp_path / "other", {"cage_for_state_hash": low.state_hash})
    with pytest.raises(ValueError, match="TOPOLOGY_MISMATCH"):
        projection_audit(low, {"high_snapshot": str(low.root), "cage_snapshot": str(other.root)},
                         tmp_path / "bad2", time.monotonic()+60)


@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender integration dependency not installed")
def test_imported_explicit_cage_is_publicly_usable(tmp_path: Path) -> None:
    low = panel(tmp_path / "low")
    obj = tmp_path / "cage.obj"
    obj.write_text("v 0 0 .02\nv 1 0 .02\nv 1 1 .02\nv 0 1 .02\n"
                   "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nf 1/1 2/2 3/3\nf 1/1 3/3 4/4\n")
    destination = tmp_path / "import_cage"
    destination.mkdir()
    imported = import_asset({"path": str(obj), "cage_for_snapshot": str(low.root),
                             "cage_for_state_hash": low.state_hash}, destination, time.monotonic()+60)
    report = projection_audit(low, {"high_snapshot": str(low.root), "cage_snapshot": imported["snapshot"],
                                   "views": False}, tmp_path / "probe", time.monotonic()+60)
    assert report["status"] == "pass"
    assert report["ray_image"] is None
    assert not (tmp_path / "probe/rays.png").exists()


@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender integration dependency not installed")
def test_explicit_zero_cage_direction_and_timeout(tmp_path: Path) -> None:
    low = panel(tmp_path / "low")
    cage = save_snapshot(low.state, tmp_path / "cage", {"cage_for_state_hash": low.state_hash})
    report = projection_audit(low, {"high_snapshot": str(low.root), "cage_snapshot": str(cage.root)},
                              tmp_path / "zero", time.monotonic()+60)
    assert cast(dict, report["counts"])["invalid_direction"] == 32
    output = tmp_path / "timeout"
    output.mkdir()
    many = cylinder(tmp_path / "many")
    result = preflight({"snapshot": str(many.root), "high_snapshot": str(many.root), "checks": ["projection"],
                        "projection": {"timeout_seconds": .001}, "views": False}, output, time.monotonic()+60)
    report = read_object(output / "preflight.json")
    assert result["execution_success"] is False
    assert report["complete"] is False
    assert cast(dict, cast(dict, report["checks"])["projection"])["samples_completed"] < 1024


@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender integration dependency not installed")
def test_nonuniform_negative_instance_transform(tmp_path: Path) -> None:
    low = panel(tmp_path / "source")
    mesh = trimesh.Trimesh(vertices=low.state.surface.mesh.vertices, faces=low.state.surface.mesh.faces,
                           process=False)
    mesh.visual = trimesh.visual.TextureVisuals(uv=low.state.surface.mesh.vertices[:, :2],
                                              material=trimesh.visual.material.PBRMaterial())
    transform = np.diag([-2., .5, 1., 1.])
    transform[:3, 3] = [3., 2., 0.]
    scene = trimesh.Scene()
    scene.add_geometry(mesh, transform=transform)
    path = tmp_path / "instance.glb"
    scene.export(path)
    destination = tmp_path / "import"
    destination.mkdir()
    imported = import_asset({"path": str(path)}, destination, time.monotonic()+60)
    from reseam.agent_tools.state import load_snapshot
    snapshot = load_snapshot(Path(str(imported["snapshot"])))
    np.testing.assert_allclose(snapshot.state.surface.mesh.vertices.min(axis=0), [1., 2., 0.])
    report = projection_audit(snapshot, {"high_snapshot": imported["snapshot"]}, tmp_path / "probe",
                              time.monotonic()+60)
    assert report["status"] == "pass" and cast(dict, report["counts"])["hit"] == 32


@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender integration dependency not installed")
def test_worker_cage_degenerate_reversed_and_nonadjacent_intersection(tmp_path: Path) -> None:
    # Invalid cages cannot enter a canonical Snapshot. Test the worker's defensive diagnostics directly.
    low = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.],
                    [.2, .2, -.5], [.2, .2, .5], [.8, .2, 0.],
                    [2., 0., 0.], [3., 0., 0.], [2., 1., 0.],
                    [4., 0., 0.], [5., 0., 0.], [4., 1., 0.]])
    faces = np.arange(12).reshape(4, 3)
    cage = low.copy()
    cage[6:9] = [2., 0., .1]
    cage[[10, 11]] = cage[[11, 10]]
    arrays = tmp_path / "arrays.npz"
    np.savez_compressed(arrays, low_vertices=low, low_faces=faces, cage_vertices=cage,
                        high_vertices=low, high_faces=faces)
    config = {"output": str(tmp_path), "arrays": str(arrays), "diagonal": 6., "seconds": 30.,
              "back_distance": .1, "part_pairings": [],
              "high_objects": [{"name": "high", "face_start": 0, "face_count": 4}]}
    write_json(tmp_path / "request.json", config)
    worker = Path(__file__).resolve().parents[1] / "scripts/preflight_projection_worker.py"
    subprocess.run([str(BLENDER), "--background", "--factory-startup", "--threads", "1", "--python-exit-code",
                    "1", "--python", str(worker), "--", str(tmp_path / "request.json")],
                    capture_output=True, check=True, timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    result = read_object(tmp_path / "projection.json")
    cage_result = cast(dict, result["cage"])
    assert 2 in cage_result["degenerate_faces"]
    assert 3 in cage_result["direction_reversal_faces"]
    assert [0, 1] in cage_result["nonadjacent_intersections"]
    assert result["status"] == "fail"
