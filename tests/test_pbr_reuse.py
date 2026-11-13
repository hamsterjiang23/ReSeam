"""Real artifacts and source-derived full PBR sharing, never last-writer maps."""

import json
import struct
import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from reseam.agent_tools import pbr_reuse
from reseam.agent_tools.color_reuse import freeze_target
from reseam.agent_tools.material import _reference, bake_material_tool, export_asset, validate_quality
from reseam.agent_tools.pbr_reuse import PBRTarget
from reseam.agent_tools.state import Snapshot, read_object, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb, interpolated_frames
from reseam.budget_state import BudgetState
from reseam.joint_types import ReuseGroup
from reseam.mesh import make_mesh
from reseam.types import MeshState


def asset(root: Path, shared: bool = False) -> Snapshot:
    vertices = np.asarray([[-2, 0, 0], [-1, 0, 0], [-2, 1, 0], [2, 0, 0], [2, 1, 0], [1, 0, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    uv = np.asarray([[.1, .2], [.4, .2], [.1, .8], [.9, .2], [.9, .8], [.6, .2]])
    surface = MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())
    color = np.full((32, 32, 4), [.8, .1, .1, 1])
    color[:, 16:] = [.1, .1, .8, 1]
    atlas = atlas_from_arrays(color, np.full((32, 32, 3), [.6, 0, .8]), np.full((32, 32, 3), [1, .6, .2]))
    reference = root / "reference.glb"
    export_material_glb(surface, atlas, reference)
    state = BudgetState(surface)
    if shared:
        surface.uv[:3] = [[.15, .15], [.85, .15], [.15, .85]]
        surface.uv[3:] = surface.uv[[0, 2, 1]]
        state.groups = (ReuseGroup("mirror", (0,), (1,), (1., 0., 0.), 0., 1, -1),)
    return save_snapshot(state, root / "snapshot", {"reference_glb": str(reference)})


def folder(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir()
    return path


def test_master_boundary_extension_is_explicit_bounded_and_audited(tmp_path: Path) -> None:
    parent = asset(tmp_path, shared=True)
    state = parent.state.copy()
    state.surface.uv[3:, 0] -= .003
    snapshot = save_snapshot(state, tmp_path / "shifted", parent.metadata, parent)
    for pixels, expected in ((0., False), (.1, False), (.5, True)):
        destination = folder(tmp_path, "target_" + str(pixels))
        result = freeze_target({"snapshot": str(snapshot.root), "mode": "pbr_reuse", "policy": "master",
            "projection_method": "same_surface_face_corner", "acceptance_policy": "visual",
            "master_boundary_tolerance_pixels": pixels,
            "geometry_profile": {"resolution": 128, "quality_constraints": False}},
            destination, time.monotonic() + 30)
        target = PBRTarget(snapshot, Path(str(result["target"])), time.monotonic() + 30)
        samples = target.sample(1, np.asarray([[1., 0., 0.]]))
        assert bool(samples.valid[0]) is expected
        assert target.boundary_projection_count == int(expected)
        if expected:
            assert target.boundary_projection_max_uv == pytest.approx(.003)


def test_full_pbr_roundtrip_mips_and_diagnostic_validation(tmp_path: Path) -> None:
    snapshot = asset(tmp_path)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(snapshot.root), "mode": "pbr_reuse", "resolution": 32}
    frozen = freeze_target(request, folder(tmp_path, "target"), deadline)
    assert frozen["status"] == "OK"
    request["target"] = frozen["target"]
    baked = bake_material_tool(request, folder(tmp_path, "bake"), deadline)
    assert baked["status"] == "OK", baked
    assert len(list((tmp_path / "bake/texture").glob("*_mip*.png"))) == 18
    request["bake"] = baked["bake"]
    checked = validate_quality(request, folder(tmp_path, "validation"), deadline)
    assert checked["quality_accepted"] is False
    report = read_object(Path(str(checked["validation"])))
    assert len(cast(list[object], report["mips"])) == 3
    request["mode"] = "diagnostic"
    delivered = export_asset(request, folder(tmp_path, "export"), deadline)
    assert delivered["quality_accepted"] is False
    assert (tmp_path / "export/asset.glb").exists()


def test_conflicting_members_reject_instead_of_overwrite(tmp_path: Path) -> None:
    snapshot = asset(tmp_path, shared=True)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(snapshot.root), "mode": "pbr_reuse", "resolution": 32}
    frozen = freeze_target(request, folder(tmp_path, "target"), deadline)
    request["target"] = frozen["target"]
    baked = bake_material_tool(request, folder(tmp_path, "bake"), deadline)
    assert baked["status"] == "REJECTED", baked
    assert baked["reason"] == "PBR_SHARED_MEMBER_CONFLICT"
    assert not (tmp_path / "bake/bake.json").exists()
    audit = read_object(tmp_path / "bake/member_audit.json")
    assert cast(dict[str, dict[str, float]], audit["groups"])["mirror"]["max_color"] > .8


def test_frozen_master_reports_modification_and_transports_normal(tmp_path: Path) -> None:
    snapshot = asset(tmp_path, shared=True)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(snapshot.root), "mode": "pbr_reuse", "policy": "master", "resolution": 32}
    frozen = freeze_target(request, folder(tmp_path, "target"), deadline)
    assert frozen["status"] == "OK", frozen
    target = PBRTarget(snapshot, Path(str(frozen["target"])), deadline)
    bary = np.asarray([[.2, .3, .5]])
    source = target.original(np.asarray([0]), bary[:, [0, 2, 1]])
    member = target.sample(1, bary)
    first, _ = interpolated_frames(*target.frames, np.asarray([0]), bary[:, [0, 2, 1]])
    second, _ = interpolated_frames(*target.frames, np.asarray([1]), bary)
    a = np.einsum("nji,nj->ni", first, source.normal_world)
    b = np.einsum("nji,nj->ni", second, member.normal_world)
    np.testing.assert_allclose(a, b, atol=1e-7)
    assert abs(a[0, 0]) > .5  # source normal texture detail is present
    changes = read_object(tmp_path / "target/target_changes.json")
    assert cast(dict[str, dict[str, list[float]]], changes["groups"])["mirror"]["maximum"][0] > .8
    request["target"] = frozen["target"]
    baked = bake_material_tool(request, folder(tmp_path, "bake"), deadline)
    assert baked["status"] == "OK", baked


