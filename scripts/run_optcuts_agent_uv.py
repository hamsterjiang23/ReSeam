"""Existing tools only: OptCuts proposes cuts; Agent selects adjacent-island edits."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import cast

import numpy as np

from reseam.agent_tools.seam_selection import _geometry
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json
from reseam.mesh import chart_labels
from reseam.validation import jacobians
from scripts.build_legacy_reseam_report import audit_glb
from scripts.run_chloe_hard_surface import CAMERAS, POLICY, Trial, freeze, metrics

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "tripo": ROOT / "artifacts/tripo_spike_hard_surface/round03/original/prepare/import/snapshot/snapshot.json",
    "dragon": ROOT / "artifacts/meshquery_v1/midplane_cleanup01/create/unshared/snapshot.json",
}


class AgentTrial(Trial):
    def prepare(self) -> None:
        source = str(SOURCES[self.geometry])
        original = load_snapshot(Path(source))
        self.state["source"] = source
        self.state["source_geometry_hash"] = read_object(Path(source))["geometry_hash"]
        result = self.call("initial/optcuts", "uv.baseline", {"snapshot": source, "method": "optcuts", "seed": 0})
        self.state["optcuts"] = result
        self.save()
        if "snapshot" not in result:
            return
        current = str(result["snapshot"])
        base = load_snapshot(Path(current))
        labels = chart_labels(base.state.surface)
        mesh = base.state.surface.mesh
        if not np.array_equal(mesh.faces, original.state.surface.mesh.faces):
            raise ValueError("OPTCUTS_CHANGED_ORIGINAL_FACE_IDS")
        _, angles, _ = _geometry(mesh)
        adjacency: dict[tuple[int, int], list[int]] = {}
        for edge, entries in enumerate(mesh.edge_faces):
            if len(entries) == 2:
                a, b = sorted(int(labels[f]) for f, _ in entries)
                if a != b:
                    adjacency.setdefault((a, b), []).append(edge)
        proposals = []
        for (a, b), edges in adjacency.items():
            faces = np.flatnonzero((labels == a) | (labels == b)).tolist()
            if len(faces) > 1800:
                continue
            proposals.append({"charts": [a, b], "faces": faces, "remove_edge_ids": edges,
                "boundary_mean_angle": float(np.degrees(angles[edges]).mean()),
                "boundary_max_angle": float(np.degrees(angles[edges]).max()),
                "face_count": len(faces), "surface_area": float(mesh.areas[faces].sum()),
                "reason": "adjacent original edges; inspect natural patch before deleting common UV boundary"})
        proposals.sort(key=lambda p: (float(str(p["boundary_mean_angle"])), int(str(p["face_count"]))))
        self.state["proposals"] = proposals[:12]
        self.state["baseline_metrics"] = metrics(current)
        self.save()
        write_json(self.folder / "initial/adjacent_islands.json", {"state_hash": base.state_hash,
            "proposals": proposals[:12], "selection_is_agent_decision": True,
            "other_optcuts_openings_retained": True})
        self.call("initial/charts", "view.render", {"snapshot": current, "mode": "groups",
            "size": 640, "cameras": CAMERAS, "seams": True, "annotate": True})
        for index, row in enumerate(proposals[:3]):
            self.call(f"initial/proposal{index}_views", "view.render", {"snapshot": current,
                "selected_faces": row["faces"], "mode": "groups", "size": 384,
                "cameras": CAMERAS, "seams": True, "annotate": False})

    def merge(self, index: int) -> None:
        base_result = cast(dict[str, object], self.state["optcuts"])
        if "snapshot" not in base_result:
            return
        source = str(base_result["snapshot"])
        base = load_snapshot(Path(source))
        row = cast(list[dict[str, object]], self.state["proposals"])[index]
        faces = cast(list[int], row["faces"])
        evidence = self.folder / f"initial/proposal{index}_views/views.json"
        if not evidence.is_file():
            rendered = self.call(f"initial/proposal{index}_views", "view.render", {
                "snapshot": source, "selected_faces": faces, "mode": "groups", "size": 384,
                "cameras": CAMERAS, "seams": True, "annotate": False})
            self.state["merge_review_required"] = {"proposal_index": index, "views": rendered,
                "reason": "Inspect generated local evidence before rerunning the selected merge"}
            self.save()
            return
        self.state.pop("merge_review_required", None)
        removed = set(cast(list[int], row["remove_edge_ids"]))
        mesh = base.state.surface.mesh
        retained = [list(mesh.edges[e]) for e in sorted(base.state.surface.seams - removed)
                    if all(f in faces for f, _ in mesh.edge_faces[e])]
        defined = self.call(f"merge{index}/region", "region.define", {"snapshot": source,
            "name": f"agent_adjacent_patch_{index}", "faces": faces,
            "rationale": "Agent-selected adjacent patch; remove only their common boundary, preserve other OptCuts openings",
            "evidence": str(evidence)})
        attempts = cast(list[dict[str, object]], self.state.get("merge_attempts", []))
        for backend in ("blender_lscm", "blender_minimum_stretch"):
            accepted_current = False
            result = self.call(f"merge{index}/{backend}", "uv.reparameterize", {
                "snapshot": defined.get("snapshot", source), "faces": faces,
                "backend": backend, "seam_edges": retained, "profile": POLICY})
            attempt: dict[str, object] = {"proposal_index": index, "backend": backend, "result": result,
                "removed_boundary_edges": sorted(removed), "retained_seam_vertex_pairs": retained}
            if "snapshot" in result:
                snapshot = str(result["snapshot"])
                measured = metrics(snapshot)
                current = load_snapshot(Path(snapshot))
                old_s = np.linalg.svd(jacobians(base.state.surface), compute_uv=False)
                new_s = np.linalg.svd(jacobians(current.state.surface), compute_uv=False)
                old_ratio = old_s[faces, 0] / old_s[faces, 1]
                new_ratio = new_s[faces, 0] / new_s[faces, 1]
                local_max = float(new_ratio.max())
                candidate_ok = (int(str(measured["charts"])) < int(str(cast(dict[str, object], self.state["baseline_metrics"])["charts"]))
                    and local_max <= max(2., float(old_ratio.max()) * 1.1))
                attempt.update(metrics=measured, local_max=local_max,
                    local_p95=float(np.percentile(new_ratio, 95)), numeric_screen=candidate_ok,
                    screen_policy="heuristic stretch screen; still requires Agent appearance review")
                self.call(f"merge{index}/{backend}_checker", "view.render", {"snapshot": snapshot,
                    "selected_faces": faces, "mode": "checker", "size": 384, "cameras": CAMERAS})
                if candidate_ok:
                    self.state["merged"] = snapshot
                    self.state["chosen_merge"] = attempt
                    accepted_current = True
            attempts.append(attempt)
            if accepted_current:
                break
        self.state["merge_attempts"] = attempts
        self.save()

    def targets(self) -> None:
        baseline = cast(dict[str, object], self.state.get("optcuts", {}))
        if "snapshot" not in baseline:
            self.state["target"] = None
            self.state["target_unavailable"] = baseline
            self.save()
            return
        source = str(baseline["snapshot"])
        result = self.call("target/freeze", "material.target", {"snapshot": source,
            "policy": "source_original", "geometry_profile": POLICY,
            **({"source_mode": "projected_high", "projection_max_fraction": .08} if self.geometry == "tripo" else {})})
        self.state["target"] = result.get("target")
        self.state.pop("target_unavailable", None)
        self.save()
        if "target" in result:
            self.call("target/views", "view.render", {"snapshot": source, "target": result["target"],
                "mode": "basecolor", "size": 640, "seams": False, "cameras": CAMERAS})

    def evaluate(self) -> None:
        target = self.state.get("target")
        if not target:
            return
        paths = {"optcuts": cast(dict[str, object], self.state["optcuts"])["snapshot"]}
        if self.state.get("merged"):
            paths["agent_merge"] = self.state["merged"]
        rows: list[dict[str, object]] = []
        for method, source in paths.items():
            for seed in (17, 29, 43):
                key = f"{method}/seed{seed}"
                row: dict[str, object] = {"method": method, "seed": seed, "source_snapshot": source}
                packed = self.call(f"evaluate/{key}/pack", "uv.pack", {"snapshot": source,
                    "backend": "blender_concave", "density_normalize": True, "packing_seed": seed, "profile": POLICY})
                row["packing"] = packed
                if "snapshot" not in packed:
                    rows.append(row)
                    write_json(self.folder / "evaluations.json", {"rows": rows})
                    continue
                snapshot = str(packed["snapshot"])
                row.update(snapshot=snapshot, metrics=metrics(snapshot))
                inspected = self.call(f"evaluate/{key}/inspect", "uv.inspect", {"snapshot": snapshot, "profile": POLICY})
                row["inspection"] = inspected
                bake = self.call(f"evaluate/{key}/bake", "material.bake", {"snapshot": snapshot,
                    "mode": "basecolor_reuse", "target": target, "resolution": 256, "padding_pixels": 4, "geometry_profile": POLICY})
                row["bake"] = bake
                if "bake" in bake:
                    quality = self.call(f"evaluate/{key}/quality", "quality.validate", {"snapshot": snapshot,
                        "bake": bake["bake"], "mip_levels": [0, 1, 2], "render_size": 96, "geometry_profile": POLICY})
                    row["quality"] = quality
                    if "validation" in quality:
                        exported = self.call(f"evaluate/{key}/export", "asset.export", {"snapshot": snapshot,
                            "bake": bake["bake"], "validation": quality["validation"],
                            "mode": "accepted" if quality.get("quality_accepted") else "diagnostic"})
                        row["export"] = exported
                        texture = Path(str(bake["bake"])).parent / "texture/BaseColor_mip0.png"
                        row["texture"] = str(texture)
                        if "asset" in exported:
                            row["reimport"] = audit_glb(Path(str(exported["asset"])), Path(snapshot), texture,
                                require_uv1=self.geometry == "dragon" and "extra_uv" in load_snapshot(Path(snapshot)).metadata)
                        if seed == 17:
                            for mode in ("basecolor", "groups", "checker"):
                                self.call(f"evaluate/{key}/{mode}", "view.render", {"snapshot": snapshot,
                                    "mode": mode, "size": 640, "seams": False, "annotate": False, "cameras": CAMERAS,
                                    **({"texture": str(texture)} if mode == "basecolor" else {})})
                rows.append(row)
                write_json(self.folder / "evaluations.json", {"rows": rows})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "merge", "targets", "evaluate"])
    parser.add_argument("--asset", choices=list(SOURCES), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pair", type=int, default=0)
    args = parser.parse_args()
    freeze(args.root)
    path = args.root / "agent_script.json"
    record = {"path": str(Path(__file__).resolve()), "sha256": file_hash(Path(__file__))}
    if path.exists() and read_object(path) != record:
        raise ValueError("AGENT_SCRIPT_CHANGED_USE_NEW_ROUND")
    write_json(path, record)
    trial = AgentTrial(args.root, args.asset)
    try:
        trial.merge(args.pair) if args.stage == "merge" else getattr(trial, args.stage)()
    finally:
        write_json(trial.folder / "direct_timing.json", {"seconds": trial.previous_direct + max(
            0., time.monotonic() - trial.phase_started - trial.phase_tool_seconds)})


if __name__ == "__main__":
    main()
