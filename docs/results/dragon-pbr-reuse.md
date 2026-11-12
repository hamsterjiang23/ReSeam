# 蓝龙完整 PBR 烘焙与 UV 共享：实验完成，外观未通过

后续用户要求沿用旧“40图块”龙的方法，已另建[低模自身 PBR 的半边镜像共享实验](dragon-legacy-method-replay.md)。该分支使用新的对称几何和低模自身材质，不能用其结果改写本页高模到低模投射的负面结论。`[verified: 本轮用户最新请求；round09_legacy_method/protocol.json material_source、high_to_low_boundary]`

**当前状态：EXPERIMENT_COMPLETED_QUALITY_NOT_ACCEPTED。本轮限定实验已结束，计划中的外观与节省验收条件未满足。** 已交付可打开的四版本 Blender 对照场景，未获得完整 PBR 外观通过或可验收的贴图节省结论。 修正后的 round07 已消除早期总览中的大片黑翼，但 2048² 无共享和共享版在头冠、嘴边、翼根近景仍有原高模没有的锯齿黑线；因此两者保留诊断身份。`[verified: ../../artifacts/dragon-pbr-reuse/round07/main_appearance_review.json findings、whole_model_acceptance；round07/comparison2048/scene_audit.json reopened]`

[实际模型、场景与预览入口](../../artifacts/dragon-pbr-reuse/index.html) · [机器状态与工具结果](../../artifacts/dragon-pbr-reuse/status.json) · [资产准备审计](../../artifacts/dragon-pbr-reuse/round01/asset_preparation_audit.json)。本轮负面结果及旧失败保留。`[opinion]`

## 最新实际产物与结论（round07）

[打开四版本 Blender 对照场景](../../artifacts/dragon-pbr-reuse/round07/comparison2048/comparison.blend) · [同镜头完整 PBR／中性法线对照](../../artifacts/dragon-pbr-reuse/round07/comparison2048/comparison.html) · [实际看图记录](../../artifacts/dragon-pbr-reuse/round07/main_appearance_review.json)。场景包含 `reference_high`、`direct_low`、`noshare`、`shared` 四个同原点集合；默认仅共享版可见，切换时在 Outliner 中只保留一个模型集合的视口及渲染可见性。`[verified: round07/comparison2048/scene_audit.json models、default_visible；scripts/pbr_comparison_scene_worker.py README]`

场景打包 12 张实际材质图片，保存后重新打开，对四份模型再次检查几何、UV 和原始世界变换；没有为排成一行移动模型。无共享和共享均为实际 2048² 导出 GLB，另两份是原始直接 PBR 参考。`[verified: round07/comparison2048/scene_audit.json all_images_packed、image_count、reopened、models_bound、transforms_modified]`

|对照|已导出尺寸|UV 连通岛／实际图块|当前观察|
|---|---|---|---|
|原 UV＋必要修复|1024²、2048²|857／未授予严格统计|数值诊断未授予质量通过；两尺寸各 32 张实际图已由 PBR Agent 查看，仍有嘴边、角刺根部和翼根暗线，未接受完整外观。`[verified: round07/original1024_preview/agent_observation.json；original2048_preview/agent_observation.json decision、image_count_actually_viewed；final_matrix.json rows[tri_original1024].uv_metrics]`|
|OptCuts 无共享、统一密度矩形排布|1024²、2048²|45／45|2K 总览黑翼已消失，头冠／嘴边／腋下近景仍有新增或放大的黑线。`[verified: round07/noshare_metrics/result.json；main_appearance_review.json findings.no_share2048]`|
|Agent 保留腿部共享|1024²、2048²|65／57，1 个共享组|2K 双脚白爪范围和腿部颜色在已看近景中保留；完整模型仍有无共享版同类缺陷，不判完整 PBR 通过。`[verified: round07/shared_metrics/result.json；main_appearance_review.json findings.shared2048、leg_sharing_subset；tri_agent1024/export/export.json]`|
|几何候选多加角刺共享|未形成合规最终导出|角刺组触发保护冲突|保留失败，不用忽略保护的结果冒充正式几何基线。`[verified: round05/geometry_protected/result.json reason]`|
|4,487 面补充低模|无共享 1024²、2048²，分别实际观察 44／68 张图|15／15|腿部三种对应均被拒绝，继续独占；近景仍有腋下黑三角、头冠及翼角根部黑线，完整质量未接受。`[verified: round07/mixed_metrics/result.json；mixed_supplemental_summary.json；mixed_1024_complete_observation.json；mixed_2048_complete_observation.json]`|

