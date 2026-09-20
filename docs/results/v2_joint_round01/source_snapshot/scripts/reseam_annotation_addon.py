"""Run this text in annotation.blend to register the explicit human confirmation panel."""

import json
from pathlib import Path
from typing import Any

import bmesh
import bpy

bl_info = {"name": "ReSeam Annotation", "blender": (4, 5, 0), "category": "Mesh"}


def data_for(context: Any) -> dict[str, Any]:
    obj = context.active_object
    if obj is None or "reseam_annotation" not in obj:
        raise ValueError("Select the ReSeam annotation mesh")
    return json.loads(obj["reseam_annotation"])


class RESEAM_OT_mark(bpy.types.Operator):
    bl_idname = "reseam.mark"
    bl_label = "标记当前选择"
    kind: bpy.props.StringProperty()

    def execute(self, context: Any) -> set[str]:
        try:
            data = data_for(context)
            mesh = bmesh.from_edit_mesh(context.active_object.data)
            edge_layer = mesh.edges.layers.int.get("reseam_edge_id")
            face_layer = mesh.faces.layers.int.get("reseam_face_id")
            if self.kind == "clear_region":
                selected_faces = {int(face[face_layer]) for face in mesh.faces if face.select}
                regions = []
                for region in data["protected_regions"]:
                    remaining = sorted(set(region["face_ids"]) - selected_faces)
                    if remaining:
                        regions.append({**region, "face_ids": remaining})
                data["protected_regions"] = regions
                for face in mesh.faces:
                    if face.select:
                        face.material_index = 0
            elif self.kind == "protect_region":
                selected = [int(face[face_layer]) for face in mesh.faces if face.select]
                data["protected_regions"].append({"face_ids": selected, "label": "human_selected_region"})
                for face in mesh.faces:
                    if face.select:
                        face.material_index = 1
            else:
                selected = {int(edge[edge_layer]) for edge in mesh.edges if edge.select}
                for key in ("must_cut", "must_join"):
                    data[key] = sorted(set(data[key]) - selected)
                data["preferences"] = [
                    item for item in data["preferences"] if item["edge_id"] not in selected
                ]
                if self.kind in ("must_cut", "must_join"):
                    data[self.kind] = sorted(set(data[self.kind]) | selected)
                elif self.kind == "prefer_cut":
                    data["preferences"].extend(
                        {"edge_id": edge, "kind": "prefer_cut", "confidence": 1, "source": "human"}
                        for edge in sorted(selected)
                    )
                for edge in mesh.edges:
                    if edge.select:
                        edge.seam = self.kind in ("must_cut", "prefer_cut")
            data["confirmed"] = False
            data["confirmed_by"] = None
            context.active_object["reseam_annotation"] = json.dumps(data)
            bmesh.update_edit_mesh(context.active_object.data)
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class RESEAM_OT_export(bpy.types.Operator):
    bl_idname = "reseam.export"
    bl_label = "确认并导出约束"

    def execute(self, context: Any) -> set[str]:
        artist = context.scene.reseam_artist.strip()
        output = Path(bpy.path.abspath(context.scene.reseam_output))
        if not artist or output.exists():
            self.report({"ERROR"}, "填写确认者，并选择尚不存在的 JSON 输出文件")
            return {"CANCELLED"}
        data = data_for(context)
        data["confirmed"] = True
        data["confirmed_by"] = artist
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        context.active_object["reseam_annotation"] = json.dumps(data)
        self.report({"INFO"}, f"已导出 {output}")
        return {"FINISHED"}


class RESEAM_PT_panel(bpy.types.Panel):
    bl_label = "ReSeam：人工约束"
    bl_idname = "RESEAM_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ReSeam"

    def draw(self, context: Any) -> None:
        layout = self.layout
        layout.label(text="编辑模式选择边／面后标记")
        layout.label(text="红边为切缝提示；蓝面为保护区域")
        layout.label(text="建议不是硬约束，需明确确认导出")
        for name, kind in (
            ("必须切", "must_cut"),
            ("禁止切", "must_join"),
            ("偏好切", "prefer_cut"),
            ("清除选中边标记", "clear"),
            ("保护选中面区域", "protect_region"),
            ("清除选中面保护", "clear_region"),
        ):
            op = layout.operator("reseam.mark", text=name)
            op.kind = kind
        layout.prop(context.scene, "reseam_artist")
        layout.prop(context.scene, "reseam_output")
        layout.operator("reseam.export")
        layout.prop(context.scene, "reseam_uv_output")
        layout.operator("reseam.export_uv")


class RESEAM_OT_export_uv(bpy.types.Operator):
    bl_idname = "reseam.export_uv"
    bl_label = "导出当前 UV 供技术验收"

    def execute(self, context: Any) -> set[str]:
        annotation = data_for(context)
        obj = context.active_object
        output = Path(bpy.path.abspath(context.scene.reseam_uv_output))
        if output.exists() or not context.scene.reseam_artist.strip():
            self.report({"ERROR"}, "填写确认者，并使用新的 UV 输出路径")
            return {"CANCELLED"}
        if any(abs(obj.matrix_world[r][c] - float(r == c)) > 1e-8 for r in range(4) for c in range(4)):
            self.report({"ERROR"}, "对象变换已改变，请保持原始几何与变换")
            return {"CANCELLED"}
        if obj.mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.select_all(action="SELECT")
        bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        if any(len(face.vertices) != 3 for face in obj.data.polygons):
            self.report({"ERROR"}, "不得改变三角面拓扑")
            return {"CANCELLED"}
        data = {
            "kind": "reseam_edited_uv_v1",
            "mesh_identity": annotation.get("mesh_identity"),
            "reviewer": context.scene.reseam_artist.strip(),
            "vertices": [[v.co.x, v.co.z, -v.co.y] for v in obj.data.vertices],
            "faces": [list(face.vertices) for face in obj.data.polygons],
            "corner_uv": [
                [list(obj.data.uv_layers.active.data[i].uv) for i in face.loop_indices]
                for face in obj.data.polygons
            ],
            "seam_edges": [sorted(edge.vertices) for edge in obj.data.edges if edge.use_seam],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        self.report({"INFO"}, f"已导出 {output}；尚需独立技术检查和重烘焙")
        return {"FINISHED"}


def register() -> None:
    for cls in (RESEAM_OT_mark, RESEAM_OT_export, RESEAM_OT_export_uv, RESEAM_PT_panel):
        bpy.utils.register_class(cls)
    bpy.types.Scene.reseam_artist = bpy.props.StringProperty(name="确认者", default="")
    bpy.types.Scene.reseam_output = bpy.props.StringProperty(
        name="输出", subtype="FILE_PATH", default="//confirmed.json"
    )
    bpy.types.Scene.reseam_uv_output = bpy.props.StringProperty(
        name="UV 输出", subtype="FILE_PATH", default="//edited_uv.json"
    )


def unregister() -> None:
    for cls in (RESEAM_PT_panel, RESEAM_OT_export_uv, RESEAM_OT_export, RESEAM_OT_mark):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.reseam_artist
    del bpy.types.Scene.reseam_output
    del bpy.types.Scene.reseam_uv_output


if __name__ == "__main__":
    register()
