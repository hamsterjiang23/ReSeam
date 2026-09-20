# V2 固定 UV 岛 Packing 对照结果

**结论：重新排布能改善部分能量刀 ReSeam 布局，但本轮没有解决整体利用率偏低的问题。** 这是冻结岛形后的排布对照；完整 V2 和 artist 验收仍待完成。`[verified: results/v2_fixed_uv_round01_summary.json“groups”“artist_acceptance”]`

本轮既未实现 packing 引导切缝，也不能从有限 packing 搜索的不足证明重新切缝必有收益。`[opinion]`

## 实测结果

以下为三个种子的中位数；两后端均保留最好合法输入，因此无收益时回退到原布局或仅共同放大的参考。所有百分比按 UV 三角形并集计算。`[verified: results/v2_fixed_uv_round01_summary.json“groups”]`

- **能量刀 / OptCuts 岛形**：48.77% → 49.28%，Blender 与 xatlas 相同；全部增长由仅共同放大解释，未找到额外排布收益。`[verified: groups 中 energy_blade/optcuts 的 initial、scale_only_median、occupancy_median]`
- **能量刀 / ReSeam adaptive 岛形**：47.36% → Blender 48.52%，xatlas 47.69%；Blender 三种种子范围 48.14%–51.46%，仅共同放大为 47.69%。Blender 中位线性纹素密度提升约 1.22%，最好种子不能代表平均表现。`[verified: groups 中 energy_blade/reseam_adaptive]`
- **能量刀 / ReSeam large 岛形**：44.94% → Blender 46.89%，xatlas 45.16%；仅共同放大为 45.16%。Blender 中位线性纹素密度提升约 2.15%。`[verified: groups 中 energy_blade/reseam_large]`
- **幻想龙 / 三种来源相同**：28.09% → 约 28.20%，主要由仅共同放大解释。xatlas 的额外收益仅约 0.00074 个百分点；Blender 的额外变化更小，不能据此称空间利用率问题已解决。三份输入 SHA-256 完全相同，不是三种独立切法证据。`[verified: groups 中 dragon；input_sha256；duplicate_inputs]`

因此，能量刀 adaptive 的最好一次排布超过 OptCuts 本轮固定岛参考，但其中位数仍较低；本轮不能得出 ReSeam 稳定优于 OptCuts 的结论。未来值得比较更强的固定岛搜索，再测试由 packing 瓶颈引导的区域接缝重构。`[opinion]`

## 完成度与约束

36/36 个运行完成，144/144 个提案有记录；21 个运行至少接受过一个提案，15 个运行回退。逐提案为接受 26、拒绝 10、未找到合格尺度 108；没有子进程失败或超时，所有运行未超过 90 秒上限。`SUCCESS` 只表示接受过几何提案，包含微小收益，不表示游戏交付达标。`[verified: results/v2_fixed_uv_round01_summary.json“status_counts”“trials”“overruns”]`

重新读取 36 份最终状态复核：固定几何、接缝、面角身份与岛形；无翻转/自重叠/岛间重叠；最小岛间距离 16.7110px，最小边界 8px（浮点约差 6×10⁻¹⁴px）；最大刚性重构误差 8.88×10⁻¹⁶，相对密度保持误差 2.46×10⁻¹¹。源码、六套输入及两后端文件哈希均未改变。`[verified: results/v2_fixed_uv_round01_summary.json 中约束极值和 unchanged 字段]`

固定配置为 1024²、gap 16px、border 8px、扩边配置 8px，允许旋转/平移/共同缩放，禁止逐岛缩放和重叠。本次没有重新烘焙、纹理优化、mip/法线视觉或人工验收；导出的 GLB 为无贴图模型，不套用旧贴图。`[verified: ../configs/v2_fixed_uv_round01.json“profile”；summary 的 bake_and_render_acceptance 与 artist_acceptance]`

## 复现与边界

- [锁定配置](../configs/v2_fixed_uv_round01.json)；[运行计划与命令](v2-plan.md)。每次使用新 round_id，既有目录拒绝覆盖。
- [可交互对照页](http://127.0.0.1:8773/index.html)；[本地 HTML](../artifacts/v2/v2_fixed_uv_round01/index.html)，包含全部种子和逐次日志入口。
- [跟踪的结果汇总](results/v2_fixed_uv_round01_summary.json)；完整原始产物位于 `artifacts/v2/v2_fixed_uv_round01/`，收尾复核位于 `artifacts/delivery/v2_fixed_uv_round01/`。
- [文献索引](../references/literature.md)；AT、GFPack++、SQP、区域搜索和受控复用的用途与接入状态单独记录。`[verified: 上述配置、汇总和索引文件]`

本轮正式运行 542.078 秒，收尾状态复核 17.640 秒；包含失败冒烟和交付检查预留后，延续原 8 小时预算的保守总记账为 21319.122 秒，剩余 7480.878 秒。未重置实验预算。详见 [记账](results/v2_budget_accounting.json)。`[verified: results/v2_budget_accounting.json]`

这是两模型、每种子最多四个姿态提案的有限搜索。xatlas 关闭会改变方向的轴对齐/旋转选项，使用随机预旋转和重构验收。`[verified: ../configs/v2_fixed_uv_round01.json“trials”；../scripts/fixed_pack_worker.py“rotate_charts”]`

未找到改进不等于固定岛 packing 的数学上界，未找到合格尺度不等于该姿态不可行；本轮结果不代表 xatlas 所有模式的最好性能。`[opinion]`