共享版的几何授权重叠覆盖原三维表面积约 **7.81%**，计算包含两个成员的覆盖面积；`material_validated=false`，因此这不是“已验收的完整 PBR 共享面积”。共享版 65 岛／57 图块多于无共享的 45 岛／45 图块，当前方案没有减少碎片或实际图块。最终无共享与共享均为统一密度的矩形排布，但此前 UV／候选处理路径不同，不能把当前端到端差异直接归因于 Agent 判断。`[verified: round07/shared_metrics/uv_metrics.json global.actual_shared_surface_fraction、material_validated；noshare_metrics/result.json；round04/tri_rectangle_uniform/request.json；../../src/reseam/agent_tools/geometry.py L421–444、L653、L810–818]`

原 UV 的重新测量得到 857 个连通岛，保留 34 处来源冲突，实际独立图块与有效共享面积返回 null；严格几何前提未满足，因此不能把原有交叠计入新增共享，也不能把 null 改写为零或通过。`[verified: round07/final_matrix.json rows[tri_original1024].uv_metrics uv_connected_charts、preserved_source_conflict_count、actual_independent_tiles、metrics_verified]`

相同尺寸下，导出记录的完整 mip 原始贴图 payload 相同：1024² 为 **16,777,212 字节**，2048² 为 **67,108,860 字节**。这只说明既定通道格式与尺寸的运行时贴图预算相同，PNG／GLB 文件体积另列；尚无“相同可接受外观下共享可以缩到更小尺寸”的证据。`[verified: round07/tri_rectangle1024/export/export.json payload_bytes；tri_agent1024/export/export.json payload_bytes；tri_rectangle2048/export/export.json payload_bytes；tri_agent2048/export/export.json payload_bytes；opinion: 验收边界]`

本 Agent 查看了 11 张接触表中包含的 104 张总览／近景缩略图，另查看 15 张原尺寸图片；所有路径记录在看图凭据中。源高模、直接低模、无共享和共享的头／翼根／爪子都已另行渲染，只有凭据列出的图被本 Agent 实际观察。参考高模对应头冠和嘴边较平滑；直接低模有自身翼脊暗块，但没有烘焙版本同样的锯齿裂纹。无共享已经有同类缺陷，不能将其归因于共享。`[verified: round07/main_appearance_review.json contact_sheets_observed、full_resolution_images_observed、findings]`

![实际来源与烘焙近景对照](../../artifacts/dragon-pbr-reuse/round07/source-bake-closeup-comparison.png)

上图依次为高模、Tripo 直接低模、无共享 2K、腿部共享 2K；上排是真实 PBR 头部近景，下排是中性灰材质的法线照明。每一格直接使用对应 Blender 渲染图，未修补黑线。`[verified: round07/source-bake-closeup-comparison.png；round07/main_appearance_review.json full_resolution_images_observed]`

[共享 1K 实际预览](../../artifacts/dragon-pbr-reuse/round07/shared1024_preview/preview.html) · [共享 2K 颜色／粗糙度／金属度通道](../../artifacts/dragon-pbr-reuse/round07/shared2048_channels/preview.html) · [无共享 2K 通道](../../artifacts/dragon-pbr-reuse/round07/noshare2048_channels/preview.html)。这些页面是实际产物入口，是否被哪位 Agent 看过以独立看图记录为准。`[verified: 对应 preview.json images；opinion: 证据边界]`

原 UV 的 [1024² 看图凭据](../../artifacts/dragon-pbr-reuse/round07/original1024_preview/agent_observation.json)与 [2048² 看图凭据](../../artifacts/dragon-pbr-reuse/round07/original2048_preview/agent_observation.json)分别记录 PBR Agent 实际查看的 32 张完整材质／中性法线图及 SHA-256。两尺寸均保留局部暗线和反光不连续，结论为 `DIAGNOSTIC_RETAINED_FULL_APPEARANCE_NOT_ACCEPTED`；这些整龙视角不等同于显式 mip 目视验收。`[verified: 两份 agent_observation.json observations、decision、explicit_mip_image_review]`

## 同镜头 1K 对照与公开回退结果

