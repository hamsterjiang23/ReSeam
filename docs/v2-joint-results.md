# V2 联合原型：正式对照与结论

本轮归档作为后续 [独立求解器设计](../goal/solver-redesign.md) 的对照。Git内可读的矩阵、原始评分与预览见 [紧凑证据归档](results/v2_joint_round01/README.md)；下文artifacts链接指本机完整产物。`[verified: 用户本轮方向；results/v2_joint_round01/archive_manifest.json]`

轮次：`v2_joint_round01`。24项搜索、24项下游流程与18份有效代理盲评已完成，人工状态为 `NOT_REVIEWED`。本轮改善了能量刀的排布，未解决龙的空间利用率，也未取得真实模型的可接受近似复用。`[verified: artifacts/v2_joint/v2_joint_round01/matrix.json、audit.json、review_summary.json]`

入口：[逐运行对比页](../artifacts/v2_joint/v2_joint_round01/index.html)、[原始矩阵](../artifacts/v2_joint/v2_joint_round01/matrix.json)、[候选/成对比较审计](../artifacts/v2_joint/v2_joint_round01/audit.json)、[运行命令](running-v2.md)。`[verified: 本轮对应产物]`

## 已实现与已验证的范围

- Python四轨道调度，独立原版OptCuts初始化；区域/曲面对应、成对UV求解、部分区域提案、组级排布、接缝反馈和事务回退；相关入口为 `joint-unwrap`、`joint-benchmark`。`[verified: src/reseam/joint_search.py run_joint；src/reseam/paired_solver.py solve_pair]`
- 共享UV只允许通过实际交叠单元的曲面兼容检查；仍检查成员单射、方向、畸变、逐面密度、1px对应误差、16px组间距和8px图集边界。合法共享构造例可通过材质证书并提交；这不等于真实AI模型已经可复用。`[verified: joint_validation.validate_joint_quality；tests/test_joint_material.py test_legal_shared_candidate_commits_with_material_certificate]`
- 完整低模上分成员烘焙，线性RGB合成，按成员TBN检验共享法线；实际GLB的逐角UV与NORMAL/TANGENT属性往返检查。镜像构造例包含3个负手性面角，验证通过。`[verified: artifacts/v2_joint/worker_smoke03_export/result.json export_frames；joint_downstream.evaluate_joint]`
- 24个最终状态均完成Base Color、法线和256²/128² mip渲染以及GLB导出。这里的“完成”表示流程执行和导出检查通过，源投射/框架异常另列，不是artist交付合格。`[verified: matrix.json downstream_status；各downstream/result.json]`

## 四轨道正式结果

两模型×四轨道×种子17、29、43。每项搜索300秒上限，包含独立初始化；同模型/种子的四轨道初始完整状态JSON逐字节一致，共六组匹配。没有沿用上一轮最好状态，算法源码、资产及后端冻结检查为PASS。`[verified: protocol.json；audit.json initial_state_audit；freeze_status.json]`

以下是UV三角形面积并集占1024²图集的比例。重复UV不重复计入唯一覆盖率；颜色或dilation覆盖不参与这个指标。`[verified: joint_validation.validate_joint_geometry unique_coverage]`

| 模型 | 轨道 | 种子17 / 29 / 43 | 中位数 |
| --- | --- | --- | --- |
| 能量刀 | 固定岛排布 | 48.16% / 47.04% / 46.65% | 47.04% `[verified: audit.json groups energy_blade/packing_only]` |
| 能量刀 | 接缝—排布 | 48.41% / 47.50% / 48.61% | 48.41% `[verified: audit.json groups energy_blade/seam_pack]` |
| 能量刀 | 仅近似复用 | 48.16% / 47.04% / 46.65% | 47.04% `[verified: audit.json groups energy_blade/reuse_pack]` |
| 能量刀 | 联合 | 49.14% / 49.42% / 49.14% | 49.14% `[verified: audit.json groups energy_blade/joint]` |
| 龙 | 固定岛排布 | 28.21% / 28.21% / 28.21% | 28.21% `[verified: audit.json groups dragon/packing_only]` |
| 龙 | 接缝—排布 | 28.21% / 28.21% / 28.21% | 28.21% `[verified: audit.json groups dragon/seam_pack]` |
| 龙 | 仅近似复用 | 28.21% / 28.21% / 28.21% | 28.21% `[verified: audit.json groups dragon/reuse_pack]` |
| 龙 | 联合 | 28.21% / 28.21% / 28.21% | 28.21% `[verified: audit.json groups dragon/joint]` |

