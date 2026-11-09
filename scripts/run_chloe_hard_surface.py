"""Chloe-only bounded experiment orchestration through public ReSeam tools.

No alternate geometry/bake implementation. Dev and formal rounds use fresh
directories; every tool request, failure and runtime remains in the ledger.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import numpy as np

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.seam_selection import _face_components
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv
from reseam.validation import intersection_errors, jacobians, seam_metrics, topology_errors

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "artifacts/legacy_assets_reseam/round02/chloe"
POLICY: dict[str, object] = {"resolution": 256, "gap_pixels": 4, "border_pixels": 4,
    "quality_constraints": False, "normal_angle_degrees": 180., "correspondence_fraction": .08}
CAMERAS = [{"name": "front", "azimuth": 0, "elevation": 0},
           {"name": "oblique", "azimuth": 35, "elevation": 15},
           {"name": "back", "azimuth": 180, "elevation": 0}]


@contextmanager
def exclusive_geometry_tool(folder: Path) -> Iterator[None]:
    """Reject simultaneous writers on one geometry; separate assets stay independent."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "execution.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("GEOMETRY_TOOL_ALREADY_RUNNING_USE_SEQUENTIAL_STAGES") from error
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("GEOMETRY_TOOL_ALREADY_RUNNING_USE_SEQUENTIAL_STAGES") from error
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def metrics(path: str) -> dict[str, object]:
    surface = load_snapshot(Path(path)).state.surface
    singular = np.linalg.svd(jacobians(surface), compute_uv=False)
    ratio = singular[:, 0] / np.maximum(singular[:, 1], 1e-30)
    return {**seam_metrics(surface), "anisotropy_median": float(np.median(ratio)),
        "anisotropy_p95": float(np.percentile(ratio, 95)), "anisotropy_max": float(ratio.max()),
        "anisotropy_above_two_faces": np.flatnonzero(ratio > 2).tolist(),
        "anisotropy_definition": "per-triangle UV/3D Jacobian sigma_max/sigma_min; face-count percentiles",
        "geometry_hash": read_object(Path(path))["geometry_hash"]}