[1024² 同镜头 PBR 对照](../../artifacts/dragon-pbr-reuse/round07/comparison1024/comparison.html)使用与 2K 对照相同的八个方向、正交视角及两种照明；正／背／左／右仰角为 0°，四个斜向为 20°。Root 实际查看四张接触表的 **64 张图**，记录在[匹配镜头观察凭据](../../artifacts/dragon-pbr-reuse/round07/comparison1024/root_observation.json)。两版整体蓝色、白爪和翼膜保留，但局部头冠／嘴边／腋下黑线仍可见，未接受完整质量；整龙小图中的接近不构成近景或显式 mip 验收。`[verified: comparison1024/root_observation.json image_count_seen_in_contact_sheets、matching_settings、observations、decision、explicit_mip_views]`

2K 观察另经公开工具执行完整回退链：先将当前实际模型、贴图、烘焙、预览与图片文件显式冻结为[完整 PBR 对照凭据](../../artifacts/dragon-pbr-reuse/round08/comparison2048_frozen/comparison.json)，再执行[审查](../../artifacts/dragon-pbr-reuse/round08/review2048/review.json)，结果为 **`VISUAL_REVERT`**；随后[正式视觉导出请求](../../artifacts/dragon-pbr-reuse/round08/refused_visual_export/result.json)返回 **`REJECTED / PBR_VISUAL_REVERT`**，该输出目录没有生成 `asset.glb`。已存在的八份 GLB 保持诊断用途，不通过此回退记录改写成正式验收资产。`[verified: comparison2048_frozen/comparison.json scope、freeze_note；review2048/review.json status、visual_accepted；refused_visual_export/result.json status、reason；实际文件存在检查 asset.glb=false]`

回退凭据选择已核验的无共享 2K 父版本，并绑定其快照、模型、烘焙及导出文件哈希；这仅表示撤回共享选择，没有修改不可变快照，也没有认可父版本的局部外观。新冻结记录绑定冻结当时的实际文件，不追认更早历史图像已经不可变。`[verified: round08/review2048/review.json restore、restore_scope、notes；comparison2048_frozen/comparison.json freeze_note]`

## 实际 UV 布局对照

下列图片来自公开 `view.render` 的实际快照：主低模原 UV、OptCuts 无共享、腿部共享以及独立的混合低模无共享。布局图的颜色和编号仅用于分辨图块；它们不构成材质通过或节省证明。共享图仍有额外碎片，计数以 `uv.inspect` 的 65 连通岛／57 实际图块为准，不能把图上 50 个 owner 标签当成 50 个实际图块。`[verified: round07/original_uv/result.json；noshare_uv/result.json；shared_uv/result.json；mixed_uv/result.json；shared_metrics/result.json；opinion: 验收边界]`

|主低模原 UV|OptCuts 无共享|腿部共享|混合低模无共享|
|---|---|---|---|
|![原 UV](../../artifacts/dragon-pbr-reuse/round07/original_uv/uv.png)|![无共享 UV](../../artifacts/dragon-pbr-reuse/round07/noshare_uv/uv.png)|![共享 UV](../../artifacts/dragon-pbr-reuse/round07/shared_uv/uv.png)|![混合低模 UV](../../artifacts/dragon-pbr-reuse/round07/mixed_uv/uv.png)|

三维编号定位图：[原 UV](../../artifacts/dragon-pbr-reuse/round07/original_uv/front.png) · [OptCuts 无共享](../../artifacts/dragon-pbr-reuse/round07/noshare_uv/front.png) · [腿部共享](../../artifacts/dragon-pbr-reuse/round07/shared_uv/front.png) · [混合低模](../../artifacts/dragon-pbr-reuse/round07/mixed_uv/front.png)。`[verified: 对应 view.render result.json images]`

## 最终尺寸矩阵与数值检查

[完整八项清单、文件哈希与 mip 数据](../../artifacts/dragon-pbr-reuse/round07/final_matrix.json)重新读取了实际导出 GLB、嵌入图片及导出文件哈希。四项语义通道编码为三张 RGBA8 图片：BaseColor、切线 Normal、ORM；当前来源 AO 为常数 1。下表只报告实际文件及诊断误差，八项均只返回数值诊断，未授予质量通过。`[verified: round07/final_matrix.json scope、channels、rows[].numerical_quality_accepted]`

