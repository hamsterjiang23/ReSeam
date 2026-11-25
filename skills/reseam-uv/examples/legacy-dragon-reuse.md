# 历史2,000面龙的UV重构与镜像对照

## 适用条件与输入

适用于无UV低模已有对应高模颜色，需要比较部件重展开、近似共享与已授权镜像分支的任务。先识别原始输入和各轮新父几何。`[opinion]`

本例历史龙低模原始2,000面／996顶点，无原UV，xatlas初始化177岛。镜像展示分支为2,062面／1,092顶点，原始资产保留。`[verified: docs/results/legacy-assets-reseam.md L53–L57；docs/results/meshquery-uv-reconstruction.md L9]`

## 历史过程与关键决策

1. 从保留高模投射颜色到固定低模，再将来源对应继承至后续独立镜像变体。无UV输入的岛数以本轮初始化记录。`[verified: docs/results/legacy-assets-reseam.md L53–L57]`
2. 固定几何龙翼重构先尝试自然选区，全模型177→186岛。扩大到相关完整265面旧岛后得到173岛。外侧残片在全模型统计中显现。`[verified: docs/results/meshquery-uv-reconstruction.md L35]`
3. 完整旧岛重展与共享路径得到173岛／171图块、2组共享，实际共享表面积0.9189%，第三对对应拒绝，颜色数值诊断未通过。`[verified: docs/results/meshquery-uv-reconstruction.md L14]`
4. 独立镜像路线完成同新几何共享/解除共享控制，展示版6岛／3图块。颜色由保留半侧继承，跨几何外观单列。`[verified: README.md L23–L27；docs/results/legacy-assets-reseam.md L63–L71]`

## 失败、取舍与结果

镜像展示版实际全表面参与成对共享，覆盖仅20.40%，综合数值质量拒绝，Agent外观预览保留。该轮范围是静态CPU UV＋BaseColor、LOD0/1/2。`[verified: README.md L23；docs/results/legacy-assets-reseam.md L5]`

后续45项指标补测对原始参考及36个方法/尺寸条目共39行增加测量，记录已有UV、贴图与质量状态。`[verified: docs/results/legacy-assets-45-metrics.md L3]`

## 迁移到当前资产

用当前高低模来源显式建立投射目标，先取得无共享烘焙。逻辑部件跨旧岛时比较自然选区与完整旧岛闭包对全模型的影响，同时检查保护区。参数依当前 `tools describe` 查询，保留对应失败。`[opinion]`

镜像路线依据当前授权创建新父几何，并从相同新父建立共享/解除共享对照。分别报告固定几何共享结果、镜像外观变化、覆盖、字节与验收范围。`[opinion]`

## 证据入口

以下路径按 [索引的仓库定位方法](index.md) 读取。`[opinion]`

|用途|仓库根相对路径及读取位置|
|---|---|
|输入、镜像过程与产物|`docs/results/legacy-assets-reseam.md` 的“历史龙”“比较口径与保留边界”。`[verified: docs/results/legacy-assets-reseam.md L43–L77]`|
|选区失败及固定几何共享|`docs/results/meshquery-uv-reconstruction.md` 的“三资产结果”“反馈实际改变了什么”，明细 `artifacts/meshquery_v1/history_report01/report.md`。`[verified: docs/results/meshquery-uv-reconstruction.md L14、L22、L35]`|
|固定几何颜色GLB|`artifacts/meshquery_v1/history_dragon_shared01/dragon_skill_wholechart/bake2048/color_only_preview.glb`。`[verified: docs/results/meshquery-uv-reconstruction.md L27]`|
|镜像实际GLB|`docs/assets/legacy-assets-reseam/round02/models/dragon/mirror_shared_final/2048/asset.glb`。`[verified: docs/results/legacy-assets-reseam.md L55]`|
|镜像真实贴图|`docs/assets/legacy-assets-reseam/round02/textures/dragon/mirror_shared_final/2048/BaseColor_mip0.png`。`[verified: docs/results/legacy-assets-reseam.md L55]`|
|模型与UV图|`docs/assets/legacy-assets-reseam/round02/figures/dragon_models.png`、`docs/assets/legacy-assets-reseam/round02/figures/dragon_uv.png`。`[verified: docs/results/legacy-assets-reseam.md L45–L47]`|
|步骤、成本与失败索引|`docs/assets/legacy-assets-reseam/round02/steps.json`、`docs/assets/legacy-assets-reseam/round02/calls.csv`、`docs/assets/legacy-assets-reseam/round02/failure_index.json`。`[verified: docs/results/legacy-assets-reseam.md L77]`|
