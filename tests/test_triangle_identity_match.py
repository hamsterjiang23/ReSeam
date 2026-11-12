import numpy as np

from scripts.triangle_identity_match import triangle_bijection


def fixture() -> np.ndarray:
    return np.array([[[.12344999, 0., 0., .2, .3], [1., 0., 0., .8, .3], [0., 1., 0., .2, .8]],
                     [[.12345001, 0., 0., .3, .3], [1., 0., 0., .9, .3], [0., 1., 0., .3, .8]]])


def test_rounding_boundary_and_cyclic_reorder_pass() -> None:
    expected = fixture()
    actual = expected[::-1].copy()
    actual[:, :, 0] += 4e-8
    actual = np.roll(actual, 1, axis=1)
    assert triangle_bijection(actual, expected, 1e-4)


def test_changed_uv_and_reversed_winding_fail() -> None:
    expected = fixture()
    actual = expected.copy()
    actual[0, 0, 3] += .001
    assert not triangle_bijection(actual, expected, 1e-4)
    assert not triangle_bijection(expected[:, ::-1], expected, 1e-4)


def test_nonbijective_nearest_matches_fail() -> None:
    expected = fixture()
    actual = expected.copy()
    actual[1] = actual[0]
    assert not triangle_bijection(actual, expected, 1e-4)


def test_ambiguous_matches_use_augmenting_paths() -> None:
    expected = np.repeat(fixture()[:1], 3, axis=0)
    actual = expected.copy()
    assert triangle_bijection(actual, expected, 1e-4)
