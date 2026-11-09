# 完成龙：MeshQuery 全基线对照

同一份已完成镜像龙的11338面几何与冻结2048 BaseColor参考；各原生算法输出从独立输入派生。`[verified: measurements.json input；各原生输入SHA256]`

各方法原理、实际运行入口与原生结果/资产处理的分工见[Baseline介绍](../dragon-baselines-overview.md)。`[verified: dragon-baselines-overview.md各方法Research Track与Asset Track章节]`

下方两图新增最左侧原始Tripo输入列：10832面、121岛、121图块、8192贴图、72.55%覆盖。其后八条路径仍使用同一完成镜像龙几何；原始列只作跨几何来源参考，不加入本轮质量排名。原始模型补充相同正视棋盘格与35°/15°斜视原色图，不重新求解或重烘焙。`[verified: with-original-input/comparison-evidence.json original、cameras；original/*_request.json]`

![原始输入、UV与棋盘格](../assets/dragon-paper-baselines/with-original-input/uv-and-checker-comparison.png)

上排为相同正视棋盘格，红线是实际切缝叠加；下排为UV。流程拒绝项展示完整原生坐标域并标记未完成排布，不与已排图集覆盖率混排。颜色对照图关闭切缝叠加，使用相同斜视镜头。`[verified: 各view.render请求；native-domain-full.png；clean_color_views请求]`

<!-- ALL_METRICS_START -->
全部69项指标合为一张表：指标为行、原始输入及八条路径为列。`[verified: all-metrics.json metrics、methods]`

来源简称：M=[measurements.json](../assets/dragon-paper-baselines/measurements.json)；V=[verification.json](../assets/dragon-paper-baselines/verification.json)；O=[comparison-evidence.json](../assets/dragon-paper-baselines/with-original-input/comparison-evidence.json)；C=[cost-summary.json](../assets/dragon-paper-baselines/cost-summary.json)；P=[paper-metrics.json](../assets/dragon-paper-baselines/with-original-input/paper-metrics.json)。“—”表示未测、未核验或不适用，均不作零处理。†原始输入是跨几何参考；*MeshQuery是项目实现。`[verified: 对应JSON；opinion: 展示约定]`

**粗体表示已报告数值中的最优，并列值按显示精度全部加粗。** 原始输入与失败项均参与数值比较，这不等于有效结果排名或最终质量通过。面数、顶点数、分辨率、共享组数、面积统计、密度与样本数等没有统一最佳方向的统计项不强行选优；全部重叠率包含合法与非法重叠，不设单一优劣方向。只有一项或没有数值的比较不标最佳；布尔验收只加粗通过项。CSV保持纯数据。`[opinion]`

