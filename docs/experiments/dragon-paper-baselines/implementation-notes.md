# Deviations

- 用户追加MeshQuery与Baseline UV复用率；增加项目实现路径及三种复用口径，官方源码优先，统一归档baseline/。`[verified: 本轮用户追加]`
- FAM在独立addon目录使用Cython3重编译官方kNN；旧Cython/NumPy2不兼容，且上游pyx误写knn.cxx，构建副本修正为已有knn_.cxx。上游源码不改，模型/损失/训练循环不改。`[verified: 本轮编译错误及成功FAM冒烟]`
- 评估排布使用生产8px留边，区别于论文.001；固定相同完成几何和完成BaseColor目标，不能视作复现论文数据集结论。`[opinion]`

- 当前任务使用最新已完成的镜像龙，不复用历史2000面dragon的运行指标。论文四边形范围与本例三角形输入、Blender版本差异在报告中单列。`[opinion]`

# Discovered edge cases

- xatlas默认原生输出112个零面积UV三角形，后续排布拒绝；保留原生输出，不隔离单面假装成功。`[verified: round01实际统计与xatlas_512/pack/result.json]`
- MeshQuery自然边界初始计划有4个环带（euler0、双边界）；Agent改为各连接两侧边界的geodesic切口，修正后80块均为disk。`[verified: round01/meshquery/natural_plan与repaired_plan/plan.json]`
- 工具execution_success可能为true但status=REJECTED且无snapshot；执行器必须检查子快照字段，不把执行完成当作接受。缓存请求不同必须使用新label，避免复用过期结果。`[verified: 开发轮PACKED_GEOMETRY_REJECTED与修正后run_dragon.py]`

- PartUV冒烟因适配脚本命名partuv.py遮蔽上游包而失败；更名partuv_worker.py，失败日志保留，修正后另建冒烟输出。round01为开发轮，正式轮冻结代码后另建。`[verified: round01/smoke/partuv/native/process.log]`
- Blender v4.5.11源码克隆遇到上游LFS二进制笔刷404；跳过非代码LFS资源完成源码检出，记录未下载的资源，不将其描述为可独立构建的完整运行资源。`[verified: 本轮Blender克隆stderr]`

- 仓库AGENTS引用的 memory/pages/index.md 本机不存在；已读取当前AGENTS与Codex记忆索引，不据缺失索引推断历史事实。`[verified: 本轮Get-Content错误]`
- 已有Smart UV worker使用margin=0，论文指定.03；本轮必须显式传参，不能称旧默认与论文相同。`[verified: scripts/blender_worker.py smart_project调用；论文附录A.4]`
- PartUV历史adapter有预处理步骤；必须检查输出几何与原面对应，避免将重建拓扑混入固定几何对照。`[verified: scripts/partuv_worker.py preprocess调用]`

# Questions for review

- 暂无阻塞用户决定；所有适配及实验写新目录，保留父资产。`[opinion]`

## Completion summary

- Deviations: 4 recorded.
- Most likely to revisit: replace MeshQuery project implementation when official code becomes available; FAM point-to-triangle UV conversion remains explicitly limited.
- Edge cases: 8 recorded, including native UV degeneracy and rejected-but-executed tools.
- Outcome: all six methods attempted, six real model/texture exports including two ReSeam controls, two rejected native UV paths retained.
- Read first next time: docs/results/dragon-paper-baselines.md and baseline/sources.json; no quality rejection was upgraded.
