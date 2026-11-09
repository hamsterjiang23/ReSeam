# 历史能量刀：45项指标

|指标|original|xatlas|optcuts|mirror_shared_final|mirror_unshared_final|来源|
|---|---|---|---|---|---|---|
|三角面数|500|500|500|700|700|`[verified: summary.json rows.values.faces]`|
|顶点数|252|252|252|434|434|`[verified: summary.json rows.values.vertices]`|
|输入／目标贴图边长 px|8,192|2,048|2,048|2,048|2,048|`[verified: summary.json rows.values.resolution]`|
|UV连通岛数 ↓|—|68|16|2|2|`[verified: summary.json rows.values.charts]`|
|独立图块数 ↓|—|68|16|1|2|`[verified: summary.json rows.values.tiles]`|
|声明共享组数|0|0|0|1|0|`[verified: summary.json rows.values.declared_groups]`|
|声明参与共享表面 ↑|0.00%|0.00%|0.00%|100.00%|0.00%|`[verified: summary.json rows.values.declared_shared_surface_fraction]`|
|复用几何核验完成|否|是|是|是|是|`[verified: summary.json rows.values.sharing_geometry_verified]`|
|实际参与共享表面 ↑|—|0.00%|0.00%|100.00%|0.00%|`[verified: summary.json rows.values.actual_shared_surface_fraction]`|
|有效重复UV占位节省 ↑|—|0.00%|0.00%|50.00%|0.00%|`[verified: summary.json rows.values.valid_duplicate_uv_area_saved_fraction]`|
|排布覆盖率／原始覆盖率 ↑|—|57.06%|55.31%|76.72%|52.52%|`[verified: summary.json rows.values.coverage]`|
|原生／最终单位方格覆盖 ↑|—|57.06%|55.31%|76.72%|52.52%|`[verified: summary.json rows.values.unit_square_coverage]`|
|UV三角面积之和|—|0.570623|0.553093|1.53438|0.525201|`[verified: summary.json rows.values.summed_uv_area]`|
|UV面积并集|—|0.570623|0.553093|0.767188|0.525201|`[verified: summary.json rows.values.raw_uv_union_area]`|
|单位方格外UV面积|—|1.11022e-16|0|0|0|`[verified: summary.json rows.values.uv_outside_unit_square_area]`|
|全部重叠率（含未许可）|—|0.00%|0.00%|50.00%|0.00%|`[verified: summary.json rows.values.all_overlap_fraction_not_validated_reuse]`|
|零UV面积面数|—|0|0|0|0|`[verified: summary.json rows.values.zero_uv_area_faces]`|
|岛内混合方向面数|—|0|0|0|0|`[verified: summary.json rows.values.mixed_orientation_faces]`|
|记录切缝边数|—|258|138|0|0|`[verified: summary.json rows.values.seam_edge_count]`|
|总面积口径密度 px/模型单位|—|2530.96|2491.78|4115.79|2407.97|`[verified: summary.json rows.values.aggregate_texel_density]`|
|AO显式切缝 ↑|—|0.508245|0.528108|—|—|`[verified: summary.json rows.values.paper.seam_ao]`|
|AO含原边界 ↑（附加范围）|—|0.508245|0.528108|0.577692|0.577692|`[verified: summary.json rows.values.paper.boundary_ao]`|
|Non-disk非圆盘岛比例 ↓|—|0.00%|0.00%|0.00%|0.00%|`[verified: summary.json rows.values.paper.non_disk_fraction]`|
|Compactness紧凑度 ↑|—|0.343449|0.43607|0.996584|0.996584|`[verified: summary.json rows.values.paper.compactness]`|
|Convexity凸性 ↑|—|0.894552|0.841405|1|1|`[verified: summary.json rows.values.paper.convexity]`|
|Jaggedness边界二阶差分 ↓|—|0.000358943|0.000387367|5.0306e-05|8.62038e-05|`[verified: summary.json rows.values.paper.jaggedness]`|
|Angular orthogonality正交度 ↑|—|0.981665|0.900001|0.256101|0.256101|`[verified: summary.json rows.values.paper.angular_quality]`|
|形状指标计入岛数|—|68|16|2|2|`[verified: summary.json rows.values.paper.shape_valid_charts]`|
|凸性指标计入岛数|—|68|16|2|2|`[verified: summary.json rows.values.paper.convexity_valid_charts]`|
|边界二阶差分采样点数|—|7,014|3,094|1,246|728|`[verified: summary.json rows.values.paper.jaggedness_samples]`|
|正交度计入面数|—|500|500|700|700|`[verified: summary.json rows.values.paper.angular_quality_faces]`|
|面积畸变D_area ↓|—|1.03661|1.07804|4.62835|4.62835|`[verified: summary.json rows.values.distortion.area_distortion_face_mean]`|
|面积畸变计入面数|—|500|500|700|700|`[verified: summary.json rows.values.distortion.area_distortion_included_faces]`|
|保角K（所有非退化方向）↓|—|1.03951|1.19717|11.8792|11.8792|`[verified: summary.json rows.values.distortion.conformal_mean_all_nondegenerate]`|
|论文保角K（仅正向）↓|—|1.03497|1.19717|11.8792|11.8792|`[verified: summary.json rows.values.distortion.conformal_mean_paper_positive_only]`|
|论文保角K计入面数|—|386|500|350|350|`[verified: summary.json rows.values.distortion.conformal_paper_included_faces]`|
|纹素分配离散度 octave ↓|—|0.0746024|0.123547|2.94354|2.94354|`[verified: summary.json rows.values.distortion.texel_spread_octaves]`|
|记录3D切缝长度 ↓（原表“新增”口径）|—|14.0337|6.63795|0|0|`[verified: summary.json rows.values.distortion.new_seam_length_3d]`|
|记录切缝长／表面积 ↓（原表口径）|—|37.5607|17.7662|0|0|`[verified: summary.json rows.values.distortion.new_seam_length_per_surface_area]`|
|UV边界长度|—|34.8946|15.4263|6.22055|3.63937|`[verified: summary.json rows.values.distortion.uv_boundary_length]`|
|颜色均值误差 ↓|—|0.00090574|0.000905883|0.000886028|0.00088703|`[verified: summary.json rows.values.color_mean]`|
|颜色P95误差 ↓|—|0.00227646|0.00228086|0.00220915|0.00219297|`[verified: summary.json rows.values.color_p95]`|
|颜色最大误差 ↓|—|0.0352301|0.0165323|0.0155158|0.0150294|`[verified: summary.json rows.values.color_max]`|
|最差视图均值误差 ↓|—|0.0155978|0.0155509|0.0247357|0.0330929|`[verified: summary.json rows.values.worst_render_mean]`|
|最差视图最大误差 ↓|—|0.235678|0.241256|0.323184|0.391844|`[verified: summary.json rows.values.worst_render_max]`|

原始与镜像为不同几何；共享/解除共享为同几何对照。原低模无UV项不适用，失败项仅保留原生UV。`[verified: summary.json rows.geometry_hash/layout_stage]`
原表“新增3D切缝”实际计算当前state.seams总长；本表改称“记录3D切缝”，不表示相对作者UV的净新增。`[verified: protocol.json new_seam_scope]`
— 的逐项原因及所有尺寸详见summary.json；历史数值拒绝状态保留。`[verified: summary.json rows.null_reasons/historical_quality_accepted]`
不进行跨几何或失败方法的自动质量排名。`[opinion]`