class Trial:
    def __init__(self, root: Path, geometry: str) -> None:
        self.root = root.resolve()
        self.geometry = geometry
        self.folder = self.root / geometry
        self.state_path = self.folder / "state.json"
        self.state = read_object(self.state_path) if self.state_path.exists() else {}
        self.phase_started = time.monotonic()
        self.phase_tool_seconds = 0.
        timing = self.folder / "direct_timing.json"
        self.previous_direct = float(str(read_object(timing)["seconds"])) if timing.exists() else 0.

    def save(self) -> None:
        write_json(self.state_path, self.state)

    def call(self, label: str, tool: str, request: dict[str, object]) -> dict[str, object]:
        with exclusive_geometry_tool(self.folder):
            return self._call_serial(label, tool, request)

    def _call_serial(self, label: str, tool: str, request: dict[str, object]) -> dict[str, object]:
        output = self.folder / label
        if (output / "result.json").exists():
            original = read_object(output / "request.json")
            if any(original.get(k) != v for k, v in request.items() if k != "timeout_seconds"):
                raise ValueError("REQUEST_CHANGED_USE_NEW_ROUND:" + label)
            return read_object(output / "result.json")
        ledger = self.folder / "calls.json"
        calls = cast(list[dict[str, object]], read_object(ledger)["calls"]) if ledger.exists() else []
        spent = (sum(float(str(row["seconds"])) for row in calls) + self.previous_direct
                 + time.monotonic() - self.phase_started - self.phase_tool_seconds)
        if spent >= 2400:
            result: dict[str, object] = {"status": "BUDGET_EXHAUSTED", "tool": tool, "label": label}
            write_json(output / "request.json", request)
            write_json(output / "result.json", result)
            return result
        request = {**request, "timeout_seconds": min(300., 2400. - spent)}
        req = self.folder / "requests" / (label.replace("/", "__") + ".json")
        write_json(req, request)
        started = time.monotonic()
        result = run_tool(tool, req, output)
        seconds = time.monotonic() - started
        self.phase_tool_seconds += seconds
        calls.append({"label": label, "tool": tool, "seconds": seconds,
            "request": str(req), "result": result})
        write_json(ledger, {"calls": calls})
        print(self.geometry, label, result.get("status"), result.get("reason", ""), flush=True)
        return result

    def prepare(self) -> None:
        old = read_object(OLD / "states.json")
        if self.geometry == "original":
            original = load_snapshot(Path(str(cast(dict[str, object], old["import"])["snapshot"])))
            binding = cast(dict[str, object], original.metadata["static_color_binding"])
            result = self.call("prepare/import", "asset.import", {
                "path": original.metadata["source_path"], "basecolor_texture": binding["source_texture"],
                "uv_channel": "UVChannel_1", "collect_normal_evidence": True})
            if "snapshot" not in result:
                self.state["prepare_failure"] = result
                self.save()
                return
            snapshot = str(result["snapshot"])
            current = load_snapshot(Path(snapshot))
            if not (np.array_equal(current.state.surface.mesh.vertices, original.state.surface.mesh.vertices)
                    and np.array_equal(current.state.surface.mesh.faces, original.state.surface.mesh.faces)
                    and np.array_equal(current.state.surface.uv, original.state.surface.uv)
                    and np.array_equal(current.state.surface.uv_faces, original.state.surface.uv_faces)):
                raise ValueError("DIAGNOSTIC_IMPORT_CHANGED_SOURCE")
            write_json(self.folder / "prepare/import_parity.json", {"passed": True,
                "old_snapshot": str(original.root / "snapshot.json"), "new_snapshot": snapshot,
                "geometry_uv_identity_equal": True})
            self.state["source"] = snapshot
            self.state["reference"] = snapshot
        else:
            mirror = cast(dict[str, object], old["mirror"])
            self.state["source"] = mirror["unshared_snapshot"]
            self.state["reference"] = mirror["variant_root"]
        self.save()
        source = str(self.state["source"])
        original_state = read_object(self.root / "original/state.json")
        indexed = self.call("prepare/mesh_index", "mesh.index", {"snapshot": source, "ao_rays": 16})
        if "index" in indexed:
            self.state["mesh_index"] = indexed["index"]
        else:
            self.state["ao_failure"] = indexed
        for angle in (45, 30, 60):
            result = self.call(f"prepare/hard_edges_{angle}", "mesh.hard_edges", {
                "snapshot": source, "source_snapshot": original_state["source"], "angle_degrees": angle})
            if "report" not in result:
                self.state["prepare_failure"] = result
                self.save()
                return
            self.state[f"hard_edges_{angle}"] = result["report"]
        self.state["source_views"] = self.call("prepare/source_views", "view.render", {
            "snapshot": source, "mode": "basecolor", "size": 640, "cameras": CAMERAS,
            "seam_edge_ids": read_object(Path(str(self.state["hard_edges_45"])))["edge_ids"],
            "annotate": False})
        self.save()

    def smoke(self) -> None:
        if "hard_edges_45" not in self.state:
            self.state["smoke_local_backend_passed"] = False
            self.save()
            return
        source = str(self.state["source"])
        snapshot = load_snapshot(Path(source))
        mesh = snapshot.state.surface.mesh
        report = read_object(Path(str(self.state["hard_edges_45"])))
        components = _face_components(mesh, set(range(len(mesh.faces))), set(cast(list[int], report["edge_ids"])))
        eligible = [c for c in components if 2 <= len(c) <= 80]
        if not eligible:
            raise ValueError("NO_LOCAL_SMOKE_REGIONS")
        # These are geometric selection hypotheses; images must confirm semantics.
        centers = mesh.vertices[mesh.faces].mean(axis=1)
        center = mesh.vertices.mean(axis=0)
        tip = max(eligible, key=lambda c: float(np.linalg.norm(centers[sorted(c)] - center, axis=1).max()))
        flat = max(eligible, key=lambda c: float(mesh.areas[sorted(c)].sum()))
        remaining = [c for c in eligible if c != tip and c != flat]
        fold = min(remaining or eligible, key=lambda c: float(np.linalg.norm(centers[sorted(c)].mean(axis=0)-center)))
        regions = {"tip_hypothesis": sorted(tip), "fold_hypothesis": sorted(fold), "flat_hypothesis": sorted(flat)}
        self.state["smoke_regions"] = regions
        for name, faces in regions.items():
            defined = self.call(f"smoke/{name}/define", "region.define", {"snapshot": source,
                "name": name, "faces": faces, "role": "diagnostic", "rationale": "geometric smoke hypothesis; see image review",
                "evidence": str(self.state["hard_edges_45"])})
            selected = str(defined.get("snapshot", source))
            seams = [list(mesh.edges[e]) for e in cast(list[int], report["edge_ids"])
                     if all(f in faces for f, _ in mesh.edge_faces[e])]
            result = self.call(f"smoke/{name}/lscm", "uv.reparameterize", {
                "snapshot": selected, "faces": faces, "backend": "blender_lscm",
                "seam_edges": seams, "profile": POLICY})
            if "snapshot" not in result:
                result = self.call(f"smoke/{name}/minimum_stretch", "uv.reparameterize", {
                    "snapshot": selected, "faces": faces, "backend": "blender_minimum_stretch",
                    "seam_edges": seams, "profile": POLICY})
            self.call(f"smoke/{name}/views", "view.render", {"snapshot": result.get("snapshot", selected),
                "selected_faces": faces, "mode": "checker", "size": 512, "cameras": CAMERAS})
            audits = []
            for native in (self.folder / f"smoke/{name}").glob("*/blender/unwrapped.npz"):
                with np.load(native, allow_pickle=False) as data:
                    local = state_from_corner_uv(make_mesh(data["vertices"], data["faces"]),
                        data["corner_uv"], corner_ids=data["corner_ids"])
                record = read_object(native.parent.parent / "reparameterization.json")
                original_ids = np.asarray(record["temporary_original_vertex_ids"])
                same_faces = np.array_equal(original_ids[local.mesh.faces], mesh.faces[faces])
                local_errors = [*topology_errors(local), *intersection_errors(local)]
                signs = np.sign(np.linalg.det(jacobians(local)))
                labels = chart_labels(local)
                orientation_ok = all(len(np.unique(signs[labels == label])) == 1
                                     and np.all(signs[labels == label] != 0) for label in np.unique(labels))
                audits.append({"backend_output": str(native), "indexed_faces_preserved": bool(same_faces),
                    "local_errors": local_errors, "orientation_consistent": bool(orientation_ok),
                    "local_backend_passed": bool(same_faces and not local_errors and orientation_ok),
                    "full_snapshot_accepted": (native.parent.parent / "snapshot/snapshot.json").exists()})
            write_json(self.folder / f"smoke/{name}/native_audit.json", {"audits": audits})
        self.state["smoke_local_backend_passed"] = all(any(
            row["local_backend_passed"] for row in cast(list[dict[str, object]], read_object(
                self.folder / f"smoke/{name}/native_audit.json")["audits"])) for name in regions)
        self.save()

    def candidates(self) -> None:
        if not self.state.get("smoke_local_backend_passed"):
            self.state["candidates"] = {}
            self.state["candidate_failure"] = "LOCAL_BACKEND_SMOKE_NOT_PASSED"
            self.save()
            return
        source = str(self.state["source"])
        snapshot = load_snapshot(Path(source))
        candidates: dict[str, str] = {}
        baseline = self.call("candidates/optcuts", "uv.baseline", {
            "snapshot": source, "method": "optcuts", "seed": 0})
        if "snapshot" in baseline:
            candidates["optcuts_unshared"] = str(baseline["snapshot"])
        trials: list[dict[str, object]] = []
        # All thresholds have the same indexed geometry and source signal.
        # Try the default threshold first; extra attempts are kept, not overwritten.
        for angle in (45, 60, 30):
            seams = read_object(Path(str(self.state[f"hard_edges_{angle}"])))["vertex_edges"]
            for backend in ("blender_lscm", "blender_minimum_stretch"):
                result = self.call(f"candidates/hard_{angle}_{backend}", "uv.reparameterize", {
                    "snapshot": source, "faces": list(range(len(snapshot.state.surface.mesh.faces))),
                    "backend": backend, "seam_edges": seams, "profile": POLICY})
                if "snapshot" in result:
                    measured = metrics(str(result["snapshot"]))
                    trials.append({"snapshot": result["snapshot"], "angle": angle, "backend": backend,
                        "metrics": measured})
                    if float(str(measured["anisotropy_p95"])) <= 2:
                        break
            if trials:
                break
        self.state["hard_trials"] = trials
        if not trials:
            enriched = read_object(self.root / "original/state.json")["source"]
            planned = self.call("candidates/polygon_fan_plan", "mesh.hard_edges", {
                "snapshot": source, "source_snapshot": enriched, "angle_degrees": 45, "patch_polygons": 8,
                **({"mesh_index": self.state["mesh_index"]} if "mesh_index" in self.state else {})})
            plan = (cast(dict[str, object], read_object(Path(str(planned["report"])))["patch_plan"])
                    if "report" in planned else None)
            for backend in (("blender_lscm", "blender_minimum_stretch") if plan is not None else ()):
                assert plan is not None
                result = self.call(f"candidates/polygon_fans_{backend}", "uv.reparameterize", {
                    "snapshot": source, "faces": list(range(len(snapshot.state.surface.mesh.faces))),
                    "backend": backend, "seam_edges": plan["vertex_edges"], "profile": POLICY})
                if "snapshot" in result:
                    measured = metrics(str(result["snapshot"]))
                    trials.append({"snapshot": result["snapshot"], "angle": 45, "backend": backend,
                        "cut_strategy": plan["method"], "metrics": measured})
                    if float(str(measured["anisotropy_p95"])) <= 2:
                        break
        if trials:
            chosen = min(trials, key=lambda row: float(str(cast(dict[str, object], row["metrics"])["anisotropy_p95"])))
            hard = str(chosen["snapshot"])
            candidates["hard_unshared"] = hard
            self.state["hard_choice"] = chosen
            shared = self.share(hard, "hard")
            if shared is not None:
                candidates["hard_shared"] = shared
        if self.geometry == "mirror":
            pairs = self.mirror_pairs()
            result = self.call("candidates/harmonic", "uv.reparameterize", {"snapshot": source,
                "faces": pairs[:, 0].tolist(), "backend": "harmonic", "seam_edges": [], "profile": POLICY})
            if "snapshot" in result:
                shared = self.share(str(result["snapshot"]), "harmonic")
                if shared is not None:
                    candidates["harmonic_shared"] = shared
        self.state["candidates"] = candidates
        self.save()

    def mirror_pairs(self) -> np.ndarray:
        snapshot = load_snapshot(Path(str(self.state["source"])))
        with np.load(str(snapshot.metadata["source_correspondence"]), allow_pickle=False) as data:
            return data["face_pairs"].copy()

    def share(self, source: str, prefix: str) -> str | None:
        current = source
        if self.geometry == "mirror":
            pairs = self.mirror_pairs()
            partner = {int(a): int(b) for a, b in pairs}
            labels = chart_labels(load_snapshot(Path(source)).state.surface)
            proposals = [{"first_faces": [int(f) for f in pairs[:, 0] if labels[f] == label],
                          "plane_normal": [1., 0., 0.], "plane_offset": .7814623713493347}
                         for label in np.unique(labels[pairs[:, 0]])]
            for row in proposals:
                row["second_faces"] = [partner[f] for f in cast(list[int], row["first_faces"])]
            modes = ("exact", "topology")
        else:
            found = self.call(f"candidates/{prefix}_find", "reuse.find_candidates", {
                "snapshot": current, "max_candidates": 3, "max_regions": 512,
                "center_fraction": .08, "minimum_area_ratio": .7})
            if "report" not in found:
                return None
            rows = cast(list[dict[str, object]], read_object(Path(str(found["report"])))["candidates"])
            proposals = [cast(dict[str, object], row["check_request"]) for row in rows]
            modes = ("exact", "topology", "approximate")
        for index, row in enumerate(proposals):
            used = {f for g in load_snapshot(Path(current)).state.groups for f in g.first_faces + g.second_faces}
            if used.intersection(cast(list[int], row["first_faces"]) + cast(list[int], row["second_faces"])):
                continue
            for mode in modes:
                checked = self.call(f"candidates/{prefix}_pair{index}_{mode}", "reuse.check_pair", {
                    **{k: v for k, v in row.items() if k not in ("state_hash", "snapshot", "mode", "asset_id")},
                    "snapshot": current, "mode": mode, "profile": POLICY})
                if "proposal" not in checked:
                    continue
                applied = self.call(f"candidates/{prefix}_pair{index}_apply", "reuse.apply", {
                    "snapshot": current, "proposal": checked["proposal"]})
                if "snapshot" in applied:
                    current = str(applied["snapshot"])
                break
        return current if load_snapshot(Path(current)).state.groups else None

    def targets(self) -> None:
        if "source" not in self.state:
            self.state["target_failure"] = "PREPARATION_FAILED"
            self.save()
            return
        candidates = cast(dict[str, str], self.state["candidates"])
        source = candidates.get("hard_shared", candidates.get("hard_unshared", str(self.state["source"])))
        policy = "symmetrize" if self.geometry == "original" and "hard_shared" in candidates else "source_original"
        target = self.call("target/freeze", "material.target", {
            "snapshot": source, "policy": policy, "geometry_profile": POLICY})
        if "target" not in target:
            self.state["target_failure"] = target
            self.save()
            return
        self.state["target"] = target["target"]
        self.state["target_views"] = self.call("target/views", "view.render", {
            "snapshot": source, "target": target["target"], "mode": "basecolor",
            "size": 640, "seams": False, "cameras": CAMERAS})
        self.call("target/color_sphere", "reuse.check_color", {
            "snapshot": source, "target": target["target"], "level": "medium"})
        self.save()

    def evaluate(self) -> None:
        review_path = self.folder / "target/review.json"
        review = read_object(review_path) if review_path.exists() else {}
        if review.get("accepted") is not True or review.get("target") != self.state["target"]:
            raise ValueError("TARGET_VISUAL_REVIEW_REQUIRED_BEFORE_BAKE")
        source_hash = read_object(Path(str(self.state["source"])))["geometry_hash"]
        candidates = cast(dict[str, str], self.state["candidates"])
        results_path = self.folder / "evaluations.json"
        rows = cast(list[dict[str, object]], read_object(results_path)["rows"]) if results_path.exists() else []
        methods = ["optcuts_unshared", "hard_unshared", "hard_shared"]
        if self.geometry == "mirror":
            methods.append("harmonic_shared")
        for method in methods:
            source = candidates.get(method)
            for seed in (17, 29, 43):
                key = f"{method}/seed{seed}"
                if any(row["key"] == key for row in rows):
                    continue
                row: dict[str, object] = {"key": key, "geometry": self.geometry, "method": method,
                    "seed": seed, "source_snapshot": source, "target": self.state["target"], "resolution": 256}
                if source is None:
                    row["status"] = "NOT_RUN_NO_ACCEPTED_CANDIDATE"
                    rows.append(row)
                    write_json(results_path, {"rows": rows})
                    continue
                packed = self.call(f"evaluate/{key}/pack", "uv.pack", {"snapshot": source,
                    "backend": "blender_concave", "density_normalize": True, "packing_seed": seed, "profile": POLICY})
                row["packing"] = packed
                if "snapshot" not in packed:
                    row["status"] = "PACK_REJECTED"
                    rows.append(row)
                    write_json(results_path, {"rows": rows})
                    continue
                current = str(packed["snapshot"])
                measured = metrics(current)
                if measured["geometry_hash"] != source_hash:
                    raise ValueError("CANDIDATE_FIXED_GEOMETRY_CHANGED")
                row["snapshot"] = current
                row["metrics"] = measured
                row["inspection"] = self.call(f"evaluate/{key}/inspect", "uv.inspect", {
                    "snapshot": current, "profile": POLICY})
                bake = self.call(f"evaluate/{key}/bake", "material.bake", {
                    "snapshot": current, "target": self.state["target"], "mode": "basecolor_reuse",
                    "resolution": 256, "padding_pixels": 4, "geometry_profile": POLICY})
                row["bake"] = bake
                row["status"] = bake.get("status")
                if "bake" in bake:
                    quality = self.call(f"evaluate/{key}/quality", "quality.validate", {
                        "snapshot": current, "bake": bake["bake"], "mip_levels": [0, 1, 2],
                        "render_size": 96, "geometry_profile": POLICY})
                    row["quality"] = quality
                    if "validation" not in quality:
                        row["status"] = "VALIDATION_" + str(quality.get("status"))
                        rows.append(row)
                        write_json(results_path, {"rows": rows})
                        continue
                    row["export"] = self.call(f"evaluate/{key}/export", "asset.export", {
                        "snapshot": current, "bake": bake["bake"], "validation": quality["validation"],
                        "mode": "accepted" if quality.get("quality_accepted") else "diagnostic"})
                    texture = Path(str(bake["bake"])).parent / "texture/BaseColor_mip0.png"
                    row["texture"] = str(texture)
                    from scripts.build_legacy_reseam_report import audit_glb

                    exported = row["export"]
                    assert isinstance(exported, dict)
                    glbs = list((self.folder / f"evaluate/{key}/export").rglob("*.glb"))
                    if len(glbs) == 1:
                        audit = audit_glb(glbs[0], Path(current), texture, require_uv1=True)
                        write_json(self.folder / f"evaluate/{key}/reimport.json", audit)
                        row["reimport"] = audit
                        if not audit["passed"]:
                            raise ValueError("EXPORTED_GLB_REIMPORT_FAILED")
                    if seed == 17:
                        for mode in ("basecolor", "checker", "distortion", "groups"):
                            self.call(f"evaluate/{key}/views_{mode}", "view.render", {
                                "snapshot": current, "mode": mode, "size": 640, "cameras": CAMERAS,
                                "seams": mode != "basecolor", "annotate": False,
                                **({"texture": str(texture)} if mode == "basecolor" else {})})
                        for name, faces in cast(dict[str, list[int]], self.state["smoke_regions"]).items():
                            self.call(f"evaluate/{key}/closeup_{name}", "view.render", {
                                "snapshot": current, "texture": str(texture), "mode": "basecolor", "size": 384,
                                "selected_faces": faces, "cameras": CAMERAS, "seams": False})
                rows.append(row)
                write_json(results_path, {"rows": rows})


