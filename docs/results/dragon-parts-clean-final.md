# 恐龙按部件合岛、小片清理与共享：最终交付

完成逐部件切缝重建、真实合岛、小片归位和实际烘焙，最终为8个部件、18个UV岛、7组共享、11个独立图块。已生成具名多对象GLB，UV＋BaseColor的LOD0/1/2数值验收通过，Agent六视角与特征近景保留。完整PBR、GPU和人工验收独立。`[verified: artifacts/dragon_parts/final/summary.json；visual_keep/review.json；output/dragon-parts-clean-20261125/verification.json]`

## 逐部件结果

|部件|UV岛|处理|
|---|---|---|
|头部 `tripo_part_0`|1|后侧开口后ABF重展，将OptCuts的10块并为一块，保留眼嘴。`[verified: artifacts/dragon_parts/round12/head_merge/reparameterization.json；final/summary.json per_part]`|
|身体与尾巴 `tripo_part_1`|1|归位边界小片后OptCuts重展。`[verified: round10/cleanup_plan.json；rebuild_clean/rebuild.json；final/summary.json]`|
|正X侧腿与爪 `tripo_part_2`|1|完整部件OptCuts重展，真实白爪保留。`[verified: round10/rebuild_clean/part_2/reparameterization.json；final/closeup_claws/lower.png]`|
|负X侧腿与爪 `tripo_part_3`|1|吸收小part_8后完整重展。`[verified: round10/cleanup_plan.json；final/summary.json]`|
|两侧翼膜 `tripo_part_4/5`|各6|保留有实际表面贡献的薄翼/翼骨片；使用负X侧的布局镜像，六组真实共享。`[verified: round12/prepared/parts.json；final/summary.json]`|
|两侧翼支撑 `tripo_part_6/7`|各1|完整重展，再建立一组镜像共享。`[verified: round10/rebuild_clean/rebuild.json；round12/prepared/parts.json；final/summary.json]`|

## 小碎片如何处理

Tripo输入有9对象、10,832面但没有UV／贴图。最终使用原FBX的权威顶点/边身份与原贴图，将Tripo分割位置作为面标签参考；原FBX的非流形边通过显式split_fans独立变体处理，逐面逐角位置保留。最近分割表面只提供标签候选，采色使用原资产自身的逐角UV。`[verified: round04/original_connected/inputs/identity.json；sources与connectivity_variant.json；round04/connected_labels/segmentation_transfer.json；round10/prepared_clean/parts.json]`

1. 将腿边的小part_8并回负X侧腿，清理25张面的分区归属，再按原边重建部件连接；不通过空间接近焊接。原来被切碎的头、身体和四肢得到完整选区。`[verified: artifacts/dragon_parts/round10/cleanup_plan.json]`
2. 只在新变体中删除5张完全孤立的小面，合计0.0222246%表面积；原输入文件保留且哈希一致。清理前后六个512px原色视图中，超过3字节的变化分别为20、6、0、0、0、0像素。有限视角比较不证明所有表面都不可见。`[verified: round10/cleanup_plan.json；cleanup_image_differences.json；final/originals_unchanged.json]`
3. 额外删除翼根／翼骨小片的68面和15面候选，在实际正、背或底视角产生缺口，已回退。剩下的真实翼面不能仅因UV小就删除，失败图与面ID保留。`[verified: round13/deleted_faces.json、source_views、image_differences.json；round14/removed.json、views、image_differences.json；opinion: 保留判断]`
4. ABF腿一岛试做曾使白爪在512烘焙中丢失，脚底/爪尖切口试验恢复了白爪；最终清理连接后改用OptCuts完整腿一岛，白爪仍保留。这些分支均保留，不以岛少自动决定保留。`[verified: round07/mirror512/actual_views/front.png；round08/feet_rebuilt.json；round10/rebuild_clean/rebuild.json；final/closeup_claws/lower.png]`

## 四配置对照与收益边界

2K配置固定种子17、Blender凹形排布、gap/border/padding各8px。各几何内共享和无共享使用相同颜色来源；镜像几何与清理的影响单列。`[verified: artifacts/dragon_parts/final/*2048/pack/request.json；summary.json]`

|配置|三角面|UV岛|独立图块|共享组|覆盖率|RGBA8完整mip|
|---|---|---|---|---|---|---|
|清理后原几何、无共享|10,827|19|19|0|56.031%|22,369,620字节。`[verified: final/summary.json rows original2048]`|
|清理后原几何、尝试共享|10,827|19|19|0，拒绝后退回独占|56.031%|显式复用上一行产物。`[verified: final/natural_shared/result.json；summary.json natural_shared_attempt]`|
|局部镜像、无共享|10,819|18|18|0|52.418%|22,369,620字节。`[verified: final/summary.json rows mirror_unshared2048]`|
|相同局部镜像、共享|10,819|18|11|7|48.977%|22,369,620字节。`[verified: final/summary.json rows mirror_shared2048]`|

共享成员表面积占25.036%，实际授权UV交叠面积为0.0700831；同几何共享减少7个图块，并不减少连通岛数。本轮共享版的空白覆盖率未优于无共享版，2K字节也相同，不能宣称所有利用率指标或包体均改善。重点交付是按部件连续可编辑的UV、小片清理与可追溯共享。`[verified: final/summary.json；mirror_shared2048/inspect/inspection.json；opinion: 结论范围]`

另有同UV的1K诊断附件，padding按比例改为4px，完整RGBA8 mip为5,592,404字节；实际正面预览已查看，但没有同等完整数值验收，主交付保持2K。缩图收益不归因于UV共享。`[verified: final/shrink1024/bake.json；shrink1024_view/front.png；opinion: 交付选择]`

## 验收、交付与继续处理

三份2K配置的基本UV／几何、颜色、接缝和渲染子项均通过声明的UV＋BaseColor协议，LOD0/1/2采用4采样/面及32px渲染探针；形状阈值沿用visual/advisory，不称为严格全形状／PBR验收。所有GLB均独立回读，逐角位置一致。`[verified: final/*2048/validate/validation.json；*2048/preview.json]`

Agent已看共享／无共享六视角、头部正面、爪底及翼支撑近景和LOD1/2正面。可见眼嘴、白爪和翼面保留，小幅采样差异接受；孤立选区的切口不作为全模型裂缝。人工和真实游戏渲染未执行。`[verified: final/visual_keep/review.json；opinion: 目视判断]`

- [交付目录](../../output/dragon-parts-clean-20261125)：主模型 `model-parts.glb` 保留8个具名对象；`model.glb` 为合并预览，两者共享同一张2K BaseColor，完整mip位于 `texture/`。多对象版本逐部件位置、UV、贴图与面覆盖回读一致。`[verified: output/dragon-parts-clean-20261125/verification.json]`
- [共享／无共享对照](../../artifacts/dragon_parts/final/compare_shared/review.html)、[汇总JSON](../../artifacts/dragon_parts/final/summary.json)、[数值验收](../../artifacts/dragon_parts/final/mirror_shared2048/validate/validation.json)与[继续处理快照](../../artifacts/dragon_parts/final/mirror_shared2048/pack/snapshot/snapshot.json)。`[verified: 对应文件及summary.json路径]`
- [早期保留旧UV的199岛阶段](dragon-segmented-parts.md)与全部失败分支保留，不能用它代替这次按part整理完成的结果。操作入口见[部件流程](../segmented-parts-workflow.md)。`[opinion]`