|实际 GLB|三角形|贴图尺寸|GLB 字节|全部 mip PNG 字节|法线 P95 角误差 mip 0／1／2|
|---|---:|---:|---:|---:|---|
|[原 UV 1024](../../artifacts/dragon-pbr-reuse/round07/tri_original1024/export/asset.glb)|3,113|1024²|3,073,152|4,118,647|4.93°／11.51°／22.39°。`[verified: final_matrix.json rows[name=tri_original1024]]`|
|[原 UV 2048](../../artifacts/dragon-pbr-reuse/round07/tri_original2048/export/asset.glb)|3,113|2048²|7,943,272|12,343,526|1.97°／5.32°／12.46°。`[verified: final_matrix.json rows[name=tri_original2048]]`|
|[OptCuts 无共享 1024](../../artifacts/dragon-pbr-reuse/round07/tri_rectangle1024/export/asset.glb)|3,113|1024²|1,842,868|2,125,086|7.86°／23.58°／36.17°。`[verified: final_matrix.json rows[name=tri_rectangle1024]]`|
|[OptCuts 无共享 2048](../../artifacts/dragon-pbr-reuse/round07/tri_rectangle2048/export/asset.glb)|3,113|2048²|4,433,688|6,102,987|3.11°／10.65°／24.86°。`[verified: final_matrix.json rows[name=tri_rectangle2048]]`|
|[腿部共享 1024](../../artifacts/dragon-pbr-reuse/round07/tri_agent1024/export/asset.glb)|3,113|1024²|1,794,888|2,068,778|8.65°／23.07°／35.95°。`[verified: final_matrix.json rows[name=tri_agent1024]]`|
|[腿部共享 2048](../../artifacts/dragon-pbr-reuse/round07/tri_agent2048/export/asset.glb)|3,113|2048²|4,277,188|5,894,610|3.33°／10.79°／24.04°。`[verified: final_matrix.json rows[name=tri_agent2048]]`|
|[混合低模无共享 1024](../../artifacts/dragon-pbr-reuse/round07/mixed_noshare1024/export/asset.glb)|4,487|1024²|2,483,756|2,713,475|6.70°／17.37°／27.28°。`[verified: final_matrix.json rows[name=mixed_noshare1024]]`|
|[混合低模无共享 2048](../../artifacts/dragon-pbr-reuse/round07/mixed_noshare2048/export/asset.glb)|4,487|2048²|5,966,980|7,978,183|2.49°／8.36°／18.37°。`[verified: final_matrix.json rows[name=mixed_noshare2048]]`|

数值接口目前只计算误差与无效样本，并固定写入 `status=DIAGNOSTIC`、`valid=false`、`quality_accepted=false`；它没有执行完整 PBR 的验收阈值比较。因此这些 false 不能解释成“严格数值门禁已经拒绝”。本报告的外观不接受来自实际 Blender 图片；几何工具的独立通过／拒绝另外记录。`[verified: ../../src/reseam/agent_tools/pbr_reuse.py L532–574；round07/main_appearance_review.json status]`

最终目标均为 `source_original`，共享成员分别采样原始来源，再在兼容切线框架下合成；没有冻结“主动统一颜色”的新目标，所以该合成产生的差异仍属于重建残差，矩形无共享版提供相同原始目标的参照。两版最终采用相同矩形排布与统一密度规则；但它们的父布局和候选处理路径不同，尚无共同候选池的受控选择消融，因此不能单独归因于 Agent 选择。`[verified: round07/final_matrix.json target_contract；opinion: 归因边界]`

完整 mip 链已写出，数值探针覆盖 mip 0／1／2；Blender 相机远近并非显式 mip 级别验收。无共享目标冻结的几何检查 profile 曾保留 256 分辨率，实际烘焙覆盖到 1024／2048，烘焙门禁 gap／border 仍为 4／4；实际像素扩边为 8，另行 UV 测量使用 1K 的 8／4 与 2K 的 16／8。该配置不一致已列出，不能把工具成功解释为预先计划的所有像素留边条件均已满足。`[verified: round07/final_matrix.json geometry_profile_audit、profile_pixel_evidence、evidence_boundaries]`

## 来源、几何与法线版本

