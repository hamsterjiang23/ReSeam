"""Native scientific OBJ rounding cannot erase original indexed face identity."""

from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.reparameterize import _import_optcuts
from reseam.mesh import make_mesh, state_from_corner_uv


def test_optcuts_original_indices_survive_scientific_rounding(tmp_path: Path) -> None:
    vertices = np.array([[.123456789, 0., 0.], [.223456789, 0., 0.], [.123456789, .1, 0.]])
    faces = np.array([[0, 1, 2]])
    source = state_from_corner_uv(make_mesh(vertices, faces), np.array([[[0., 0.], [1., 0.], [0., 1.]]]),
                                  corner_ids=faces)
    path = tmp_path / "native.obj"
    path.write_text("".join("v " + " ".join(format(float(x), ".6e") for x in row) + "\n" for row in vertices)
                    + "vt 0 0\nvt 0.5 0\nvt 0 0.5\nf 1/1 2/2 3/3\n", encoding="utf-8")
    imported = _import_optcuts(source, path)
    np.testing.assert_array_equal(imported.mesh.vertices, vertices)
    np.testing.assert_array_equal(imported.mesh.faces, faces)
    np.testing.assert_allclose(imported.uv[imported.uv_faces], [[[0., 0.], [.5, 0.], [0., .5]]])
    path.write_text(path.read_text().replace("1.234568e-01", "9.234568e-01"), encoding="utf-8")
    with pytest.raises(ValueError, match="IDENTITY_AMBIGUOUS"):
        _import_optcuts(source, path)


def test_reordered_coincident_opposite_faces_use_winding(tmp_path: Path) -> None:
    vertices = np.tile(np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]), (2, 1))
    faces = np.array([[0, 1, 2], [3, 5, 4]])
    source = state_from_corner_uv(make_mesh(vertices, faces), np.zeros((2, 3, 2)), corner_ids=faces)
    path = tmp_path / "opposite.obj"
    path.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\n"
                    "vt 0 0\nvt 1 0\nvt 0 1\n"
                    "f 1/1 3/3 2/2\nf 1/1 2/2 3/3\n", encoding="utf-8")
    imported = _import_optcuts(source, path)
    np.testing.assert_array_equal(imported.mesh.faces, faces)
    np.testing.assert_allclose(imported.uv[imported.uv_faces],
        [[[0, 0], [1, 0], [0, 1]], [[0, 0], [0, 1], [1, 0]]])
