# V2 对称 UV 复用：当前实现与评审协议

> 历史阶段：本页是首版整面复用诊断协议。新联合流程允许 subagent 代理盲评替代等待人工评分，并推进近似曲面对应与成对参数化；当前目标和实施入口见 [V2 目标](../goal/v2.md) 与 [联合计划](v2-joint-plan.md)。旧拒绝候选仍然是拒绝，不因流程更新改写其结论。

用户已选择允许小幅纹理差异，先输出原图／复用对拍，再人工确认。当前把对称复用从后续路线推进为可运行的候选与诊断预览；不自动把技术候选作为交付资产。`[verified: 本轮用户对复用策略的回复]`

## 目标和本次边界

对称区域经确认后共用纹理，随后对唯一纹理区域重新排布，在固定分辨率下提高表面有效采样密度，或在固定质量下减少纹理存储。复用后唯一覆盖率可能先下降，这表示释放了空间；单纯重叠不会自动提高密度，也不能把重复覆盖相加冒充唯一覆盖率。`[opinion]`

首版依次执行：候选对称平面 → 顶点和完整三角形对应 → 连片区域 → 颜色/拉伸/密度检查 → 白名单重叠 → 原图/复用/差异/区域对拍。原始几何、面顺序和输入纹理不变；UV 副本按显式原始顶点与对应身份重建，不按坐标焊接。`[verified: ../src/reseam/symmetry.py“propose_reuse”“apply_reuse”；../src/reseam/reuse_preview.py“render_comparison”]`

采用模型坐标轴和 PCA 轴的六个候选法向，迭代拟合平面偏移；只使用互为最近点、能恢复原始完整三角形的配对。不是全空间最优对称搜索，也不支持不同三角化之间的连续配准。没有候选不能证明模型不存在可复用结构。`[verified: ../src/reseam/symmetry.py“mirror_mapping”；局限为 opinion]`

这次借鉴论文的对称共享思想，**未复现最优传输、chart 内连续折叠优化或 Texture Carving**。[原论文](https://doi.org/10.1145/3714408) 的方法包含对称重叠与纹理 carving 两部分；不把该论文报告的质量或压缩率当作我们的结果。`[verified: ACM 摘要及 Section 3 Method]`

## 暂定技术门限

门限是诊断起点，不是用户已经认可的 artist 交付标准；记录拒绝原因，不通过调整门限把坏结果变成成功。`[opinion]`

- 顶点镜像误差 ≤ √三维总面积的 0.5%；映射后的面法线夹角 ≤15°。
- 至少三个相邻 donor 面，保留明确的 donor→owner 面和逐角对应；同一组不允许循环共享或多重占用所有者。
- 每面 28 个对顶点排列对称的内部采样点，在 linear RGB 比较颜色：平均误差 ≤0.025、95 分位误差 ≤0.08。
- 每面奇异值比相对原 UV 增幅 ≤10%，线性纹素密度比变化 ≤10%。
- 烘焙投射探针缺失命中或采样位置 alpha 不完整时标记不确定；有限采样不能证明所有像素正确。
- 允许重叠的仅是记录中的整面对；其他正面积交叠拒绝。镜像面的负 Jacobian 只在该白名单下解释，不放宽原无重叠轨道验证器。`[verified: ../src/reseam/symmetry.py“ReuseProfile”“barycentric_samples”“validate_reuse”]`

若没有候选通过门限，仍输出违例种类最少、其次共享面积最大的一个**拒绝候选**供诊断，不删除失败记录，不自动批准。所有候选和逐面误差保存在 JSON；视角不参与候选选择或调参。`[verified: ../src/reseam/reuse_preview.py“selection_rule”]`

## 对拍和后续接入

新增 [龙翼近似顶点—曲面对应探测](dragon-approximate-symmetry.md)：允许对应点落在不同三角化的内部，以位置、法线和往返误差筛选。它替代严格顶点/完整面匹配作为后续区域配准入口；当前尚未与共享 UV 参数化连接，旧整面白名单仍保留为基线。`[verified: ../src/reseam/surface_match.py；近似对应结果报告]`

使用固定两套低模的 OptCuts seed17 UV 与已有 Base Color 烘焙，保持六个既有相机。导出只含 Base Color 的诊断 GLB；同时渲染原图、共享后、放大差异及 donor/owner 标色。尚未验证镜像切线法线、其他材质通道、mip 和采样边界，故不提供完整材质批准。`[verified: ../src/reseam/reuse_preview.py“normal_map_status”“comparison”]`

本次不重排或缩小贴图；人确认可接受的对应区域后，再将复用组作为唯一纹理区域排布，重烘焙并验证镜像 TBN/法线。若直接拷贝另一侧 UV 明显增加拉伸，应先做成对参数化；若三角化不匹配，应添加局部曲面配准，而非强迫最近面一一重叠。`[opinion]`

入口：`uv run reseam reuse-preview --config configs/v2_reuse_preview_round01.json`。新目录保存源代码快照、输入与 Blender 哈希及所有候选；已有输出拒绝覆盖。每模型 300 秒上限，继续消耗原 8 小时余额；不把这两个诊断预览称为正式收益 A/B。`[verified: ../src/reseam/reuse_preview.py“run_preview”；../configs/v2_reuse_preview_round01.json]`