最终再次核对原始资产目录中的 22 个模型／贴图文件和 6 份来源 ZIP，全部 SHA-256 与原清单一致；实验没有覆盖原始输入。`[verified: ../../artifacts/dragon-pbr-reuse/round07/original_integrity_final.json all_match、files、source_archives]`

原始资产来自 `model/dragon/pbr_bake_test`。三个实验导入副本的面角位置、UV、归一化逐角法线与来源一致，源文件及复制文件哈希一致；连接修复没有移动、删除或新增三角形。`[verified: ../../artifacts/dragon-pbr-reuse/round01/asset_preparation_audit.json assets]`

|资产|三角形|连接修复前后顶点|修复内容|后续目标法线|
|---|---:|---:|---|---|
|PBR 来源高模|10,832|5,383 → 5,641|124 条非流形边、1 条方向冲突边拆连接|保留导入法线。`[verified: round01/import_high/connectivity_variant.json；asset_preparation_audit.json]`|
|主三角低模|3,113|1,546 → 1,656|55 条非流形边拆连接|实验副本另修复 10 个逐角法线；原始 Tripo 直接材质预览保留未修复法线。`[verified: round01/import_low_tri/connectivity_variant.json；repair_normals_tri/normal_repair.json；round04/source_tri_preview/request.json]`|
|补充混合低模|4,487|2,232 → 2,374|71 条非流形边拆连接|法线修复工具没有改动任何面角。`[verified: round01/import_low_mixed/connectivity_variant.json；repair_normals_mixed/normal_repair.json]`|

主低模的法线修复仅针对原生 MikkTSpace 无法生成有效切线的两个平滑扇区，涉及 3＋7 个面角。两个扇区的法线变化约为 80.267° 和 8.082°；这不是“完全保留原始法线”的结果，且修复记录的外观验收仍为 NOT_RUN。修复前后几何和 UV 保持不变。`[verified: ../../artifacts/dragon-pbr-reuse/round01/repair_normals_tri/normal_repair.json operations、angular_change_degrees、appearance_acceptance]`

三份 FBX 均显式绑定标准 PBR：BaseColor 使用 sRGB，其余 Non-Color；导出 GLB 真实绑定 BaseColor、Normal、Metallic-Roughness，并包含 NORMAL/TANGENT/TEXCOORD_0。没有凭 Phong 字段默认解释材质。OpenGL 法线约定目前是实验声明，尚未完成专门方向验证。`[verified: ../../artifacts/dragon-pbr-reuse/round01/asset_preparation_audit.json pbr_binding、gltf_materials、gltf_attributes]`

连接修复后的原多边形顶点保留原索引空间，并有显式标记。逐三角形原多边形 ID 可用于硬边来源检查；四边形边环工具要求当前顶点索引，会拒绝该输入。当前路径未调用四边形边环，不声称该能力已支持。`[verified: ../../artifacts/dragon-pbr-reuse/round01/asset_preparation_summary.json quad_loop_limitation]`

## 历史开发轮进展（round01—round05，不代表最新结果）

下表是已运行情况，不构成冻结条件齐全的最终公平比较。尤其矩形统一密度排布是独立诊断，不能和保留相对密度的凹轮廓排布混作同条件结果。`[opinion]`

|历史路径|当时实际完成|当时状态|
|---|---|---|
|原 UV＋必要修复|128² 四通道烘焙、逐 mip 数值诊断、GLB 导出；2048² 烘焙一次超时|诊断产物，未验收。`[verified: round04/tri_original128/*/result.json；tri_original2048/bake/result.json]`|
|OptCuts 不共享|三角低模 45 岛，补充低模 15 岛；均有排布及 128² PBR 导出|三角低模实际渲染目视拒绝；混合低模数值诊断未授予质量通过，未完成完整外观验收。`[verified: round02/optcuts_tri/result.json；round03/mixed_optcuts/result.json；round04/smoke128_appearance_review.json；mixed_noshare128/validation/validation.json]`|
|几何共享候选|腿部 153＋153 面共享一组；另一个几何候选增加 3＋3 面角刺组|几何工具通过不代表 PBR 兼容；腿部一组版本已有真实 128² 共享贴图和 GLB，但实际渲染目视拒绝。`[verified: round03/leg_apply/snapshot/snapshot.json groups；geometry_apply/snapshot/snapshot.json groups；round04/tri_geometry128_retry/export/result.json；smoke128_appearance_review.json]`|
|Agent 选择候选|腿部共享状态已冻结 PBR 目标，翅膀对应尝试及打包失败保留|最终 Agent 方案和与几何方案的同预算结果尚未形成。`[verified: round04/tri_agent2048/target/target.json；round03/wing_topology02/result.json；round04/agent_pack/result.json]`|
|矩形统一密度诊断|1024² PBR 烘焙、验证与实际 GLB 导出，mip 0/1/2 的离散无效采样数为零|`valid=false`、`full_pbr_passed=false`，零无效采样不代表图像质量通过。`[verified: round04/tri_rectangle1024/validate/validation.json；export/export.json]`|
|补充低模共享|实际运行腿部对应检查|`AFFINE_UV_OR_PACKING_REJECTED`；未交付补充低模共享版。`[verified: round04/mixed_leg_topology/result.json]`|

