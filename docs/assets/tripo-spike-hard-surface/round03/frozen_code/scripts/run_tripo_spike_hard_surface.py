"""Fixed existing Tripo low mesh: declared normal-map + geometric AO seam cues."""

from __future__ import annotations

import argparse
import io
import time
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image

from reseam.agent_tools.extra_uv import read_glb
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json
from reseam.budget_texture import load_reference
from scripts.build_legacy_reseam_report import audit_glb
from scripts.run_chloe_hard_surface import CAMERAS, POLICY, Trial, freeze, metrics

SOURCE = Path("F:/Model/简单/模型/2_超高模重拓扑1000/spiked dagger 3d model.glb")
WORKING = Path("F:/Aseet_Production_WorkFlow/MODEL_TEST/processed/spiked_dagger_lowpoly/spiked_dagger_retopology_final.glb")


class TripoTrial(Trial):
    def prepare(self) -> None:
        result = self.call("prepare/import", "asset.import", {"path": str(WORKING), "reference": str(SOURCE)})
        if "snapshot" not in result:
            self.state["prepare_failure"] = result
            self.save()
            return
        source = str(result["snapshot"])
        self.state["source"] = source
        self.state["reference"] = source
        self.save()
        frozen_reference = Path(str(load_snapshot(Path(source)).metadata["reference_glb"]))
        document, binary = read_glb(frozen_reference)
        normal_binding = document["materials"][0]["normalTexture"]
        image_index = document["textures"][normal_binding["index"]]["source"]
        image_record = document["images"][image_index]
        view = document["bufferViews"][image_record["bufferView"]]
        offset = view.get("byteOffset", 0)
        data = binary[offset:offset + view["byteLength"]]
        normal = self.folder / "prepare/source_normal.png"
        adjusted = self.folder / "prepare/normal_internal_uv_basis.png"
        with Image.open(io.BytesIO(data)) as image:
            image.save(normal)
            pixels = np.asarray(image.convert("RGB")).copy()
        # Public _texture uses UV with bottom-left V; glTF stored TEXCOORD uses
        # the opposite V axis. Keep the actual original map and explicitly invert
        # the green component for this diagnostic tangent-frame calculation only.
        pixels[:, :, 1] = 255 - pixels[:, :, 1]
        Image.fromarray(pixels).save(adjusted)
        write_json(self.folder / "prepare/channel_sources.json", {
            "asset": str(SOURCE), "frozen_reference": str(frozen_reference), "sha256": file_hash(frozen_reference),
            "generator": document["asset"].get("generator"),
            "normal": {"source_binding": normal_binding, "original_png": str(normal),
                "original_sha256": file_hash(normal), "cue_png": str(adjusted), "cue_sha256": file_hash(adjusted),
                "transform": "G'=255-G compensates source glTF V versus internal bottom-left UV basis; cue only",
                "scale": normal_binding.get("scale", 1), "full_normal_bake_or_shader_reproduction": False},
            "ao_texture": "not_bound", "height_displacement": "not_bound", "geometry_ao_rays": 16,
            "working_asset": str(WORKING), "working_sha256": file_hash(WORKING),
            "geometry_policy": "user-selected existing 424-face retopology fixed; source 1061-face import rejection retained; no new retopology or mirror"})
        indexed = self.call("prepare/index", "mesh.index", {"snapshot": source, "ao_rays": 16})
        self.state["mesh_index"] = indexed.get("index")
        if "index" not in indexed:
            self.state["prepare_failure"] = indexed
            self.save()
            return
        hard = self.call("prepare/hard_edges", "mesh.hard_edges", {"snapshot": source,
            "mesh_index": indexed["index"], "angle_degrees": 45})
        self.state["hard_edges"] = hard.get("report")
        if "report" not in hard:
            self.state["prepare_failure"] = hard
            self.save()
            return
        self.state["projected_normal_cues"] = self.normal_cues(source, adjusted)
        self.call("prepare/source_views", "view.render", {"snapshot": source, "mode": "basecolor",
            "size": 640, "seams": False, "cameras": CAMERAS,
            "source_mode": "projected_high", "projection_max_fraction": .08})
        self.save()

    def normal_cues(self, source: str, normal_map: Path) -> str:
        """Read-only source-surface projection; never author UV or bake geometry."""
        snapshot = load_snapshot(Path(source))
        mesh = snapshot.state.surface.mesh
        deadline = time.monotonic() + 300.
        projection_scale = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
        reference = load_reference(Path(str(snapshot.metadata["reference_glb"])))
        with Image.open(normal_map) as image:
            pixels = np.asarray(image.convert("RGB"), dtype=float) / 255.
        rows: list[dict[str, object]] = []
        for edge, entries in enumerate(mesh.edge_faces):
            if time.monotonic() >= deadline:
                raise TimeoutError("NORMAL_CUE_DIAGNOSTIC_300_SECONDS")
            if len(entries) != 2:
                continue
            points = []
            for face, _ in entries:
                bary = np.full(3, .02)
                for vertex in mesh.edges[edge]:
                    bary[int(np.flatnonzero(mesh.faces[face] == vertex)[0])] = .49
                points.append(bary @ mesh.vertices[mesh.faces[face]])
            samples = reference.sample(np.asarray(points), projection_scale * .08, deadline=deadline)
            world_normals = []
            for face, bary in zip(samples.target_faces, samples.barycentric, strict=True):
                uv = (bary @ reference.corner_uv[face]) % 1
                x, y = min(int(uv[0] * pixels.shape[1]), pixels.shape[1]-1), min(int((1-uv[1])*pixels.shape[0]), pixels.shape[0]-1)
                tangent_normal = pixels[y, x] * 2 - 1
                tri = reference.triangles[face]
                duv = reference.corner_uv[face, 1:] - reference.corner_uv[face, :1]
                if abs(float(np.linalg.det(duv))) < 1e-14:
                    break
                frame = np.linalg.solve(duv, tri[1:] - tri[:1])
                n = np.cross(tri[1]-tri[0], tri[2]-tri[0])
                n /= max(float(np.linalg.norm(n)), 1e-30)
                t = frame[0] - n * (frame[0] @ n)
                t /= max(float(np.linalg.norm(t)), 1e-30)
                b = np.cross(n, t) * np.sign(np.linalg.det(duv))
                world = tangent_normal @ np.stack((t, b, n))
                world_normals.append(world / max(float(np.linalg.norm(world)), 1e-30))
            angle = (float(np.degrees(np.arccos(np.clip(world_normals[0] @ world_normals[1], -1, 1))))
                     if len(world_normals) == 2 and samples.valid.all() else None)
            rows.append({"edge_id": edge, "normal_map_world_angle_degrees": angle,
                "source_faces": samples.target_faces.tolist(), "source_barycentric": samples.barycentric.tolist(),
                "projection_distances": samples.distances.tolist(), "valid": bool(samples.valid.all())})
        output = self.folder / "prepare/projected_normal_cues.json"
        write_json(output, {"edges": rows, "source_reference_sha256": file_hash(Path(str(snapshot.metadata["reference_glb"]))),
            "normal_map": str(normal_map), "normal_map_sha256": file_hash(normal_map),
            "projection_max_fraction": .08, "source": "real bound Tripo normal map through nearest source triangle",
            "projection_normalization": "fixed original-low AABB diagonal; same as projected_high color target",
            "projection_scale": projection_scale,
            "scope": "two inset midpoint probes per edge, approximate tangent frames; not Mikk/PBR acceptance"})
        return str(output)

    def candidates(self) -> None:
        if "source" not in self.state or not self.state.get("hard_edges") or not self.state.get("projected_normal_cues"):
            self.state["candidates"] = {}
            self.save()
            return
        source = str(self.state["source"])
        baseline = self.call("candidates/optcuts", "uv.baseline", {
            "snapshot": source, "method": "optcuts", "seed": 0})
        candidates: dict[str, str] = {}
        if "snapshot" not in baseline:
            self.state["candidates"] = candidates
            self.save()
            return
        base = str(baseline["snapshot"])
        candidates["optcuts_unshared"] = base
        snapshot = load_snapshot(Path(base))
        original = load_snapshot(Path(source))
        edge_report = read_object(Path(str(self.state["hard_edges"])))
        rows = cast(list[dict[str, object]], edge_report["edges"])
        normal_rows = cast(list[dict[str, object]], read_object(Path(str(self.state["projected_normal_cues"])))["edges"])
        normal_by_edge = {int(str(row["edge_id"])): row["normal_map_world_angle_degrees"] for row in normal_rows}
        # Keep valid OptCuts openings; add declared strong material/geometric
        # candidate edges. This combines tools without claiming new topology.
        seams = set(snapshot.state.surface.seams)
        cue_edges: list[int] = []
        for row in rows:
            geometry_angle = float(str(row["geometric_angle_degrees"]))
            normal_angle = float(str(normal_by_edge.get(int(str(row["edge_id"]))) or 0))
            occlusion = float(str(row.get("geometry_ao_occlusion") or 0))
            if row["suggested_cut"] or (normal_angle >= 45 and geometry_angle >= 15) or (occlusion >= .5 and geometry_angle >= 20):
                edge = int(str(row["edge_id"]))
                if not row["boundary"]:
                    seams.add(edge)
                    cue_edges.append(edge)
        write_json(self.folder / "candidates/cut_decision.json", {"base_snapshot": base,
            "base_openings": sorted(snapshot.state.surface.seams), "added_cue_edges": cue_edges,
            "cut_edges": sorted(seams), "reason": "Retain verified OptCuts openings; add strong normal-map/occlusion+fold cues, then inspect stretch and color",
            "thresholds_are_heuristics": True, "triangulation_diagonal_provenance": "unavailable in triangular GLB; not inferred from triangle pairs"})
        # Local smoke uses the same public backend on the largest source-UV chart.
        from reseam.mesh import chart_labels

        labels = chart_labels(snapshot.state.surface)
        smoke_faces = np.flatnonzero(labels == int(np.argmax(np.bincount(labels)))).tolist()
        if len(smoke_faces) > 150:
            # Choose an original adjacency patch rather than an arbitrary prefix.
            group = {smoke_faces[0]}
            for _ in range(3):
                group |= {f for entries in original.state.surface.mesh.edge_faces
                          if any(f in group for f, _ in entries) for f, _ in entries}
            smoke_faces = sorted(group)
        for label, faces in (("smoke", smoke_faces), ("whole", list(range(len(original.state.surface.mesh.faces))))):
            local_seams = [list(original.state.surface.mesh.edges[e]) for e in sorted(seams)
                           if all(f in faces for f, _ in original.state.surface.mesh.edge_faces[e])]
            for backend in ("blender_lscm", "blender_minimum_stretch"):
                result = self.call(f"candidates/{label}_{backend}", "uv.reparameterize", {"snapshot": base,
                    "faces": faces, "backend": backend, "seam_edges": local_seams, "profile": POLICY})
                if "snapshot" in result:
                    if label == "whole":
                        hard = str(result["snapshot"])
                        candidates["hard_unshared"] = hard
                        self.state["hard_metrics"] = metrics(hard)
                        shared = self.share(hard, "hard")
                        if shared is not None:
                            candidates["hard_shared"] = shared
                    break
            if label == "smoke" and "snapshot" not in result:
                self.state["smoke_failure"] = result
                break
        self.state["candidates"] = candidates
        self.save()

    def targets(self) -> None:
        if "source" not in self.state:
            self.state["target_failure"] = "PREPARATION_FAILED"
            self.save()
            return
        candidates = cast(dict[str, str], self.state["candidates"])
        source = candidates.get("hard_shared", candidates.get("hard_unshared", str(self.state["source"])))
        target = self.call("target/freeze", "material.target", {"snapshot": source,
            "policy": "symmetrize" if "hard_shared" in candidates else "source_original", "geometry_profile": POLICY,
            "source_mode": "projected_high", "projection_max_fraction": .08})
        if "target" not in target:
            self.state["target_failure"] = target
            self.save()
            return
        self.state["target"] = target["target"]
        self.call("target/views", "view.render", {"snapshot": source, "target": target["target"],
            "mode": "basecolor", "size": 640, "seams": False, "cameras": CAMERAS})
        self.save()

    def evaluate(self) -> None:
        review = read_object(self.folder / "target/review.json")
        if review.get("accepted") is not True or review.get("target") != self.state["target"]:
            raise ValueError("TARGET_REVIEW_REQUIRED")
        results_path = self.folder / "evaluations.json"
        rows = cast(list[dict[str, object]], read_object(results_path)["rows"]) if results_path.exists() else []
        source_geometry = read_object(Path(str(self.state["source"])))["geometry_hash"]
        candidates = cast(dict[str, str], self.state["candidates"])
        for method in ("optcuts_unshared", "hard_unshared", "hard_shared"):
            source = candidates.get(method)
            for seed in (17, 29, 43):
                key = f"{method}/seed{seed}"
                if any(row["key"] == key for row in rows):
                    continue
                row: dict[str, object] = {"geometry": "tripo_spiked_dagger", "method": method,
                    "seed": seed, "key": key, "target": self.state["target"], "source_snapshot": source}
                if source is None:
                    row["status"] = "NOT_RUN_NO_ACCEPTED_CANDIDATE"
                else:
                    packed = self.call(f"evaluate/{key}/pack", "uv.pack", {"snapshot": source,
                        "backend": "blender_concave", "density_normalize": True, "packing_seed": seed, "profile": POLICY})
                    row["status"] = packed.get("status")
                    if "snapshot" in packed:
                        current = str(packed["snapshot"])
                        row["snapshot"] = current
                        row["metrics"] = metrics(current)
                        if cast(dict[str, object], row["metrics"])["geometry_hash"] != source_geometry:
                            raise ValueError("FIXED_GEOMETRY_CHANGED")
                        row["inspection"] = self.call(f"evaluate/{key}/inspect", "uv.inspect", {"snapshot": current, "profile": POLICY})
                        bake = self.call(f"evaluate/{key}/bake", "material.bake", {"snapshot": current,
                            "mode": "basecolor_reuse", "resolution": 256, "padding_pixels": 4,
                            "target": self.state["target"], "geometry_profile": POLICY})
                        row["bake"] = bake
                        if "bake" in bake:
                            quality = self.call(f"evaluate/{key}/quality", "quality.validate", {"snapshot": current,
                                "bake": bake["bake"], "mip_levels": [0, 1, 2], "render_size": 96, "geometry_profile": POLICY})
                            row["quality"] = quality
                            if "validation" in quality:
                                exported = self.call(f"evaluate/{key}/export", "asset.export", {"snapshot": current,
                                    "bake": bake["bake"], "validation": quality["validation"],
                                    "mode": "accepted" if quality.get("quality_accepted") else "diagnostic"})
                                row["export"] = exported
                                texture = Path(str(bake["bake"])).parent / "texture/BaseColor_mip0.png"
                                row["texture"] = str(texture)
                                if "asset" in exported:
                                    audit = audit_glb(Path(str(exported["asset"])), Path(current), texture, require_uv1=False)
                                    row["reimport"] = audit
                                    write_json(self.folder / f"evaluate/{key}/reimport.json", audit)
                                    if not audit["passed"]:
                                        raise ValueError("TRIPO_GLB_REIMPORT_FAILED")
                                if seed == 17:
                                    for mode in ("basecolor", "checker", "distortion", "groups"):
                                        self.call(f"evaluate/{key}/views_{mode}", "view.render", {"snapshot": current,
                                            "mode": mode, "size": 640, "cameras": CAMERAS, "annotate": False,
                                            **({"texture": str(texture)} if mode == "basecolor" else {})})
                rows.append(row)
                write_json(self.folder / "evaluations.json", {"rows": rows})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "candidates", "targets", "evaluate"])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    freeze(args.root)
    script_record = {"path": str(Path(__file__).resolve()), "sha256": file_hash(Path(__file__))}
    protocol = args.root / "tripo_script.json"
    if protocol.exists() and read_object(protocol) != script_record:
        raise ValueError("TRIPO_SCRIPT_CHANGED_USE_NEW_ROUND")
    write_json(protocol, script_record)
    trial = TripoTrial(args.root, "original")
    try:
        getattr(trial, args.stage)()
    except Exception as error:
        write_json(trial.folder / (args.stage + "_failure.json"), {"stage": args.stage,
            "error_type": type(error).__name__, "error": str(error), "completed_outputs_retained": True})
        trial.save()
        raise
    finally:
        direct = max(0., time.monotonic() - trial.phase_started - trial.phase_tool_seconds)
        write_json(trial.folder / "direct_timing.json", {"seconds": trial.previous_direct + direct})


if __name__ == "__main__":
    main()
