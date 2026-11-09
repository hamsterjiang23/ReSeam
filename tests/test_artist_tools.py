"""Artist workflow regressions exercise actual immutable states, textures and picking."""

import time
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from reseam.agent_tools.artist import checkpoint, groups, protect, relax, select, trials
from reseam.agent_tools.artist_review import compare, export_visual, review
from reseam.agent_tools.color_reuse import bake_color_reuse, freeze_target
from reseam.agent_tools.state import Snapshot, load_snapshot, read_object, save_snapshot, snapshot_result
from reseam.agent_tools.views import render_views
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.joint_types import ReuseGroup, ReuseMember
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.reuse_transform import similarity_transform


def deadline() -> float:
    return time.monotonic() + 60


def source(tmp_path: Path, shared: bool = False) -> Snapshot:
    vertices = np.array([[0., 0, 0], [1., 0, 0], [1., 1, 0], [0., 1, 0], [.25, .375, 0]])
    faces = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    if shared:
        vertices = np.concatenate((vertices, vertices + [2, 0, 0]))
        faces = np.concatenate((faces, faces + 5))
    mesh = make_mesh(vertices, faces)
    uv = .15 + .6*vertices[:, :2]
    if shared:
        uv[5:] = uv[:5]
    surface = state_from_corner_uv(mesh, uv[faces], corner_ids=faces)
    transform = similarity_transform([[1, 0, 0, 2], [0, 1, 0, 0], [0, 0, 1, 0]])
    sharing = (ReuseGroup("paired", (0, 1, 2, 3), (4, 5, 6, 7), (1., 0, 0), 0,
                          1, 1, transform),) if shared else ()
    atlas = atlas_from_arrays(np.full((32, 32, 4), [.2, .5, .8, 1]),
                              np.full((32, 32, 3), [0., 0, 1]), np.full((32, 32, 3), [1., 1., 0]))
    path = tmp_path / "source.glb"
    export_material_glb(surface, atlas, path)
    return save_snapshot(BudgetState(surface, sharing), tmp_path / "source",
                         {"reference_glb": str(path)})


def baked(snapshot: Snapshot, output: Path) -> str:
    output.mkdir()
    result = bake_color_reuse({**snapshot_result(snapshot), "resolution": 32, "padding_pixels": 1,
                              "acceptance_policy": "visual"}, output, deadline())
    assert result["status"] == "OK", result
    return str(result["bake"])


def test_uv_and_pixel_picking_return_all_shared_members(tmp_path: Path) -> None:
    snapshot = source(tmp_path, True)
    result = select({**snapshot_result(snapshot), "uv_point": [.4, .4]}, tmp_path / "selection", deadline())
    assert result["selected_faces"] == list(range(8))
    folder = tmp_path / "view"
    folder.mkdir()
    view = render_views({**snapshot_result(snapshot), "size": 64, "mode": "groups",
                         "cameras": [{"name": "front", "azimuth": 0, "elevation": 0}]}, folder, deadline())
    with np.load(folder / "front_faces.npz") as data:
        y, x = np.argwhere(data["face_ids"] >= 0)[0]
    result = select({**snapshot_result(snapshot), "view_pick": {"report": view["report"], "camera": "front",
                    "pixel": [int(x), int(y)]}}, tmp_path / "picked", deadline())
    assert len(cast(list[int], result["selected_faces"])) == 4
    assert result["linked_faces"] == list(range(8))


def test_relax_pins_boundary_synchronizes_partner_and_preserves_parent(tmp_path: Path) -> None:
    snapshot = source(tmp_path, True)
    result = relax({**snapshot_result(snapshot), "selected_faces": [5]}, tmp_path / "relax", deadline())
    after = load_snapshot(Path(str(result["snapshot"])))
    before_uv = snapshot.state.surface.uv[snapshot.state.surface.uv_faces]
    after_uv = after.state.surface.uv[after.state.surface.uv_faces]
    np.testing.assert_allclose(after_uv[:4], after_uv[4:], atol=1e-12)
    assert float(str(result["max_uv_motion"])) > .05
    fixed = snapshot.state.surface.uv_faces[snapshot.state.surface.mesh.faces % 5 != 4]
    np.testing.assert_array_equal(after.state.surface.uv[fixed], snapshot.state.surface.uv[fixed])
    np.testing.assert_array_equal(load_snapshot(snapshot.root).state.surface.uv[snapshot.state.surface.uv_faces], before_uv)