能量刀的联合轨道相对固定岛轨道，逐种子有效纹素密度比为1.010140、1.024970、1.026363；**成对比值中位数为+2.497%**。相对接缝—排布轨道为+0.745%。这是逐种子先比较再取中位数，不能替换成两个组中位数的比值。龙的对应比值均为1。`[verified: audit.json paired_comparisons；独立计数审查]`

826个候选中，43个接受、403个质量拒绝、286个未严格改善、94个在其他阶段拒绝。43个接受动作包括34次排布与9次保持逐角形状的切分；全部来自能量刀。24个最终状态的复用组数均为0，龙的12项运行均为完整回退/保留初值。不能把24项流程SUCCESS解释为24项机制收益，也不能把本轮联合轨道的改善归因于纹理复用。`[verified: audit.json candidates、accepted_candidate_kinds；逐候选独立审计]`

## 近似复用卡在哪里

能量刀未产生通过区域筛选的初始复用提案。龙在每个相关运行中检测到13个初始区域，随后还执行缩区；正式轮共评价120个复用候选。它们的互斥阶段结果是80个质量门禁拒绝、22个缩区后不足、12个初始化失败、6个排布阶段未返回合法布局。`[verified: dragon与energy_blade相关result.json region_count；候选独立审计]`

复用候选中，密度底线涉及72个，1px耦合门限涉及58个，各向异性门限涉及49个。**同一候选可同时触发多个代码，不能把这些数量相加当作候选数。** 86次成对求解返回SOLVED，最终通过完整几何门禁的复用候选仍为0；数值求解完成不代表接受条件满足，也不能据此证明数学不可行。`[verified: audit.json reuse_rejection_codes；paired solve日志独立审计]`

本轮真实模型的复用候选没有进入材质门禁，`geometry_valid_reuse=0`、`material_checks=0`。因此不能把失败归因为左右颜色不一致，也不能宣称已验证真实龙翼的共享烘焙质量；材质/TBN的正向验证来自明确标注的构造例。`[verified: audit.json candidates；worker_smoke03_export/result.json]`

下一轮应优先调查成对参数化怎样同时满足密度、对应和各向异性约束，并验证区域缩减后的可行性；进一步增加固定岛packing搜索不是本轮证据支持的首要方向。当前结果没有否定近似对称复用的可能性。`[opinion]`

## 下游与评审边界

龙的每项最终烘焙都有相同的23个面被标记为源覆盖诊断异常，能量刀对应计数为0。进一步核对dragon/seed17/joint，这23面均为Base Color采样alpha不足，法线alpha和TBN通过，占模型表面积约1.47%；这个观察不能单独区分投射未命中、源透明度或光栅覆盖。它们独立于UV几何合法性，不能因为GLB导出成功就忽略。`[verified: audit.json downstream；bake_coverage_diagnostic.json faces/surface_area_fraction]`

六个相机是既有回归视角；开发冒烟也使用了它们，没有落实原计划的独立开发相机。两模型是开发/回归集，本轮不提供独立模型或未观察视角的泛化证据。256²/128²图是固定下采样诊断，不代表覆盖所有引擎压缩与LOD设置。`[verified: controller_smoke与正式downstream views.json；implementation-notes.md视觉证据边界]`

另对同一批24个冻结输出完成附加相机检查：方位15°/105°/195°/285°、仰角35°，包含Base Color、法线和128² mip；24项均成功，原始状态哈希未改变。该相机组预先写入补充协议，不参与候选选择，也不修改原代理盲评包；它是补充验收，不增加独立模型/搜索样本数。`[verified: additional_views/protocol.json、matrix.json；check_additional_views.py 状态哈希检查]`

