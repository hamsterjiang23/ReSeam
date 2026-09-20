# 对称 UV 复用：首轮诊断结果

已实现镜像对应、区域候选、颜色/拉伸/密度筛选、白名单重叠与原图/共享对拍。两套真实低模各找到 4 个几何配对区域，**0 个区域通过全部暂定小差异门限**；当前输出是拒绝候选的诊断预览，不是已批准的复用结果。`[verified: results/v2_reuse_preview_round01_summary.json“candidate_counts”“passed_preview_gate_counts”]`

## 查看产物

- [局部对拍与区域标色](http://127.0.0.1:8774/review_v2/index.html)；[本地页面](../artifacts/v2/v2_reuse_preview_round01/review_v2/index.html)。
- [完整六视角与 GLB 下载](http://127.0.0.1:8774/index.html)。只有 Base Color，未附加镜像法线贴图。
- [规则与实现说明](v2-reuse-plan.md)；[机器可读汇总](results/v2_reuse_preview_round01_summary.json)。`[verified: v2_reuse_preview_round01 的 index.html、review_v2/index.html 与汇总]`

橙色区域读取蓝色区域的像素。每模型选择违例种类最少、其次共享面积最大的一个候选，视角不参与选择。原图与共享后固定几何、纹理和相机，只修改 UV 对应；同画面另显示差异 ×8 和配对标色。`[verified: ../src/reseam/reuse_preview.py“selection_rule”“comparison”]`

## 实际发现

**能量刀**展示 3 对面，共享方占三维表面积 0.448%。几何白名单检查通过，局部拉伸指标最大增幅约 3.65%，但颜色超过暂定门限：最大单面 linear RGB 平均误差 0.099，95 分位误差 0.243。可见共享区的 sRGB 渲染 MAE 约 0.026–0.039；两种颜色口径不同，不应直接相互比较。`[verified: results/v2_reuse_preview_round01_summary.json“models/energy_blade”]`

**幻想龙**展示 3 对面，共享方占三维表面积 0.160%。除颜色差异外，最大局部拉伸指标增幅约 32.5%，超过 10% 暂定门限。可见共享区的 sRGB 渲染 MAE 约 0.066–0.078。不能只因整图误差小就接受，因为变化只覆盖很少的像素。`[verified: results/v2_reuse_preview_round01_summary.json“models/dragon”]`

没有重新排布时，能量刀和龙分别释放约 0.229、0.0469 个百分点的唯一 UV 面积；贴图仍为 1024²，**没有获得新的分辨率或存储收益**。当前选择的是拒绝候选，不能把这些释放面积计为有效算法收益。`[verified: 汇总中的 geometry/freed_unique_area；scope]`

## 对 V2 的影响

首版只覆盖能恢复完整三角形的镜像配对。AI 重拓扑的左右网格不一定相同，而独立展开后的左右 UV 也不一定具有同样拉伸；直接复制另一侧 UV 的适用范围有限。本轮结果不能否定更一般的对称复用。`[opinion]`

下一步应先确认可容忍的颜色差异，再研究**对称区域成对参数化与局部曲面对应**：让两侧共享统一纹理域，同时分别满足形变约束；确认复用组后再做唯一区域 packing、重烘焙和镜像法线/mip 验收。论文的 OT 配准与 Texture Carving 均未实现。`[opinion]`

这轮按用户“先出对拍再确认”的要求停在评审产物；未自动批准区域、放宽门限或更改旧资产。原始候选误差、拒绝原因、版本、哈希及所有渲染保存在独立 round。源码与 Blender 哈希在执行期间未变化。`[verified: results/v2_reuse_preview_round01_summary.json“code_unchanged”“blender_unchanged”“inputs_unchanged”]`

验证：45 项测试通过，ruff 与 basedpyright 通过；两份最终 UV 重新进行重叠白名单检查。时间记账见 [原预算延续记录](results/v2_reuse_budget_accounting.json)。`[verified: 本轮 pytest/ruff/basedpyright 输出；review_v2/audit.json]`
