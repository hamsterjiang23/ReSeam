# 历史幻想龙：45项指标

|指标|original|xatlas|optcuts|mirror_shared_final|mirror_unshared_final|来源|
|---|---|---|---|---|---|---|
|三角面数|2,000|2,000|2,000|2,062|2,062|`[verified: summary.json rows.values.faces]`|
|顶点数|996|996|996|1,092|1,092|`[verified: summary.json rows.values.vertices]`|
|输入／目标贴图边长 px|8,192|2,048|2,048|2,048|2,048|`[verified: summary.json rows.values.resolution]`|
|UV连通岛数 ↓|—|177|9|6|6|`[verified: summary.json rows.values.charts]`|
|独立图块数 ↓|—|—|9|3|6|`[verified: summary.json rows.values.tiles]`|
|声明共享组数|0|0|0|3|0|`[verified: summary.json rows.values.declared_groups]`|
|声明参与共享表面 ↑|0.00%|0.00%|0.00%|100.00%|0.00%|`[verified: summary.json rows.values.declared_shared_surface_fraction]`|
|复用几何核验完成|否|否|是|是|是|`[verified: summary.json rows.values.sharing_geometry_verified]`|
|实际参与共享表面 ↑|—|—|0.00%|100.00%|0.00%|`[verified: summary.json rows.values.actual_shared_surface_fraction]`|
|有效重复UV占位节省 ↑|—|—|0.00%|50.00%|0.00%|`[verified: summary.json rows.values.valid_duplicate_uv_area_saved_fraction]`|
|排布覆盖率／原始覆盖率 ↑|—|—|28.55%|20.40%|29.85%|`[verified: summary.json rows.values.coverage]`|
|原生／最终单位方格覆盖 ↑|—|45.09%|28.55%|20.40%|29.85%|`[verified: summary.json rows.values.unit_square_coverage]`|
|UV三角面积之和|—|0.45091|0.285476|0.40807|0.298462|`[verified: summary.json rows.values.summed_uv_area]`|
|UV面积并集|—|0.45091|0.285476|0.204035|0.298462|`[verified: summary.json rows.values.raw_uv_union_area]`|
|单位方格外UV面积|—|0|2.77556e-16|0|3.88578e-16|`[verified: summary.json rows.values.uv_outside_unit_square_area]`|
|全部重叠率（含未许可）|—|0.00%|0.00%|50.00%|0.00%|`[verified: summary.json rows.values.all_overlap_fraction_not_validated_reuse]`|
|零UV面积面数|—|2|0|0|0|`[verified: summary.json rows.values.zero_uv_area_faces]`|
|岛内混合方向面数|—|0|0|0|0|`[verified: summary.json rows.values.mixed_orientation_faces]`|
|记录切缝边数|—|787|334|300|300|`[verified: summary.json rows.values.seam_edge_count]`|
|总面积口径密度 px/模型单位|—|1910.63|1520.26|1813.71|1551.12|`[verified: summary.json rows.values.aggregate_texel_density]`|
|AO显式切缝 ↑|—|0.53639|0.503605|0.541187|0.541187|`[verified: summary.json rows.values.paper.seam_ao]`|
|AO含原边界 ↑（附加范围）|—|0.53639|0.503605|0.52615|0.52615|`[verified: summary.json rows.values.paper.boundary_ao]`|
|Non-disk非圆盘岛比例 ↓|—|0.56%|22.22%|0.00%|0.00%|`[verified: summary.json rows.values.paper.non_disk_fraction]`|
|Compactness紧凑度 ↑|—|0.335844|0.282847|0.289756|0.289756|`[verified: summary.json rows.values.paper.compactness]`|
|Convexity凸性 ↑|—|0.865949|0.720484|0.662783|0.662783|`[verified: summary.json rows.values.paper.convexity]`|
|Jaggedness边界二阶差分 ↓|—|0.000824531|0.00116094|0.000979966|0.00112009|`[verified: summary.json rows.values.paper.jaggedness]`|
|Angular orthogonality正交度 ↑|—|0.962711|0.891894|0.895916|0.889533|`[verified: summary.json rows.values.paper.angular_quality]`|
|形状指标计入岛数|—|175|9|6|6|`[verified: summary.json rows.values.paper.shape_valid_charts]`|
|凸性指标计入岛数|—|175|9|6|6|`[verified: summary.json rows.values.paper.convexity_valid_charts]`|
|边界二阶差分采样点数|—|9,158|2,651|3,052|2,610|`[verified: summary.json rows.values.paper.jaggedness_samples]`|
|正交度计入面数|—|1,998|2,000|2,062|2,062|`[verified: summary.json rows.values.paper.angular_quality_faces]`|
|面积畸变D_area ↓|—|1.11428|1.1039|1.10396|1.10396|`[verified: summary.json rows.values.distortion.area_distortion_face_mean]`|
|面积畸变计入面数|—|1,998|1,999|2,062|2,062|`[verified: summary.json rows.values.distortion.area_distortion_included_faces]`|
|保角K（所有非退化方向）↓|—|2.96689|1.23834|1.24929|1.24929|`[verified: summary.json rows.values.distortion.conformal_mean_all_nondegenerate]`|
|论文保角K（仅正向）↓|—|4.0278|1.23834|1.24929|1.24929|`[verified: summary.json rows.values.distortion.conformal_mean_paper_positive_only]`|
|论文保角K计入面数|—|1,261|1,999|1,031|1,031|`[verified: summary.json rows.values.distortion.conformal_paper_included_faces]`|
|纹素分配离散度 octave ↓|—|0.186707|0.145113|0.141695|0.141695|`[verified: summary.json rows.values.distortion.texel_spread_octaves]`|
|记录3D切缝长度 ↓（原表“新增”口径）|—|24.1447|9.23301|7.92803|7.92803|`[verified: summary.json rows.values.distortion.new_seam_length_3d]`|
|记录切缝长／表面积 ↓（原表口径）|—|46.6043|17.8216|15.2373|15.2373|`[verified: summary.json rows.values.distortion.new_seam_length_per_surface_area]`|
|UV边界长度|—|45.0424|13.2265|15.2469|13.0394|`[verified: summary.json rows.values.distortion.uv_boundary_length]`|
|颜色均值误差 ↓|—|—|0.00151788|0.00153483|0.00151753|`[verified: summary.json rows.values.color_mean]`|
|颜色P95误差 ↓|—|—|0.00383611|0.00388796|0.00383435|`[verified: summary.json rows.values.color_p95]`|
|颜色最大误差 ↓|—|—|0.140592|0.0561082|0.033529|`[verified: summary.json rows.values.color_max]`|
|最差视图均值误差 ↓|—|—|0.0325997|0.0289004|0.0320196|`[verified: summary.json rows.values.worst_render_mean]`|
|最差视图最大误差 ↓|—|—|0.412517|0.2681|0.307447|`[verified: summary.json rows.values.worst_render_max]`|

原始与镜像为不同几何；共享/解除共享为同几何对照。原低模无UV项不适用，失败项仅保留原生UV。`[verified: summary.json rows.geometry_hash/layout_stage]`
原表“新增3D切缝”实际计算当前state.seams总长；本表改称“记录3D切缝”，不表示相对作者UV的净新增。`[verified: protocol.json new_seam_scope]`
— 的逐项原因及所有尺寸详见summary.json；历史数值拒绝状态保留。`[verified: summary.json rows.null_reasons/historical_quality_accepted]`
不进行跨几何或失败方法的自动质量排名。`[opinion]`
