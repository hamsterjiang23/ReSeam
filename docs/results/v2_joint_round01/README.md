# V2 round01 紧凑证据归档

本目录随Git同步，保留完整24项矩阵、冻结协议/源码哈希、原始与导入评分、匿名映射、评审尝试/失败记录、补充相机矩阵及固定seed17预览。194个复制文件的字节数与SHA-256见 [archive_manifest.json](archive_manifest.json)。它是筛选后的证据归档，不是完整运行目录。`[verified: archive_manifest.json files]`

完整原始运行约1.98GB、12984个文件，仍在本机 `artifacts/v2_joint/v2_joint_round01/`，按既有 `.gitignore` 排除；包括逐候选状态/日志、全部渲染图、模型、临时求解文件和编译/运行缓存。冻结源码快照已单独保留在本目录source_snapshot，按原字节归档。归档中的原始路径与图片哈希保留不变，因此评分manifest引用的完整图片不在本目录；不能仅凭这个紧凑归档重新执行图像盲评。`[verified: 本次目录统计；.gitignore /artifacts/]`

阅读入口：

- [量化及代理评分摘要](../v2_joint_round01_summary.json)、[中文结论](../../v2-joint-results.md)。
- [完整矩阵](matrix.json)、[冻结协议](protocol.json)、[实际版本/输入哈希](source.json)。
- [评分汇总](review_summary.json)、[评审编排记录](review_control/coordinator_final.json)。
- [固定岛能量刀UV](previews/energy_blade/packing_only/uv.png)、[联合能量刀UV](previews/energy_blade/joint/uv.png)、[龙UV](previews/dragon/joint/uv.png)。

预览统一使用seed17，贴图模型图为主评估相机view00；不挑选各方法最好种子，也不替代六视角及法线/mip证据。用于重建报告的手写辅助程序已纳入 `scripts/joint_*.py`；历史artifact中的原脚本保留，以维持冻结哈希。新求解器方向见 [重新设计目标](../../../goal/solver-redesign.md)。`[verified: archive_manifest.json source；scripts/joint_audit_round.py 等]`
