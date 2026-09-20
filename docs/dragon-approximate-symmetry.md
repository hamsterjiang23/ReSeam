# 龙左右翼的近似几何对应

**可以找到大量近似对应，不需要左右顶点或三角形完全相同。** 在外侧翼区候选的 478 个顶点中，采用位置/往返误差均不超过模型包围盒对角线 0.5%、法线夹角 ≤45° 的探索门限，427 个通过（89.3%）；其中 418 个目标落在另一侧三角形内部或边上，而非恰好落在目标顶点。`[verified: results/dragon_surface_match_round01_summary.json“sweep”，tolerance_fraction=0.005]`

[查看可旋转、可点选的三维对应图](http://127.0.0.1:8775/index.html)；[本地 HTML](../artifacts/v2/dragon_surface_match_round01/index.html)；[全部顶点—目标面及重心坐标](../artifacts/v2/dragon_surface_match_round01/matches.json)。`[verified: dragon_surface_match_round01 的 index.html 与 matches.json]`

## 这次如何匹配

使用固定的龙低模（996 顶点、2000 三角形），保持几何和 UV 不变。沿该已对齐资产的 X 法向拟合镜像平面偏移；对每侧离中线足够远的顶点，反射后在另一侧三角形上找最近点，输出目标面 ID、三个重心权重及坐标，再反射返回源侧曲面检查往返误差。另比较反射后的源顶点法线与目标插值法线。`[verified: ../src/reseam/surface_match.py“fit_offset”“match_surfaces”]`

三角形数、对角线切分和采样密度可以不同。构造测试包含一侧两个三角形、另一侧四个三角形且多一个中心顶点，曲面对应与往返误差均为数值零；最近顶点法却找不到中心的精确对应。`[verified: ../tests/test_surface_match.py“test_different_triangulations_match_surface_interior”]`

外侧翼区暂以 `abs(x−plane_offset) ≥ 横向总跨度×20%` 定义，是几何范围而非人工确认语义；全模型有 932 个顶点离开对称平面 2% 对角线的排除带，其余近中线顶点不参与。平面仅优化偏移，法向固定，不宣称任意姿态下的全局最佳对称检测。`[verified: 结果 summary 的 roi、queried_source_vertices、plane；surface_match.py 的 band_fraction]`

## 误差及具体例子

外侧翼区全部 478 个查询的曲面误差中位数为对角线的 **0.0665%**，95 分位为 **0.3900%**；最近顶点误差中位数为 **0.4037%**，往返曲面误差中位数为 **0.0261%**。这说明在该资产上，要求目标必须是一个现成顶点，会混入较大的网格离散误差。`[verified: results/dragon_surface_match_round01_summary.json 的 outer_* 字段；最后一句为实验解释]`

例如源顶点 **420** 对应目标面 **856**（顶点 421、418、462），重心权重约 **0.8417、0.1351、0.0233**。曲面误差 **0.1585% D**、最近顶点误差 **0.4027% D**、往返误差 **0.0497% D**、法线夹角 **4.58°**；D 是包围盒对角线，ID 从零开始。目标是三角形内部的点，不需要捏造一个同位置目标顶点。`[verified: matches.json 中 source_vertex=420；浏览器对应点详情]`

门限敏感性保留全部结果：0.25% D 时 405/478，0.5% D 时 427/478，1% D 时 429/478，2% D 时 430/478；法线门限始终为 45°。计数是两个方向的源顶点查询，不能当成相互独立的一一配对数量。`[verified: 结果 summary 的 sweep]`

## 对 V2 的意义与限制

这解决了严格三角形配对造成的入口限制，可以用这些对应约束组织左右区域、再研究成对参数化。最近曲面点可能多对一，往返检查也不证明全局双射；还需要检查邻域连续性、对应折叠与区域边界，然后评价共享 UV 的拉伸和材质误差。本轮没有修改 UV、批准复用或计算纹理节省。`[opinion]`

复现：`uv run python scripts/inspect_surface_symmetry.py --output artifacts/v2/<新目录>`。运行不足一秒，原 low.obj SHA-256 未变；结果记录输入、库版本、代码哈希、候选和门限。`[verified: 结果 summary 的 wall_seconds、input_unchanged、packages、code_sha256]`
