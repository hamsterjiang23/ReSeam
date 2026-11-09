# 三模型45项指标：最终补测轮次

对Chloe红刺、历史能量刀和历史幻想龙的原始参考及36个方法／尺寸条目补测，共39行、45项字段；本轮没有生成新UV、烘焙或数值质量分数。`[verified: summary.json row_count/new_bakes/new_quality_scores]`

[完整CSV](all-metrics.csv) · [完整JSON与逐项空值原因](summary.json) · [锁定定义和源码哈希](protocol.json) · [核验记录](verification.json) · [正文报告](../../../results/legacy-assets-45-metrics.md)。`[verified: 本目录对应文件]`

- [Chloe主尺寸对照](chloe-comparison.md)／[CSV](chloe-comparison.csv)：256px，原始、OptCuts无共享、镜像共享、同镜像几何解除共享。`[verified: chloe-comparison.csv 表头与resolution行]`
- [能量刀主尺寸对照](energy_blade-comparison.md)／[CSV](energy_blade-comparison.csv)：2048px，原始低模、xatlas、OptCuts无共享、镜像共享、同镜像几何解除共享。`[verified: energy_blade-comparison.csv 表头与resolution行]`
- [幻想龙主尺寸对照](dragon-comparison.md)／[CSV](dragon-comparison.csv)：2048px，同上；xatlas保留原生UV指标和排布失败。`[verified: dragon-comparison.csv；summary.json dragon/xatlas/2048 layout_stage]`

全部CSV另含Chloe的128／64px及能量刀、幻想龙的1024／512px。案例原始测量保存在cases/，实际执行的五个Python模块保存在code_snapshot/。`[verified: summary.json rows.resolution；protocol.json code_sha256；verification.json executed_modules_frozen]`

## 定义与比较范围

45项字段与单位在protocol.json fields逐项列出，沿用蓝龙补测的形状／AO／畸变定义；AO使用seed17、每个样本集1000点、每点8条余弦半球射线。原文未指定的采样约定属于项目实现，不能将单资产诊断称为论文整套数据集结果。`[verified: protocol.json previous_protocol/fields/seed/ao_samples_per_population/ao_cosine_rays_per_point；opinion: 结论边界]`

原低模无UV的两个资产，其UV面积、畸变和密度均不适用；8192px仅为原高模颜色来源尺寸。排布被拒绝的条目只计算保留的原生UV，最终排布覆盖与颜色误差为空。无显式切缝时切缝AO也不适用。每一个空值均有null_reasons，不能将空值填0参与排名。`[verified: summary.json rows.layout_stage/null_reasons；verification.json original_source_images]`

旧字段distortion.new_seam_length_3d及其单位面积版本实际计算当前state.seams总长，原作者UV也可能非零；本表改称“记录3D切缝长度”，不宣称相对作者UV的净新增。旧公式和字段名保留以便核对历史数据。`[verified: protocol.json new_seam_scope；code_snapshot/baseline/report.py distortion_metrics]`

共享表面比例统计参与双方，100%参与不等于贴图减少100%；这三个成对镜像结果的有效重复UV占位节省为50%。原始交叠、全部重叠率与经许可的有效复用分列。原始与镜像具有不同几何，只在同一镜像几何内直接比较共享与解除共享。`[verified: summary.json rows.geometry_hash/values.actual_shared_surface_fraction/values.valid_duplicate_uv_area_saved_fraction]`

Chloe与能量刀的圆盘布局虽紧凑度接近1，全方向保角K约为9.97／11.88；同几何解除共享后畸变基本不变。指标补测不改变现有综合数值拒绝或Agent预览记录，也不代表同质量缩图、人工艺术认可或完整PBR通过。`[verified: summary.json rows.values.paper.compactness/values.distortion.conformal_mean_all_nondegenerate/historical_quality_accepted；opinion: 结论范围]`

## 复现与核验

在仓库根目录运行，输出目录必须不存在：`[verified: baseline/evaluate_legacy_metrics.py run]`

```powershell
uv run python -X utf8 -u -m baseline.evaluate_legacy_metrics --output artifacts/legacy_metrics_new_round --samples 1000
```

补测逐案例共53.064秒，创建6份几何AO场景；130个输入文件哈希、30份历史颜色结果、30张mip0尺寸及哈希、3份原始颜色来源尺寸均已核对。源码后续只做括号格式整理，当前工作文件与冻结执行源码AST相同，未用新哈希冒充旧执行版本。`[verified: summary.json metric_seconds/unique_ao_geometries；verification.json]`

展示PDF位于output/pdf/legacy-assets-with-metrics/，共9页：原6页图像及3页完整指标。PDF的源图像、旧PDF哈希、45行中文提取、全部页面渲染和逐页外观检查独立保留；未覆盖旧六页PDF。`[verified: output/pdf/legacy-assets-with-metrics/verification.json；evidence.json]`