最近点材质采样和有限射线预检是两条不同路径。当时冻结的 `pbr_reuse` 目标记录 `projection=nearest_surface_low_bbox_diagonal`；不能把预检射线结果写成该贴图逐像素命中证据。`[verified: ../../artifacts/dragon-pbr-reuse/round04/tri_agent2048/target/target.json projection；round01/preflight_tri/preflight.json checks.projection]`

早期腿部目标中的 `protected_faces=[]`、`target_appearance_reviewed=false` 已保留。新一轮原 UV／Agent 目标写入 1,346 个保护面；包含 3＋3 面角刺共享的几何候选触发 `PROTECTED_PBR_COMPOSITION_CONFLICT:reuse:e690da1823f8a788` 并被拒绝。目标外观审查仍为 false，不能将保护检查通过等同于材质外观通过。`[verified: ../../artifacts/dragon-pbr-reuse/round04/tri_agent2048/target/target.json；round05/tri_agent128/target/target.json；round05/tri_original128/target/target.json；round05/geometry_protected/result.json]`

## 历史实际看图与问题（round04—round05）

来源高模和 Tripo 直接低模各渲染 16 张 512² 图：8 视角 × 柔光／侧光；Agent 实际查看每份正面柔光和 45° 斜视侧光，共 4 张。来源直接低模的爪部高光和翼部窄暗条已经与高模不同，不能把这些差异记成 ReSeam 误差。`[verified: ../../artifacts/dragon-pbr-reuse/round04/source_previews_audit.json；opinion: 有限视角观察]`

|来源高模|OptCuts 无共享 128² 贴图|腿部共享 128² 贴图|
|---|---|---|
|![来源](../../artifacts/dragon-pbr-reuse/round04/source_high_preview/material-front-soft-1.png)|![无共享](../../artifacts/dragon-pbr-reuse/round04/noshare128_preview/material-front-soft-1.png)|![共享](../../artifacts/dragon-pbr-reuse/round04/shared128_preview/material-front-soft-1.png)|

无共享与共享实际导出 GLB 各渲染 20 张图：5 视角 × 两种照明 × 完整材质／中性灰法线照明。Agent 实际查看共享 4 张、无共享 3 张，均观察到双翼膜大片近黑区域及头冠斑驳，中性灰法线照明下翼膜问题仍存在。目视结论为 REJECTED；无共享已经出现同类问题，尚不能归因于共享，也不能仅用 BaseColor 分辨率低解释。`[verified: ../../artifacts/dragon-pbr-reuse/round04/smoke128_appearance_review.json assets.inspected_views；opinion: 观察与归因边界]`

128² 只用于冒烟，正式 1024²／2048² 外观、近景、mip 和法线约定仍需在修正后独立核验。完整渲染图片数与实际逐张审阅数分别保留。`[opinion]`

新一轮 1024² 矩形统一密度无共享版已使用其实际导出 GLB 再渲染 8 张 512² 图，保存 [Blender 场景](../../artifacts/dragon-pbr-reuse/round05/rectangle1024_preview/review.blend) 与 [预览](../../artifacts/dragon-pbr-reuse/round05/rectangle1024_preview/preview.html)。Agent 实际查看正面材质柔光、正面中性灰法线侧光、45° 斜视材质侧光 3 张：头冠和脸部细节较 128² 平顺，但大块翼膜黑区仍存在，目视仍为 REJECTED。该结果排除了“升到 1024² 即消除大块黑翼”的解释，没有单凭图像认定最终技术根因。`[verified: ../../artifacts/dragon-pbr-reuse/round05/rectangle1024_appearance_review.json；opinion: 观察与解释边界]`

