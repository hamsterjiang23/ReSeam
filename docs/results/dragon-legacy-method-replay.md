# 当前蓝龙：历史镜像共享方法复刻

**已做成完整 PBR 的 2K 主交付：3,316 三角形、60 连通 UV 岛、30 独立图块。Agent 实际查看全部准备的 32 组全身对照与重点近景，正式保留；数值仍为诊断，人工美术和游戏 Shader 尚未验收。** `[verified: ../../artifacts/dragon-pbr-reuse/round09_legacy_method/pbr_review2048_final/review.json: status、all_prepared_views_reviewed；pbr_pack17/geometry.json: chart_count、packing_groups；pbr_delivery2048/result.json: numerical_quality_accepted、human_acceptance、game_shader_acceptance]`

直接打开 [图片与模型页面](../../artifacts/dragon-pbr-reuse/round09_legacy_method/index.html)、[Blender 四组对照场景](../../artifacts/dragon-pbr-reuse/round09_legacy_method/pbr_comparison2048/comparison.blend) 或 [共享 2K GLB](../../artifacts/dragon-pbr-reuse/round09_legacy_method/pbr_delivery2048/asset.glb)。完整记录见 [本轮报告](../../artifacts/dragon-pbr-reuse/round09_legacy_method/report.md)。`[verified: pbr_comparison2048/comparison.json: comparison_scene；pbr_delivery2048/result.json: model]`

![镜像来源、不共享60图块、共享30图块](../../artifacts/dragon-pbr-reuse/round09_legacy_method/pbr_comparison/hero.png)

方法沿用“整理一半 → 裁切镜像 → 两侧共用 UV → 凹形排布 → 重烘焙”。历史版本的“40”实际为 80 岛／40 图块，11,338 三角形，主要是无光照 BaseColor；本轮在当前低模另建变体，保留原件，使用低模自身 Tripo PBR，不是修复此前高模到低模投射。`[verified: ../../artifacts/meshquery_v1/midplane_cleanup01/create/mirror_variant.json、seed29/pack/geometry.json、seed29/views/views.json；../../artifacts/dragon-pbr-reuse/round09_legacy_method/protocol.json: material_source、high_to_low_boundary]`

| 分支 | 几何 / UV | 当前结论 |
|---|---|---|
| PBR 2K 主交付 | 负 X 半边，X=-0.001；3,316 面，60 岛／30 图块，覆盖 54.5349% | 同几何无共享60图块对照完成；已看角度中眼嘴、白爪、翼缘和高光保留，Agent VISUAL_KEEP；mip2有1/13,264无效探针。`[verified: pbr_pack17/geometry.json；pbr_review2048_final/review.json；pbr_shared2048/validation/validation.json:mips]` |
| PBR 1K 较小试验 | 同一 3,316 面几何、60 岛／30 图块 | 32组实际对照与近景已看，有限范围VISUAL_KEEP，细节稍软；mip2有427/13,264无效探针，不作为最低尺寸证明。`[verified: pbr_review1024/review.json:status、reviewed_views；pbr_closeup_review.json:observations；pbr_shared1024/validation/validation.json:mips；opinion:版本用途]` |
| BaseColor 复刻步骤 | 正 X 半边，X=0；3,312 面，86 岛／43 图块 | 实际2K颜色和GLB重读完成，保留已看无光照预览；1个mip2渲染探针超限，诊断交付。`[verified: basecolor_comparison/metrics.json、appearance_receipt.json、export_readback.json；basecolor_validate2048/validation.json:render.views]` |

表中未写完整前缀的证据均位于 `artifacts/dragon-pbr-reuse/round09_legacy_method/`。两分支保留不同半边、不同几何，不能视为材质通道消融。PBR 负半边避开了正侧零切线源面；小幅调平面解决已定位的中线薄条，未修改原始材质图片。`[verified: execution_decisions.json:comparability；source_frame_choice_audit.json；mirror_pbr_negative/mirror_variant.json:midplane_strip_candidates；mirror_pbr_clean/mirror_variant.json]`

同分辨率2K下，共享与无共享的三张 RGBA8 图集含mip都是67,108,860字节，**纹理原始数据节省为0**；实际全mip PNG文件从8,868,456降到7,552,247字节（14.84%），GLB从6,327,660降到5,416,072字节（14.41%）。这些是编码／文件体积变化，不是图块减半就使纹理内存减半。`[verified: pbr_storage_comparison.json:same_resolution_comparisons[0].metrics、storage_interpretation]`

完整 mip 质量、同画质最低尺寸、人工美术与游戏验收仍未确认。先前高低模投射的负面结果保留；本轮交付是按旧方法完成的同表面镜像共享版本。`[verified: pbr_closeup_review.json:decision；pbr_delivery2048/result.json；protocol.json:high_to_low_boundary]`
