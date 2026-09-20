# V2 联合原型运行

本页用于复现已归档的V2方案；新求解器按 [独立设计方向](../goal/solver-redesign.md) 开展。可随Git查看的矩阵、原始评分和预览见 [紧凑证据归档](results/v2_joint_round01/README.md)。完整运行文件仍按原规则保存在本地artifacts中。`[verified: 用户本轮方向；.gitignore /artifacts/]`

独立入口已接入 CLI。V1 与前期固定岛/复用诊断保留原命令和产物；正式联合轮使用新 `v2_joint` 账本。`[verified: src/reseam/cli.py main；src/reseam/joint_budget.py JointBudget]`

```powershell
$env:PYTHONUTF8 = '1'
uv run reseam joint-unwrap --model dragon --track joint --seed 17 --seconds 300 --output artifacts/v2_joint/development_new
uv run reseam joint-benchmark --config configs/v2_joint_round01.json
uv run reseam review-export --round artifacts/v2_joint/v2_joint_round01
uv run reseam review-import --packet artifacts/v2_joint/v2_joint_round01/blind/dragon_seed_17/manifest.json --review path/to/reviewer.json --output artifacts/v2_joint/v2_joint_round01/reviews/dragon_seed_17
uv run reseam joint-report --round artifacts/v2_joint/v2_joint_round01
```

Windows 当前 Python 默认编码为 CP936；中文评审文件统一使用 UTF-8。上述会话设置只影响后续子进程，也可使用 `uv run python -X utf8 -m reseam.cli joint-report --round ...`；本轮采用显式 UTF-8 后处理，保持冻结源码不变。`[verified: locale.getencoding()=cp936；sys.flags.utf8_mode=0]`

`joint-unwrap` 是开发入口，扣 smoke 阶段；`joint-benchmark` 才创建24项正式矩阵。已有输出目录禁止复用；修改方法后新建 round_id。评审导入不生成分数，必须先由全新上下文评审实际查看匿名图像。`[verified: joint_search.run_joint；joint_benchmark.run_joint_benchmark；joint_review.import_review]`

运行记录含初始 OptCuts 哈希、完整候选 UV、删/加缝、对应、组、门禁和拒绝原因。正式行预算必须完整预约；对照前核对相同模型/种子的初始哈希，失败/缺失样本不从矩阵删除。`[verified: joint_search.run_joint；joint_benchmark.run_joint_benchmark]`

开发初始化优先使用法线锥内连续近似平面片；左右顶点/三角化无需相同。密度惩罚权重10属于可行性求解项，最终仍独立检查畸变、密度、间距、1px耦合和重叠曲面兼容性。失败面可以从复用区域移除并重新提案，不能放宽门限。`[verified: joint_regions.planar_regions；joint_search.shrink_pair；joint_validation.validate_joint_quality]`

能量刀 controller_smoke01 搜索/烘焙通过，龙 controller_smoke02_dragon 搜索/下游通过；它们是开发冒烟，不是正式 A/B。构造共享烘焙验证另存 worker_smoke 系列，不能用构造成功代替真实资产复用收益。`[verified: artifacts/v2_joint/controller_smoke01/summary.json；artifacts/v2_joint/controller_smoke02_dragon/summary.json]`

本轮正式结果为真实复用组0，详见 [联合结果](v2-joint-results.md)；没有取得真实模型复用收益。`[verified: results/v2_joint_round01_summary.json groups/candidates]`

手写后处理程序已从忽略目录复制到受版本控制的scripts，历史原件不移动。以下命令针对本机完整运行目录，不能直接把紧凑证据归档当作运行目录：

```powershell
uv run python -X utf8 scripts/joint_audit_round.py --round artifacts/v2_joint/v2_joint_round01
uv run python -X utf8 scripts/joint_build_delivery_page.py --round artifacts/v2_joint/v2_joint_round01
```

`joint_check_additional_views.py` 会启动新的补充渲染，`joint_review_budget_guard.py` 会预约评审预算；仅用于明确的新运行，不为重新查看历史报告而调用。`joint_review_batch.py` 与同目录 `joint_review_prompt_template.txt` 配套使用。`[verified: 对应scripts入口]`