def test_shape_lock_enforced_at_shared_persistence_boundary(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    locked = protect({**snapshot_result(snapshot), "name": "eye", "selected_faces": [0, 1, 2, 3],
                      "kinds": ["shape"]}, tmp_path / "protect", deadline())
    with pytest.raises(ValueError, match="ARTIST_SHAPE_LOCK"):
        relax(locked | {"selected_faces": [0]}, tmp_path / "blocked", deadline())
    before = load_snapshot(Path(str(locked["snapshot"])))
    state = before.state.copy()
    state.surface.uv += [.1, .1]
    save_snapshot(state, tmp_path / "moved", before.metadata, before)
    released = protect(locked | {"name": "eye", "action": "remove"}, tmp_path / "remove", deadline())
    assert float(str(relax(released | {"selected_faces": [0]}, tmp_path / "allowed", deadline())["max_uv_motion"])) > 0


def test_checkpoint_restores_groups_locks_and_baked_attachment(tmp_path: Path) -> None:
    snapshot = source(tmp_path, True)
    bake = baked(snapshot, tmp_path / "bake")
    saved = checkpoint({**snapshot_result(snapshot), "files": {"bake": bake}}, tmp_path / "checkpoint", deadline())
    edited = groups({**snapshot_result(snapshot), "action": "release_member", "group_id": "paired", "member": 1},
                    tmp_path / "release", deadline())
    assert len(load_snapshot(Path(str(edited["snapshot"]))).state.groups) == 0
    restored = checkpoint(edited | {"action": "restore", "checkpoint": saved["checkpoint"]},
                          tmp_path / "restored", deadline())
    assert restored["state_hash"] == snapshot.state_hash
    assert len(load_snapshot(Path(str(restored["snapshot"]))).state.groups) == 1


def test_rebase_and_release_four_member_group_keeps_remaining_transforms(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    surf = snapshot.state.surface
    vertices = np.concatenate([surf.mesh.vertices + [2*i, 0, 0] for i in range(4)])
    faces = np.concatenate([surf.mesh.faces + 5*i for i in range(4)])
    corners = np.concatenate([surf.uv[surf.uv_faces] for _ in range(4)])
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners, corner_ids=faces)
    members = tuple(ReuseMember(tuple(range(4*i, 4*i+4)), 1,
                    similarity_transform([[1, 0, 0, 2*i], [0, 1, 0, 0], [0, 0, 1, 0]])) for i in range(1, 4))
    group = ReuseGroup("four", (0, 1, 2, 3), tuple(range(4, 16)), (1., 0, 0), 0, 1, 1, None, members)
    parent = save_snapshot(BudgetState(surface, (group,)), tmp_path / "four", {})
    result = groups({**snapshot_result(parent), "action": "set_master", "group_id": "four", "member": 2},
                    tmp_path / "rebase", deadline())
    rebased = load_snapshot(Path(str(result["snapshot"]))).state.groups[0]
    assert rebased.first_faces == (8, 9, 10, 11)
    assert np.asarray(rebased.member_maps[0].first_to_member)[0, 3] == -4
    released = groups(result | {"action": "release_member", "group_id": "four", "member": 2},
                      tmp_path / "release", deadline())
    assert len(load_snapshot(Path(str(released["snapshot"]))).state.groups[0].member_maps) == 2


def test_independent_trials_keep_failed_and_successful_alternatives(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    result = trials({**snapshot_result(snapshot), "alternatives": [
        {"label": "bad", "tool": "artist.relax", "request": {"selected_faces": [0], "strength": -1}},
        {"label": "good", "tool": "artist.relax", "request": {"selected_faces": [0]}}]}, tmp_path / "trials", deadline())
    rows = cast(list[dict[str, object]], result["alternatives"])
    assert cast(dict[str, object], rows[0]["result"])["status"] == "REJECTED"
    assert cast(dict[str, object], rows[1]["result"])["status"] == "OK"
    assert result["selected"] is None


def test_actual_comparison_visual_review_export_and_stale_rejection(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    bake = baked(snapshot, tmp_path / "bake")
    binding = {**snapshot_result(snapshot), "bake": bake}
    compared = compare(binding | {"before": binding, "size": 64,
                       "cameras": [{"name": "front", "azimuth": 0, "elevation": 0}]}, tmp_path / "compare", deadline())
    assert Path(str(compared["viewer"])).is_file()
    result = review({**snapshot_result(snapshot), "comparison": compared["comparison"], "decision": "keep",
                     "reviewer": "agent", "notes": "Identical source image", "reviewed_cameras": ["front"]},
                    tmp_path / "review", deadline())
    (tmp_path / "export").mkdir()
    exported = export_visual({**snapshot_result(snapshot), "review": result["review"]}, tmp_path / "export", deadline())
    assert exported["visual_accepted"] is True
    assert Path(str(exported["model"])).is_file()
    state = snapshot.state.copy()
    state.surface.uv *= .9
    changed = save_snapshot(state, tmp_path / "changed", {}, snapshot)
    with pytest.raises(ValueError, match="STALE_OR_CHANGED_VISUAL_REVIEW"):
        export_visual({**snapshot_result(changed), "review": result["review"]}, tmp_path / "stale", deadline())


def test_color_protection_reaches_target_generation(tmp_path: Path) -> None:
    snapshot = source(tmp_path, True)
    locked = protect({**snapshot_result(snapshot), "name": "claw", "selected_faces": [4], "kinds": ["color"]},
                     tmp_path / "protect", deadline())
    (tmp_path / "target").mkdir()
    with pytest.raises(ValueError, match="PROTECTED_FACE_IN_SHARED_TARGET"):
        freeze_target(locked | {"acceptance_policy": "visual"}, tmp_path / "target", deadline())


def test_numeric_gate_stays_separate_from_visual_authoring(tmp_path: Path) -> None:
    snapshot = source(tmp_path, True)
    state = snapshot.state.copy()
    state.groups = (replace(state.groups[0], first_to_second=similarity_transform(
        [[1, 0, 0, 20], [0, 1, 0, 0], [0, 0, 1, 0]])),)
    changed = save_snapshot(state, tmp_path / "wrong_correspondence", snapshot.metadata, snapshot)
    (tmp_path / "strict").mkdir()
    with pytest.raises(ValueError, match="SYMMETRIC_TARGET_REQUIRES_LEGAL_SHARED_STATE"):
        freeze_target(snapshot_result(changed), tmp_path / "strict", deadline())
    actual = baked(changed, tmp_path / "visual")
    assert read_object(Path(actual))["numeric_geometry_passed"] is False
    assert read_object(Path(actual))["acceptance_policy"] == "visual"


def test_slave_pin_is_preserved_during_synchronized_relaxation(tmp_path: Path) -> None:
    snapshot = source(tmp_path, True)
    surface = snapshot.state.surface
    pin = int(surface.uv_faces[4, 2])
    result = relax({**snapshot_result(snapshot), "selected_faces": [4], "pin_uv_ids": [pin]},
                   tmp_path / "pinned", deadline())
    after = load_snapshot(Path(str(result["snapshot"])))
    np.testing.assert_allclose(after.state.surface.uv[pin], surface.uv[pin], rtol=0, atol=1e-12)
    assert result["requested_pin_uv_ids"] == [pin]


def test_existing_target_cannot_bypass_new_color_lock(tmp_path: Path) -> None:
    snapshot = source(tmp_path, True)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = freeze_target(snapshot_result(snapshot) | {"acceptance_policy": "visual"}, target_dir, deadline())
    protected = protect(snapshot_result(snapshot) | {"name": "claw", "selected_faces": [4], "kinds": ["color"]},
                        tmp_path / "protected", deadline())
    with pytest.raises(ValueError, match="ARTIST_COLOR_LOCK_CONFLICTS_WITH_FROZEN_TARGET"):
        bake_color_reuse(protected | {"target": target["target"], "resolution": 32, "acceptance_policy": "visual"},
                         tmp_path / "bake", deadline())


def test_checkpoint_detects_changed_texture_dependency(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    bake = baked(snapshot, tmp_path / "bake")
    saved = checkpoint(snapshot_result(snapshot) | {"files": {"bake": bake}}, tmp_path / "saved", deadline())
    (Path(bake).parent / "texture/BaseColor_mip0.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="CHECKPOINT_DEPENDENCY_CHANGED"):
        checkpoint(snapshot_result(snapshot) | {"action": "restore", "checkpoint": saved["checkpoint"]},
                   tmp_path / "restore", deadline())


def test_set_master_does_not_clear_unfinished_packing(tmp_path: Path) -> None:
    snapshot = source(tmp_path, True)
    pending = save_snapshot(snapshot.state, tmp_path / "pending", {**snapshot.metadata, "requires_pack": True}, snapshot)
    result = groups(snapshot_result(pending) | {"action": "set_master", "group_id": "paired", "member": 1},
                    tmp_path / "master", deadline())
    assert result["requires_pack"] is True
    assert load_snapshot(Path(str(result["snapshot"]))).metadata["requires_pack"] is True


def test_partial_camera_review_cannot_claim_full_prepared_review(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    bake = baked(snapshot, tmp_path / "bake")
    binding = snapshot_result(snapshot) | {"bake": bake}
    compared = compare(binding | {"before": binding, "size": 64,
                       "cameras": [{"name": "front", "azimuth": 0, "elevation": 0},
                                   {"name": "back", "azimuth": 180, "elevation": 0}]}, tmp_path / "compare", deadline())
    result = review(snapshot_result(snapshot) | {"comparison": compared["comparison"], "decision": "keep",
                    "reviewer": "agent", "notes": "front only", "reviewed_cameras": ["front"]}, tmp_path / "review", deadline())
    assert result["all_prepared_views_reviewed"] is False
    with pytest.raises(ValueError, match="KEEP_WHOLE_SURFACE_COMPARISON"):
        export_visual(snapshot_result(snapshot) | {"review": result["review"]}, tmp_path / "export", deadline())


def test_protecting_new_region_does_not_silently_replace_old_one(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    first = protect(snapshot_result(snapshot) | {"name": "feature", "selected_faces": [0], "kinds": ["shape"]},
                    tmp_path / "first", deadline())
    with pytest.raises(ValueError, match="PROTECTION_NAME_IN_USE"):
        protect(first | {"name": "feature", "selected_faces": [1], "kinds": ["shape"]}, tmp_path / "collision", deadline())
    second = protect(first | {"name": "other", "selected_faces": [1], "kinds": ["shape"]}, tmp_path / "second", deadline())
    assert len(cast(list[object], load_snapshot(Path(str(second["snapshot"]))).metadata["artist_locks"])) == 2
