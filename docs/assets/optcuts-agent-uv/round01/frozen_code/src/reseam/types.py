"""Explicit original topology and UV-copy state, independent of file importers."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
Edge = tuple[int, int]


@dataclass(frozen=True)
class Mesh:
    vertices: FloatArray
    faces: IntArray
    edges: tuple[Edge, ...]
    edge_faces: tuple[tuple[tuple[int, int], ...], ...]
    areas: FloatArray
    scale: float


@dataclass
class MeshState:
    mesh: Mesh
    seams: frozenset[int]
    uv: FloatArray
    uv_faces: IntArray
    version: int = 0

    def copy(self) -> "MeshState":
        return MeshState(self.mesh, self.seams, self.uv.copy(), self.uv_faces.copy(), self.version)


@dataclass
class Proposal:
    kind: str
    region_faces: tuple[int, ...]
    seams: frozenset[int]
    error: str | None = None


@dataclass
class Candidate:
    candidate_id: str
    proposal_type: str
    region_faces: tuple[int, ...]
    removed: frozenset[int]
    added: frozenset[int]
    state: MeshState
    base_version: int
    changed_vertices: frozenset[int]


@dataclass
class SolveResult:
    status: str
    state: MeshState | None
    seconds: float
    iterations: int = 0
    support_size: int = 0
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
