# 新龙 tripo_p2：导入、UV 修复与颜色缩图实验

已用这份新龙完成独立实验，生成了真实颜色贴图和可打开的颜色预览模型；目前没有通过最终质量门禁的复用或缩图结果。`[verified: ../../artifacts/agent_tools_v1/tripo_p2_audit.json color_experiments / full_material_supported]`

## 输入与规范化

|项目|观测|
|---|---|
|原始资产|`model/dragon/tripo_p2/blue dragon 3d model/blue_dragon_3d_model.fbx`，10,832 面、5,383 顶点。`[verified: tripo_p2_audit.json faces/source_vertices/files]`|
|贴图|一张 8192×8192 RGB Base Color JPG；没有把它描述为多通道高模材质。`[verified: 本轮 PIL image.size/mode；tripo_p2_audit.json source_texture_size]`|
|原始拓扑|124 条边拥有超过两张邻面，直接导入被非流形检查拒绝。`[verified: tripo_p2_audit.json nonmanifold_edges；tripo_p2_smoke/import_loaded/result.json]`|
|规范化变体|Blender GLB 按 UV/法线属性拆分为 7,007 顶点，保留 10,832 面；逐面逐角位置最大绝对差约 6.15×10⁻⁸。原 FBX/JPG 未覆盖，不能把连接关系变化归于 UV 优化。`[verified: tripo_p2_audit.json canonical_vertices/canonical_face_corner_max_abs_difference]`|
|原 UV|121 岛，实际 UV 三角并集占 72.55%；查出 33 个正面积交叠单元，另有未授权接触和间距问题。并集占比不等于存储收益。`[verified: tripo_p2_smoke/inspect/inspection.json geometry]`|
|材料边界|FBX 转换后的 GLB 带 `KHR_materials_specular`；完整 PBR 路径明确返回 `EXTENDED_SOURCE_PBR_UNSUPPORTED`。`[verified: tripo_p2_smoke/bake_material_checked/result.json reason]`|

![原始编号模型](../../artifacts/agent_tools_v1/tripo_p2_smoke/view/front.png)

![原始编号 UV](../../artifacts/agent_tools_v1/tripo_p2_smoke/view/uv.png)

## 已执行修复与复用检查

将交叠/非法接触涉及面以及超过默认拉伸门限的面合并为 78 面清单，沿原网格边隔离并生成等距三角 UV，再统一排布。修复后结构安全通过，仍有邻面密度比超标；原三角形和形状没有删除、强制镜像或改造。`[verified: tripo_p2_smoke/repair.json exclusive_faces；repair/geometry.json hard_valid/errors；reuse.apply 的固定几何不变量]`

首次检查脚、翅膀等三对区域时，尖角顶点被最近点查询分配给了法线不兼容的邻面：例如位置误差仅 7.14×10⁻⁹，却测到 125.3° 法线角。工具现已先按原有法线门限筛选目标面，再做最近点查询；距离、往返和最终交叠门禁没有放宽。折面反例测试已验证这项修正。`[verified: tripo_p2_smoke/check_0/check.json failed_samples source_face=132/source_sample=2；geometry.py _surface_constraints；test_agent_tools_geometry.py 新增折面测试]`

修正后另建 [对应检查轮](../../artifacts/agent_tools_v1/tripo_p2_correspondence_round02/matrix.json)，并继续检查 [三个较小区域](../../artifacts/agent_tools_v1/tripo_p2_small_region_round03/matrix.json)。当前曲面 UV 转移在局部产生方向不一致，六对均被拒绝，未提交共享。例如 21 面的小区域中有 3 面方向与其余 18 面相反。这个结果只拒绝当前对应/转移，不能证明新龙无法复用。`[verified: 两轮 matrix.json；small_region_round03/check_1/check.json face_signs]`

下一项值得验证的是：对这些接近同拓扑的成对区域，先构造可追溯的拓扑对应，再复制共同参数域并通过完整几何检查，避免顶点最近点在尖角处跨错面。仍需独立处理颜色差异。`[opinion]`

## 实际颜色贴图

使用显式 `material.bake mode=basecolor_diagnostic`，只测真实 Base Color。Specular、法线、ORM、PBR 渲染均不算验收；预览 GLB 的粗糙度/金属度只用于显示颜色。三个尺寸使用 16 个固定面内采样和 LOD `[0,1]`，下表误差不是 GPU 或连续误差上界。`[verified: color_diagnostic.json color_quality.samples_per_face/mip_levels/ignored_for_color_only；color.py bake_color_diagnostic]`

|尺寸|完整 RGBA8 mip 字节|面积加权颜色均误差|失败面数|颜色通过|
|---|---|---|---|---|
|256|349,524|0.04047|7,253|否。`[verified: tripo_p2_audit.json color_experiments[0]]`|
|512|1,398,100|0.02258|4,840|否。`[verified: tripo_p2_audit.json color_experiments[1]]`|
|1024|5,592,404|0.01316|2,807|否。`[verified: tripo_p2_audit.json color_experiments[2]]`|

分辨率提高后平均颜色误差下降，但局部峰值和失败面仍未过门禁，不能据此宣布“8192 可无损缩到 1024”。`[verified: tripo_p2_audit.json color_experiments；结论为验收规则]`

![256 实际贴图](../../artifacts/agent_tools_v1/tripo_p2_smoke/color_baseline/texture/BaseColor_mip0.png)

![512 实际贴图](../../artifacts/agent_tools_v1/tripo_p2_resolution_diagnostic/color_512/texture/BaseColor_mip0.png)

![1024 实际贴图](../../artifacts/agent_tools_v1/tripo_p2_resolution_diagnostic/color_1024/texture/BaseColor_mip0.png)

[1024 颜色预览模型](../../artifacts/agent_tools_v1/tripo_p2_resolution_diagnostic/color_1024/color_only_preview.glb) · [1024 全量指标](../../artifacts/agent_tools_v1/tripo_p2_resolution_diagnostic/color_1024/color_diagnostic.json) · [输入审计及全部文件哈希](../../artifacts/agent_tools_v1/tripo_p2_audit.json)。`[verified: 对应产物已写出并读回]`
