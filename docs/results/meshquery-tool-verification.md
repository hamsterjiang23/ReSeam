# MeshQuery 公开工具验证

本页记录项目工具行为、构造资产冒烟和独立 Skill 使用反馈。当前证据支持“可以查询真实网格、编排接缝、调用原生展开、烘焙并导出”，不支持“完整复现作者系统”或“工业资产已全面验收”。实现定义见[工具说明](../meshquery-tools.md)。`[verified: ../../artifacts/meshquery_skill_forward01/commands-results.json calls; ../../artifacts/meshquery_backend_smoke01/summary.json]` `[opinion]`

## 公开入口与覆盖

入口统一为 `uv run reseam tools run <工具名> --request <JSON> --output <新目录>`；表中“实际证据”与测试覆盖分开，未单独运行的 CLI 不借用其它入口的成功结论。`[verified: ../../skills/reseam-uv/SKILL.md 每次调用示例]` `[opinion]`

|公开工具|行为与实现入口|测试覆盖|实际证据与边界|
|---|---|---|---|
|`mesh.index`|原面／边身份索引、AO／厚度与特征；`mesh_query.index_mesh`|`test_index_queries_original_ids_and_real_thickness` 验证闭合立方体射线厚度与原边身份；`test_genus_candidate_is_verified_on_torus_not_inferred_from_shape` 验证颈环|独立前向圆柱48面、96边；该次 `ao_rays=0`，不作为AO实测。`[verified: ../../tests/test_agent_tools_mesh_query.py L28–44,L120–136; ../../artifacts/meshquery_skill_forward01/index/result.json; index/request.json]`|
|`mesh.query`|绑定快照的只读SQL、完整选择文件；`mesh_query.query_mesh`|完整结果、禁写／ATTACH、数据库与状态篡改、执行中deadline|先因 `edges` 表名失败，再查schema得到 `edge`，定位边38。`[verified: ../../tests/test_agent_tools_mesh_query.py L47–72,L109–117; ../../artifacts/meshquery_skill_forward01/edges/result.json; schema/selection.json; longitudinal/result.json]`|
|`region.propose`|几何候选或独立PartField研究后端；`region_proposals.propose_regions`|立方体六侧＋整体候选跨旧UV、父快照不变；缺权重明确失败|独立前向输出一个48面连通候选，未自动赋予语义名。`[verified: ../../tests/test_agent_tools_mesh_query.py L75–94; ../../artifacts/meshquery_skill_forward01/propose/result.json]`|
|`region.define`|Agent命名并保存原面集合；`regions.define_region`|本轮实际CLI检查|圆柱侧壁48面，`uv_modified=false`、`geometry_modified=false`。`[verified: ../../artifacts/meshquery_skill_forward01/region/result.json]`|
|`seam.select`|十种原边选择算子；`seam_selection.select_seam`|边界区别、四边形来源、薄片闭环、曲率路径、端部候选、SQL实体类型和超时；详见下表|独立前向因参数说明不足改用SQL＋显式边ID；补充公开boundary_loop调用返回48条真实边界边，见 `artifacts/meshquery_v1/plan_management01/select_boundary/result.json`；其余算子覆盖见测试。`[verified: ../../tests/test_agent_tools_seam_selection.py L34–172; ../../artifacts/meshquery_skill_forward01/report.md 卡点2]`|
|`seam.preview`|渲染实际选边；`seam_plans.preview_seams`|DSL依赖顺序及预览返回真实边集合|圆柱边38预览后再提交。`[verified: ../../tests/test_agent_tools_seam_plans.py L61–73; ../../artifacts/meshquery_skill_forward01/preview/result.json]`|
|`seam.submit_plan`|保存计划、父哈希、切割拓扑；`seam_plans.submit_plan`|循环／未知引用、类型错误、保护面、过期计划不发布|纵切计划得到1个disk、1个边界环、无自由端。`[verified: ../../tests/test_agent_tools_seam_plans.py L42–58,L87–104; ../../artifacts/meshquery_skill_forward01/plan/result.json topology]`|
|`seam.inspect_cut_topology`|检查切割连通分量、边界与盘拓扑；`seam_plans.inspect_cut_topology`|`cut_topology` 切前／切后检查；顶点link与bowtie测试|独立前向通过提交计划取得拓扑；随后公开CLI独立检查圆柱，得到1个disk且父状态不变，见 `artifacts/meshquery_v1/plan_management01/topology/result.json`。`[verified: ../../tests/test_agent_tools_seam_plans.py L22–39; ../../tests/test_agent_tools_seam_refinement.py test_vertex_link_detects_bowtie_without_nonmanifold_edges/test_disk_certificate_records_vertex_links; ../../artifacts/meshquery_skill_forward01/commands-results.json calls]`|
|`seam.apply`|绑定计划、显式展开后端、新快照与反馈；`seam_plans.apply_seams`|8面构造圆柱多碎片→1岛，父状态不变|独立前向48→1；后续圆柱48→1、圆锥24→1，均须继续烘焙。`[verified: ../../tests/test_agent_tools_seam_plans.py L22–39; ../../artifacts/meshquery_skill_forward01/apply/result.json; ../../artifacts/meshquery_v1/smoke02/cylinder/apply/result.json; ../../artifacts/meshquery_v1/smoke02/cone/apply/result.json]`|
|`seam.plan`|get／compare／select计划及父状态；`seam_plans.manage_plan`|陈旧父状态拒绝|公开CLI的get／compare／select均已实际成功：同父圆柱计划对比新增边5、删除边2，select返回原父快照且UV未修改，见 `artifacts/meshquery_v1/plan_management01/{get,compare,select}/result.json`。`[verified: ../../tests/test_agent_tools_seam_plans.py L42–50; ../meshquery-tools.md 计划、执行与反馈; ../../artifacts/meshquery_skill_forward01/commands-results.json calls]`|
|`uv.reparameterize`|原面角映射下局部展开；`reparameterize.reparameterize`|三种原生Blender方法、缺依赖不改父状态、三角面不猜四边形|Blender 4.5.11 LTS三后端真实CPU执行，8面原几何与面集合一致。`[verified: ../../tests/test_agent_tools_blender_unwrap.py L17–87; ../../artifacts/meshquery_backend_smoke01/summary.json; blender_lscm/blender/backend.json; blender_abf/blender/backend.json; blender_minimum_stretch/blender/backend.json]`|
|`uv.pack`|重排并检查冲突／畸变；`geometry.pack_uv`|本轮实际CLI检查|前向原始48图块因畸变失败；重展开1图块严格几何通过。`[verified: ../../artifacts/meshquery_skill_forward01/baseline_pack/geometry.json errors; pack/geometry.json valid/chart_count]`|
|`view.render`|颜色、棋盘、线框等视图与像素定位；公开入口 `assets.render_views`|wireframe／normals／distortion返回图与附近原边|前向看图包含原色、切口、棋盘、实际贴图及GLB重新导入图；前后轴向空白视图不等于空模型。`[verified: ../../tests/test_agent_tools_seam_plans.py L76–84; ../../artifacts/meshquery_skill_forward01/report.md 图像清单; source/result.json coordinate_convention]`|
|`material.target`|冻结源颜色及对应；`color_reuse.freeze_target`|本轮实际CLI检查|旧输入因来源面角身份拒绝；新统一来源输入进入限定颜色验收。`[verified: ../../artifacts/meshquery_skill_forward01/target/result.json; ../../artifacts/meshquery_v1/smoke02/cylinder/validate/result.json scope]`|
|`material.bake`|真实通道与完整mip；`material.bake_material_tool`|真实CPU烘焙、共享冲突、常量材质、纹理篡改测试|512 BaseColor实际PNG与10层mip；诊断报告不等于bake manifest。`[verified: ../../tests/test_agent_tools_material.py test_actual_cpu_bake_independent_quality_and_accepted_export/test_shared_material_conflict_is_averaged_then_rejected/test_absent_maps_use_declared_gltf_constants; ../../artifacts/meshquery_skill_forward01/bake/color_diagnostic.json files]`|
|`quality.validate`|独立几何、颜色、渲染及指定LOD验收；`material.validate_quality`|材质、陈旧快照与篡改拒绝测试|前向PBR拒绝；新圆柱限定颜色通过；新圆锥padding反馈后通过，详见下文。`[verified: ../../tests/test_agent_tools_material.py test_tampered_texture_and_stale_snapshot_cannot_validate; ../../artifacts/meshquery_skill_forward01/pbr_validate/result.json; ../../artifacts/meshquery_v1/smoke02/cylinder/validate/result.json; ../../artifacts/meshquery_v1/smoke_feedback01/cone_padding_feedback/validate/result.json]`|
|`asset.import`|冻结资产与原UV／来源身份；`assets.import_asset`|OBJ原多边形映射、三角来源不制造四边形|交付GLB重新导入，面角位置完全一致；UV仅有float32写出量化误差。`[verified: ../../tests/test_agent_tools_blender_unwrap.py L58–87; ../../artifacts/meshquery_v1/reimport_smoke02/verification.json records]`|
|`asset.export`|已验收导出或显式diagnostic；`material.export_asset`|拒绝无验收accepted，diagnostic单列|前向诊断GLB真实生成且重新载入；后续导出重新导入核验不等于GPU或artist通过。`[verified: ../../tests/test_agent_tools_material.py test_unvalidated_export_refused_and_diagnostic_explicit; ../../artifacts/meshquery_skill_forward01/pbr_export/export.json; reimport/result.json; ../../artifacts/meshquery_v1/reimport_smoke02/verification.json]`|

