# 整半模型镜像变体：UV共享与排布

|选定产物|可直接检查的模型|实际贴图|接受范围|
|---|---|---|---|
|2048紧凑主结果|[GLB](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/export/asset.glb)|[BaseColor](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/bake/texture/BaseColor_mip0.png)|声明范围BaseColor质量通过；Agent有限视图接受 `[verified: appearance_review.json selected_quality_result/cases]`|
|1024保守留边预览|[GLB](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/export/asset.glb)|[BaseColor](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/bake/texture/BaseColor_mip0.png)|Agent有限视图接受；数值渲染门禁拒绝 `[verified: appearance_review.json selected_small_preview/cases]`|

1024预览的两个LOD渲染探针最大误差仍为0.201654与0.212479，超过0.2阈值；不声称同质量最小存储已经证实。人工美术、GPU与完整PBR均未接受；两个固定UV缩留边尝试不选为交付，512不推荐近景。`[verified: shared_compact_1024/validate/validation.json render.views/profile.thresholds；appearance_review.json selection_reason/same_quality_minimum_storage_claim/human_accepted/full_pbr_passed]`

[可重建汇总JSON](../../artifacts/meshquery_v1/whole_half_report01/report.json) · [只读实验结果的汇总脚本](../../artifacts/meshquery_v1/whole_half_report01/build_report.py)

本轮先保留原模型正X半侧、沿X=0裁切并镜像，建立独立的新父几何；共享与无共享都从该父几何派生。原始121岛模型是另一几何版本，不能把121与本轮134的差异当固定几何UV收益。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

来源 10832 面→新变体 11450 面；保留侧与镜像侧各 5725 面，新几何修改标记 `True`，原资产修改标记 `False`。[变体与哈希](../../artifacts/meshquery_v1/whole_half01/create/mirror_variant.json) · [逐面角来源对应](../../artifacts/meshquery_v1/whole_half01/create/source_correspondence.npz)。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
中线采用独立的原始身份，不按坐标自动焊接；镜像角顺序为[0,2,1]。此变体提供明确来源的对称几何，不等于已经完成人工拓扑交付。`[verified: mirror_variant.json midplane_geometry/mirrored_corner_permutation；opinion: 交付边界]`

## 已验证的几何共享

|同一新父几何|连通岛|独立图块|共享组|实际共享表面积|严格几何|来源保留口径|
|---|---|---|---|---|---|---|
|镜像共享|134|67|67|100.0000%|False|True `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|未共享|134|134|0|0.0000%|False|True `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|

两侧整块一一共享共 67 对，100%表示新变体的表面都参与了授权共享，不是贴图占用100%，也不是纹理字节减少100%。来源保留规则记录 52 个已有冲突；严格失败不被覆盖，没有据此先逐面隔离。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

[共享来源保留检查](../../artifacts/meshquery_v1/whole_half01/shared_preserve/inspection.json) · [未共享来源保留检查](../../artifacts/meshquery_v1/whole_half01/unshared_preserve/inspection.json) · [共享严格拒绝](../../artifacts/meshquery_v1/whole_half01/shared_inspect/inspection.json) · [未共享严格拒绝](../../artifacts/meshquery_v1/whole_half01/unshared_inspect/inspection.json)

## 512排布冒烟与margin修正

|路径|实际UV面积并集占用|native margin|状态与含义|
|---|---|---|---|
|共享矩形父排布|26.6768%|不适用|来源保留几何通过 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|compact512|24.7500%|0.02032520325203252|geometry.valid=True；完整证据保留 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|compact512_gap_corrected|42.3997%|0.01016260162601626|geometry.valid=True；完整证据保留 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|

首次native margin为校正值的两倍，24.75%低占用结果保留；校正后为42.40%，矩形父排布为26.68%。这是排布适配修正的512冒烟，不是正式2048同设置比较，也不是已经获得同质量缩图收益。[校正设置](../../artifacts/meshquery_v1/whole_half01/compact512_gap_corrected/packing.json)。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

独立只读复算：5725对镜像三角形对应角UV差为0；新参考贴图与原8192贴图逐像素相等，来源重心插值UV最大差约4.35e-8。512校正排布最小间距10.1396px、边界10px，7项断言通过=True。[复核范围与命令](../../artifacts/meshquery_v1/whole_half01/independent_audit/README.md) · [复算原始字段](../../artifacts/meshquery_v1/whole_half01/independent_audit/results.json)。`[verified: independent_audit/results.json pairs/source/spacing/assertions]`

