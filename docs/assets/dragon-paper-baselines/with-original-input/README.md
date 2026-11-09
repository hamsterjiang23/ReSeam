# 原始 Tripo 输入与完成龙基线的九列对照

两张图均在最左侧加入原始Tripo输入，保留其余八条路径的测量数据与实际图像。原始输入10832面、121岛、121图块、8192×8192 BaseColor、覆盖72.55%；八条实验路径统一使用11338面完成镜像几何与2048贴图。原始列为跨几何参考，不参加固定几何的质量排名。`[verified: comparison-evidence.json original、texture_size、columns；上级measurements.json input]`

- `uv-and-checker-comparison.png`：同一正视棋盘格与彩色UV，原始输入列以金色边框标出。`[verified: 对应PNG；comparison-evidence.json checker_camera]`
- `basecolor-comparison.png`：同一35°/15°斜视模型与各自真实贴图；失败方法继续保留未产生合格重烘焙输出的占位。`[verified: 对应PNG；original/color_request.json；上级measurements.json rows]`
- `original/`：原始输入的实际视图、原始8K贴图及补充渲染请求/响应。`[verified: manifest.json files]`
- `comparison-evidence.json`、`manifest.json`：来源指标、镜头、未重跑求解/烘焙的范围、看图检查和文件哈希。`[verified: 对应JSON字段]`
- [全部指标总表](all-metrics.md)、[CSV](all-metrics.csv)与`all-metrics.json`：69项指标×9条路径，包含UV、论文接缝/形状指标、畸变、颜色/渲染误差、质量、字节与分项耗时。不可重建的历史耗时和不存在的烘焙仍保持空缺，原始输入仅作跨几何参考。`[verified: all-metrics.json methods、metrics]`
- `paper-metrics.json`、`paper-metrics-protocol.json`与`paper-metrics-details/`：九份已有快照的新增评估和逐岛数据；原始几何指标同时补算。AO显式切缝与含原边界分别记录，Jaggedness保留原始UV单位。`[verified: 对应JSON protocol、rows]`

在仓库根目录运行：

```powershell
uv run python scripts/build_baseline_input_comparisons.py
uv run python scripts/build_baseline_metrics_table.py
```

默认读取本轮`artifacts/dragon_paper_baselines/with_original_input01/`补充渲染，缺失时使用本目录已保留的原始素材。八条基线图像从上级目录读取；该脚本只重排图表，不执行展开、共享或烘焙。重新生成后需要重新看图，`visual_review`会恢复为`PENDING`。`[verified: scripts/build_baseline_input_comparisons.py build、figure]`

新增指标的可复算入口：

```powershell
uv run python -u -m baseline.evaluate_paper_metrics --output artifacts/dragon_paper_baselines/metrics_new_round
```

输出目录必须为新目录。每组AO使用1000个等弧长点、8条余弦半球射线、seed17；整个有限网格范围内查询，使用原始面法线且不改几何。公开附录未明确的采样和边界聚合细节均写入协议，这是按公式的项目实现，不宣称与作者未公开代码逐项相同。`[verified: paper-metrics-protocol.json；baseline/evaluate_paper_metrics.py run]`

无显式切缝的FlattenAnything保留显式AO“不适用”，另外提供含原边界的AO；无烘焙输出的方法不生成颜色/存储假值。Jaggedness使用0.005步长、8—4096点、边界点二阶差分均值；未为对齐论文表格数字乘未声明的倍率。`[verified: paper-metrics.json rows.flatten_anything；paper-metrics-protocol.json]`
