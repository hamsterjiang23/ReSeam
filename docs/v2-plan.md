# V2 实施计划

> 历史阶段：本页记录已经执行的固定岛 packing 对照，原预算说明不再约束新联合流程。当前目标与新增8小时预算见 [V2 目标](../goal/v2.md)，当前实施入口见 [联合计划](v2-joint-plan.md)。本页保留原协议，不追溯改写旧实验。

## 当前决定

先隔离固定岛排布的收益：冻结 UV 拓扑、形状与相对密度，仅改变姿态和共同尺度。后端输出只作姿态提案，用原始规范化岛形重新构造，独立检查后才提交。这样即便后端有量化或逐岛尺度差异，也不能悄悄变更比较条件。`[opinion]`

实现入口为 `uv run reseam pack-benchmark --config configs/v2_fixed_uv_round01.json`；本地 Blender 路径沿用 `configs/runtime.local.json`。结果位于新建的 `artifacts/v2/<round_id>`，目录存在即拒绝覆盖。`[opinion]`

前置数据是本机 V1 round03 导出的六份 `downstream/pack/state.json`；配置锁定其 SHA-256，缺失或不匹配会拒绝运行。`artifacts/` 不随 Git 提交；新机器须带上对应冻结产物，不能生成不同 UV 后沿用这轮哈希或覆盖旧 round。每轮另存源码快照和配置。`[verified: configs/v2_fixed_uv_round01.json“sources”；packing_benchmark.py“LOCKED_INPUT_HASH_MISMATCH”]`

## 固定协议

输入为 V1 round03 已有 packed state，三种 UV 来源 × 两模型 × 两个 pack 后端 × 三种种子，共 36 个条件运行。这里比较的是给定 UV 的排布能力，不重新比较原始展开时间，也不声称是全部 V1 方法的 V2 排名。dragon 的三种来源可能重复，重复记录不增加样本量。`[opinion]`

每运行最多 90 秒、4 个提案，总轮次上限 3600 秒；初始化、原布局验证、仅共同缩放、失败、终验及导出均计时。终验预留 5 秒；如实际超时记录 overrun，不把超时输出纳入等预算优胜结论。种子 17、29、43 从同一输入独立启动，不继承前次最好状态。首个提案是无随机预旋转，后续使用确定性派生随机数。`[opinion]`

单提案子进程最多 45 秒，同时受本次剩余预算约束。原先草案 25 秒/运行、10 秒/提案被 Blender 19.453 秒的真实探测否定，故在正式运行前统一提高上限。两后端同上限及提案数，实际耗时不同，不声称使用了完全相同计算量。`[verified: artifacts/v2/blender_bounded_probe/timing.json“wall_seconds”]`

使用原先 8 小时总预算剩余额度，不重新获得 8 小时。既有保守记账为 20534.514154 秒，剩余 8265.485846 秒；本次冒烟和正式轮次另记实际墙钟。编程和人工评审不计入自动实验预算。`[verified: artifacts/delivery/v1_round03/budget_accounting.json；本轮延续记账]`

共同尺度搜索：先计算边界/总面积必要上界，有限采样，再局部细化。凹岛绕顶点均值缩放的可行性不一定单调，因此不能把二分局部细化称为全局最优尺度，采样失败也不证明该姿态不可行。接受只根据重新构造的精确三角形验证。`[opinion]`

验收条件：原始几何、面和 UV 副本身份不变；所有岛为正向刚性相似变换且共享尺度；无翻转、自交、岛间重叠；1024² 下 gap≥16px、border≥8px；任何面的密度不低于原布局。浮点验收容差为 1e-5 像素，不能以降低 padding 换覆盖率。`[opinion]`

## 实施顺序

1. 实现规范化状态、后端 packing-only 入口、姿态投影、独立验证和最好可行档案。
2. 构造网格覆盖尺度/密度不变量、非法重叠与间距、形状改变、超时回退及不覆盖。
3. 小规模真实模型冒烟，检查 worker API、UV 身份及 GLB 往返；记录资源与成本。
4. 锁定配置与代码哈希，串行跑完整矩阵，独立审查结果；代码修复另建轮次。
5. 输出中文对照页与实测结论，更新 V2 验收清单。`[opinion]`

收尾复核命令：`uv run python scripts/summarize_packing.py --round artifacts/v2/v2_fixed_uv_round01 --output artifacts/delivery/v2_fixed_uv_round01`。它重新读取最终状态验证不变量，并按全部种子输出中位数、范围、失败和超时；不是第二套独立数值算法。`[verified: scripts/summarize_packing.py“summarize”]`

## 后续路线与可证伪条件

OptCuts/AutoCuts 提供切缝—参数化联合优化参照；LNS 提供区域替换的搜索组织方式；AT 提供连续松弛候选；GFPack++ 提供不规则 packing 的学习优化参照；SQP/ALM 提供连续约束求解工具。它们不是当前代码中已经实现的组件。`[opinion]`

“packing 瓶颈引导重新开放三维区域内的 seam 自由度”是待检验研究假设。若等预算多起点 packing 已解释全部收益，或者重新切缝没有稳定收益，应报告负结果，不把组合流程自动称为新颖方法。接触近邻只是启发式诊断，不是真正的 KKT 乘子或瓶颈因果证明。`[opinion]`

UV 复用/Texture Carving 另设质量—存储实验：固定渲染误差比较所需纹理尺寸，不能用未经批准的 UV 重叠提高当前 occupancy 分数。查阅入口统一在 [参考文献](../references/literature.md)。`[opinion]`
