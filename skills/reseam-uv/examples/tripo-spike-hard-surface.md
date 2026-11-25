# Tripo带刺匕首的切缝线索与局部合岛

## 适用条件与输入

适用于已有低模和真实normal材质线索，需要判断初始切缝、硬边加缝和局部合岛收益的任务。`[opinion]`

本例由用户选择已有424面重拓扑。1,061面参考因非流形边被直接导入拒绝，材质通过该参考GLB投射。约191万面高模未运行。normal线索保留源面、重心坐标、距离与有效性，AO来自16射线几何查询。`[verified: docs/results/tripo-spike-hard-surface.md L3–L5]`

## 历史过程与关键决策

1. 比较OptCuts无共享、硬边无共享及少量共享。OptCuts19岛，拉伸P95约1.619，硬边路线196岛，P95约1.192，共享路线196岛／195图块，实际共享表面约0.004。`[verified: docs/results/tripo-spike-hard-surface.md L13–L23]`
2. 后续固定几何与颜色目标，从OptCuts实际开口出发，Agent试合相邻岛10/12，仅删除共同原边377，并保留其它开口。局部LSCM重展后19→18岛、局部最大拉伸1.261。以上编号绑定该轮父快照。`[verified: docs/results/optcuts-agent-uv.md L5、L13]`
3. 相同排布设置比较三种子，最好覆盖45.17%→43.87%，最终保留OptCuts。第二候选也到18岛，最好覆盖43.84%，排布筛查后停止，未烘焙/导出。`[verified: docs/results/optcuts-agent-uv.md L13]`

## 失败、取舍与结果

硬边路线在256像素下保留轮廓和红黑大块结构，高频纹理明显损失。表中数值验收均False、GLB回读均True。normal用于切口判断，该轮交付范围为静态UV＋BaseColor。`[verified: docs/results/tripo-spike-hard-surface.md L7、L13–L23；docs/results/optcuts-agent-uv.md L7]`

局部18岛候选实际生成贴图和GLB，最终OptCuts19岛保留，该轮静态UV＋BaseColor检查LOD0/1/2。第二候选停在排布筛查，保留相应成本与未运行项。`[verified: docs/results/optcuts-agent-uv.md L7、L13、L19–L24]`

## 迁移到当前资产

按 [硬表面Skill](../../reseam-hard-surface/SKILL.md) 先取得OptCuts实际切缝。选择当前原边相邻的完整岛或自然区域，预览后局部删缝重展开，并比较全模型岛数、保护特征、拉伸、覆盖及真实烘焙。`[opinion]`

根据已定位问题追加AO/normal线索，记录真实来源与当前切线约定。以当前资产数据重新选择边和区域，参数依 `tools describe` 查询。保留父快照和候选，依据实际收益决定继续、修订或回退。`[opinion]`

## 证据入口

以下路径按 [索引的仓库定位方法](index.md) 读取。`[opinion]`

|用途|仓库根相对路径及读取位置|
|---|---|
|来源、AO/normal与失败|`docs/results/tripo-spike-hard-surface.md`，明细 `artifacts/tripo_spike_hard_surface/round03`。`[verified: docs/results/tripo-spike-hard-surface.md L3–L29]`|
|硬边实际图像|`docs/assets/tripo-spike-hard-surface/round03/comparison.png`。`[verified: docs/results/tripo-spike-hard-surface.md L9]`|
|合岛过程与取舍|`docs/results/optcuts-agent-uv.md` 的“Tripo424面带刺匕首”，调用与失败入口 `docs/assets/optcuts-agent-uv/round01/summary.json`、`docs/assets/optcuts-agent-uv/round01/failures.json`。`[verified: docs/results/optcuts-agent-uv.md L9–L24、L49]`|
|实际UV、模型及贴图对照图|`docs/assets/optcuts-agent-uv/round01/tripo_comparison.png`。`[verified: docs/results/optcuts-agent-uv.md L11]`|
|保留OptCuts模型|`docs/assets/optcuts-agent-uv/round01/models/tripo/optcuts/seed17/asset.glb`。`[verified: docs/results/optcuts-agent-uv.md L19]`|
|真实合岛候选模型|`docs/assets/optcuts-agent-uv/round01/models/tripo/agent_merge/seed17/asset.glb`。`[verified: docs/results/optcuts-agent-uv.md L22]`|