def freeze(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    files = sorted((ROOT / "src/reseam").rglob("*.py"))
    files += sorted((ROOT / "scripts").glob("agent_*.py"))
    files += [Path(__file__).resolve(), ROOT / "skills/reseam-hard-surface/SKILL.md"]
    files += [ROOT / "scripts/build_legacy_reseam_report.py"]
    files += sorted((ROOT / "baseline").glob("*.py"))
    files += sorted((ROOT / "baseline/adapters").glob("*.py"))
    record = {str(p): file_hash(p) for p in files}
    path = root / "code_freeze.json"
    if path.exists() and read_object(path)["sha256"] != record:
        raise ValueError("CODE_CHANGED_USE_NEW_ROUND")
    write_json(path, {"sha256": record})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "smoke", "candidates", "targets", "evaluate"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--geometry", choices=["original", "mirror"], required=True)
    args = parser.parse_args()
    freeze(args.root)
    trial = Trial(args.root, args.geometry)
    try:
        getattr(trial, args.stage)()
    finally:
        direct = max(0., time.monotonic() - trial.phase_started - trial.phase_tool_seconds)
        write_json(trial.folder / "direct_timing.json", {"seconds": trial.previous_direct + direct,
            "scope": "in-process measurements, audits and orchestration in addition to calls.json; excludes coding and idle review"})


if __name__ == "__main__":
    main()
