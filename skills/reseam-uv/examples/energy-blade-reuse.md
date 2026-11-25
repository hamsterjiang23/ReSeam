# 能量刀的完整旧岛选区与共享

## 适用条件与输入

适用于自然部件跨旧UV岛、重展开后外侧出现碎片，或部件拓扑开口与二维展开需要分步排查的任务。无UV低模投射与镜像展示可参考本例的独立阶段。`[opinion]`

原始能量刀低模500面／252顶点、无UV，xatlas初始化68岛，独立镜像变体700面／434顶点。颜色从保留高模投射并沿来源对应继承。`[verified: docs/results/legacy-assets-reseam.md L35–L39；docs/results/meshquery-uv-reconstruction.md L9]`

## 历史过程与关键决策

1. 以113面自然选区重展开，选区内部变整块，全局68→70岛。扩大到相关完整143面旧岛，在同OptCuts后端得到62岛。`[verified: docs/results/meshquery-uv-reconstruction.md L35]`
2. 仅保留边界的计划形成环带。补连续纵切获得单盘后，LSCM仍自交。保留切口、改正权调和后通过对应展开步骤。`[verified: docs/results/meshquery-uv-reconstruction.md L36]`
3. 完整旧岛共享路径62岛／59图块、3组共享，实际共享表面积13.2886%，产生真实2048 BaseColor、颜色预览GLB和匹配解除共享对照，颜色数值诊断未通过。`[verified: docs/results/meshquery-uv-reconstruction.md L13、L26]`
4. 授权镜像分支另建父几何，完成同目标共享/解除共享对照，展示版本2岛／1图块。`[verified: README.md L23–L27；docs/results/legacy-assets-reseam.md L33–L39、L63]`

## 失败、取舍与结果

真实PartField推理得到8组，几何多尺度产生195候选。PartField选区后续LSCM/ABF自交拒绝，预定烘焙未运行。相关调用保留在历史明细中。`[verified: docs/results/meshquery-uv-reconstruction.md L38]`

镜像展示版全表面参与成对共享，Agent外观预览保留，综合数值拒绝。原始低模、xatlas起点和镜像新父分别记录。`[verified: README.md L23–L27；docs/results/legacy-assets-reseam.md L35–L39]`

## 迁移到当前资产

用 `mesh.index/query` 和 `uv.inspect` 比较选区内外及全模型岛数。选区切穿旧岛时构造完整旧岛闭包候选，核查保护区后再决定范围。保存相同父状态和后端的比较，读取实际切口与返回快照。`[opinion]`

按拓扑开口、二维自交、对应、颜色与最终渲染逐项定位反馈。先验证当前无共享展开/烘焙，再尝试共享，保留每次失败及成本。具体算子和展开参数通过当前 `tools describe` 核实。`[opinion]`

## 证据入口

以下路径按 [索引的仓库定位方法](index.md) 读取。`[opinion]`

|用途|仓库根相对路径及读取位置|
|---|---|
|固定几何修正与失败|`docs/results/meshquery-uv-reconstruction.md` 的“反馈实际改变了什么”，明细 `artifacts/meshquery_v1/history_report01/report.md` 的“关键修正与消融”。`[verified: docs/results/meshquery-uv-reconstruction.md L35–L38]`|
|固定几何颜色模型|`artifacts/meshquery_v1/history_bestshared01/energy_wholechart_shared/bake2048/color_only_preview.glb`。`[verified: docs/results/meshquery-uv-reconstruction.md L26]`|
|固定几何实际贴图|`artifacts/meshquery_v1/history_bestshared01/energy_wholechart_shared/bake2048/texture/BaseColor_mip0.png`。`[verified: docs/results/meshquery-uv-reconstruction.md L26]`|
|镜像过程与原始来源|`docs/results/legacy-assets-reseam.md` 的“历史能量刀”。`[verified: docs/results/legacy-assets-reseam.md L25–L39]`|
|镜像GLB及真实贴图|`docs/assets/legacy-assets-reseam/round02/models/energy_blade/mirror_shared_final/2048/asset.glb`、`docs/assets/legacy-assets-reseam/round02/textures/energy_blade/mirror_shared_final/2048/BaseColor_mip0.png`。`[verified: docs/results/legacy-assets-reseam.md L37]`|
|UV、模型和贴图对照图|`docs/assets/legacy-assets-reseam/round02/figures/energy_blade_uv.png`、`docs/assets/legacy-assets-reseam/round02/figures/energy_blade_models.png`、`docs/assets/legacy-assets-reseam/round02/figures/energy_blade_textures.png`。`[verified: docs/results/legacy-assets-reseam.md L27–L31]`|