|指标|原始输入†|FlattenAnything|OptCuts|xatlas|Smart UV|PartUV|MeshQuery*|ReSeam共享|ReSeam解除共享|来源|
|---|---|---|---|---|---|---|---|---|---|---|
|三角面数|10,832|11,338|11,338|11,338|11,338|11,338|11,338|11,338|11,338|`[verified: M.rows.faces; O.original.faces]`|
|顶点数|6,552|7,120|7,120|7,120|7,120|7,120|7,120|7,120|7,120|`[verified: M.rows.vertices; P.rows.original_tripo.base_metrics.vertices]`|
|输入/目标边长 px|8,192|2,048|2,048|2,048|2,048|2,048|2,048|2,048|2,048|`[verified: M.rows.resolution; O.texture_size[0]]`|
|UV连通岛数|121|**80**|**80**|643|931|1,115|**80**|**80**|**80**|`[verified: M.rows.charts; O.original.charts]`|
|独立图块数|121|—|80|—|931|1,115|80|**40**|80|`[verified: M.rows.tiles; O.original.independent_tiles]`|
|声明共享组数|0|0|0|0|0|0|0|40|0|`[verified: M.rows.declared_groups; O.original.groups]`|
|声明参与共享表面|0.00%|0.00%|0.00%|0.00%|0.00%|0.00%|0.00%|**100.00%**|0.00%|`[verified: M.rows.declared_shared_surface_fraction; O.original.authorized_shared_surface_fraction]`|
|复用几何核验完成|—|否|**是**|否|**是**|**是**|**是**|**是**|**是**|`[verified: M.rows.sharing_geometry_verified]`|
|实际参与共享表面|—|—|0.00%|—|0.00%|0.00%|0.00%|**100.00%**|0.00%|`[verified: M.rows.actual_shared_surface_fraction]`|
|有效重复UV占位节省|—|—|0.00%|—|0.00%|0.00%|0.00%|**50.00%**|0.00%|`[verified: M.rows.valid_duplicate_uv_area_saved_fraction]`|
|有效复用倍数|—|—|1|—|1|1|1|**2**|1|`[verified: M.rows.valid_uv_reuse_factor]`|
|正式排布覆盖率|**72.55%**|—|63.30%|—|38.44%|40.09%|62.72%|62.93%|62.10%|`[verified: M.rows.coverage; O.original.coverage]`|
|原生/最终单位方格覆盖|**72.55%**|50.84%|63.30%|55.83%|38.44%|40.09%|62.72%|62.93%|62.10%|`[verified: M.rows.unit_square_coverage; O.original.coverage]`|
|UV三角面积之和|0.725575|4.64533|0.633018|0.558324|0.384414|0.400888|0.62716|1.25873|0.621011|`[verified: M.rows.summed_uv_area; P.rows.original_tripo.base_metrics.summed_uv_area]`|
|UV面积并集|0.725486|1.75058|0.633018|0.558324|0.384388|0.400888|0.62716|0.629308|0.620953|`[verified: M.rows.raw_uv_union_area; P.rows.original_tripo.base_metrics.raw_uv_union_area]`|
|单位方格外UV面积|**0**|1.24213|**0**|**0**|**0**|**0**|**0**|**0**|**0**|`[verified: M.rows.uv_outside_unit_square_area; P.rows.original_tripo.base_metrics.uv_outside_unit_square_area]`|
|全部重叠率（含未许可）|0.01%|62.32%|0.00%|0.00%|0.01%|0.00%|0.00%|50.00%|0.01%|`[verified: M.rows.all_overlap_fraction_not_validated_reuse; P.rows.original_tripo.base_metrics.all_overlap_fraction_not_validated_reuse]`|
|零UV面积面数|**0**|110|**0**|112|**0**|**0**|**0**|**0**|**0**|`[verified: M.rows.zero_uv_area_faces; P.rows.original_tripo.base_metrics.zero_uv_area_faces]`|
|岛内混合方向面数|**0**|3,208|**0**|**0**|**0**|**0**|**0**|**0**|**0**|`[verified: M.rows.mixed_orientation_faces; P.rows.original_tripo.base_metrics.mixed_orientation_faces]`|
|记录切缝边数|410|**0**|147|2,300|3,065|2,503|20|102|102|`[verified: M.rows.seam_edge_count; P.rows.original_tripo.base_metrics.seam_edge_count]`|
|总面积口径密度 px/模型单位|5703.75|3611.06|1333.01|1251.9|1038.79|1060.81|1326.83|1879.72|1320.31|`[verified: M.rows.aggregate_texel_density; P.rows.original_tripo.base_metrics.aggregate_texel_density]`|
|AO显式切缝 ↑|0.592949|—|**0.667242**|0.480523|0.49184|0.444804|0.546212|0.293837|0.293837|`[verified: P.rows.paper_metrics.seam_ao]`|
|AO含原边界 ↑（附加范围）|0.67365|**0.692708**|0.683539|0.58199|0.58604|0.551423|0.684591|0.676314|0.676314|`[verified: P.rows.paper_metrics.boundary_ao]`|
|Non-disk非圆盘岛比例 ↓|2.48%|5.00%|2.50%|0.47%|0.54%|0.09%|**0.00%**|5.00%|5.00%|`[verified: P.rows.paper_metrics.non_disk_fraction]`|
|Compactness紧凑度 ↑|0.595387|**0.638236**|0.554509|0.439199|0.414399|0.373414|0.570268|0.552364|0.552364|`[verified: P.rows.paper_metrics.compactness]`|
|Convexity凸性 ↑|0.890907|0.917431|0.880644|0.887093|0.901666|**0.951874**|0.865193|0.886384|0.886384|`[verified: P.rows.paper_metrics.convexity]`|
|Jaggedness边界二阶差分 ↓|0.000893332|**0.000512769**|0.000979739|0.00136264|0.00167745|0.00157929|0.000924399|0.000696659|0.000962787|`[verified: P.rows.paper_metrics.jaggedness]`|
|Angular orthogonality正交度 ↑|0.952124|0.499381|0.908237|**0.973591**|0.880921|0.945098|0.780143|0.929493|0.929493|`[verified: P.rows.paper_metrics.angular_quality]`|
|形状指标计入岛数|121|80|80|531|931|1,115|80|80|80|`[verified: P.rows.paper_metrics.shape_valid_charts]`|
|凸性指标计入岛数|121|80|80|531|931|1,115|80|80|80|`[verified: P.rows.paper_metrics.convexity_valid_charts]`|
|无法闭合为环的边界分量|**0**|**0**|**0**|**0**|**0**|**0**|**0**|**0**|**0**|`[verified: P.rows.paper_metrics.invalid_boundary_components]`|
|边界二阶差分采样点数|7,799|14,073|6,342|16,011|17,585|17,249|6,038|8,744|6,154|`[verified: P.rows.paper_metrics.jaggedness_samples]`|
|正交度计入面数|10,832|11,228|11,338|11,226|11,338|11,338|11,338|11,338|11,338|`[verified: P.rows.paper_metrics.angular_quality_faces]`|
|面积畸变D_area ↓|1.34933|4.29094|1.1903|1.11204|1.09542|**1.09478**|1.62156|1.70903|1.70903|`[verified: M.rows.distortion.area_distortion_face_mean; P.rows.original_tripo.base_metrics.distortion.area_distortion_face_mean]`|
|面积畸变计入面数|10,832|11,228|11,328|11,226|11,328|11,328|11,328|11,328|11,328|`[verified: M.rows.distortion.area_distortion_included_faces; P.rows.original_tripo.base_metrics.distortion.area_distortion_included_faces]`|
|保角K（所有非退化方向）↓|**1.0846**|16.2998|1.49691|1.52979|1.23136|3.85253|4.17535|1.15205|1.15205|`[verified: M.rows.distortion.conformal_mean_all_nondegenerate; P.rows.original_tripo.base_metrics.distortion.conformal_mean_all_nondegenerate]`|
|论文保角K（仅正向）↓|**1.0846**|64.8663|1.49691|1.56659|1.23136|3.85253|4.17535|1.15205|1.15205|`[verified: M.rows.distortion.conformal_mean_paper_positive_only; P.rows.original_tripo.base_metrics.distortion.conformal_mean_paper_positive_only]`|
|论文保角K计入面数|10,831|1,410|11,328|6,084|11,328|11,328|11,328|5,664|5,664|`[verified: M.rows.distortion.conformal_paper_included_faces; P.rows.original_tripo.base_metrics.distortion.conformal_paper_included_faces]`|
|纹素分配离散度 octave ↓|0.516874|1.78607|0.163681|0.178737|**0.152672**|0.154268|0.446409|1.11354|1.11354|`[verified: M.rows.distortion.texel_spread_octaves; P.rows.original_tripo.base_metrics.distortion.texel_spread_octaves]`|
|新增3D切缝长度|6.8857|**0**|2.59564|43.8394|57.7072|55.5798|0.430004|1.38788|1.38788|`[verified: M.rows.distortion.new_seam_length_3d; P.rows.original_tripo.base_metrics.distortion.new_seam_length_3d]`|
|新增切缝长/表面积|4.60052|**0**|1.73715|29.3398|38.6209|37.1971|0.287783|0.928846|0.928846|`[verified: M.rows.distortion.new_seam_length_per_surface_area; P.rows.original_tripo.base_metrics.distortion.new_seam_length_per_surface_area]`|
|UV边界长度|38.6642|70.1808|31.5128|77.2215|80.2496|73.1549|**30.0159**|43.4739|30.536|`[verified: M.rows.distortion.uv_boundary_length; P.rows.original_tripo.base_metrics.distortion.uv_boundary_length]`|
|颜色均值误差 ↓|—|—|0.00194834|—|0.00208756|0.00235807|0.00194283|0.00195983|**0.00193977**|`[verified: M.rows.color_mean]`|
|颜色P95误差 ↓|—|—|0.0043982|—|0.00444922|0.00454395|0.00436457|0.00448448|**0.00434282**|`[verified: M.rows.color_p95]`|
|颜色最大误差 ↓|—|—|0.0984025|—|0.344088|0.41048|0.0503255|**0.0302737**|0.106192|`[verified: M.rows.color_max]`|
|最差视图均值误差 ↓|—|—|0.00644238|—|0.00753624|0.00749349|0.00663304|**0.0063268**|0.00819809|`[verified: M.rows.worst_render_mean]`|
|最差视图最大误差 ↓|—|—|**0.166028**|—|0.192477|0.199685|0.166734|0.362178|0.410023|`[verified: M.rows.worst_render_max]`|
|严格几何通过|—|—|**是**|—|否|否|**是**|否|否|`[verified: M.rows.strict_geometry_valid]`|
|颜色通过|—|—|**是**|—|否|否|**是**|**是**|否|`[verified: M.rows.material_valid]`|
|接缝通过|—|—|**是**|—|否|否|**是**|**是**|**是**|`[verified: M.rows.seams_valid]`|
|渲染通过|—|—|**是**|—|**是**|**是**|**是**|否|否|`[verified: M.rows.render_valid]`|
|综合质量通过|—|—|**是**|—|否|否|**是**|否|否|`[verified: M.rows.quality_accepted]`|
|已写出完整mip层数|—|—|12|—|12|12|12|12|12|`[verified: V.models_reimported.full_mips]`|
|RGBA8完整mip字节|—|—|**22,369,620**|—|**22,369,620**|**22,369,620**|**22,369,620**|**22,369,620**|**22,369,620**|`[verified: M.rows.payload_bytes]`|
|PNG完整mip文件字节|—|—|4,978,856|—|5,066,952|4,979,961|4,936,130|**4,139,630**|4,866,471|`[verified: M.rows.png_mip_bytes]`|
|导出GLB文件字节|—|—|4,083,832|—|3,925,604|3,888,412|4,058,992|**3,519,784**|4,016,996|`[verified: M.rows.glb_bytes]`|
|原生算法工具耗时 s|—|1083.14|196.172|**1.563**|1.86|52.61|—|—|—|`[verified: M.rows.native_cost_seconds]`|
|独立规划工具耗时 s|—|—|—|—|—|—|146.922|—|—|`[verified: M.rows.planner_tool_cost_seconds]`|
|共同评估工具耗时 s|—|108.735|279.579|**5.578**|297.172|436.375|302.75|289.204|283.921|`[verified: M.rows.evaluation_cost_seconds]`|
|LLM思考耗时 s|—|—|—|—|—|—|—|—|—|`[verified: C.llm_reasoning_seconds]`|
|LLM token数|—|—|—|—|—|—|—|—|—|`[verified: C.llm_tokens]`|
|LLM费用|—|—|—|—|—|—|—|—|—|`[verified: C.llm_fee]`|
|本轮附加指标评估耗时 s|20.406|13.75|13.86|13.109|13.391|12.203|12.984|9.969|**0.468**|`[verified: P.rows.seconds]`|
|显式切缝AO状态|OK|NOT_APPLICABLE_NO_EXPLICIT_CUTS|OK|OK|OK|OK|OK|OK|OK|`[verified: P.rows.paper_metrics.seam_ao_status]`|
|含原边界AO状态|OK|OK|OK|OK|OK|OK|OK|OK|OK|`[verified: P.rows.paper_metrics.boundary_ao_status]`|
|原生算法状态|—|OK|OK|OK|OK|OK|PLANNED¹|PRECOMPUTED|PRECOMPUTED|`[verified: M.rows.algorithm_status]`|
|共同排布状态|—|REJECTED|OK|REJECTED|OK|OK|OK|OK|OK|`[verified: M.rows.pack_status]`|
|排布拒绝原因|—|PACKED_GEOMETRY_REJECTED|—|DEGENERATE_PACKING_GROUP|—|—|—|—|—|`[verified: M.rows.pack_reason]`|
|导出执行状态|—|NOT_RUN|OK|NOT_RUN|OK|OK|OK|OK|OK|`[verified: M.rows.export_status]`|

