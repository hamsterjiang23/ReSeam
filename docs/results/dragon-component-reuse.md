# 部件UV复用与颜色球：真实龙结果

本轮完成部件切口、锚点共同域和颜色球工具，并在 `tripo_p2` 固定规范化父资产上交付19组共享、实际贴图及完整龙预览模型。几何保持6552顶点、10832原面，计算用临时切分后仅写回UV。`[verified: components_auto_uniform02/states.json；components_report02/export_integrity.json]`

|路径|UV连通岛|独立图块|共享组|实际共享表面积|
|---|---|---|---|---|
|修复父资产无共享|203|203|0|0% `[verified: components_auto_uniform02/no_share_inspect/uv_metrics.json]`|
|OptCuts部件重展开|169|169|0|0% `[verified: components_auto_uniform02/optcuts_no_share_inspect/uv_metrics.json]`|
|纯几何复用|169|157|12|23.2825% `[verified: components_auto_uniform02/geometry_auto_inspect/uv_metrics.json]`|
|Agent部件复用|169|150|19|44.6587% `[verified: components_auto_uniform02/agent_paired_inspect/uv_metrics.json]`|
|同布局解除共享|169|169|0|0% `[verified: components_auto_uniform02/same_layout_unshared_inspect/uv_metrics.json]`|

表面积指标计入两侧成员；34个图块减少来自重展开，19个来自堆叠，均不直接等于纹理字节节省。原始Tripo参考为121岛，包含原有交叠和拓扑问题，本轮从固定修复父资产203岛进行对照；最终169个连通岛并未低于原始121，不能宣称原始到最终岛数下降。`[verified: uv_metrics.json measurement_method；artifacts/agent_tools_v1/tripo_p2_audit.json charts；opinion: 归因边界]`

双翼8类自然部件、大腿、手臂、脚底、内外踝、4类成对角及2组重复背刺均保留整部件共享。脚背与脚趾整块独占，保护白爪；头脸、躯干和尾巴主体保留当前区域布局。逐组面集合、切口、失败及回退见[完整报告](../../artifacts/dragon_reuse_development/components_report02/report.md)。`[verified: components_selection02/manifest.json；components_auto_uniform02/states.json agent_paired 指向快照 groups]`

## 颜色容忍度

`reuse.check_color` 使用Oklab颜色球：每个原色为球心，目标颜色在半径内即满足该点的颜色容忍条件。工具默认中档，可自定义正半径；本轮优先复用预览采用高档，球外位置保留给Agent看图判断。`[verified: color_tolerance.py tolerance/color_distances/check_color；opinion: 预览策略]`

|档位|半径|修改区域采样面积的球内比例|
|---|---|---|
|低|5|78.12% `[verified: components_color_high02/color_check.json presets.low]`|
|中|10|93.46% `[verified: components_color_high02/color_check.json presets.medium]`|
|高|20|99.24% `[verified: components_color_high02/color_check.json presets.high]`|

统计来自40576个采样；高档仍有449个样本、141个面包含球外位置，全部采样通过标记为False。颜色球衡量原始外观到共享目标的变化，与烘焙重建误差分开。转换公式来源及调用方式见[工具说明](../dragon-component-reuse.md#颜色球容忍度)。`[verified: components_color_high02/color_check.json summary、scope]`

## 实际交付与验收边界

- [完整龙2048预览模型](../../artifacts/dragon_reuse_development/components_color02/agent_paired_2048_export/asset.glb)、[真实颜色贴图](../../artifacts/dragon_reuse_development/components_color02/agent_paired_2048_bake/texture/BaseColor_mip0.png)及完整mip位于同级导出目录。`[verified: components_color02/agent_paired_2048_export/export.json；export_integrity.json]`
- [同布局无共享模型](../../artifacts/dragon_reuse_development/components_color02/same_layout_unshared_2048_export/asset.glb)、[对照贴图](../../artifacts/dragon_reuse_development/components_color02/same_layout_unshared_2048_bake/texture/BaseColor_mip0.png)使用同一冻结颜色目标。`[verified: components_color02/protocol.json、matrix.json]`
- 五条路径分别完成512和2048，共10项实际烘焙、GLB导出及独立重新载入；原面角位置仅有float32序列化误差，UV有限，GLB内嵌图与实际PNG逐像素一致。`[verified: components_report02/export_integrity.json checked_exports=10、all_checked_passed=true]`
- 2048共享版的材质颜色与覆盖检查通过，颜色面积均值0.002091；渲染尾部和接缝检查仍拒绝，导出身份为diagnostic。Agent已看过实际正侧背图，按允许小幅颜色差异的取舍接受为预览；未认定完整PBR、GPU或人工艺术通过。`[verified: components_report02/quality_summary.json agent_paired/2048；appearance_review.json；agent_paired_2048_export/export.json]`

![2048实际贴图预览](../../artifacts/dragon_reuse_development/components_report02/clean_agent_paired_2048/front.png)

![实际2048颜色贴图](../../artifacts/dragon_reuse_development/components_color02/agent_paired_2048_bake/texture/BaseColor_mip0.png)

## 可追溯记录

[完整报告](../../artifacts/dragon_reuse_development/components_report02/report.md)包含编号部件、前后UV、OptCuts实际调用、完整边界修正、旧目标拒绝、uniform排布对照、逐LOD失败及全部调用成本。自动路径与Agent路径的对应算法不同，属于端到端对比，不构成LLM贡献的单变量证明。`[verified: components_report02/report.md；opinion: 结论边界]`

工具代码通过679项pytest、ruff、basedpyright、Skill校验及git diff空白检查；原始输入与历史结果保留。该轮交付部件工具和UV＋BaseColor预览，不替代历史完整求解器及原质量协议目标。`[verified: 本轮pytest“679 passed”、ruff、basedpyright及Skill校验输出；opinion: 项目范围]`