def test_protected_slave_cannot_be_changed(tmp_path: Path) -> None:
    snapshot = asset(tmp_path, shared=True)
    with pytest.raises(ValueError, match="PROTECTED_PBR"):
        freeze_target({"snapshot": str(snapshot.root), "mode": "pbr_reuse", "policy": "master", "protected_faces": [1]},
                      folder(tmp_path, "target"), time.monotonic() + 60)


def test_original_source_composition_must_not_average_protected_faces(tmp_path: Path) -> None:
    snapshot = asset(tmp_path, shared=True)
    with pytest.raises(ValueError, match="PROTECTED_PBR_COMPOSITION"):
        freeze_target({"snapshot": str(snapshot.root), "mode": "pbr_reuse", "policy": "source_original",
                       "protected_faces": [0]}, folder(tmp_path, "target"), time.monotonic() + 60)


def test_frozen_unshared_target_protects_later_shared_candidate(tmp_path: Path) -> None:
    snapshot = asset(tmp_path, shared=True)
    unshared = snapshot.state.copy()
    unshared.groups = ()
    independent = save_snapshot(unshared, tmp_path / "independent", snapshot.metadata, snapshot)
    deadline = time.monotonic() + 60
    frozen = freeze_target({"snapshot": str(independent.root), "mode": "pbr_reuse", "policy": "source_original",
                           "protected_faces": [0], "acceptance_policy": "visual"}, folder(tmp_path, "target"), deadline)
    with pytest.raises(ValueError, match="PROTECTED_PBR_COMPOSITION"):
        PBRTarget(snapshot, Path(str(frozen["target"])), deadline)


def test_target_detects_source_tampering(tmp_path: Path) -> None:
    snapshot = asset(tmp_path)
    deadline = time.monotonic() + 60
    frozen = freeze_target({"snapshot": str(snapshot.root), "mode": "pbr_reuse"}, folder(tmp_path, "target"), deadline)
    source = tmp_path / "reference.glb"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="PBR_TARGET_SOURCE_CHANGED"):
        PBRTarget(snapshot, Path(str(frozen["target"])), deadline)


