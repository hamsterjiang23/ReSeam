# 蓝龙高低模测试资产

这里集中保存本轮选定的七个版本、原始压缩包和来源清单。`[verified: manifest.json assets/source_archives]`

高模、低模是本轮预定测试角色。几何对应、材质一致性、烘焙与实际渲染状态均为待验证。`[verified: manifest.json assets[].pairing_status/render_validation]`

## 模型入口

|版本|模型入口|原始多边形|三角化后|材质资料|
|---|---|---:|---:|---|
|原始蓝龙 BaseColor 来源|[打开模型](high/source_basecolor/blue_dragon_3d_model.fbx)|6,339|10,832|BaseColor。`[verified: manifest.json assets id=high/source_basecolor]`|
|蓝龙 PBR 来源|[打开模型](high/source_pbr/tripo_convert_46c1f05d-c694-43a4-8bc3-48066bf4f53a.fbx)|6,339|10,832|BaseColor / Normal / Roughness / Metallic。`[verified: manifest.json assets id=high/source_pbr]`|
|早期三角版低模|[打开模型](low/tri_2436/blue_dragon_3d_model_low.fbx.fbx)|2,436|2,436|一张文件名含 normal 的 JPG。`[verified: manifest.json assets id=low/tri_2436]`|
|早期混合面低模|[打开模型](low/mixed_3214/blue_dragon_3d_model.fbx.fbx)|1,835|3,214|一张文件名含 normal 的 JPG。`[verified: manifest.json assets id=low/mixed_3214]`|
|PBR 三角版低模|[打开模型](low/pbr_tri_3113/tripo_convert_a1645f84-d4d2-4f8d-bb62-78111f52c3de.fbx)|3,113|3,113|BaseColor / Normal / Roughness / Metallic。`[verified: manifest.json assets id=low/pbr_tri_3113]`|
|PBR 混合面低模|[打开模型](low/pbr_mixed_4487/tripo_convert_87da5a29-7d30-4baa-9a21-cd0ad157f5bf.fbx)|2,466|4,487|BaseColor / Normal / Roughness / Metallic。`[verified: manifest.json assets id=low/pbr_mixed_4487]`|
|独立高精度 PBR 龙参考|[打开模型](independent_reference/tripo_v31/tripo_pbr_model_7c54d8c5-4965-4c29-a6aa-40608a24f379.glb)|1,470,916|1,470,916|BaseColor / Normal / Metallic-Roughness；AO 未绑定。`[verified: manifest.json assets id=independent_reference/tripo_v31]`|

## 使用与核验范围

- 模型旁的 `.fbm` 目录保存关联贴图，使用时一并保留目录结构。`[verified: manifest.json assets[].files/inspection.relative_texture_references]`
- 三份新 PBR FBX 使用 Phong 材质字段；导入后应按颜色、法线、粗糙度、金属度的真实用途核对接线。`[verified: manifest.json material_notes.pbr_fbx；opinion: 使用建议]`
- 两份早期低模的 JPG 文件名含 normal，真实材质连接列在清单中。`[verified: manifest.json assets id=low/tri_2436、low/mixed_3214 的 inspection.material_bindings]`
- 独立高精度龙使用单独目录，配对状态为待核对。`[verified: manifest.json assets id=independent_reference/tripo_v31]`
- 后续实验输出按独立轮次保存，并绑定本清单中的输入哈希。`[opinion]`

## 原始压缩包

- [blue dragon 3d model.zip](source_archives/blue%20dragon%203d%20model.zip)。`[verified: manifest.json source_archives]`
- [tripo_convert_46c1f05d-c694-43a4-8bc3-48066bf4f53a.zip](source_archives/tripo_convert_46c1f05d-c694-43a4-8bc3-48066bf4f53a.zip)。`[verified: manifest.json source_archives]`
- [blue_dragon_3d_model_low_tri.fbx.zip](source_archives/blue_dragon_3d_model_low_tri.fbx.zip)。`[verified: manifest.json source_archives]`
- [blue_dragon_3d_model_low_quad.fbx.zip](source_archives/blue_dragon_3d_model_low_quad.fbx.zip)。`[verified: manifest.json source_archives]`
- [tripo_convert_a1645f84-d4d2-4f8d-bb62-78111f52c3de.zip](source_archives/tripo_convert_a1645f84-d4d2-4f8d-bb62-78111f52c3de.zip)。`[verified: manifest.json source_archives]`
- [tripo_convert_87da5a29-7d30-4baa-9a21-cd0ad157f5bf.zip](source_archives/tripo_convert_87da5a29-7d30-4baa-9a21-cd0ad157f5bf.zip)。`[verified: manifest.json source_archives]`

## 来源与检查

- [来源、哈希、贴图尺寸与材质连接清单](manifest.json)。`[verified: manifest.json]`
- 共校验 7 个模型、6 份 ZIP、28 份复制文件、15 张外部图片和 3 张 GLB 内嵌图片。`[verified: manifest.json validation]`
- 最早上传的 `E:/Downloads/blue_dragon_3d_model_low.fbx.zip` 当前状态为 `missing_at_organization`，与现存 low_tri 的文件一致性待核实。`[verified: manifest.json historical_uploads]`
- 当前完成文件、结构和引用检查，实际渲染、PBR 材质效果及高低模配对为待验证。`[verified: manifest.json validation.render_validation、assets[].pairing_status]`