代理盲评已完成六个匿名包，每包三份有效评分；编排记录包含20次新上下文尝试和实际agent ID，两次缺失各重试一次。24个样例均为 `AGENT_EVALUATED / NEEDS_WORK`，UV可编辑性中位分均为3，36组成对比较的多数结论全部为“平”。没有出现同一维度分差≥2的分歧标记，原始分数和理由仍保留。`[verified: review_control/coordinator_final.json；review_summary.json samples/preferences]`

每个模型/种子包内，四轨道的维度中位分相同。按种子17/29/43排列：能量刀外观与接缝均为5，法线/mip为5/5/4，编辑性均为3；龙外观为4/3/4，接缝与法线/mip均为4，编辑性均为3。部分评审提到长条、深凹边界和小碎片仍需整理；它们没有执行真实模型编辑。`[verified: audit.json proxy；energy_blade_seed_17/1970aea8524d7535.json 与 dragon_seed_17/1ada350bb04c94f1.json 的UV理由]`

这些代理评分没有支持联合方法的稳定视觉偏好；它们也不能替代真实artist或把同模型的多个上下文当作独立真人专家。客观密度提升与代理偏好应分别解释。`[opinion]`

评审编排因实际响应延迟调整为2+1；原180秒执行窗口产生两份缺失、一份及时完成，后续窗口改为420秒，原有效评分保留。调整只依据执行状态，不改变rubric、图像、匿名映射、算法或质量门禁；旧失败耗时仍计入review阶段3600秒与总28800秒。`[verified: review_orchestration.json execution_revisions；budget.json review记录]`

## 产物与复现

- [配置](../configs/v2_joint_round01.json)、[实际源码/后端/输入哈希](../artifacts/v2_joint/v2_joint_round01/source.json)、[锁定协议](../artifacts/v2_joint/v2_joint_round01/protocol.json)、[独立预算](../artifacts/v2_joint/budget.json)。`[verified: 对应文件]`
- [能量刀联合seed17模型](../artifacts/v2_joint/v2_joint_round01/energy_blade/seed_17/joint/downstream/asset.glb)、[龙联合seed17模型](../artifacts/v2_joint/v2_joint_round01/dragon/seed_17/joint/downstream/asset.glb)。固定展示seed17，其他种子和纹理均在同轮目录；完整矩阵保留全部结果。`[verified: matrix.json path；各downstream产物]`
- `uv run reseam joint-benchmark --config configs/v2_joint_round01.json` 使用新round_id重新执行；已有目录拒绝覆盖。后处理在Windows使用 `uv run python -X utf8 -m reseam.cli joint-report --round artifacts/v2_joint/v2_joint_round01`。`[verified: joint_benchmark.run_joint_benchmark；running-v2.md]`

人工复核状态仍为 `NOT_REVIEWED`。当前实现是研究原型；本轮能量刀的有限收益和龙的负结果都不足以宣称达到游戏资产artist交付标准。`[verified: matrix.json、audit.json human_acceptance]`

## 执行与交付检查

自动实验账本累计10308.973秒，约2小时52分；相对28800秒上限剩18491.027秒。分项为冒烟596.594秒、几何6800.798秒、下游1447.955秒、代理1456.813秒、报告6.813秒，编程/文档编写不计入。失败预约和重试没有从账本扣除。`[verified: docs/results/v2_joint_budget_accounting.json runs/total_charged_seconds]`

检查结果：68项pytest通过，ruff与basedpyright无错误，冻结源码/输入/后端复核通过；页面链接的584张图像可读取、24个GLB存在，JavaScript语法检查通过。自动审批拒绝启动HTTP预览服务，浏览器URL策略拒绝本地HTML预览，因此未完成浏览器视觉QA；HTML和Markdown文件均已保存。`[verified: 本轮检查命令输出；delivery_sources.json；exec_command与Browser use拒绝输出]`
