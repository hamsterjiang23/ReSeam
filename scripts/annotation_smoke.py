"""Headless addon test only; exported mock confirmation is NOT human evaluation."""

import json
import runpy
import sys
from pathlib import Path

import bmesh
import bpy


def main() -> None:
    root = Path(sys.argv[sys.argv.index("--") + 1])
    runpy.run_path(str(root / "scripts/reseam_annotation_addon.py"), run_name="__main__")
    obj = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    edge = next(edge for edge in bm.edges if len(edge.link_faces) == 2)
    edge.select = True
    original_id = int(edge[bm.edges.layers.int["reseam_edge_id"]])
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.reseam.mark(kind="must_cut")
    data = json.loads(obj["reseam_annotation"])
    assert original_id in data["must_cut"] and data["confirmed"] is False
    bpy.ops.reseam.mark(kind="clear")
    assert original_id not in json.loads(obj["reseam_annotation"])["must_cut"]
    bpy.context.scene.reseam_artist = "AUTOMATED_TEST_NOT_HUMAN"
    output = root / "artifacts/smoke/annotation_addon_mock_confirmation.json"
    bpy.context.scene.reseam_output = str(output)
    bpy.ops.reseam.export()
    assert output.exists()
    result = json.loads(output.read_text())
    assert result["confirmed_by"] == "AUTOMATED_TEST_NOT_HUMAN"
    print("PASS addon mark, clear, explicit confirmation and mesh identity export (mock only)")


if __name__ == "__main__":
    main()
