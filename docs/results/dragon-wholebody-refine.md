# 镜像龙全身继续整理

独立图案40份降到38份，实际表面仍为80片，固定11338面几何保持一致。两组重复角刺各从两份图案改为一份，实际共享面积检查覆盖全模型。`[verified: round03/appearance/candidate_inspect/uv_metrics.json actual_independent_tiles=38、uv_connected_charts=80；topology_audit.json]`

本轮38份版本覆盖63.05%，同设置三次摆放中最好的40份对照为64.53%，历史40份精调为65.08%。优先减少图块是本次选择，紧凑程度没有提高。`[verified: round03/measurements.json；round02/historical_density_comparison.json；opinion: 选择]`

## 实际改动与保留

- 上背与中背尖刺共用，尾根与尾端尖刺共用。每组包括两个部位各自的左右半边，共四个独立表面。`[verified: final_owner_origins.json source_tiles=[9,10]、[13,14] physical_charts=4]`
- 胸腹、腿、尾巴、前肢、翼膜与翼骨完成16次局部展开试验，另有2次组合。未取得相对此前最好排布的稳定优势，最终保留其原展开形状。眼嘴、白爪和已有头冠修整继续保留。`[verified: round02/shape_trials.json、packing_repeats.json；final_region_decisions.json]`
- 四组角刺完成9次跨部位尝试，3个方案有可用几何结果，6次拒绝完整保留。另以新冻结内核运行3次四成员候选，并各自试3个排布起点。`[verified: round02/cross_part/summary.json；round03/candidates.json、measurements.json]`

原40份主侧轮廓中，38份沿用原形状。中背尖刺改用上背尖刺的展开位置，尾端尖刺改用尾根尖刺的展开位置，两者的镜像半边同步更新。`[verified: shape_preservation.json changed_first_side_chart_shapes=[10,14]；final_owner_origins.json]`

## 颜色、清晰度与检查

几何检查采用本轮显式外观优先设置，保留来源UV原有冲突的限定许可。严格渲染检查保持原阈值并如实拒绝。`[verified: round03/protocol.json profile；appearance/{candidate,baseline}_validate/request.json、result.json]`

- 冻结目标后再查看源色、目标和差异。231个三角面有颜色调整，保护面改动0个，整体面积加权颜色变化均值约0.000695、最大采样差0.625469。局部蓝白边界及明暗变化由Agent预览接受。`[verified: appearance/common_target/appearance_change.json；target_review.json；opinion: 外观判断]`
- 保护区平均纹素密度为同设置对照的99.32%。按原图块平均，最小比值91.22%、最大112.56%，该指标与局部拉伸、缩小贴图和外观验收分别报告。`[verified: density_comparison.json protected_area_mean_density_ratio、minimum_original_tile_density_ratio、maximum_original_tile_density_ratio]`
- 两份实际2048 RGBA8 BaseColor均写出12级缩小图，验收LOD0/1/2。运行时贴图字节均为22,369,620，本轮字节节省为0。`[verified: 两份bake/result.json payload_bytes；export_integrity.json mips；两份validate/request.json]`

|实际验收|38份结果|40份同目标对照|
|---|---|---|
|几何|通过|通过 `[verified: appearance/{candidate,baseline}_validate/result.json geometry_valid]`|
|颜色|通过|通过 `[verified: appearance/{candidate,baseline}_validate/result.json material_valid]`|
|接缝|通过|通过 `[verified: appearance/{candidate,baseline}_validate/result.json seams_valid]`|
|严格渲染|拒绝|拒绝 `[verified: appearance/{candidate,baseline}_validate/result.json render_valid]`|

Agent实际预览接受与数值门禁分开。导出身份为diagnostic，人工、完整PBR和GPU验收尚未完成。`[verified: appearance/appearance_review.json、两份diagnostic_export/result.json]`

## 可检查交付

- [38份结果模型](../../output/dragon-wholebody-refine/fewer-tiles-38/model.glb)，同目录提供真实BaseColor、12级缩小图与原始视图。`[verified: appearance/export_integrity.json candidate]`
- [40份同目标对照](../../output/dragon-wholebody-refine/control-40/model.glb)与[前后图](../../output/dragon-wholebody-refine/before-after.png)。`[verified: appearance/export_integrity.json baseline；summary.json comparison]`
- [新版通俗图解](../../output/pdf/dragon-wholebody-refine/镜像龙制作流程图解_含各UV实际渲染.pdf)共45页，包含历史28页、本轮12页、最终38份展示与棋盘格对照2页，以及实际颜色渲染3页。第43页展示各UV的实际颜色模型及对应贴图，第44-45页提供放大图。`[verified: verification-actual-renders.json pages=45、added_pages_visually_reviewed]`
- [各UV实际颜色渲染与贴图对照](../../output/pdf/dragon-wholebody-refine/各UV实际颜色渲染与贴图对照_含38份.png)包含原始参考、六种已上色基线和最终38份版本。FlattenAnything与xatlas的排布失败保留标记，未生成颜色图。已上色的七个镜像龙状态均核对实际UV、烘焙和渲染请求。`[verified: actual_render_addendum01/evidence.json methods；verification-actual-renders.json]`

## 证据与实现边界

根目录为`artifacts/dragon_wholebody_refine/`。round01保留胸腹三条旧复制路径的失败，round02封存104份源码，round03封存107份源码，运行完后重新核对哈希。对应与工具实现变化各自在新冻结轮次建立对照。`[verified: 各轮protocol.json、closure.json；round01/calls.json]`

现有reuse.check_pair增加显式combine_mirror_groups选项，以既有跨部位检查加哈希绑定镜像面角来源形成四成员组。member_maps记录主表面到每个完整成员的变换，所有成员之间的正面积交叠均检查双向位置与法线。新类型限UV与BaseColor，局部释放先解除组共享。`[verified: src/reseam/agent_tools/multi_member.py combine_mirror_groups；src/reseam/reuse_members.py member_transforms；budget_state.py validate_budget_safety]`

独立复审发现释放无关面会丢失新增成员记录，已修复并增加两条公开事务回归。复审CLEAN，相关204项测试通过，Ruff与basedpyright通过。`[verified: round03/review.json、closure.json；本轮命令输出]`

round02控制组首次烘焙早于目视目标预检，该次目标未改色且源/目标像素相同，时序记录保留。大腿候选与round03两份正式对照均在目视接受各自目标后开始烘焙。`[verified: round02/appearance/target_review.json；round03/appearance/target_review.json、calls.json]`
