"""V2-only transactional state; V1 mesh and validation semantics remain unchanged."""

import math
from dataclasses import dataclass, field

from .types import MeshState


@dataclass(frozen=True)
class Correspondence:
    source_face: int
    source_barycentric: tuple[float, float, float]
    target_face: int
    target_barycentric: tuple[float, float, float]
    weight: float


@dataclass(frozen=True)
class ReuseGroup:
    group_id: str
    first_faces: tuple[int, ...]
    second_faces: tuple[int, ...]
    plane_normal: tuple[float, float, float]
    plane_offset: float
    first_orientation: int = 1
    second_orientation: int = -1


@dataclass
class V2State:
    surface: MeshState
    groups: tuple[ReuseGroup, ...] = ()
    correspondences: tuple[Correspondence, ...] = ()
    version: int = 0
    material_evidence: dict[str, str] = field(default_factory=dict)
    contact_classes: tuple[int, ...] | None = None

    def copy(self) -> "V2State":
        return V2State(
            self.surface.copy(),
            self.groups,
            self.correspondences,
            self.version,
            self.material_evidence.copy(),
            self.contact_classes,
        )


@dataclass(frozen=True)
class JointProfile:
    resolution: int = 1024
    gap_pixels: float = 16
    border_pixels: float = 8
    distortion_limit: float = 0.025
    anisotropy_ratio_limit: float = 1.10
    density_floor_ratio: float = 1 - 1e-5
    density_allocation_tolerance: float = 0.10
    seam_ratio_limit: float = 1.25
    gain_epsilon: float = 1e-4
    correspondence_fraction: float = 0.005
    normal_angle_degrees: float = 45

    def __post_init__(self) -> None:
        values = (
            self.gap_pixels,
            self.border_pixels,
            self.distortion_limit,
            self.anisotropy_ratio_limit,
            self.density_floor_ratio,
            self.density_allocation_tolerance,
            self.seam_ratio_limit,
            self.gain_epsilon,
            self.correspondence_fraction,
            self.normal_angle_degrees,
        )
        if (
            type(self.resolution) is not int
            or self.resolution <= 0
            or not all(math.isfinite(v) and v > 0 for v in values)
        ):
            raise ValueError("Invalid joint profile values")
        if (
            self.density_allocation_tolerance >= 1
            or self.normal_angle_degrees > 180
            or self.border_pixels * 2 >= self.resolution
        ):
            raise ValueError("Joint profile outside supported range")