## 保留的失败

|证据|结果|
|---|---|
|导入前的非流形拒绝|本轮通过独立拆连接变体继续，原始失败与原文件保留。`[verified: ../results/normal-baking-preflight.md 蓝龙真实资产；round01/asset_preparation_audit.json]`|
|蓝龙有限射线预检|三角低模正面命中 47,102、背面 2,527、范围外 145、未命中 34；混合低模分别为 69,409／2,203／132／48，均诊断 fail。`[verified: round01/preflight_tri/preflight.json checks.projection.counts；preflight_mixed/preflight.json checks.projection.counts]`|
|原 UV 128² 早期烘焙|`UNAUTHORIZED_PBR_RASTER_OVERLAP:1920`。`[verified: round02/tri_original128/bake/result.json]`|
|早期逐 mip 验证|300 秒 `TOOL_PROCESS_DEADLINE`，后续实现修正另存轮次。`[verified: round03/tri_original128/validate/result.json]`|
|主低模原 UV 2048²|300 秒 `TOOL_PROCESS_DEADLINE`，不能按存在目录算完成。`[verified: round04/tri_original2048/bake/result.json]`|
|共享版本紧凑排布|`COMPACT_PACK_REQUIRES_SINGLE_MASTER_CHART:65`／`:68`，两个拒绝结果均保留。`[verified: round04/agent_pack/result.json；geometry_pack/result.json]`|
|腿部与翼部对应尝试|保留 affine/packing 拒绝及跨现有组拒绝，不等同于证明部件数学上不能共享。`[verified: round03/leg_candidate/result.json；wing_corner/result.json；wing_topology/result.json；wing_topology02/result.json]`|
|高模首次 GLB 重读误拒绝|旧小数舍入排序不稳定；已改为保持绕序及逐角 UV 的容差一一匹配，原 `1e-4` 容差不变。新预览成功，旧失败保留。`[verified: round01/high_preview/blender/process.log；high_preview02/preview.json；tests/test_triangle_identity_match.py]`|

## 补充混合低模的实际观察与限定诊断

法线 Agent 分别观察混合低模 1024² 的 **44 张图**和 2048² 的 **68 张图**：包含八个整龙视角、柔光／侧光、完整材质／中性法线，以及头部、翼根、爪子近景；2K 另查八视角 BaseColor／粗糙度／金属度。两尺寸均无早期大片黑翼，但近景仍有腋下黑三角、头冠及翼角根部黑线、翼缘法线波动和小型爪根暗缝，因此结论均为 `AGENT_OBSERVED_QUALITY_NOT_ACCEPTED`。`[verified: round07/mixed_1024_complete_observation.json image_count、closeup_observation；mixed_2048_complete_observation.json image_count、scalar_observation、status]`

[混合低模 1K 看图记录](../../artifacts/dragon-pbr-reuse/round07/mixed_1024_complete_observation.json) · [2K 看图记录](../../artifacts/dragon-pbr-reuse/round07/mixed_2048_complete_observation.json) · [补充模型结论](../../artifacts/dragon-pbr-reuse/round07/mixed_supplemental_summary.json)。腿部严格／近似／共同域三次对应均未形成可接受共享；保留无共享版，不将工具拒绝写成数学上不可共享。`[verified: mixed_supplemental_summary.json sharing_attempts、final_conclusion]`

三次限定诊断没有改写冻结实验。混合模型腋下 6,342 个样本中，烘焙图集相对冻结目标的平均法线误差约 **0.59°**，但最大误差约 **178.41°**；投射目标已有 70 个相对低模几何法线反向的样本，图集有 67 个，关闭来源 normal 贴图仍有 49 个。改用 2%／4% 有限射线时，反向样本为 97／99 个，另有 8／6 个无效样本，没有一致改善。`[verified: round07/mixed_projection_probe.json axilla；mixed_projection_probes_2_3.json axilla.nearest、ray2percent、ray4percent]`

这些结果提示问题不能只靠加大分辨率或关闭来源 normal 贴图解决；来源到低模的局部对应、Cage 和平滑法线处理仍需分别验证。它们没有证明唯一根因，也没有提供已经成功的修复方案。`[opinion]`

