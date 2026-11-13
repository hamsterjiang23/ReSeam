# 防御塔 UV 全方法对照

输入包及原生失败保存在同级 round01，最终材质来源和结果位于 round02。

统一设置为 2048² BaseColor＋Normal，roughness=.6、metallic=0，法线约定采用 OpenGL。

|方法|三角形|岛 / owner|覆盖率|产物|状态|
|---|---:|---:|---:|---|---|
|原 UV · 统一 2K|7278|3998 / 3998|70.32%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/source_uv_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\source_uv_2048]`|
|FlattenAnything|7278|4 / 4|未测|失败记录|UV 重叠，烘焙未完成 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\flatten_anything_2048]`|
|OptCuts|7278|— / —|未测|失败记录|原生失败；切缝修复后超时 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\optcuts_2048]`|
|xatlas|7278|363 / 363|35.03%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/xatlas_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\xatlas_2048]`|
|Blender Smart UV|7278|836 / 836|28.08%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/blender_smart_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\blender_smart_2048]`|
|PartUV|7278|120 / 120|45.75%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/partuv_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\partuv_2048]`|
|MeshQuery 项目实现|7278|108 / 108|50.93%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/meshquery_repaired_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\meshquery_repaired_2048]`|
|ReSeam 原形无共享|7278|108 / 108|50.93%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/reseam_unshared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\reseam_unshared_2048]`|
|ReSeam 原形共享|7278|108 / 95|52.82%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/reseam_shared_boundary_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\reseam_shared_boundary_2048]`|
|ReSeam 原形再整理|7278|82 / 82|51.71%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/reseam_optimized_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\reseam_optimized_2048]`|
|镜像塔 · 无共享|7650|124 / 124|50.30%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_unshared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\mirror_negative_unshared_2048]`|
|镜像塔 · 共享|7650|124 / 62|60.23%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_shared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\mirror_negative_shared_2048]`|
|镜像塔 · 合岛后共享|7650|102 / 51|56.44%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_optimized_minimum_shared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\mirror_negative_optimized_minimum_shared_2048]`|
|合岛后 · 无共享对照|7650|102 / 102|50.62%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_optimized_minimum_unshared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\mirror_negative_optimized_minimum_unshared_2048]`|
|镜像塔 · 大块合岛共享|7650|50 / 25|56.71%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_aggressive_minimum_shared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\mirror_negative_aggressive_minimum_shared_2048]`|
|大块合岛 · 无共享对照|7650|50 / 50|56.95%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_aggressive_minimum_unshared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\mirror_negative_aggressive_minimum_unshared_2048]`|
|镜像塔 · 最终合岛共享|7650|48 / 24|56.82%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_exhaustive_minimum_shared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\mirror_negative_exhaustive_minimum_shared_2048]`|
|最终合岛 · 无共享对照|7650|48 / 48|57.11%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_exhaustive_minimum_unshared_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\mirror_negative_exhaustive_minimum_unshared_2048]`|
|原形共享同目标 · 无共享对照|7278|108 / 108|50.54%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/reseam_shared_boundary_control_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\reseam_shared_boundary_control_2048]`|
|原形共享 · 整组退回候选|7278|108 / 97|52.00%|[GLB](../../../../artifacts/tower_uv_comparison/round02/evaluation/reseam_shared_repaired_2048/export/asset.glb)|已烘焙／已导出 `[verified: E:\ReSem\artifacts\tower_uv_comparison\round02\evaluation\reseam_shared_repaired_2048]`|

数值状态 DIAGNOSTIC 保留完整误差，实际看图结论由 appearance_review.json 记录。美术人工与游戏 Shader 状态见 protocol.json。 `[verified: 各方法 validation.json；protocol.json]`

OptCuts 原生运行失败，显式切缝修复重试达到 2400 秒上限。MeshQuery 的 LSCM 和原形近似共享的失败及后续修正均保留。 `[verified: round01/native_retry/optcuts/result.json；round02/native_repaired/optcuts/result.json；evaluation]`

原形共享主图采用显式 4 像素主图块边界延拓目标，保留全部 13 组；原始拒绝、整组退回候选及同目标无共享对照均可追溯。 `[verified: evaluation/reseam_shared_boundary_2048/target/target.json；selected]`

原 UV 统一到 2K 时保留原布局，实测岛间距 0 像素、边界约 2.38 像素；其余完成排布的候选按 16 像素间距、8 像素边界请求并实测，CSV 提供实际值。 `[verified: source_uv inspection.json；各 pack geometry.json]`

原 UV 的 3998 岛按原始索引邻接统计，纹理坐标在图像上相接的片不自动视为已连接。ReSeam 通过显式接缝重构与重展开建立新的连接。 `[verified: source_uv inspect；craft/seam_decision.json]`

ReSeam 采用 MeshQuery 查询、原 UV 开口重建和 Blender 展开作为实际起点，后续逐步合岛与镜像共享。OptCuts 本资产未得到可用结果。 `[verified: craft、optimization、native_repaired]`