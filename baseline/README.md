# MeshQuery 全基线源码与运行入口

本目录统一保存官方上游源码、项目适配器和实验入口；原有 references/ 保留。固定版本与许可见 sources.json。`[verified: 本轮用户源码归档要求；opinion: 组织方式]`

各方法的通俗原理、Research Track/Asset Track与实际龙模型结果见[Baseline介绍](../docs/dragon-baselines-overview.md)。`[verified: docs/dragon-baselines-overview.md各方法章节]`

直接看图：[方法与论文／工具来源](../docs/assets/dragon-paper-baselines/overview-figures/01-methods-and-papers.png)、[调用方式与实现分类](../docs/assets/dragon-paper-baselines/overview-figures/02-implementation-and-provenance.png)、[整体方法导图](../docs/assets/dragon-paper-baselines/overview-figures/03-baseline-workflow-mindmap.png)。`[verified: overview-figures/sources.json outputs]`

|目录|来源与作用|
|---|---|
|FlattenAnything/|官方 keeganhk/FlattenAnything；直接调用官方模型与train_fam训练循环。`[verified: 上游README与adapters/flatten_anything.py]`|
|OptCuts/|官方 liminchen/OptCuts固定源码；实际调用已配置的编译二进制并记录哈希。`[verified: runner.py optcuts分支]`|
|xatlas/、xatlas-python/|官方算法及Python绑定；运行包版本与源码提交分别记录。`[verified: 本轮官方源码克隆；runner.py xatlas分支]`|
|Blender/|官方v4.5.11源码；Smart UV及展开/排布使用本机Blender可执行文件。LFS笔刷资源保留指针，非构建资源完整包。`[verified: 本轮git版本及LFS获取记录]`|
|PartUV/|官方EricWang12/PartUV固定源码；调用已安装0.1.2研究环境及PartField权重。`[verified: 本轮PartUV backend.json；adapters/partuv_worker.py]`|
|MeshQuery/|本轮未找到官方代码；目录明确区分论文机制的项目实现与作者代码，并保存项目源码快照。`[verified: MeshQuery/README.md；opinion: 实现定位]`|
|adapters/、runner.py、run_dragon.py|ReSeam新增IO/调用适配及实验脚本，不修改上游算法源码。`[verified: 对应文件]`|
|paper_metrics.py、evaluate_paper_metrics.py|按MeshQuery A.6公开公式补算已有快照的形状、拓扑、AO与原始几何指标；项目采样约定单列。`[verified: 对应文件；with-original-input/paper-metrics-protocol.json]`|
|evaluate_legacy_metrics.py|对Chloe红刺、历史能量刀与幻想龙的39个保留条目汇总45项指标；无原UV、失败排布与缺失颜色验收保留逐项原因，不重新求解或烘焙。`[verified: evaluate_legacy_metrics.py measure/run；metrics-round02/summary.json row_count/null_reasons]`|

## 运行

官方源码以固定提交的Git子模块提交；获取核心源码时使用下面的明确路径。Blender二进制笔刷LFS资源与各平台预编译构建库不属于本次运行必需的源码，按需另行获取。`[verified: .gitmodules；sources.json Blender记录；opinion: 获取顺序]`

```powershell
$env:GIT_LFS_SKIP_SMUDGE = '1'
git submodule update --init baseline/FlattenAnything baseline/OptCuts baseline/xatlas baseline/xatlas-python baseline/PartUV baseline/Blender
git -C baseline/xatlas-python submodule update --init --recursive
```

```powershell
uv run reseam tools describe uv.baseline
uv run python baseline/run_dragon.py init --output artifacts/dragon_paper_baselines/round02
uv run python baseline/run_dragon.py smoke --method optcuts --output artifacts/dragon_paper_baselines/round02
uv run python baseline/run_dragon.py algorithm --method optcuts --output artifacts/dragon_paper_baselines/round02
uv run python baseline/run_dragon.py meshquery --output artifacts/dragon_paper_baselines/round02
uv run python baseline/run_dragon.py finish --method optcuts --output artifacts/dragon_paper_baselines/round02
```

每次使用新目录。原生算法参数按MeshQuery附录A.4；后续2048贴图、8px生产留边另列，与论文.001排布间距有区别。`[verified: run_dragon.py protocol与runner.py设置；论文附录A.4]`

复用率必须拆开报告：表面参与有效共享比例、去除有效重复占位的比例、复用倍数。未经对应与外观确认的交叠不计有效共享；零面积、翻转、自交、超时与依赖失败均保留，不自动拆小岛修复。`[verified: 用户要求对比UV复用率；opinion: 统计口径]`

上游目录作为主仓库Git子模块交付，固定提交与远端URL见 `.gitmodules` 和 `sources.json`；项目适配器、实验脚本和MeshQuery项目源码快照按普通文件提交。更新上游版本时同时更新来源和许可记录。`[verified: .gitmodules；sources.json；opinion: 维护规则]`

## 已有快照的论文指标补算

```powershell
uv run pytest -q tests/test_paper_metrics.py
uv run python -u -m baseline.evaluate_paper_metrics --output artifacts/dragon_paper_baselines/metrics_new_round
uv run python scripts/build_baseline_metrics_table.py
```

新增评估源码保存在本目录；示例评估命令要求新输出目录，前两步不改快照，第三步从已归档的补算结果更新报告。生产归档位于`docs/assets/dragon-paper-baselines/with-original-input/`的`paper-metrics*.json`与`paper-metrics-details/`。九条路径使用同一seed17、每组1000点×8余弦半球射线；无显式切缝的AO保留不适用，并另列包含原边界的AO。`[verified: evaluate_paper_metrics.py run、main；paper-metrics-protocol.json]`

紧凑度、凸性、非圆盘比例和边界二阶差分按公开公式实现；细节、源哈希、实际成本与每岛记录随结果交付。论文表1未说明的Jaggedness显示倍率不从数值倒推，保留原始UV单位；这些是单龙诊断，原失败/质量状态不升级，缺失历史LLM成本不补零。`[verified: paper_metrics.py；paper-metrics.json；opinion: 比较边界]`

三模型的[45项补测报告](../docs/results/legacy-assets-45-metrics.md)、[完整CSV与协议](../docs/assets/legacy-assets-reseam/metrics-round02/README.md)使用相同定义。旧字段new_seam_length_3d实际是当前state.seams总长，补测表改称“记录3D切缝长度”，不能解释为相对作者UV的净新增。`[verified: metrics-round02/protocol.json new_seam_scope]`

```powershell
uv run python -X utf8 -u -m baseline.evaluate_legacy_metrics --output artifacts/legacy_metrics_new_round --samples 1000
```

该入口要求新目录；正式执行源码及哈希保存在metrics-round02/code_snapshot与protocol.json。`[verified: evaluate_legacy_metrics.py run；metrics-round02/verification.json executed_modules_frozen]`