原始列几何、畸变与论文形状指标已从原始快照补算；覆盖率72.55%沿用原始UV测量。本轮ReSeam共同排布为62.93%，历史精调65.08%不混入。颜色/渲染误差相对完成龙的冻结2K参考，原始8K未按本轮协议验收。`[verified: P.rows.original_tripo、M.input、M.rows.reseam_shared、O.original]`

论文指标按A.6公开公式做项目实现。AO为1000个切缝点与1000个全边参考点、每点8条余弦半球射线，seed17；显式切缝与含原边界分别列出，无显式切缝时前者不适用。Jaggedness为原始UV单位的二阶差分，步长0.005、8—4096点，未套用论文表1未声明的显示倍率；跳过边界与有效样本数均保留。详见paper-metrics-protocol.json。`[verified: P.protocol、P.rows.paper_metrics]`

这是单龙评估，原始跨几何输入及失败未排布布局继续作为参考；不与论文50模型均值直接排名。Jaggedness受UV尺度及重采样设置影响，不能将原生失败布局的较小数值当作更优合法展开。`[verified: M.input、M.rows.uv_layout_stage、P.protocol；opinion: 比较边界]`

¹MeshQuery顶层algorithm_status遗留PLANNED，本表保留原始值；实际规划调用与排布/验收/导出记录均已完成，不能仅凭该遗留字段解释为未运行。`[verified: M.rows.meshquery_project algorithm_status、planner_tool_cost_seconds、pack_status、quality_accepted、export_status]`