|选择算子|行为／测试的具体边界|
|---|---|
|`topological_loop`、`find_loop`|有原四边形证据才沿拓扑延伸；三角对角线、错误映射及无来源拒绝。`[verified: ../../tests/test_agent_tools_seam_selection.py L43–71]`|
|`boundary_loop`、`region_boundary`|真实开口与选区接口分开；内部单元测试分别得到0边与4边。`[verified: ../../tests/test_agent_tools_seam_selection.py L34–40]`|
|`thin_loop`|双面薄枕得到闭合外圈；单层开片拒绝伪造双面环。`[verified: ../../tests/test_agent_tools_seam_selection.py L87–102]`|
|`crease_select`、`semantic_interface`|折叠网格按二面角／语义邻接选择实际公共边。`[verified: ../../tests/test_agent_tools_seam_selection.py L74–84]`|
|`geodesic`、`seamcut_seam`|相同锚点下普通路径与曲率偏好路径不同，保持连续无分叉；重合但不连通的片不焊接。`[verified: ../../tests/test_agent_tools_seam_selection.py L105–121,L135–142]`|
|`find_extremities`|面上距离端部候选的边确为该面集边界。`[verified: ../../tests/test_agent_tools_seam_selection.py L124–132]`|

这些算子是论文公开机制的项目离散适配：PCA薄片、角亏损、边图路径、候选阈值与原UV保留策略属于本项目选择；表中测试不能证明作者代码等价或论文全部指标复现。`[verified: ../meshquery-tools.md 网格观察与语义候选／论文公开算子与项目入口／评价边界]` `[opinion]`