## 正式矩阵与实际交付

2048正式共享／未共享×矩形／轮廓四路径统一gap16、border8、padding8；实际配置逐例保存在report.json。结果缺失即pending，不由文件夹存在推断完成。质量接受仅限声明的BaseColor及LOD0／1／2，几何使用来源保持且quality_constraints=false，不是完整PBR或严格几何全通过。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

|路径|完成记录|尺寸|占用|岛／图块／组|共享面积|质量|实测工具秒|预算起点秒|
|---|---|---|---|---|---|---|---|---|
|whole_half_formal_shared01/shared_rectangles|recorded|2048|27.9833%|134/67/67|100.0000%|OK; accepted=True|263.482|250.0 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_formal_shared01/shared_blender_concave|recorded|2048|57.9466%|134/67/67|100.0000%|OK; accepted=True|294.766|250.0 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_formal_unshared01/unshared_rectangles|recorded|2048|30.0792%|134/134/0|0.0000%|REJECTED; accepted=False|249.767|250.0 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_formal_unshared01/unshared_blender_concave|recorded|2048|47.3360%|134/134/0|0.0000%|REJECTED; accepted=False|277.922|250.0 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_sizes01/shared_compact_1536|stopped|1536|54.4279%|134/67/67|100.0000%|未运行; accepted=未运行|51.578|650.0 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_sizes01/shared_compact_1024|recorded|1024|46.4890%|134/67/67|100.0000%|REJECTED; accepted=False|246.703|650.0 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_sizes01/shared_compact_512|recorded|512|28.4400%|134/67/67|100.0000%|REJECTED; accepted=False|133.407|650.0 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_margin01/repack1024_fixed_pixel_gap|recorded|1024|46.5333%|134/67/67|100.0000%|REJECTED; accepted=False|233.673|1189.25099999964 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_margin01/retain2048_layout_1024_scaled_gap|recorded|1024|57.9466%|134/67/67|100.0000%|REJECTED; accepted=False|219.127|1189.25099999964 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|whole_half_padding01/retain2048_layout_1024_padding8|recorded|1024|57.9466%|134/67/67|100.0000%|REJECTED; accepted=False|203.203|1648.6579999998212 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|

**shared_compact_1536烘焙拒绝**：`BASECOLOR_REUSE_REQUIRES_POWER_OF_TWO_RESOLUTION`；排布产物不等于有效贴图或模型，后续验收与导出未运行。[失败证据](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1536/bake/result.json)。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

|路径|来源保留几何／严格几何|逐面颜色／渲染／接缝|拒绝的渲染视图及最大误差|
|---|---|---|---|
|shared_rectangles|True/False|True/True/True|无已记录拒绝项 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|shared_blender_concave|True/False|True/True/True|无已记录拒绝项 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|unshared_rectangles|True/False|True/False/True|azimuth287_mip2: 0.202034 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|unshared_blender_concave|True/False|True/False/True|azimuth287_mip2: 0.201291 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|shared_compact_1536|True/False|未运行/未运行/未运行|无已记录拒绝项 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|shared_compact_1024|True/False|True/False/True|azimuth287_mip1: 0.201654; azimuth287_mip2: 0.212479 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|shared_compact_512|True/False|True/False/True|azimuth17_mip2: 0.304462; azimuth107_mip1: 0.257438; azimuth107_mip2: 0.443369; azimuth197_mip2: 0.235511; azimuth287_mip1: 0.217909; azimuth287_mip2: 0.367878 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|repack1024_fixed_pixel_gap|True/False|True/False/True|azimuth107_mip2: 0.214460; azimuth287_mip2: 0.204024 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|retain2048_layout_1024_scaled_gap|True/False|False/False/False|azimuth287_mip2: 0.217399 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|retain2048_layout_1024_padding8|True/False|False/False/False|azimuth287_mip2: 0.217399 `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|

### 1024留边消融

两个候选从同一个已通过声明范围验收的2048轮廓父快照派生；控制组重新排布，另一组保留原UV并按尺寸缩放gap／border／padding。该消融同时改变排布策略与像素留边策略，不将结果归于单一参数。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

|路径|像素gap／border／padding|UV动作|结果|
|---|---|---|---|
|repack1024_fixed_pixel_gap|16/8/8|重新轮廓排布|REJECTED; accepted=False `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|
|retain2048_layout_1024_scaled_gap|8/4/4|保留2048 UV|REJECTED; accepted=False `[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`|