PRECOMPUTED表示沿用既有ReSeam布局，历史制作成本未知；MeshQuery规划146.922秒与共同评估302.750秒分别列出。PLANNED、OK等为执行状态，不能代替质量结果。`[verified: M.rows对应字段]`

全部重叠率含未许可自交，不计作有效复用；切缝、边界长度与密度使用模型/UV单位。PNG/GLB为实际文件字节，RGBA8完整mip为纹理载荷，均不是整个工程包体。六份烘焙均2048、12级mip、22369620字节，本轮没有同质量缩图对照。`[verified: M.rows、V.models_reimported；opinion: 口径解释]`
<!-- ALL_METRICS_END -->

共享表面比例计入共享组的两侧；整龙左右共享可为100%，对应重复UV面积节省约50%、复用倍数约2。三者均不是同质量内存节省。无有效共享组的基线声明共享率为0；若合法性失败，实测共享留空，不把自交当复用。`[verified: measurements.json共享与拒绝字段；opinion: 指标解释]`

![UV复用率对比](../assets/dragon-paper-baselines/reuse-rate-comparison.png)

![原始输入、实际颜色与贴图](../assets/dragon-paper-baselines/with-original-input/basecolor-comparison.png)

原八列图与全部测量文件保留；新增图的素材、视图请求、独立清单与复现说明见[原始输入对照包](../assets/dragon-paper-baselines/with-original-input/README.md)。`[verified: with-original-input/manifest.json；comparison-evidence.json baseline_frozen_files_unchanged]`

