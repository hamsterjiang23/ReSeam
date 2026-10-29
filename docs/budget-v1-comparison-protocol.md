# 新求解器 v1：对照协议与持续调优规则

本文件规定比较合同，实际参数由每轮配置与协议快照锁定，结果由该轮矩阵证明。这里的 v1 指 [derivation/v1](../derivation/v1.md)；未运行的项目保留为 `NOT_RUN`，不以开发诊断代替完整 baseline 对比。`[opinion]`

## 固定数据与独立轮次

只使用 `assets/v1/energy_blade` 的500面低模与 `assets/v1/dragon` 的2000面低模，源高模带真实材质。启动/结束核对 manifest 和实际输入 SHA-256；不更改位置、原始面或已有 alignment。两者是持续调优与回归集，不据此声称未知模型泛化。`[verified: assets/v1/manifest.json models.low_triangles、coordinate_policy、scope]`

每次算法、参数、采样或阈值变更新建 round_id 和不存在的输出目录。开发轮用于定位失败、调整代理和候选策略；随后冻结代码、配置、种子、数据、候选池、质量门槛和总计算预算，再运行比较轮。根据比较轮验收失败提出的改动必须进入下一轮，不改本轮代码或覆盖失败记录。`[opinion]`

固定原始面内开发点用于求解；另一组分层面内点、接缝带、规定 mip/过滤足迹及独立灯光/相机用于检查。覆盖率分母包含全部预定原始面与必测参考，不能将难面从模型中删去后称全模型通过。低模和高模投射/几何差异单列；只测颜色时只称颜色诊断。`[opinion]`

## 方法与共同条件

| 方法 | 构造 | 对照作用 |
|---|---|---|
| 独立展开 | 全模型 xatlas 无共享；ROI 从共同 `paired_problem(planar=True)` 初值独立排布 | 无共享基线。xatlas 运行保留 `mapping[faces] == original_faces`；适配器返回普通UV提案，材质另验收。`[verified: src/reseam/budget_baselines.py prepare_baseline、solve_roi_baseline]` |
| 旧成对求解 | 同ROI、同初值、同曲面对应进入历史 `solve_pair` | 冻结历史目标及面积恢复行为，不将其改写成新方法。`[verified: src/reseam/budget_baselines.py solve_roi_baseline old_pair分支；src/reseam/paired_solver.py solve_pair]` |
| 新几何消融 | 同ROI、同初值/对应进入 `solve_budget_pair` | 分离新形状/规范与真正材质联合更新的效果。`[verified: src/reseam/budget_baselines.py geometry_only分支]` |
| 新联合求解 | 每个预算独立初始化，UV/纹理分块恢复，区域/独占候选事务更新 | 由完整验收与真实字节判断有效性；实现和运行状态由对应轮证据填写。`[opinion]` |
| OptCuts补充对照 | 现有独立适配器，保留原始几何与面角验证 | 只作强参考，绝不作新求解器初始化或后端。当前本机 runtime 配置的 binary 文件存在，但“文件存在”不等于本轮运行通过。`[verified: 本轮 runtime.local.json 检查输出 exists=true；src/reseam/backends.py optcuts分支]` |

所有方法使用相同预算集合、实际材质通道、编码/解码和 mip 合同、原面质量门槛及初始化/计算预算规则。基线若也使用纹理优化，必须调用与新方法相同的纹理更新器；另设“仅烘焙”和“烘焙后纹理优化”消融，避免将优化器附加收益归给共享。共享所需切缝和导出属性副本计入开销。`[opinion]`

每个预算独立起点，不从前一档热启动冒充独立试验；允许另列统一热启动轨道。若公共准备缓存一次供多方法使用，既报告实际总成本，也将相同准备成本归入每条比较行。ROI 内核比较固定相同原始面范围，不能将缩小区域的新方法与完整区域的旧方法比较。`[opinion]`

种子默认17/29/43。xatlas 与当前平面初始化本身确定性，种子没有伪造的后端参数，不把三次相同展开称为三个随机独立样本；若联合求解采用随机候选，记录其实际随机作用。`[verified: src/reseam/budget_baselines.py prepare_baseline seed说明与显式命令；其余为协议要求]`

## 最终矩阵与报告

每条预定行记录模型、方法、种子、图集预算、代码/配置/输入哈希、ROI或全模型覆盖、阶段耗时、原始面角映射、最终共享组、实际通道、全 mip 有效载荷、文件包体、顶点/索引增量及各原面误差。对同画质均合格的行比较最小纹理字节；无合格行时写“未取得合格输出”，而不是从最低平均误差或最高复用比例推断成功。`[opinion]`

状态至少区分 `INITIALIZATION_FAILED`、`STRUCTURALLY_INVALID`、`NUMERICAL_FAILURE`、`QUALITY_REJECTED`、`BUDGET_EXHAUSTED`、`VALIDATED`、`NOT_RUN`。求解终止、几何合格、完整材质验收、引擎/artist批准分别记录。数学不可行需要独立证明，数值或预算失败不能用该名称替代。`[opinion]`

报告必须展示全部行和失败原因、有效输出率、质量—存储曲线及实际耗时；新方法即使没有降低合格存储，也要如实交付负结果和下一轮改进依据。神经网络辅助属于额外消融，保持同可表达范围、原始面角仿射导出、同质量与时间预算，不允许单靠训练loss宣布改善。`[opinion]`
