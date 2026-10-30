# MeshQuery 工具与 ReSeam 适配

本页对应论文公开机制的项目实现，使用现有 `uv run reseam tools list/describe/run`。算子源码不等于论文作者实现；本轮的完整实验、外观与泛化结论必须另由实际结果支持。`[opinion]`

## 轮廓排布扩展

`uv.pack backend=blender_concave` 调用隔离 Blender 的 CONCAVE／ANY 旋转排布；每组仅送一个主图块，
统一尺度和相似变换回放到全部成员，保持原面角UV身份及共享关系。`density_normalize=false` 保留
岛间相对密度，最终间距、边界、交叠由工具独立检查；Blender的margin按双侧轮廓膨胀换算，
不能把请求值当实测间距。缺失Blender或变换回放不符就拒绝，不退化成假成功。`[verified: compact_packing.py compact_pack；agent_pack_worker.py main；geometry.py pack_uv]`

实际对照、失败与缩图见[整龙镜像复用及紧凑排布](results/dragon-whole-half-reuse.md)。`[verified: whole_half_report01/report.json]`

## 网格观察与语义候选

项目扩展 `asset.mirror_variant` 需要显式 `allow_geometry_change=true`，沿x/y/z平面裁切保留半边并镜像。它创建新asset及同几何共享／无共享快照，保存新面到原面重心对应；不修改父资产，不属于MeshQuery论文已有工具。完整源PBR未复现，当前为BaseColor变体，须继续烘焙与查看。`[verified: mirror_asset.py mirror_asset；symmetry_variant.py build_symmetry_variant]`

`view.render`／`seam.preview` 支持具名 `cameras`，替换默认四个水平视角，提供顶视、底视与任意斜视；像素拾取、线框、切缝和源色／目标／烘焙比较共用镜头。角度单位为度、Y轴向上，完整参数以 `tools describe` 为准。真实龙补充视角见 `artifacts/meshquery_v1/camera_extension01`；默认五张PNG与冻结版本逐字节相同。`[verified: views.py _cameras/render_views；camera_extension01/regression.json default_png_byte_equal]`

`mesh.index` 读取快照，写出 `index.json` 和 `mesh.db`，不修改 UV。`face.id` 是原三角面行号，`edge.id` 是原 `mesh.edges` 序号，`v0/v1` 是原顶点身份；数据库不能用坐标合并重合顶点。几何版本与 UV／语义状态版本同时保存，任何子快照都需要匹配的索引。`[verified: mesh_query.py index_mesh/load_index]`

索引表为 `collection`、`mesh`、`component`、`face`、`edge`、`face_edge`、`edge_neighbor`、`feature`。`face` 包含部件、原 UV 岛、标签、面积、位置和法线；`edge` 另含接缝、边界、二面角、曲率、AO、厚度。位置 x/y/z 使用索引记录的 origin/scale 归一化；长度、面积和 thickness_raw 为输入模型单位。`[verified: mesh_query.py index_mesh SQL schema]`

`ao_rays=8` 默认执行真实 BVH 射线查询；`ao_rays=0` 明确跳过并写 NULL。厚度仅对闭合分量报告四条内向射线命中距离中位数；开口或无命中保持 NULL，不填零冒充测量。曲率使用边界感知顶点角亏损，AO 使用 Fibonacci 半球采样；这些定义在索引中记录，不与论文全部数值指标自动视为等价。`[verified: mesh_query.py _features/index_mesh definitions]`

`mesh.query` 接受只读 SQL，完整结果保存在 selection.json，响应最多展示前20行及数值摘要。SQL 写入、ATTACH 和扩展加载被拒绝；运行中的 SQL 受 deadline 中断。选择边或面时显式起别名：`[verified: mesh_query.py query_rows/query_mesh/query_selection]`

```sql
SELECT id AS edge_id FROM edge WHERE boundary = 1;
SELECT id AS face_id FROM face WHERE label = 'wing_membrane';
SELECT label, count(*) AS count FROM face GROUP BY label;
```

`region.propose backend=geometry` 从连通分量、多个折痕阈值及薄片 PCA 正反侧生成候选。它输出原面集合与几何依据，不自动赋予语义名；Agent 仍需看高亮图，再调用 region.define。默认折痕阈值为25／45／70度，是项目候选策略。`[verified: region_proposals.py propose_regions]`

`backend=partfield` 是独立研究路径，优先调用 runtime.local.json 指定的 WSL PartUV Python、已固定哈希的 checkpoint 和本项目隔离 worker。临时归一化必须与上游公式一致、面顺序不变，结果恢复原始几何，只导入每个原面的分区标签；默认按层次树撤销最近合并得到8组，再拆分不连通标签。此适配不调用上游焊接或修复网格流程。`[verified: region_proposals.py PartField branch；partfield_worker.py main]`

可配置 `partfield_command` 使用其它独立运行器，但它必须读取规范化输入 NPZ，输出包含完全一致 vertices/faces 与整数 labels 的 NPZ；另提供 backend_manifest 记录版本来源。研究权重与许可证不得混入生产默认依赖，实际运行成功与论文复现分别报告。`[verified: region_proposals.py PartField branch]` `[opinion]`

## 论文公开算子与项目入口