def test_visual_conflict_is_evidence_not_numeric_acceptance(tmp_path: Path) -> None:
    snapshot = asset(tmp_path, shared=True)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(snapshot.root), "mode": "pbr_reuse", "resolution": 32,
                                 "acceptance_policy": "visual"}
    frozen = freeze_target(request, folder(tmp_path, "target"), deadline)
    request["target"] = frozen["target"]
    baked = bake_material_tool(request, folder(tmp_path, "bake"), deadline)
    assert baked["status"] == "OK"
    assert baked["quality_accepted"] is False
    assert baked["member_conflicts"] == ["mirror"]
    request.update(bake=baked["bake"], mode="accepted")
    denied = export_asset(request, folder(tmp_path, "export"), deadline)
    assert denied["status"] == "REJECTED"


def test_export_detects_changed_frozen_target(tmp_path: Path) -> None:
    snapshot = asset(tmp_path)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(snapshot.root), "mode": "pbr_reuse", "resolution": 32}
    frozen = freeze_target(request, folder(tmp_path, "target"), deadline)
    request["target"] = frozen["target"]
    baked = bake_material_tool(request, folder(tmp_path, "bake"), deadline)
    target = Path(str(frozen["target"]))
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    request.update(bake=baked["bake"], mode="diagnostic")
    with pytest.raises(ValueError, match="PBR_BAKE_TARGET_CHANGED"):
        export_asset(request, folder(tmp_path, "export"), deadline)


def test_export_rejects_bake_under_changed_frame_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam import budget_material

    snapshot = asset(tmp_path)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(snapshot.root), "mode": "pbr_reuse", "resolution": 32}
    frozen = freeze_target(request, folder(tmp_path, "target"), deadline)
    request["target"] = frozen["target"]
    baked = bake_material_tool(request, folder(tmp_path, "bake"), deadline)
    original = budget_material.frame_arrays

    def changed(surface: MeshState) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        n, t, s = original(surface)
        return n, t, -s

    monkeypatch.setattr(budget_material, "frame_arrays", changed)
    request.update(bake=baked["bake"], mode="diagnostic")
    with pytest.raises(ValueError, match="PBR_BAKED_FRAME_CONTRACT_CHANGED"):
        export_asset(request, folder(tmp_path, "export"), deadline)


@pytest.mark.parametrize("policy", ["source_original", "master"])
def test_duplicate_query_cache_is_bit_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str) -> None:
    snapshot = asset(tmp_path, shared=True)
    deadline = time.monotonic() + 60
    frozen = freeze_target({"snapshot": str(snapshot.root), "mode": "pbr_reuse", "policy": policy},
                          folder(tmp_path, "target"), deadline)
    target = PBRTarget(snapshot, Path(str(frozen["target"])), deadline)
    cached, cached_audit = pbr_reuse.compose(snapshot, target, 32, 2, deadline)

    def duplicate(target: PBRTarget, face: int, bary: np.ndarray) -> tuple[pbr_reuse.MaterialSamples, pbr_reuse.MaterialSamples]:
        return target.original(np.full(len(bary), face, dtype=np.int64), bary), target.sample(face, bary)

    monkeypatch.setattr(pbr_reuse, "source_and_target", duplicate)
    uncached, uncached_audit = pbr_reuse.compose(snapshot, PBRTarget(snapshot, Path(str(frozen["target"])), deadline), 32, 2, deadline)
    for a, b in zip((*cached.base_color, *cached.normal, *cached.orm),
                    (*uncached.base_color, *uncached.normal, *uncached.orm), strict=True):
        np.testing.assert_array_equal(a, b)
    cached_counts = cast(dict[str, object], cached_audit.pop("source_side_orientation"))
    uncached_counts = cast(dict[str, object], uncached_audit.pop("source_side_orientation"))
    assert int(cast(int, cached_counts["queried_samples"])) < int(cast(int, uncached_counts["queried_samples"]))
    assert cached_audit == uncached_audit