## 独立 Skill 前向测试及反馈修正

[前向记录](../../artifacts/meshquery_skill_forward01/report.md)只用Skill、链接说明、公开CLI和给定旧输入，共23次工具调用、工具报告累计27.858秒；用纵切将48图块改为1图块，512真实BaseColor与诊断GLB可查看。旧输入的 `material.target` 来源身份拒绝、PBR LOD1颜色p95拒绝均保留；未把后来新输入的成功倒写成这次测试通过。`[verified: ../../artifacts/meshquery_skill_forward01/commands-results.json calls/tool_wall_seconds_sum; target/result.json reason; pbr_validate/validation.json material.faces face=1,mip=1]`

|暴露的问题|本轮修正及证据边界|
|---|---|
|SQL表名与算子参数不清楚|`tools describe mesh.query` 新增单数表名、schema查询和示例；`seam.select` 新增逐算子 `operator_parameters`；Skill引用同步说明有序锚点和 `crease_select` 的edge过滤。`[verified: 当前CLI describe mesh.query request.schema／seam.select request.operator_parameters; ../../skills/reseam-uv/references/meshquery.md 末段]`|
|诊断报告送入验证／导出得到 `SOURCE_REFERENCE_CHANGED`|现在明确返回 `DIAGNOSTIC_REPORT_IS_NOT_BAKE_MANIFEST`，指向已有预览GLB及所需 `bake.json`；旧失败目录保留。本页依据代码分支核验错误文案，不声称独立前向测试已重跑。`[verified: ../../src/reseam/agent_tools/material.py L362–363; ../../artifacts/meshquery_skill_forward01/validate/result.json; export/result.json]`|
|构造OBJ双精度与GLB float32来源不能精确对应|新fixtures02生成器先将顶点量化为float32再转回double，供后续OBJ／GLB共同来源；新资产目录独立，旧fixtures01不覆盖。`[verified: ../../scripts/run_meshquery_experiments.py L225–257; ../../artifacts/meshquery_v1/fixtures02/fixtures.json assets; ../../artifacts/meshquery_skill_forward01/commands-results.json read_sources]`|