下列公开算子来自 [MeshQuery Table 4](https://arxiv.org/html/2610.02507v1#A1.SS2)，统一使用 `seam.select` 的 operator 和 arguments。最终参数以 CLI describe 及源码为准。`[verified: MeshQuery 附录 A.2；seam_selection.py OPERATORS]`

|operator|主要输入|行为与适用范围|
|---|---|---|
|topological_loop|edge_ids 种子集合|沿有原多边形证据的四边形边环延伸；三角网格不自动猜测四边形。`[verified: seam_selection.py _quad_topology/_topological]`|
|boundary_loop|face_ids|所选面中的模型真实开口边。`[verified: seam_selection.py _boundary]`|
|region_boundary|face_ids|面集合边界，可跨原 UV 岛。`[verified: seam_selection.py _boundary]`|
|thin_loop|face_ids|比较折痕切分与 PCA 正反侧，寻找一个闭合分界环。`[verified: seam_selection.py _thin]`|
|semantic_interface|first_label、second_label|查询相邻的两种语义标签分界。`[verified: seam_selection.py select_edges]`|
|crease_select|edge_ids、angle_degrees|选二面角大于阈值的边；未指定角度时用相对排序。`[verified: seam_selection.py select_edges]`|
|find_loop|edge_ids|在候选范围搜索可用边环。`[verified: seam_selection.py select_edges]`|
|find_extremities|edge_ids、cap_fraction|寻找突起端部候选。`[verified: seam_selection.py select_edges]`|
|geodesic|waypoint_edges、edge_ids、turn_weight|沿边邻接图搜索连续路径。`[verified: seam_selection.py _path/select_edges]`|
|seamcut_seam|waypoint_edges、edge_ids、beta|带局部曲率方向偏好及短路径历史的各向异性搜索；项目离散实现，不声称作者二进制等价。`[verified: seam_selection.py _path/select_edges]`|

SQL 选择使用 arguments.index 与 edge_query／face_query，返回列必须命名 edge_id／face_id。直接 ID 列表和 SQL 都绑定当前原始身份。`[verified: seam_selection.py select_edges；mesh_query.py query_selection]`

## 计划、执行与反馈

`seam.preview` 预览显式 edge_ids 或 program；`seam.submit_plan` 保存实际切缝、执行轨迹、拓扑检查与修正记录。program 使用 `{name, operator, arguments, emit?, mirror?, comment?}`，`{ref: name}` 引用已生成的边集合。循环／未知引用拒绝，镜像选择不自动授权 UV 共享。`[verified: seam_plans.py evaluate_program/submit_plan]`

`seam.inspect_cut_topology` 返回切割分量、边界与拓扑信息；`seam.apply` 只接受匹配父状态的计划，生成新 UV 快照并要求重新烘焙。`seam.plan action=get|compare|select` 查看、比较或选取计划；select 返回该计划的父快照，不是覆盖现有结果。`[verified: seam_plans.py inspect_cut_topology/apply_seams/manage_plan]`

展开使用显式后端，默认 blender_lscm。LSCM、ABF++、Minimum Stretch 的实际可用性由本机 Blender 运行确认；缺失依赖不得静默换算法。局部重展开仍沿用原面角映射，重排后的真实颜色和 mip 要单独检查。`[opinion]`

## 评价边界

论文工具能力、项目改进、实际收益分开记录。现有实现的候选阈值、角亏损边界处理、离散路径与保留原 UV 策略均属于本项目选择；不直接套用论文的专业艺术家偏好、指标或速度结论。`[opinion]`

报告至少列出同父状态的原 UV、无共享展开、共享后结果；数量区分连通岛、独立图块和共享组。无反馈／有反馈、通用 Skill／项目 Skill、几何候选／PartField 使用独立消融；失败、依赖缺失和超时保留。实际贴图与导出模型作为结果证据，Skill 及测试通过不替代外观认可。`[opinion]`

## 本轮可核验进展

`tests/test_agent_tools_mesh_query.py` 覆盖稳定 ID、实际闭合立方体射线厚度、查询只读与执行中超时、状态／数据库篡改、跨旧 UV 候选，以及环面 genus_neck 的临时切割验证。索引 feature 表还保存 ring、curvature_ridge 和对称候选；找不到可定位颈环时记录 genus_unlocalized，不凭外形推断可切路径。`[verified: mesh_query.py index_mesh/_handle_candidate；本轮8项pytest通过]`

本机 PartField 已在12面立方体上实际推理，输出4个连通候选；记录模型加载／推理与聚类耗时7.823秒、峰值CUDA分配1,672,975,872字节。保留首次Windows读取WSL符号链接失败及修正后运行；这里只证明小型研究后端可执行，不是整龙或论文数据集复现。`[verified: ../artifacts/meshquery_development/partfield_smoke02/propose/partfield_labels.json；partfield_smoke01/result.json]`

SQL超过100万结果行明确拒绝，要求缩小查询范围；不会把截断的结果冒充完整选择。默认索引与几何候选均在CPU执行，只有显式选择PartField才调用研究GPU后端。`[verified: mesh_query.py query_rows；region_proposals.py backend分支]`

PartUV 顶层 Apache-2.0 与其 PartField 依赖的 NVIDIA License 分开记录。固定源码附带许可第3.3节将该依赖限制为非商业研究与教育；本轮只做独立研究对照，不据此宣布生产授权。权重路径、SHA256 与许可证证据见 [依赖记录](../artifacts/meshquery_v1/verification_and_dependencies.json)，checkpoint 的单独许可状态仍标记未独立确定。[上游许可](https://raw.githubusercontent.com/nv-tlabs/PartField/main/LICENSE)。`[verified: references/PartUV/LICENSE L209–238；上游 LICENSE §3.3]`