MeshQuery未找到官方公开代码，使用现有ReSeam工具的项目实现：当前Agent观察几何，预览接缝计划，依据4个环带的拓扑拒绝反馈添加测地连接，再由Minimum Stretch展开。此路径不等同论文作者Claude/PartField/Blender5.1.2配置，也不作为作者结果。`[verified: baseline/MeshQuery/README.md；本轮meshquery计划与调用；opinion: 结果边界]`

FlattenAnything直接调用官方train_fam与模型，按公开mesh_verts测试思路在原始顶点推理UV；其点式神经映射到传统三角UV的转换并非MeshQuery作者公开实现，未发明额外切缝修补算法来伪造合法结果。原生点映射、权重、混合方向和后续拒绝均保留。`[verified: baseline/adapters/flatten_anything.py；FlattenAnything官方test_mesh.py；measurements.json native_settings]`

原生算法设置按论文附录A.4；所有方法后续统一使用本机Blender4.5.11轮廓排布、8px间距/边界、8px烘焙padding与LOD0/1/2。同论文.001间距存在明确差异。本例输入为完成的镜像三角网格，不能代表论文四边形数据集的排名或泛化。`[verified: protocol.json；各request.json；opinion: 适用范围]`

ReSeam共享与解除共享使用同一已完成布局，仅去掉共享关系后重排；历史制作成本不是0，也未计作本轮从零优化时间。源码优先官方，集中在baseline/，版本与许可见sources.json；开发失败、依赖适配和完整调用成本保留。`[verified: baseline/sources.json；各native_cache.json与receipt.json；opinion: 成本归因]`