## 真实烘焙、反馈与重新导入

|构造资产／轮次|实际结果|
|---|---|
|新圆柱 `smoke02`|48面、48碎片→1图块；512 BaseColor限定范围通过，`full_pbr_passed=false`、`artist_reviewed=false`。颜色验收绑定显式 `quality_constraints=false` 几何配置，不能称为该阶段使用全部严格畸变门禁。`[verified: ../../artifacts/meshquery_v1/smoke02/cylinder/apply/result.json; validate/result.json geometry_profile/scope; ../../artifacts/meshquery_v1/reimport_smoke02/verification.json records]`|
|新圆锥 `smoke02`|24面、24碎片→1图块；13面LOD2覆盖失败，`color_failed_faces=[]`，不是颜色误差超限。例face2的LOD2 `alpha_min=0.9517725`。`[verified: ../../artifacts/meshquery_v1/smoke02/cone/apply/result.json; validate/validation.json material.coverage_failed_faces/material.faces]`|
|圆锥padding反馈 `smoke_feedback01`|同UV快照、冻结目标、512分辨率和验收配置，padding由4变8；LOD0／1／2限定颜色验收通过，仍非完整PBR。`[verified: ../../artifacts/meshquery_v1/smoke02/cone/bake/request.json; ../../artifacts/meshquery_v1/smoke_feedback01/cone_padding_feedback/bake/request.json; validate/request.json; validate/result.json]`|
|导出重新导入|圆柱／圆锥原面角位置完全一致；UV最大绝对误差分别为 `4.4362457485e-8`／`3.0761592051e-8`。这证明这两份导出身份保留，不代表所有资产或渲染器兼容。`[verified: ../../artifacts/meshquery_v1/reimport_smoke02/verification.json records]` `[opinion]`|

[统一验证记录](../../artifacts/meshquery_v1/verification_and_dependencies.json)记载 `uv run --extra compression pytest -q` 为731 passed／78.18秒，所列新工具、worker和测试的ruff通过，basedpyright为0 errors／warnings／notes；仓库与已安装Skill使用 `python -X utf8`＋pyyaml校验均有效，SHA256均为 `accbe5af00352a655fcf31a276ddaaec05e055b104156689d8d889d334b77e2b`。此处转录该验证记录，没有再运行完整测试。`[verified: ../../artifacts/meshquery_v1/verification_and_dependencies.json verification]`

本页不把工具覆盖或两个构造模型推广为复杂模型泛化、工业生产质量或作者完整复现。PartField仍是独立研究路径；其依赖许可与checkpoint许可状态见统一记录，不能据源码或推理可用性推断生产授权。UV共享、PBR、GPU、artist与真实节省仍按各自证据分别判断。`[verified: ../../artifacts/meshquery_v1/verification_and_dependencies.json partfield.scope/checkpoint_separate_license_status; ../meshquery-tools.md 评价边界]` `[opinion]`
