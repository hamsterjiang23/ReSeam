# 保留原始121岛的部件重展开与复用

本轮从原始UV的121个连通岛出发，跳过78面独立三角岛修复，保留外观可用的UV。整龙原几何、面和面角身份保持不变；翅膀、四肢及角刺使用前轮部件范围和已修正锚点重新执行，旧203岛父资产及结果保留。`[verified: 用户最新指示；raw121_selection01/manifest.json raw_uv_policy]`

## 锁定比较

- 原始UV参考、保留原岛统一排布、OptCuts部件重展开无共享、同候选池自动复用、Agent锚点复用，以及同展开/同目标解除堆叠的无共享对照。`[opinion]`
- 原始UV有33对正面积小交叠、20对接触。保留模式绑定原始快照与状态哈希，只容忍原有面片对的局部布局在共同相似变换下未变的冲突；严格检查状态单列，原始交叠不计作本轮共享收益。`[verified: 原始geometry_report输出；source_uv.py preserve_source_conflicts]`
- 各路径使用相同父几何、部件、源材质、冻结颜色目标、排布与颜色容忍度；先512像素冒烟，再2048像素实际颜色对照。历史203岛结果只作历史比较。`[opinion]`
- 逐步请求、父子状态、图、成本与拒绝证据写入新目录；不自动挖出三角例外，失败优先整块保留原UV。`[opinion]`

## 实测结果

完整UV＋BaseColor预览已生成：原始121岛降至117岛，实际独立图块降至98个，19组共享覆盖模型表面积44.6587%。独立图块较统一排布的无共享对照减少23个（19.01%）。`[verified: raw121_repairs03/final_inspect/uv_metrics.json；raw121_color02各路径uv_metrics.json]`

|2048像素、同一排布规则|连通岛|独立图块|共享组|
|---|---|---|---|
|保留原UV无共享|121|121|0 `[verified: no_share_2048_inspect/uv_metrics.json及snapshot groups]`|
|OptCuts部件重展开无共享|117|117|0 `[verified: optcuts_no_share_2048_inspect/uv_metrics.json及snapshot groups]`|
|自动几何复用|117|105|12 `[verified: geometry_auto_2048_inspect/uv_metrics.json及snapshot groups]`|
|Agent部件复用|117|98|19 `[verified: agent_paired_2048_inspect/uv_metrics.json及snapshot groups]`|
|同布局解除共享|117|117|0 `[verified: same_layout_unshared_2048_inspect/uv_metrics.json及snapshot groups]`|

4个图块减少来自部件重展开，另外19个来自实际堆叠。原始与最终均3个单面岛，未选区域内部新增切缝为0，原UV局部形状在排布相似变换下保持不变；原几何和面集合未改。旧169岛／150图块是203岛修复父资产上的历史结果，不混为本轮同父对照。`[verified: raw121_report01/preservation.json；各路径uv_metrics.json；opinion: 历史比较边界]`

OptCuts旧UV初值与纯几何初值已有[同父状态消融](../../artifacts/dragon_reuse_development/raw121_initialization_ablation02/steps.md)；大腿与手臂沿[原自然边界共同域修正](../../artifacts/dragon_reuse_development/raw121_repairs03/steps.md)，未通过逐面隔离解决。完整请求、失败、父子快照、颜色目标、实际贴图、数值误差、密度、字节和成本见[完整报告](../../artifacts/dragon_reuse_development/raw121_report01/report.md)。`[verified: 对应steps.json及report.md]`

## 实际资产与外观

- [完整龙GLB](../../artifacts/dragon_reuse_development/raw121_color02/agent_paired_2048_export/asset.glb)。`[verified: 对应export.json asset]`
- [真实2048颜色贴图](../../artifacts/dragon_reuse_development/raw121_color02/agent_paired_2048_bake/texture/BaseColor_mip0.png)，另有完整12级mip。`[verified: 对应bake.json及texture目录]`
- [同布局无共享GLB](../../artifacts/dragon_reuse_development/raw121_color02/same_layout_unshared_2048_export/asset.glb)。`[verified: 对应export.json asset]`

Agent已查看实际2048贴图的正、侧、背面，按用户允许小幅色差的取舍接受为外观预览，眼嘴与白爪特征保留。颜色重建平均误差0.00191363，颜色检查通过；旧渲染与接缝数值门禁仍拒绝，因此导出保留diagnostic身份。完整PBR、GPU和人工艺术验收未认定。`[verified: raw121_report01/appearance_review.json actual_bake_review；agent_paired_2048_validate/validation.json material/render/seams；对应export.json；opinion: 外观判断]`

高档颜色球半径20内覆盖修改区域采样面积99.2368%，仍有141个面含范围外采样；没有把这些面自动拆成小岛。固定2048尺寸的RGBA8完整mip仍为22,369,620字节，不将图块减少写成编码压缩或已验收最小存储。`[verified: raw121_tolerance03/color_check.json summary；各路径bake/result.json payload_bytes；opinion: 结果边界]`

![同一排布规则下的UV前后](../../artifacts/dragon_reuse_development/raw121_report01/uv_before_after.png)

![实际贴图正面](../../artifacts/dragon_reuse_development/raw121_final_views02/actual/front.png)