为展示实际外观，所有路径使用相同规则：可保留各自原生输出中已有、未改变局部布局的冲突进行诊断重采样；新冲突仍检查。此策略不表示这些原生冲突符合严格几何协议，strict_geometry_valid、最终质量及失败原因另外保留。共享率使用声明的几何检查口径，不自动等于材质通过或人工接受。`[verified: 各请求geometry_profile.preserve_source_uv；measurements.json严格几何及质量字段；opinion: 解释边界]`

完整数字与产物索引：[measurements.json](../assets/dragon-paper-baselines/measurements.json)。人工艺术认可、完整PBR、GPU材质验证均未自动通过。`[verified: measurements.json各状态字段]`



面积畸变采用论文A.6的全局面积比归一化、10封顶和保留面普通平均；K同时给出原文正方向筛选和镜像友好的所有非退化方向。被剔除的退化/反向面数量保留在JSON，不能因有限平均较低而把非法候选算通过。纹素分配离散度按3D面积加权。切缝统计使用稳定原边；UV边界在共享两侧分别计数，不能将其当唯一图块占位。`[verified: report.py distortion_metrics；论文A.6；opinion: 解释边界]`


成本为实际工具调用，包含已记录的失败；MeshQuery规划调用另列planner_tool_cost_seconds，LLM思考时间/token/费用未知，不能写为0。重用原生输出的调用成本按原记录计入。`[verified: measurements.json成本字段、native_cache.json、receipt.json]`

开发轮冒烟、修正与失败另汇总在[cost-summary.json](../assets/dragon-paper-baselines/cost-summary.json)，不把已计入的5项原生算法重复相加。源码获取/安装、编程和LLM思考耗时未完整测量，留空；这些不是零成本。`[verified: cost-summary.json]`

复现：先按baseline/sources.json准备源码与runtime配置，再运行 `uv run python baseline/replay.py --input docs/assets/dragon-paper-baselines/input --output artifacts/dragon_paper_baselines/replay01` 重建本机路径的不可变父快照，然后使用baseline/README.md中的各阶段命令。`[verified: baseline/replay.py；opinion: 复现顺序]`

