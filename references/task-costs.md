# 运行成本与资源边界

- Agent工具集：两固定模型98次CLI调用合计278.971秒，12行256/128完整材质验证；串行CPU1、独立Blender1线程；同机存在测试进程，耗时只作诊断、不做速度排名。新龙10832面/8192²单色图的颜色烘焙+16采样/面验收，256/512/1024分别37.562/44/67秒，120秒/调用上限；启动可用RAM约23GiB，源图保持uint8，不转整图float64。只读model与旧资产，写artifacts/agent_tools_v1独立目录。`[verified: agent_tools_v1/verification.json；tripo_p2_smoke/color_baseline/result.json；tripo_p2_resolution_diagnostic/matrix.json；本轮Win32_OperatingSystem输出]`

- 单元测试：小型合成网格，预计少于 1 分钟、低于 2 GB；仅写 pytest 缓存。
- V2 固定岛对照：Blender 单提案实测 19.453 秒，xatlas 秒级；串行 CPU、Blender 4 线程，无 GPU，xatlas 内部线程数未另校验。只读 V1 round03，写独立 artifacts/v2/round；每运行 90 秒、全轮 3600 秒封顶，延续原 8 小时余额。
- C++ 适配器编译：先复用已锁定 OptCuts 构建对象，单编译任务；写 artifacts/runtime。
- UV 单模型：未知成本先冒烟，单进程、最多 4 CPU 线程、180 秒上限。
- PartUV／可微贴图：串行占用 GPU，先测单模型；不得和另一个 GPU 任务重叠。
- 实验矩阵：总 8 小时上限，状态和产物写独立 artifacts/v1/round 目录，输入及参考源码只读。
- 初始资源快照：Windows 可用 RAM 41.7/63.8 GB；WSL 可用约 30 GB；RTX 4070 显存已用 892/12282 MiB，利用率 6%。后续启动前重新检查。
- V2 联合：controller_smoke01 能量刀搜索132.7秒、下游43.1秒；龙搜索73.5秒（仅8候选）。正式24行各300秒上限，下游各270秒；串行CPU、Blender4线程，独立artifacts/v2_joint输出，资产/后端只读；启动时可用RAM约19GB、E盘空闲约1.2TB。

- 新求解器v1开发：CPU串行ROI，独立artifacts/budget_v1目录与每轮budget_account.json；首轮计划上限300秒，不扣历史V1/V2账户。ROI候选预探测实测4.33秒，单PBR高模加载0.47秒；8192² RGBA8约256MiB/颜色图，不展开全图float64，不使用GPU。启动可用RAM17.4/63.8GiB，E盘空闲1059GiB；正式首跑前再次检查。
- 新求解器v1实测：development_round01/02分别4.265/7.437秒；resolution_diagnostic_round03为22.469秒；matched_raster_diagnostic_round04为2.000秒墙钟、1.921875秒CPU，未超各自账户上限。以上均为局部开发诊断，未训练神经网络。`[verified: artifacts/budget_v1/对应目录 result.json 与 budget_account.json]`

- 新求解器完整模型探针：共同xatlas初始化约1秒/模型；多通道dragon36面冒烟2.360秒；全模型64²四项矩阵46.719秒（主要公共参考准备，排布阶段全部拒绝）。当前串行CPU，RAM可用约25GiB、E盘约1059GiB，不使用GPU；后续每target独立限时60秒，模型公共准备120秒，新轮独立budget_account，不扣旧账户。`[verified: full_mesh_probe02、material_multichannel_smoke02、budget_v1_complete_smoke_round01 结果；启动资源快照]`