最后padding消融保留同一个2048 UV、target、1024 profile和质量阈值，只将padding4恢复为8。没有新增pack或inspect时，岛数／图块等读自不变父UV的inspect报告，metrics_source保留确切路径；本次质量仍以新validate结果为准。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

### 矩形与轮廓排布的实际图像

以下内嵌图来自已完成的2048路径；UV密集程度以面积并集指标为准，图像供直接检查布局。`[verified: 四个2048案例views/uv.png与bake/texture/BaseColor_mip0.png；opinion: 阅读方式]`

**UV布局**

|同一新父几何|矩形排布|轮廓排布|
|---|---|---|
|67图块共享 `[verified: 对应案例inspect/uv_metrics.json actual_independent_tiles]`|![67图块共享 rectangles UV布局](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/views/uv.png)|![67图块共享 blender_concave UV布局](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views/uv.png)|
|134图块未共享 `[verified: 对应案例inspect/uv_metrics.json actual_independent_tiles]`|![134图块未共享 rectangles UV布局](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/views/uv.png)|![134图块未共享 blender_concave UV布局](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/views/uv.png)|

**实际BaseColor图集**

|同一新父几何|矩形排布|轮廓排布|
|---|---|---|
|67图块共享 `[verified: 对应案例inspect/uv_metrics.json actual_independent_tiles]`|![67图块共享 rectangles 实际BaseColor图集](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/bake/texture/BaseColor_mip0.png)|![67图块共享 blender_concave 实际BaseColor图集](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/bake/texture/BaseColor_mip0.png)|
|134图块未共享 `[verified: 对应案例inspect/uv_metrics.json actual_independent_tiles]`|![134图块未共享 rectangles 实际BaseColor图集](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/bake/texture/BaseColor_mip0.png)|![134图块未共享 blender_concave 实际BaseColor图集](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/bake/texture/BaseColor_mip0.png)|

### 贴图、模型、同镜头视图

镜像源参考：[正面](../../artifacts/meshquery_v1/whole_half01/source_views768/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half01/source_views768/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half01/source_views768/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half01/source_views768/underside.png)