@pytest.mark.parametrize("double_sided", [True, False])
def test_declared_double_sided_source_reverses_full_normal_on_opposite_low_side(tmp_path: Path, double_sided: bool) -> None:
    snapshot = asset(tmp_path)
    high = snapshot.state.surface.copy()
    high.mesh = make_mesh(high.mesh.vertices, high.mesh.faces[:, [0, 2, 1]])
    high.uv_faces = high.uv_faces[:, [0, 2, 1]]
    source = tmp_path / "opposite-high.glb"
    atlas = atlas_from_arrays(np.full((8, 8, 4), [.4, .2, .1, 1]), np.full((8, 8, 3), [.2, .6, .77]),
                             np.full((8, 8, 3), [1., .5, .2]))
    export_material_glb(high, atlas, source)
    if not double_sided:
        raw = source.read_bytes()
        size = struct.unpack_from("<I", raw, 12)[0]
        document = json.loads(raw[20:20 + size])
        document["materials"][0]["doubleSided"] = False
        text = json.dumps(document, separators=(",", ":")).encode()
        text += b" " * (-len(text) % 4)
        binary_chunk = raw[20 + size:]
        source.write_bytes(struct.pack("<III", 0x46546C67, 2, 20 + len(text) + len(binary_chunk))
                           + struct.pack("<II", len(text), 0x4E4F534A) + text + binary_chunk)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(snapshot.root), "mode": "pbr_reuse", "source_reference": str(source)}
    frozen = freeze_target(request, folder(tmp_path, "target"), deadline)
    target = PBRTarget(snapshot, Path(str(frozen["target"])), deadline)
    bary = np.asarray([[.2, .3, .5]])
    points = bary @ snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces[0]]
    raw = target.reference.sample(points, target.distance)
    oriented = target.original(np.asarray([0]), bary)
    np.testing.assert_array_equal(oriented.base_color, raw.base_color)
    np.testing.assert_array_equal(oriented.orm, raw.orm)
    np.testing.assert_allclose(oriented.normal_world, -raw.normal_world if double_sided else raw.normal_world)
    assert oriented.valid.all() == double_sided
    assert target.side_flips == int(double_sided)
    assert target.single_sided_opposite == int(not double_sided)


def test_source_authored_tangent_sign_and_exact_high_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = asset(tmp_path)
    source = tmp_path / "green.glb"
    atlas = atlas_from_arrays(np.ones((8, 8, 4)), np.full((8, 8, 3), [0., .6, .8]),
                              np.full((8, 8, 3), [1., .5, 0.]))
    export_material_glb(snapshot.state.surface, atlas, source)
    data = bytearray(source.read_bytes())
    size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + size])
    tangent = document["meshes"][0]["primitives"][0]["attributes"]["TANGENT"]
    accessor = document["accessors"][tangent]
    view = document["bufferViews"][accessor["bufferView"]]
    start = 28 + size + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    for index in range(accessor["count"]):
        offset = start + index * view.get("byteStride", 16) + 12
        sign = struct.unpack_from("<f", data, offset)[0]
        struct.pack_into("<f", data, offset, -sign)
    source.write_bytes(data)
    other = save_snapshot(snapshot.state, tmp_path / "custom-sign", {"reference_glb": str(source)})
    reference, _ = _reference(other, {})
    assert reference.metadata["custom_source_tangents"] is True
    assert np.all(reference.signs > 0)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Exact ray hit must not be replaced by nearest query")

    monkeypatch.setattr(type(reference.geometry), "sample", forbidden)
    exact = reference.sample_at(np.asarray([0, -1]), np.asarray([[.2, .3, .5], [.2, .3, .5]]),
                                np.asarray([.01, 0.]), np.asarray([True, False]))
    assert exact.valid.tolist() == [True, False]
    assert exact.normal_world[0, 1] > .5


def test_finite_ray_full_material_never_requeries_nearest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam.agent_tools.material import _SamplerReference

    snapshot = asset(tmp_path)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(snapshot.root), "mode": "pbr_reuse", "resolution": 32,
                                 "projection_method": "finite_ray", "front_fraction": .02, "back_fraction": .02}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Finite-ray material may not fall back to nearest")

    monkeypatch.setattr(_SamplerReference, "sample", forbidden)
    frozen = freeze_target(request, folder(tmp_path, "target"), deadline)
    request["target"] = frozen["target"]
    baked = bake_material_tool(request, folder(tmp_path, "bake"), deadline)
    assert baked["status"] == "OK", baked
    audit = read_object(tmp_path / "bake/member_audit.json")
    assert audit["invalid_samples"] == 0
    assert "ray_projection" in audit
    assert audit["projection"] == "finite_front_facing_rays_low_bbox_diagonal_no_nearest_fallback"