|方法|UV|真实颜色预览|实际BaseColor|模型|
|---|---|---|---|---|
|FlattenAnything|[uv](../assets/dragon-paper-baselines/flatten_anything\uv.png)|未产生|未产生|未产生 `[verified: manifest.json；measurements.json rows.flatten_anything.delivery_artifacts]`|
|OptCuts|[uv](../assets/dragon-paper-baselines/optcuts\uv.png)|[color](../assets/dragon-paper-baselines/optcuts\color.png)|[texture](../assets/dragon-paper-baselines/optcuts\texture.png)|[model](../assets/dragon-paper-baselines/optcuts\model.glb) `[verified: manifest.json；measurements.json rows.optcuts.delivery_artifacts]`|
|xatlas|[uv](../assets/dragon-paper-baselines/xatlas\uv.png)|未产生|未产生|未产生 `[verified: manifest.json；measurements.json rows.xatlas.delivery_artifacts]`|
|Blender Smart UV|[uv](../assets/dragon-paper-baselines/blender_smart\uv.png)|[color](../assets/dragon-paper-baselines/blender_smart\color.png)|[texture](../assets/dragon-paper-baselines/blender_smart\texture.png)|[model](../assets/dragon-paper-baselines/blender_smart\model.glb) `[verified: manifest.json；measurements.json rows.blender_smart.delivery_artifacts]`|
|PartUV|[uv](../assets/dragon-paper-baselines/partuv\uv.png)|[color](../assets/dragon-paper-baselines/partuv\color.png)|[texture](../assets/dragon-paper-baselines/partuv\texture.png)|[model](../assets/dragon-paper-baselines/partuv\model.glb) `[verified: manifest.json；measurements.json rows.partuv.delivery_artifacts]`|
|MeshQuery 项目实现|[uv](../assets/dragon-paper-baselines/meshquery_project\uv.png)|[color](../assets/dragon-paper-baselines/meshquery_project\color.png)|[texture](../assets/dragon-paper-baselines/meshquery_project\texture.png)|[model](../assets/dragon-paper-baselines/meshquery_project\model.glb) `[verified: manifest.json；measurements.json rows.meshquery_project.delivery_artifacts]`|
|ReSeam 共享|[uv](../assets/dragon-paper-baselines/reseam_shared\uv.png)|[color](../assets/dragon-paper-baselines/reseam_shared\color.png)|[texture](../assets/dragon-paper-baselines/reseam_shared\texture.png)|[model](../assets/dragon-paper-baselines/reseam_shared\model.glb) `[verified: manifest.json；measurements.json rows.reseam_shared.delivery_artifacts]`|
|ReSeam 解除共享|[uv](../assets/dragon-paper-baselines/reseam_unshared\uv.png)|[color](../assets/dragon-paper-baselines/reseam_unshared\color.png)|[texture](../assets/dragon-paper-baselines/reseam_unshared\texture.png)|[model](../assets/dragon-paper-baselines/reseam_unshared\model.glb) `[verified: manifest.json；measurements.json rows.reseam_unshared.delivery_artifacts]`|

本轮固定几何有80个按原边连接的表面分量；OptCuts和MeshQuery项目实现也达到80岛。共享版的区别在40个实际排布图块，而非宣称比所有基线更少连通岛。这里比较的是原生展开管线；未给其它基线额外添加本项目镜像堆叠阶段，0%不证明这些后端理论上不能配合共享工具。`[verified: meshquery/natural_plan/plan.json chart_count；measurements.json；opinion: 比较边界]`

共享版本轮颜色和接缝通过、严格渲染拒绝。当前共同排布种子0得到62.93%覆盖，区别于历史单独搜索种子29的65.08%；历史最好布局保留，不混入共同排布对照。本轮所有成功烘焙仍是2048 RGBA8及完整mip22369620字节，未做同质量缩图搜索，不声称实测节省运行时纹理内存。`[verified: measurements.json；dragon-midplane-cleanup.md历史排布记录；opinion: 结论边界]`
