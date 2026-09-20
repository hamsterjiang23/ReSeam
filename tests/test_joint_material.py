from pathlib import Path

import numpy as np
from PIL import Image

from reseam.joint_material import compose_material, state_fingerprint
from reseam.joint_state import accept_joint
from reseam.joint_types import Correspondence, JointProfile, ReuseGroup, V2State
from reseam.mesh import make_mesh
from reseam.pipeline import write_json
from reseam.types import MeshState


def fixture(tmp_path: Path, mismatch: bool) -> tuple[V2State, Path]:
    vertices = np.asarray([[-1, 0, 0], [-1, 0, 1], [-1, 1, 0], [1, 0, 0], [1, 1, 0], [1, 0, 1]], dtype=float)
    faces = np.arange(6).reshape(2, 3)
    uv = vertices[:, 1:] * 0.25 + 0.2
    state = V2State(
        MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy()),
        (ReuseGroup("g", (0,), (1,), (1.0, 0, 0), 0, -1, 1),),
    )
    folder = tmp_path / "bake"
    folder.mkdir()
    for slot in range(3):
        Image.new("RGBA", (64, 64), (100, 100, 100, 255)).save(folder / f"BaseColor_{slot:03d}.png")
        normal = (255, 128, 128, 255) if mismatch and slot == 2 else (128, 128, 255, 255)
        Image.new("RGBA", (64, 64), normal).save(folder / f"Normal_{slot:03d}.png")
    frames = [
        [{"normal": [sign, 0, 0], "tangent": [0, 1, 0], "sign": sign} for _ in range(3)] for sign in (-1, 1)
    ]
    write_json(folder / "result.json", {"corner_frames": frames})
    return state, folder


def test_mirrored_tbn_decodes_shared_normal_without_false_error(tmp_path: Path) -> None:
    state, bake = fixture(tmp_path, False)
    result = compose_material(
        state, bake, tmp_path / "material", JointProfile(resolution=64, gap_pixels=4, border_pixels=4)
    )
    assert result["valid"] and all(r["normal_max_degrees"] < 0.001 for r in result["faces"])
    assert result["state_fingerprint"] == state_fingerprint(state)
    assert (tmp_path / "material/BaseColor.png").exists()


def test_conflicting_normal_rejected_for_both_members(tmp_path: Path) -> None:
    state, bake = fixture(tmp_path, True)
    result = compose_material(
        state, bake, tmp_path / "material", JointProfile(resolution=64, gap_pixels=4, border_pixels=4)
    )
    assert not result["valid"] and set(result["failed_faces"]) == {0, 1}
    assert all(r["normal_mean_degrees"] > 30 for r in result["faces"])


def test_subpixel_face_filters_color_in_linear_space(tmp_path: Path) -> None:
    state, bake = fixture(tmp_path, False)
    state.surface.uv = (state.surface.uv - 0.2) * 0.0008 + 0.4999
    stripes = np.zeros((64, 64, 4), dtype=np.uint8)
    stripes[:, 32:, :3] = 255
    stripes[:, :, 3] = 255
    for slot in range(3):
        Image.fromarray(stripes).save(bake / f"BaseColor_{slot:03d}.png")
    result = compose_material(
        state, bake, tmp_path / "material", JointProfile(resolution=64, gap_pixels=4, border_pixels=4)
    )
    # No texel centre lies in these tiny triangles: black delivered output versus
    # approximately 0.5 LINEAR reference; sRGB-first interpolation would give ~0.214.
    assert all(r["samples"] == 64 and 0.48 < r["color_mean"] < 0.52 for r in result["faces"])
    assert not result["valid"]


def test_legal_shared_candidate_commits_with_material_certificate(tmp_path: Path) -> None:
    candidate, bake = fixture(tmp_path, False)
    bary = (1 / 3, 1 / 3, 1 / 3)
    candidate.correspondences = (Correspondence(0, bary, 1, bary, 1.0), Correspondence(1, bary, 0, bary, 1.0))
    reference = candidate.surface.copy()
    reference.uv = reference.mesh.vertices[:, 1:].copy() * 0.1
    reference.uv[:3, 0] *= -1
    reference.uv[:3] += [0.2, 0.1]
    reference.uv[3:] += [0.6, 0.1]
    current = V2State(reference)
    profile = JointProfile(resolution=64, gap_pixels=4, border_pixels=4)
    material = tmp_path / "material"
    assert compose_material(candidate, bake, material, profile)["valid"]
    candidate.material_evidence = {"g": str(material)}
    result, decision = accept_joint(reference, current, candidate, 0, profile)
    assert decision["status"] == "ACCEPTED" and result.version == 1
    assert decision["quality"]["gain"] > 2.49 and len(result.groups) == 1
    assert current.version == 0 and not current.groups and not current.material_evidence
