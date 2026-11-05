# 已完成龙模型：MeshQuery 全部自动基线对照

用户于2026-11-05授权：运行 MeshQuery (arXiv:2610.02507v1) 的全部五项自动基线，使用已经完成的龙，并创建持续执行goal。`[verified: 本轮用户请求]`

## 固定输入与对照

- 从最新中线修复80岛／40图块版本建立本轮独立父资产，冻结11338面几何、面角身份与当前已完成BaseColor外观。历史原始Tripo与所有旧实验保留。`[opinion]`
- 六项：FlattenAnything、OptCuts、xatlas、Blender Smart UV、PartUV，以及MeshQuery；另列当前ReSeam共享布局及相同展开解除共享。优先官方源码；本轮作者主页/定向检索未找到MeshQuery官方代码，使用现有项目实现并明确标注，不能冒充作者运行。`[verified: 用户补充MeshQuery；作者主页2026-11-05检索；opinion: 实施方式]`
- 所有基线源码、固定版本、适配器和获取清单统一保存在 baseline/；原有 references/ 不改动。`[verified: 用户最新源码归档要求；opinion: 目录选择]`
- 论文附录A.4：OptCuts lambda=.999、distortion=4.1、seed=0；PartUV seed=42、threshold=1.25；Smart UV angle=66度、margin=.03；xatlas默认chart options；FlattenAnything 10000点、10000次迭代。每项算法预算2400秒，失败/超时保留。`[verified: https://arxiv.org/html/2610.02507v1 附录A.4]`
- 本机Blender4.5与论文5.1.2的差异、三角模型与论文四边形模型的差异如实列出。当前ReSeam布局为既有制作结果，其历史成本另列，不伪装等预算从零优化。`[opinion]`

## 执行及产物

1. 核对后端、资源与来源，构造模型冒烟，再运行完整龙。所有方法使用完全相同的输入几何；预处理改变几何或无法追溯原面时记失败，不自动焊接或重拓扑。`[opinion]`
2. 保存原生输出；统一排布、2048 BaseColor、完整mip、相同采样与镜头。论文margin=.001与生产留边差异显式记录，不混排覆盖率。`[opinion]`
3. 必须逐Baseline比较UV复用率：有效共享表面积比例、有效重复占位节省比例、复用倍数、共享组数/独立图块数。未经对应与外观确认的交叠单列，不计为有效复用。另报覆盖率、接缝、畸变、颜色/渲染误差、真实字节与耗时；所有失败行保留。`[verified: 用户最新要求对比Baseline UV复用率；opinion: 指标口径]`
4. 交付真实GLB、贴图、编号UV、棋盘格、同镜头渲染、可视化总览和可复现记录；工具成功、Agent外观接受、严格质量、人工/PBR/GPU分别标记。`[opinion]`

状态：COMPLETE。六项方法均已实际运行；OptCuts、Smart UV、PartUV与MeshQuery项目实现生成2048贴图/模型，FlattenAnything与xatlas的原生输出及后续拒绝保留。另交付ReSeam共享/解除共享对照，所有路径已比较UV复用率。`[verified: docs/assets/dragon-paper-baselines/measurements.json；verification.json]`

最终报告：[论文全基线对照](../docs/results/dragon-paper-baselines.md)；源码与复现入口：[baseline](../baseline/README.md)。778项pytest、ruff和basedpyright通过；6份GLB回读、103张PNG解码、104个冻结源码哈希及可携带输入重放通过。实验完成不表示被拒绝的方法通过质量，也不升级为人工/PBR/GPU验收。`[verified: 本轮检查输出；docs/assets/dragon-paper-baselines/verification.json]`
