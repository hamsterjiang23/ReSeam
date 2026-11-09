# Chloe 红刺：45项指标

|指标|original|optcuts|mirror_shared_final|mirror_unshared_final|来源|
|---|---|---|---|---|---|
|三角面数|450|450|590|590|`[verified: summary.json rows.values.faces]`|
|顶点数|227|227|368|368|`[verified: summary.json rows.values.vertices]`|
|输入／目标贴图边长 px|256|256|256|256|`[verified: summary.json rows.values.resolution]`|
|UV连通岛数 ↓|21|3|2|2|`[verified: summary.json rows.values.charts]`|
|独立图块数 ↓|—|3|1|2|`[verified: summary.json rows.values.tiles]`|
|声明共享组数|0|0|1|0|`[verified: summary.json rows.values.declared_groups]`|
|声明参与共享表面 ↑|0.00%|0.00%|100.00%|0.00%|`[verified: summary.json rows.values.declared_shared_surface_fraction]`|
|复用几何核验完成|否|是|是|是|`[verified: summary.json rows.values.sharing_geometry_verified]`|
|实际参与共享表面 ↑|—|0.00%|100.00%|0.00%|`[verified: summary.json rows.values.actual_shared_surface_fraction]`|
|有效重复UV占位节省 ↑|—|0.00%|50.00%|0.00%|`[verified: summary.json rows.values.valid_duplicate_uv_area_saved_fraction]`|
|排布覆盖率／原始覆盖率 ↑|50.18%|26.75%|71.20%|47.01%|`[verified: summary.json rows.values.coverage]`|
|原生／最终单位方格覆盖 ↑|50.18%|26.75%|71.20%|47.01%|`[verified: summary.json rows.values.unit_square_coverage]`|
|UV三角面积之和|0.621988|0.267531|1.42391|0.47009|`[verified: summary.json rows.values.summed_uv_area]`|
|UV面积并集|0.501814|0.267531|0.711955|0.47009|`[verified: summary.json rows.values.raw_uv_union_area]`|
|单位方格外UV面积|0|0|0|0|`[verified: summary.json rows.values.uv_outside_unit_square_area]`|
|全部重叠率（含未许可）|19.32%|0.00%|50.00%|0.00%|`[verified: summary.json rows.values.all_overlap_fraction_not_validated_reuse]`|
|零UV面积面数|0|0|0|0|`[verified: summary.json rows.values.zero_uv_area_faces]`|
|岛内混合方向面数|1|0|0|0|`[verified: summary.json rows.values.mixed_orientation_faces]`|
|记录切缝边数|157|75|0|0|`[verified: summary.json rows.values.seam_edge_count]`|
|总面积口径密度 px/模型单位|288.988|189.529|452.022|259.722|`[verified: summary.json rows.values.aggregate_texel_density]`|
|AO显式切缝 ↑|0.54162|0.445767|—|—|`[verified: summary.json rows.values.paper.seam_ao]`|
|AO含原边界 ↑（附加范围）|0.54162|0.445767|0.548673|0.548673|`[verified: summary.json rows.values.paper.boundary_ao]`|
|Non-disk非圆盘岛比例 ↓|9.52%|0.00%|0.00%|0.00%|`[verified: summary.json rows.values.paper.non_disk_fraction]`|
|Compactness紧凑度 ↑|0.531774|0.349351|0.996513|0.996513|`[verified: summary.json rows.values.paper.compactness]`|
|Convexity凸性 ↑|0.907066|0.724353|1|1|`[verified: summary.json rows.values.paper.convexity]`|
|Jaggedness边界二阶差分 ↓|0.000294974|0.000571184|5.22411e-05|9.07775e-05|`[verified: summary.json rows.values.paper.jaggedness]`|
|Angular orthogonality正交度 ↑|0.845678|0.880739|0.387574|0.387574|`[verified: summary.json rows.values.paper.angular_quality]`|
|形状指标计入岛数|21|3|2|2|`[verified: summary.json rows.values.paper.shape_valid_charts]`|
|凸性指标计入岛数|21|3|2|2|`[verified: summary.json rows.values.paper.convexity_valid_charts]`|
|边界二阶差分采样点数|4,189|1,186|1,200|690|`[verified: summary.json rows.values.paper.jaggedness_samples]`|
|正交度计入面数|450|450|590|590|`[verified: summary.json rows.values.paper.angular_quality_faces]`|
|面积畸变D_area ↓|1.72422|1.10901|3.71129|3.71129|`[verified: summary.json rows.values.distortion.area_distortion_face_mean]`|
|面积畸变计入面数|450|450|590|590|`[verified: summary.json rows.values.distortion.area_distortion_included_faces]`|
|保角K（所有非退化方向）↓|2.6853|1.23523|9.96828|9.96828|`[verified: summary.json rows.values.distortion.conformal_mean_all_nondegenerate]`|
|论文保角K（仅正向）↓|3.19809|1.23523|9.96828|9.96828|`[verified: summary.json rows.values.distortion.conformal_mean_paper_positive_only]`|
|论文保角K计入面数|280|450|295|295|`[verified: summary.json rows.values.distortion.conformal_paper_included_faces]`|
|纹素分配离散度 octave ↓|0.773549|0.140266|2.85687|2.85687|`[verified: summary.json rows.values.distortion.texel_spread_octaves]`|
|记录3D切缝长度 ↓（原表“新增”口径）|9.19783|4.29917|0|0|`[verified: summary.json rows.values.distortion.new_seam_length_3d]`|
|记录切缝长／表面积 ↓（原表口径）|18.8444|8.80808|0|0|`[verified: summary.json rows.values.distortion.new_seam_length_per_surface_area]`|
|UV边界长度|20.8746|5.91858|5.99266|3.44325|`[verified: summary.json rows.values.distortion.uv_boundary_length]`|
|颜色均值误差 ↓|—|0.000839163|0.000790218|0.000771107|`[verified: summary.json rows.values.color_mean]`|
|颜色P95误差 ↓|—|0.00223244|0.00230227|0.0021631|`[verified: summary.json rows.values.color_p95]`|
|颜色最大误差 ↓|—|0.0393748|0.0576416|0.0448429|`[verified: summary.json rows.values.color_max]`|
|最差视图均值误差 ↓|—|0.0925398|0.0385563|0.0581259|`[verified: summary.json rows.values.worst_render_mean]`|
|最差视图最大误差 ↓|—|0.919318|0.656559|0.964555|`[verified: summary.json rows.values.worst_render_max]`|

原始与镜像为不同几何；共享/解除共享为同几何对照。原低模无UV项不适用，失败项仅保留原生UV。`[verified: summary.json rows.geometry_hash/layout_stage]`
原表“新增3D切缝”实际计算当前state.seams总长；本表改称“记录3D切缝”，不表示相对作者UV的净新增。`[verified: protocol.json new_seam_scope]`
— 的逐项原因及所有尺寸详见summary.json；历史数值拒绝状态保留。`[verified: summary.json rows.null_reasons/historical_quality_accepted]`
不进行跨几何或失败方法的自动质量排名。`[opinion]`