- **shared_rectangles**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_rectangles/steps.md)；RGBA8完整mip纹理载荷=22369620字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **shared_blender_concave**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/steps.md)；RGBA8完整mip纹理载荷=22369620字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **unshared_rectangles**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_rectangles/steps.md)；RGBA8完整mip纹理载荷=22369620字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **unshared_blender_concave**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/steps.md)；RGBA8完整mip纹理载荷=22369620字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **shared_compact_1536**：实际BaseColor: 未生成（已停止） · mip文件目录: 未生成（已停止） · GLB: 未生成（已停止） · UV: 未生成（已停止） · 正面: 未生成（已停止） · 侧面: 未生成（已停止） · 斜视: 未生成（已停止） · 底视: 未生成（已停止） · 质量原始报告: 未生成（已停止） · [逐步证据](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1536/steps.md)；RGBA8完整mip纹理载荷=未生成。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **shared_compact_1024**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_1024/steps.md)；RGBA8完整mip纹理载荷=5592404字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **shared_compact_512**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_sizes01/shared_compact_512/steps.md)；RGBA8完整mip纹理载荷=1398100字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **repack1024_fixed_pixel_gap**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_margin01/repack1024_fixed_pixel_gap/steps.md)；RGBA8完整mip纹理载荷=5592404字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **retain2048_layout_1024_scaled_gap**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_margin01/retain2048_layout_1024_scaled_gap/steps.md)；RGBA8完整mip纹理载荷=5592404字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`
- **retain2048_layout_1024_padding8**：[实际BaseColor](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/bake/texture/BaseColor_mip0.png) · [mip文件目录](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/bake/texture) · [GLB](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/export/asset.glb) · [UV](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/views/uv.png) · [正面](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/views/front.png) · [侧面](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/views/left.png) · [斜视](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/views/oblique.png) · [底视](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/views/underside.png) · [质量原始报告](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/validate/validation.json) · [逐步证据](../../artifacts/meshquery_v1/whole_half_padding01/retain2048_layout_1024_padding8/steps.md)；RGBA8完整mip纹理载荷=5592404字节。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

独立GLB重导入共 9 份，all_passed=True：原面角位置误差0，UV最大误差约4.47e-8，嵌入图像与实际烘焙图像逐像素相同。这是解析与图像／几何／UV回读，不替代GPU或人工验收。[九份模型完整性记录](../../artifacts/meshquery_v1/whole_half01/reimport_all.json)。`[verified: reimport_all.json exports/all_passed/verification_scope]`

## 成本和未完成项

全流程成本以 [可复算CLI账本](../../artifacts/meshquery_v1/whole_half01/cost_ledger.json) 为准，它额外覆盖最初共享／未共享512冒烟。下列逐动作总和仅是报告枚举路径与前置工具的子集，不替代全流程账本。账本与当前summary一致=True；False时是partial快照，待重算。`[verified: cost_ledger.json records.category=whole_half_smoke01；report.json cases/cost_ledger_differences]`

实测前置工具调用 97.467 秒，仅计一次；本次已读正式工具调用合计 2173.628 秒。额外源参考渲染等调用 5.719 秒，本报告子集已记实际工具壁秒总和 2276.814。正式2048每例prior_tool_seconds=250，缩图每例650，均为预算预扣，不是实测前置成本，不能加进实际耗时或按四例重复计算准备成本。runner汇总耗时另存，不与逐调用壁秒混加。并行案例的调用壁秒求和用于工作量记账，不等于用户等待的自然时间。[前置账目](../../artifacts/meshquery_v1/whole_half01/preparation_costs.json)。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

独立CLI账本累计 2473.424 秒，含本报告矩阵外的最初共享／未共享冒烟；留边前公共成本复算 1189.251 秒，padding前公共成本复算 1648.658 秒。共享自适应路径当前累计 1852.112／2400 秒，within_budget=True。这些prior来自已发生调用，不能再加一次。账本以runner summary为单位，本报告逐步骤以result.wall_seconds为单位，两套总和分列，不能混加。未计编程／测试、未计时helper与LLM成本；token与费用未获得。[逐项CLI账本](../../artifacts/meshquery_v1/whole_half01/cost_ledger.json) · [账本复算脚本](../../artifacts/meshquery_v1/whole_half01/collect_costs.py)。`[verified: cost_ledger.json records/actual_cli_seconds/margin_prior_recomputed/padding_prior_recomputed/scope]`


## 外观与接受范围

[独立外观评审原文](../../artifacts/meshquery_v1/whole_half01/appearance_review.json)；按原文限定范围，不从数值或导出状态推断人工接受。`[verified: whole_half_report01/report.json variant/shared_preserve/unshared_preserve/packing_preflight/cases及所链接原始字段]`

以下为评审文件记录的主Agent非盲、有限视图初筛；人工美术、GPU和完整PBR没有因此通过。`[verified: appearance_review.json review_kind/full_pbr_passed/human_accepted]`

|案例|Agent有限外观接受|数值接受|人工接受|实际观察范围与结论|
|---|---|---|---|---|
|shared_blender_concave|True|True|False|与镜像源的四个同镜头相比，眼睛、嘴边、白爪、翼膜和中线未见明显新增错贴；有限768像素视图初筛。 `[verified: appearance_review.json cases对应案例]`|
|unshared_blender_concave|True|False|False|仅查看正面与底面；未见明显新增错贴，不能覆盖未看视角或替代数值拒绝。 `[verified: appearance_review.json cases对应案例]`|
|shared_compact_1024|True|False|False|四个768像素视角中主要颜色边界保留，比2048略软；作为节省型预览可接受，不改写两个mip渲染探针的数值拒绝。 `[verified: appearance_review.json cases对应案例]`|
|shared_compact_512|False|False|False|眼睛高光与眼边、嘴部蓝白边界可见模糊，近景不推荐；保留作为低尺寸失败比较。 `[verified: appearance_review.json cases对应案例]`|
|retain2048_layout_1024_scaled_gap|True|False|False|已看四个768像素同镜头，未见大片新错贴；保留作为探索，不选为主交付，因为相较保守1024版增加材质／接缝数值拒绝，且未解决渲染最大误差。 `[verified: appearance_review.json cases对应案例]`|
|retain2048_layout_1024_padding8|True|False|False|已看四个768像素同镜头，未见大片新错贴；保留作为探索，不选为主交付，因为相较保守1024版增加材质／接缝数值拒绝，且未解决渲染最大误差。 `[verified: appearance_review.json cases对应案例]`|

本轮主张的收益属于显式几何对称化后的新变体；原来的左右外观不对称可能被改变。共享相对同一新父几何的优势、排布优势与几何对称化代价必须分别报告。`[opinion]`
