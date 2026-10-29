# 求解器重新设计：当前方向与边界

当前优先阶段已按用户确认扩展为[烘焙优先的拓扑变体](../docs/bake-topology-plan.md)：先验证无共享烘焙，再研究缩图与复用。固定几何v1推导和历史资产保持独立，后续对比按拓扑variant分组。`[verified: 本轮用户选择“加入拓扑优化，先做好烘焙”；assets/v1/README.md 多轮测试规则2、9]`

状态：[预算恢复和交付存档的核心缺项已修复](../docs/results/budget_v1_completion_followup.md)，新的完整真实对照仍待执行；当前优先拓扑与烘焙。旧60项轮因完整性审计中止，不能当作新算法完整对比；尚无已证明的同质量缩图收益。旧V1/V2继续归档。目标见[完整实施与对比](budget-v1-implementation.md)，诊断及实际贴图见[中止报告](../artifacts/budget_v1/budget_v1_comparison_round05/report.after_audit.md)。`[verified: docs/results/budget_v1_completion_followup.md 审计项与当前状态；artifacts/budget_v1/budget_v1_comparison_round05/audit_interruption.json reason]`

## 已确定的方向

- 借鉴OptCuts的高层思路，重新设计ReSeam求解器；不继续把OptCuts核心作为新求解器后端，也不把其输出设为新方法的默认/必经初始化。
- OptCuts保留为参考与强对照。现有依赖它的V1/V2代码、配置、子模块和结果保留用于历史复现，不追溯改写成自研求解器结果。
- 继续面向固定AI重拓扑资产的UV问题：原始几何不变，研究切缝、参数化、近似区域共享和排布的关系；目标仍需落实到有效纹素密度、畸变、密度分配与采样安全。`[verified: 用户本轮方向；goal/v2.md核心目标]`

## 下一步需要重新定义

先明确优化变量、主目标、可行域和数值求解方法，再设计候选搜索。应重点消除“内层降低替代能量，最终验收却要求另一组条件”的脱节；旧轮门限是历史协议，不自动当作新求解器已经论证合理的约束。不能仅换掉后端名称或继续增加packing候选数量就称为重新设计。`[opinion]`

先在一个真实龙翼区域上证明可用的对应与成对参数化，再扩展整模型。检查梯度、翻转/自交、密度与对应误差，并展示原始/新UV和真实纹理对拍；构造例和流程返回SUCCESS不能代替真实模型效果。具体模型、门限、预算及A/B协议在设计完成后重新锁定。`[opinion]`

## 现有证据

[V2首轮结果](../docs/v2-joint-results.md)：能量刀取得有限排布改善，龙无改善，真实复用组为0；真实复用候选没有进入材质门禁。该结果支持重新检查求解问题与可行性处理，不能证明近似复用不可行，也不能归因于左右纹理不兼容。`[verified: docs/results/v2_joint_round01_summary.json candidates/groups]`

每预算 UV/纹理联合更新、完整面独占例外和 CPU 多通道/实际纹素中心验收已接通；自动区域发现仍限于初始化的启发式几何区域对，神经网络提案与目标引擎验收尚未实施。实际效果由完整冻结对照判定，不预先承诺全局最优或优于OptCuts。`[verified: src/reseam/budget_solver.py、budget_material.py 当前入口；docs/running-budget-complete-v1.md 范围]` `[opinion]`
