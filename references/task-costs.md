# 运行成本与资源边界

- 单元测试：小型合成网格，预计少于 1 分钟、低于 2 GB；仅写 pytest 缓存。
- V2 固定岛对照：Blender 单提案实测 19.453 秒，xatlas 秒级；串行 CPU、Blender 4 线程，无 GPU，xatlas 内部线程数未另校验。只读 V1 round03，写独立 artifacts/v2/round；每运行 90 秒、全轮 3600 秒封顶，延续原 8 小时余额。
- C++ 适配器编译：先复用已锁定 OptCuts 构建对象，单编译任务；写 artifacts/runtime。
- UV 单模型：未知成本先冒烟，单进程、最多 4 CPU 线程、180 秒上限。
- PartUV／可微贴图：串行占用 GPU，先测单模型；不得和另一个 GPU 任务重叠。
- 实验矩阵：总 8 小时上限，状态和产物写独立 artifacts/v1/round 目录，输入及参考源码只读。
- 初始资源快照：Windows 可用 RAM 41.7/63.8 GB；WSL 可用约 30 GB；RTX 4070 显存已用 892/12282 MiB，利用率 6%。后续启动前重新检查。
- V2 联合：controller_smoke01 能量刀搜索132.7秒、下游43.1秒；龙搜索73.5秒（仅8候选）。正式24行各300秒上限，下游各270秒；串行CPU、Blender4线程，独立artifacts/v2_joint输出，资产/后端只读；启动时可用RAM约19GB、E盘空闲约1.2TB。