## 有限射线补充诊断（round08）

为检查最近点投射的替代路径，另在 128² 冒烟中测试前后各 4% 和 8% 包围盒对角线的有限射线，明确禁止最近点回退。4% 版本在 3,853 个实际样本中有 5 个无效样本，8% 仍有 4 个；二者均以 `PBR_PROJECTION_OR_TARGET_COVERAGE` 拒绝，没有导出为可用版本。增加距离没有消除全部失败，不能以更高命中率宣布正常外观。`[verified: round08/ray_4percent/target_request.json front_fraction、back_fraction；bake/member_audit.json samples、invalid_samples、ray_projection.nearest_point_fallback；round08/ray_8percent/bake/member_audit.json；两份 bake/result.json reason]`

## 实验后的来源读取兼容修复

[公共来源读取修复证据](../../artifacts/dragon-pbr-reuse/round07/post_experiment_public_reference_fix.json)记录了缺少切线数据时的原生／历史兼容路径修复。本轮高模已有显式 glTF 切线：修复前后的法线、切线、镜像符号一致，1,084 个实测采样的世界法线、颜色、ORM、有效性与对应信息逐位一致；证据记录 `formal_rebake_needed=false`，已有结果没有被覆盖。`custom_tangents` 修复前后仍为 true，因为原生重新生成的切线方向确实不同于来源声明切线，不能仅为消除标记而改写来源。本轮外观未通过结论保持不变。`[verified: round07/post_experiment_public_reference_fix.json checks、tested_points、source_frame_after、formal_rebake_needed、existing_artifacts_overwritten、custom_tangents_after、native_regenerated_tangent_max_abs_difference_from_declared]`

[最终实现验证记录](../../artifacts/dragon-pbr-reuse/round08/implementation_verification.json)列出确切命令与结果：相关回归测试 **142 passed**，范围内 ruff 通过，basedpyright 为 **0 errors／0 warnings／0 notes**；记录了 33 个当前代码／测试文件哈希，八份最终 GLB 哈希全部保持一致，被拒绝的视觉导出仍没有模型文件。这是实现与产物完整性检查，不能升级为完整 PBR 外观通过；当前代码哈希也不追认早期实验使用了同一版本。`[verified: round08/implementation_verification.json checks、current_checked_file_hashes、final_model_hash_checks、final_models_unchanged、refused_export_has_model、scope]`

## 耗时记录的范围

[工具调用耗时账本](../../artifacts/dragon-pbr-reuse/round07/cost_ledger.json)包含已保存公共工具的成功、拒绝、错误与超时凭据，按轮次保留每次调用及其报告时长。各调用可能嵌套或并发，因此时长加总**不是 CPU 时间，也不是项目从开始到结束的耗时**；编程、未保存的只读分析和实际看图等待也未被完整计入。这份账本用于追溯失败成本，不用于提出性能加速结论。`[verified: round07/cost_ledger.json tool_records、by_round、scope、performance_claim]`

## 未满足的计划验收条件

- 修正头冠、嘴边及翼根的局部法线／投射表现；修改后新建轮次重跑受影响的无共享和共享对照。现有图片可以定位问题，但没有单独证明具体根因。`[verified: round07/main_appearance_review.json findings.attribution_boundary；opinion: 下一步]`
- 在同一父状态、排布和密度合同下完成几何选择与 Agent 选择的受控消融。当前均使用原始来源目标，已有同目标无共享参照；但多路径诊断不能替代其余条件一致的受控比较。`[opinion]`
- 完成局部问题修正后的复验及显式 mip 目视检查；已有 mip 0／1／2 数值诊断不替代目视。未观察的图不标通过，未通过外观的尺寸不用于宣布最低可接受字节。`[opinion]`
- 保留翅膀、角刺和补充低模的失败原因；未验证可共享的部件继续独占，不凭相似外形宣称数学不可共享。`[opinion]`
- 数值诊断、美术人工、游戏 Shader 与 GPU 状态独立；当前美术人工及游戏 Shader 均为 NOT_RUN。`[verified: ../../artifacts/dragon-pbr-reuse/status.json human_acceptance、game_shader_acceptance]`

本轮限定实验以质量未通过结案，计划验收条件仍未满足。交付是可追溯的实际模型、贴图、场景和负面结果，不能作为已验收的生产资产。`[opinion]`
