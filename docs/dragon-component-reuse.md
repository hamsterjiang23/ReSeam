# 龙的部件重展开与共享

本轮按完整自然部件重做 UV，再建立左右共同域。模型中的部件是原始面 ID 集合，临时切口只复制计算网格顶点，最终写回原面角 UV；本轮范围为双翼、四肢和角刺，躯干尾巴沿用现有区域布局。`[verified: 用户本轮接受的部件计划]`

## 工具操作

1. 用 `view.render mode=regions` 查看编号；`region.define` 支持 `faces`、`chart_ids`，或 `seed_faces` 加 `boundary_edges` 三选一。边界与切口都使用原顶点 ID 对 `[a,b]`，不使用坐标焊接。区域导出 `selection.obj`，其面行顺序对应 `selection_mesh_face_ids`。`[verified: regions.py define_region/bounded_faces]`
2. 对主侧调用 `uv.reparameterize backend=optcuts`，传入 `faces` 和 `seam_edges`。空切口列表从原几何邻接重新建立区域连接，删除内部旧 UV 碎片边界。复杂部件通过切口或自然子区域处理；查看报告中的 `requested_seam_edges`、`result_seam_edges`、`old_region_chart_count` 和 `result_chart_count`。`[verified: reparameterize.py reparameterize；regional_cut.py cut_region]`
3. 对同一父快照调用 `reuse.check_pair mode=paired_domain`，输入两侧面集合（或 `first_region_id`/`second_region_id`）、两侧切口和至少三个 `boundary_anchors`。锚点格式为 `{"first":[原面ID,面角0到2],"second":[原面ID,面角0到2]}`；同一原顶点在切口两边可有两个副本，因此必须用面角指明切口侧。`[verified: paired_domain.py _anchor_vertices/_faces/paired_layouts]`
4. `domain=master_uv` 使用主侧已经展开的当前 UV，`master_member` 选择 `first` 或 `second`；锚点之间按原三维边界弧长插值，再求内部调和坐标。`allow_convex_fallback=true` 允许失败后重做共同凸边界域，报告明确记录 `fallback_used`。最终 UV 包含关系生成双向曲面对应，不经过旧最近点前置拒绝。`[verified: paired_domain.py _boundary_map/paired_layouts/_uv_correspondences]`
5. 用 `reuse.apply` 应用检查提案，然后 `material.target` 冻结源颜色目标。先 `view.render mode=basecolor,target=...` 比较原始/目标/差异，记录 Agent 外观判断；接受后才烘焙，再用实际贴图渲染。`[verified: 用户接受的计划及现有 CLI 合同]`

重复角刺可显式传入 `first_to_second` 的三乘四相似变换；该变换与最终UV对应分别检查，不会修改模型几何。`master_member` 选择UV主域，颜色来源另由 `material.target master_members` 指定。`[verified: paired_domain.py check_paired_domain；color_reuse.py freeze_target]`

具体参数以 `uv run reseam tools describe <工具>` 为准。通过工具不等于人工认可；外观优先配置与旧严格协议分别记录。`[opinion]`

## 构造与真实验证

端到端回归覆盖跨旧岛、不同三角划分、冻结源目标、真实 BaseColor 烘焙和已验收导出；数值细化失败仍保留未细化布局供独立检查。切缝测试覆盖圆柱、切口侧身份、不完整边界、过期状态和超时。`[verified: tests/test_agent_tools_component_pipeline.py；tests/test_agent_tools_regional_cut.py；tests/test_agent_tools_regions.py]`

真实部件编号与原色在 [components_selection01](../artifacts/dragon_reuse_development/components_selection01/manifest.json)。整翼存在多个断开的几何部件，按原身份分别处理；翼支撑单侧是一个曲面但有多块旧 UV，是重点碎片修复对象。脚背和脚趾保留完整特征片，先共享脚底及踝部，减少此前白爪扩张的风险。`[verified: components_selection01/manifest.json whole_region_topology、exclusive_natural_components；opinion: Agent 区域选择]`

真实结果以新轮次报告为准。对照固定原父资产，比较原 UV 无共享、OptCuts 无共享、纯几何复用、Agent 复用，并补同一最终目标/同展开的无共享路径。报告分别统计连通岛、独立图块和实际共享面积，并附真实贴图、GLB、前后图及全部失败成本。`[verified: 用户本轮接受的计划；opinion: 结果记录口径]`

颜色预检曾拒绝大腿和翼骨的可见色块错位。少量锚点可以生成合法却错位的内部映射，需根据原始/目标图修正完整边界，而不能以共享组数替代外观判断。外观优先的修订轮统一使用 `balance_density=false`：旧密度平衡在约束冲突时返回未排布状态，可能表现为后续大量交叠拒绝；修订后所有路径重新排布，受影响的自动候选重新检查。`[verified: components_target_reject_evidence01/group_appearance_changes.json；density.py balance_density 的拒绝返回；opinion: 修订协议]`

补充诊断区分“观察到色差”与“确认映射错误”：以每面4个采样查询镜像曲面原色，wing_upper 的原左右差异均值0.2516，当前目标相对原色为0.2493；wing_support分别0.1837和0.1850。因此这些翼骨的差异主要与源左右外观不一致相符，不能仅凭颜色块认定共同域错误。大腿当前相对几何参考仍有额外差异，判断以实际图像及用户容忍度为准。此诊断不证明全局对应双射。`[verified: components_target02/geometric_reference_diagnostic.json]`

## 颜色球容忍度

`reuse.check_color` 在原始颜色周围定义球形邻域，检查冻结共享目标是否落在球内：距离为 `100 × ||Oklab(目标)-Oklab(原色)||₂`，球心逐采样随原色变化。Oklab转换使用 [W3C 的 D65 转换公式](https://www.w3.org/TR/css-color-4/#color-conversion-code)，输入为已经解码的线性RGB。`[verified: color_tolerance.py linear_rgb_to_oklab/color_distances；W3C XYZ_to_OKLab 示例]`

|档位|球半径|用途|
|---|---|---|
|低 `low`|5|较少改色的项目默认。`[verified: color_tolerance.py PRESET_RADII；opinion: 用途]`|
|中 `medium`|10|工具默认档位。`[verified: color_tolerance.py tolerance]`|
|高 `high`|20|本轮优先复用的预览档位。`[verified: color_tolerance.py PRESET_RADII；opinion: 本轮选择]`|

请求传入 `snapshot`、冻结 `target`、`level`；可用正数 `radius` 覆盖档位半径，用 `selected_faces`/`region_id` 查看局部。工具同时输出三档覆盖率、逐组和逐面色差、球外面。球内采样直接满足颜色容忍条件；球外位置供Agent看图判断，不因一个越界点自动拆散整个部件。全部采样都在球内时才返回 `color_accepted=true`，不会把工具执行成功或大多数采样满足改写成全部满足。`[verified: color_tolerance.py check_color/sphere_summary；opinion: Agent处理球外位置]`

本轮 [40,576个修改区域采样](../artifacts/dragon_reuse_development/components_color_tolerance01/color_check.json)按原三维面积加权：低档球内78.12%，中档93.46%，高档99.24%；高档仍有球外点。它们是源外观→共享目标的容忍度结果，与烘焙重建、mip和人工判断分别记录。`[verified: components_color_tolerance01/color_check.json presets、acceptance_scope]`
