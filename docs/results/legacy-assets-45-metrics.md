# 三模型45项UV指标补测

本轮对原始参考及36个方法／尺寸条目补测，共39行、45项字段；未生成新UV、贴图或数值质量结果。`[verified: summary.json row_count/new_bakes/new_quality_scores]`

[全部尺寸CSV](../assets/legacy-assets-reseam/metrics-round02/all-metrics.csv) · [原始数值与缺失原因](../assets/legacy-assets-reseam/metrics-round02/summary.json) · [锁定协议](../assets/legacy-assets-reseam/metrics-round02/protocol.json)

## Chloe 红刺

|指标|原始参考|OptCuts 无共享|ReSeam 镜像共享|同镜像几何解除共享|来源|
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

[本模型CSV](../assets/legacy-assets-reseam/metrics-round02/chloe-comparison.csv)。`[verified: 对应CSV与summary.json]`

## 历史能量刀

|指标|原始参考|xatlas|OptCuts 无共享|ReSeam 镜像共享|同镜像几何解除共享|来源|
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

[本模型CSV](../assets/legacy-assets-reseam/metrics-round02/energy_blade-comparison.csv)。`[verified: 对应CSV与summary.json]`

## 历史幻想龙

|指标|原始参考|xatlas|OptCuts 无共享|ReSeam 镜像共享|同镜像几何解除共享|来源|
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

[本模型CSV](../assets/legacy-assets-reseam/metrics-round02/dragon-comparison.csv)。`[verified: 对应CSV与summary.json]`

## 指标解释与边界

原低模无UV时，UV面积、畸变、密度等不适用；8K只表示高模颜色来源的图像边长。失败的xatlas原生UV可算形状指标，其最终排布覆盖和颜色重建误差保持空值。`[verified: summary.json rows.layout_stage/null_reasons]`

旧表的“新增3D切缝”按state.seams总长度计算，原始作者UV也可能非零。本轮名称修正为“记录3D切缝”，公式和旧字段名保留，不将它冒称相对原UV的净新增。`[verified: protocol.json new_seam_scope；baseline/report.py distortion_metrics]`

原始与镜像的几何不同；共享与解除共享使用同镜像几何。表内保留所有数值，不据此进行跨几何质量排名。`[verified: summary.json rows.geometry_hash；opinion: 比较边界]`

AO是固定种子的采样比较分数；无显式切缝时为不适用，不能填成0。Jaggedness使用原始UV单位，面积畸变逐面倍率截10，K同时报告全方向和仅正向值与计入面数。`[verified: protocol.json；summary.json rows.metrics.paper/distortion]`

Chloe与能量刀当前圆盘布局的全方向K约为9.97、11.88，面积畸变约3.71、4.63；相同镜像几何解除共享后这些数值基本不变。圆盘紧凑度接近1，同时保留较高畸变。`[verified: summary.json 对应共享/解除共享行values]`

旧的外观预览判断和数值拒绝记录保持原样；新增指标不将它们升级为工业质量或同质量缩图通过。`[verified: summary.json all_historical_quality_states_preserved；opinion: 验收范围]`
