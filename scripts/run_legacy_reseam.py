"""Reproducible tool-only stages for the three legacy ReSeam assets.

Agent decisions are recorded in protocol.json. Each operation is an ordinary
public JSON tool request; no alternative mesh modifier or bake implementation.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import cast

import numpy as np

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json

ROOT = Path(__file__).resolve().parents[1]
CHLOE = Path("F:/MLBB_Test/haijiangli1_HAMSTERMLBB/Assets/Art/Prefabs/TempPrefabs/waipai/Effect/Hero/112_Chloe/skin07")
ASSETS = ("chloe", "energy_blade", "dragon")
SEEDS = (17, 29, 43)
POLICY: dict[str, object] = {"quality_constraints": False, "normal_angle_degrees": 180.,
                            "correspondence_fraction": .08}
DECISIONS: dict[str, dict[str, object]] = {
    "chloe": {"axis": "x", "offset": .7814623713493347, "keep_positive": True,
              "reason": "transverse left/right plane preserves upper/lower crest arrangement; earlier lower point-cloud-residual Y mirror was rejected after actual color preview"},
    "energy_blade": {"axis": "z", "offset": 0., "keep_positive": True,
                     "reason": "copy front/back thickness, preserving asymmetric in-plane blade outline; do not reflect its unequal left/right silhouette"},
    "dragon": {"axis": "x", "offset": 0., "keep_positive": True,
               "reason": "left/right wing and body correspondence; bounds-centered X plane has lowest measured residual"},
}
CAMERAS = [{"name": "front", "azimuth": 0, "elevation": 0},
           {"name": "oblique", "azimuth": 35, "elevation": 15},
           {"name": "back", "azimuth": 180, "elevation": 0}]


def profile(resolution: int) -> dict[str, object]:
    gap = max(1, round(resolution / 256)) if resolution >= 512 else max(1, round(resolution / 64))
    return {**POLICY, "resolution": resolution, "gap_pixels": gap, "border_pixels": gap}


class Experiment:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def state(self, asset: str) -> dict[str, object]:
        path = self.root / asset / "states.json"
        return read_object(path) if path.exists() else {}

    def save(self, asset: str, state: dict[str, object]) -> None:
        write_json(self.root / asset / "states.json", state)

    def call(self, asset: str, label: str, tool: str, request: dict[str, object]) -> dict[str, object]:
        output = self.root / asset / label
        if (output / "result.json").is_file():
            recorded_request = output / "request.json"
            if recorded_request.is_file():
                previous = read_object(recorded_request)
                if any(previous.get(key) != value for key, value in request.items() if key != "timeout_seconds"):
                    raise ValueError("LABEL_REQUEST_MISMATCH_USE_NEW_LABEL:"+str(output))
            return read_object(output / "result.json")
        calls_path = self.root / "calls.json"
        calls = cast(list[dict[str, object]], read_object(calls_path)["calls"]) if calls_path.exists() else []
        spent = sum(float(str(row["seconds"])) for row in calls if row["asset"] == asset and row["path"] == label.split("/")[0])
        if spent >= 2400:
            result: dict[str, object] = {"status": "BUDGET_EXHAUSTED", "reason": "PATH_2400_SECONDS",
                                         "execution_success": False}
            write_json(output / "result.json", result)
            return result
        timeout = float(str(request.get("timeout_seconds", 300)))
        request = {**request, "timeout_seconds": min(timeout, 2400-spent)}
        req = self.root / asset / "requests" / (label.replace("/", "__") + ".json")
        write_json(req, request)
        started = time.monotonic()
        result = run_tool(tool, req, output)
        calls = cast(list[dict[str, object]], read_object(calls_path)["calls"]) if calls_path.exists() else []
        calls.append({"asset": asset, "path": label.split("/")[0], "label": label, "tool": tool,
                      "request": str(req), "seconds": time.monotonic()-started, "result": result})
        write_json(calls_path, {"calls": calls})
        print(asset, label, result.get("status"), result.get("reason", ""), flush=True)
        return result

    def inspect(self, asset: str, label: str, snapshot: str, *, views: bool = True,
                resolution: int = 256) -> dict[str, object]:
        result = self.call(asset, label+"/inspect", "uv.inspect", {
            "snapshot": snapshot, "profile": profile(resolution)})
        if views:
            self.call(asset, label+"/uv_views", "view.render", {"snapshot": snapshot, "size": 768,
                "mode": "groups", "uv_labels": "owners", "annotate": False, "seams": False, "cameras": CAMERAS})
        return result

    def prepare(self, asset: str) -> None:
        request: dict[str, object]
        if asset == "chloe":
            request = {"path": str(CHLOE / "Mesh/Temp_FX_Mod_112_chloe_YM_95.FBX"),
                       "basecolor_texture": str(CHLOE / "Tex/Temp_FX_Tex_112_chloe_WWW_12_1.png"),
                       "uv_channel": "UVChannel_1"}
        else:
            request = {"path": str(ROOT / f"assets/v1/{asset}/low.obj"),
                       "reference": str(ROOT / f"assets/v1/{asset}/high.glb"), "initialize_uv": "xatlas"}
        result = self.call(asset, "source/import", "asset.import", request)
        state = self.state(asset)
        state["import"] = result
        if "snapshot" not in result:
            self.save(asset, state)
            return
        snapshot = str(result["snapshot"])
        self.inspect(asset, "source", snapshot)
        target = self.call(asset, "source/target", "material.target", {"snapshot": snapshot,
            "source_mode": "original_face_corner" if asset == "chloe" else "projected_high",
            "policy": "source_original", "geometry_profile": POLICY})
        if "target" in target:
            self.call(asset, "source/color_views", "view.render", {"snapshot": snapshot,
                "mode": "basecolor", "target": target["target"], "size": 512, "seams": False, "cameras": CAMERAS})
        state["source_target"] = target
        self.save(asset, state)

    def baseline(self, asset: str) -> None:
        state = self.state(asset)
        source = cast(dict[str, object], state["import"])
        if "snapshot" not in source:
            return
        result = self.call(asset, "optcuts/native", "uv.baseline", {"snapshot": source["snapshot"],
                           "method": "optcuts", "seed": 0})
        state["optcuts"] = result
        if "snapshot" in result:
            self.inspect(asset, "optcuts", str(result["snapshot"]))
        self.save(asset, state)

    def mirror(self, asset: str) -> None:
        state = self.state(asset)
        parent = cast(dict[str, object], state.get("optcuts", state["import"]))
        if "snapshot" not in parent:
            parent = cast(dict[str, object], state["import"])
        if "snapshot" not in parent:
            return
        result = self.call(asset, "mirror/create", "asset.mirror_variant", {
            "snapshot": parent["snapshot"], "allow_geometry_change": True, **DECISIONS[asset],
            "profile": profile(256), **({"source_mode": "projected_high"} if asset != "chloe" else {})})
        state["mirror"] = result
        if "snapshot" in result:
            self.inspect(asset, "mirror", str(result["snapshot"]))
        self.save(asset, state)

    def merge(self, asset: str) -> None:
        state = self.state(asset)
        mirrored = cast(dict[str, object], state.get("mirror", {}))
        if "snapshot" not in mirrored:
            return
        parent = str(mirrored["snapshot"])
        s = load_snapshot(Path(parent))
        report = read_object(Path(str(mirrored["report"])))
        retained = int(str(report["retained_faces"]))
        first = list(range(retained))
        unshared = self.call(asset, "merge/unshare", "reuse.apply", {"snapshot": parent, "action": "unshare",
            "group_ids": [group.group_id for group in s.state.groups], "profile": profile(256)})
        if "snapshot" not in unshared:
            state["merge"] = unshared
            self.save(asset, state)
            return
        for backend in ("blender_lscm", "blender_abf", "harmonic", "optcuts"):
            remap = self.call(asset, f"merge/{backend}", "uv.reparameterize", {
                "snapshot": unshared["snapshot"], "faces": first, "backend": backend,
                "seam_edges": [], "initial_uv": "geometry", "profile": profile(256)})
            if "snapshot" not in remap:
                continue
            # Contour packing requires one connected master chart per group.
            # Keep the backend's actual cuts, and copy each chart as a group.
            from reseam.mesh import chart_labels

            current = str(remap["snapshot"])
            labels = chart_labels(load_snapshot(Path(current)).state.surface)
            applied: dict[str, object] = {}
            for index, value in enumerate(np.unique(labels[first])):
                member = np.flatnonzero((labels == value) & (np.arange(len(labels)) < retained)).tolist()
                checked: dict[str, object] = {}
                for mode in ("exact", "topology"):
                    checked = self.call(asset, f"merge/{backend}_group{index}_{mode}", "reuse.check_pair", {
                        "snapshot": current, "first_faces": member, "second_faces": [face+retained for face in member],
                        "mode": mode, "plane_normal": [float(a == DECISIONS[asset]["axis"]) for a in "xyz"],
                        "plane_offset": DECISIONS[asset]["offset"], "profile": profile(256)})
                    if "proposal" in checked:
                        break
                if "proposal" not in checked:
                    applied = {}
                    break
                applied = self.call(asset, f"merge/{backend}_group{index}_apply", "reuse.apply", {
                    "snapshot": current, "proposal": checked["proposal"]})
                if "snapshot" not in applied:
                    break
                current = str(applied["snapshot"])
            if "snapshot" in applied:
                state["merge"] = applied
                self.inspect(asset, "merge/final", str(applied["snapshot"]))
                break
        self.save(asset, state)

    def fixed(self, asset: str) -> None:
        state = self.state(asset)
        parent = cast(dict[str, object], state.get("optcuts", {}))
        if "snapshot" not in parent:
            return
        snapshot = load_snapshot(Path(str(parent["snapshot"])))
        surface = snapshot.state.surface
        axis = "xyz".index(str(DECISIONS[asset]["axis"]))
        signed = surface.mesh.vertices[surface.mesh.faces, axis] - float(str(DECISIONS[asset]["offset"]))
        first = np.flatnonzero(np.all(signed > 0, axis=1)).tolist()
        second = np.flatnonzero(np.all(signed < 0, axis=1)).tolist()
        write_json(self.root / asset / "fixed/agent_region_decision.json", {"first_faces": first,
            "second_faces": second, "protected_center_faces": np.flatnonzero(~(np.all(signed > 0, axis=1) | np.all(signed < 0, axis=1))).tolist(),
            "rationale": "paired complete half regions separated along original edges; center-crossing faces stay exclusive; one geometry mechanism per attempt",
            "evidence": DECISIONS[asset]})
        for mode in ("exact", "approximate", "paired_domain"):
            if mode == "paired_domain":
                # No fabricated anchors: failure evidence is retained rather than
                # constructing unobserved correspondences from array order.
                break
            checked = self.call(asset, f"fixed/{mode}", "reuse.check_pair", {
                "snapshot": parent["snapshot"], "first_faces": first, "second_faces": second,
                "mode": mode, "plane_normal": [float(a == DECISIONS[asset]["axis"]) for a in "xyz"],
                "plane_offset": DECISIONS[asset]["offset"], "profile": profile(256)})
            if "proposal" in checked:
                state["fixed"] = self.call(asset, f"fixed/{mode}_apply", "reuse.apply", {
                    "snapshot": parent["snapshot"], "proposal": checked["proposal"]})
                break
        self.save(asset, state)

    def evaluate(self, asset: str, resolutions: list[int], methods: list[str] | None = None) -> None:
        state = self.state(asset)
        candidates: dict[str, str] = {}
        for key in ("import", "optcuts", "fixed"):
            value = cast(dict[str, object], state.get(key, {}))
            if "snapshot" in value:
                candidates["xatlas" if key == "import" and asset != "chloe" else "source_uv" if key == "import" else key] = str(value["snapshot"])
        mirrored = cast(dict[str, object], state.get("merge", state.get("mirror", {})))
        suffix = str(state.get("evaluation_suffix", "final"))
        if "snapshot" in mirrored:
            candidates["mirror_shared_"+suffix] = str(mirrored["snapshot"])
            s = load_snapshot(Path(str(mirrored["snapshot"])))
            unshared = self.call(asset, "controls/unshare_"+s.state_hash[:10], "reuse.apply", {
                "snapshot": mirrored["snapshot"], "action": "unshare",
                "group_ids": [group.group_id for group in s.state.groups], "profile": profile(256)})
            if "snapshot" in unshared:
                candidates["mirror_unshared_"+suffix] = str(unshared["snapshot"])
        if methods is not None:
            candidates = {k: v for k, v in candidates.items() if k in methods}
        target_for_mirror: object = None
        results_path = self.root / asset / "evaluations.json"
        results = cast(list[dict[str, object]], read_object(results_path)["rows"]) if results_path.exists() else []
        # Shared freezes the target first; the same target is used on its control.
        order = [k for k in candidates if not k.startswith("mirror_unshared")] + [k for k in candidates if k.startswith("mirror_unshared")]
        for method in order:
            snapshot = candidates[method]
            source = load_snapshot(Path(snapshot))
            extra_policy: dict[str, object] = {}
            if method == "source_uv" and asset == "chloe":
                extra_policy["preserve_source_uv"] = {"snapshot": snapshot, "state_hash": source.state_hash}
            geometry = {**POLICY, **extra_policy}
            policy = "symmetrize" if method == "fixed" and source.state.groups else "source_original"
            target = self.call(asset, f"{method}/frozen_target", "material.target", {
                "snapshot": snapshot, "policy": policy, "geometry_profile": geometry,
                "source_mode": "projected_high" if asset != "chloe" else "original_face_corner"})
            if method.startswith("mirror_shared"):
                target_for_mirror = target.get("target")
            target_path = target_for_mirror if method.startswith("mirror_unshared") else target.get("target")
            if target_path is None:
                continue
            self.call(asset, f"{method}/color_tolerance", "reuse.check_color", {
                "snapshot": snapshot, "target": target_path, "level": "medium"})
            self.call(asset, f"{method}/source_target_views", "view.render", {
                "snapshot": snapshot, "mode": "basecolor", "target": target_path,
                "size": 512, "cameras": CAMERAS, "seams": False})
            for resolution in resolutions:
                key = f"{method}/{resolution}"
                if any(r.get("key") == key for r in results):
                    continue
                variants: list[tuple[float, dict[str, object]]] = []
                for seed in SEEDS:
                    packed = self.call(asset, f"{key}/pack_{seed}", "uv.pack", {
                        "snapshot": snapshot, "backend": "blender_concave", "density_normalize": False,
                        "packing_seed": seed, "profile": {**profile(resolution), **extra_policy}})
                    if "snapshot" in packed:
                        record = self.inspect(asset, f"{key}/inspect_{seed}", str(packed["snapshot"]),
                                              views=False, resolution=resolution)
                        variants.append((float(str(record.get("unique_footprint", 0))), packed))
                row: dict[str, object] = {"key": key, "method": method, "resolution": resolution,
                    "target": target_path, "source_snapshot": snapshot, "packing_attempts": len(SEEDS)}
                if not variants:
                    row["status"] = "PACK_REJECTED"
                    results.append(row); write_json(results_path, {"rows": results}); continue
                _, best = max(variants, key=lambda item: item[0])
                current = str(best["snapshot"])
                row["snapshot"] = current
                row["inspection"] = self.inspect(asset, key+"/final", current, resolution=resolution)
                bake = self.call(asset, key+"/bake", "material.bake", {"snapshot": current,
                    "target": target_path, "mode": "basecolor_reuse", "resolution": resolution,
                    "timeout_seconds": 900 if asset != "chloe" and resolution >= 2048 else 300,
                    "padding_pixels": profile(resolution)["gap_pixels"], "geometry_profile": geometry})
                row["bake"] = bake
                row["status"] = bake.get("status")
                if "bake" in bake:
                    validated = self.call(asset, key+"/quality", "quality.validate", {"snapshot": current,
                        "bake": bake["bake"], "mip_levels": [0, 1, 2], "render_size": 96,
                        "timeout_seconds": 900 if asset != "chloe" and resolution >= 2048 else 300,
                        "geometry_profile": geometry})
                    row["validation"] = validated
                    row["export"] = self.call(asset, key+"/export", "asset.export", {"snapshot": current,
                        "bake": bake["bake"], "validation": validated.get("validation"),
                        "mode": "accepted" if validated.get("quality_accepted") else "diagnostic"})
                    self.call(asset, key+"/color_views", "view.render", {"snapshot": current,
                        "mode": "basecolor", "texture": str(Path(str(bake["bake"])).parent / "texture/BaseColor_mip0.png"),
                        "size": 768, "cameras": CAMERAS, "seams": False})
                results.append(row)
                write_json(results_path, {"rows": results})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["init", "prepare", "baseline", "mirror", "merge", "fixed", "evaluate"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--asset", choices=ASSETS, nargs="+", default=list(ASSETS))
    parser.add_argument("--resolutions", type=int, nargs="+")
    parser.add_argument("--methods", nargs="+")
    args = parser.parse_args()
    root = args.output.resolve()
    if args.stage == "init":
        root.mkdir(parents=True, exist_ok=False)
        sources = sorted((ROOT / "src/reseam").rglob("*.py")) + sorted((ROOT / "scripts").glob("agent_*.py"))
        write_json(root / "protocol.json", {"assets": list(ASSETS), "scope": "UV+BaseColor static CPU",
            "geometry_policy": "fixed original plus independently authorized mirrors; not retopology",
            "decisions": DECISIONS, "packing_seeds": list(SEEDS), "quality_policy": POLICY,
            "mip_levels": [0, 1, 2], "cameras": CAMERAS, "per_path_budget_seconds": 2400,
            "source_sha256": {str(p.relative_to(ROOT)): file_hash(p) for p in sources},
            "gpu_verified": False, "human_accepted": False, "pbr_verified": False})
        print(root)
        return
    if not (root / "protocol.json").is_file():
        raise FileNotFoundError("Run init into a fresh output directory first")
    frozen = cast(dict[str, str], read_object(root / "protocol.json")["source_sha256"])
    if any(file_hash(ROOT / path) != digest for path, digest in frozen.items()):
        raise ValueError("CODE_CHANGED_CREATE_NEW_ROUND")
    experiment = Experiment(root)
    for asset in args.asset:
        if args.stage == "evaluate":
            experiment.evaluate(asset, args.resolutions or ([256, 128, 64] if asset == "chloe" else [2048, 1024, 512]), args.methods)
        else:
            getattr(experiment, args.stage)(asset)


if __name__ == "__main__":
    main()
