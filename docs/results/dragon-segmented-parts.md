# 恐龙分部件参考、局部镜像与 UV 共享实测

**历史阶段：下面是保留旧UV的前期对照。最新[按part合岛与小片清理交付](dragon-parts-clean-final.md)为8部件、18岛／11图块；以下199岛版不是该要求的完成结果。** `[verified: artifacts/dragon_parts/final/summary.json；opinion: 交付边界]`

本轮交付2K UV＋BaseColor：9个可查询部件，翼膜／翼支撑两组部件对采用局部镜像，生成32个实际共享组。Agent查看实际六视角与头部、爪子、翼根近景后保留；数值综合验收仍拒绝，完整PBR、GPU和人工状态未通过。`[verified: artifacts/dragon_parts/round03/summary.json rows；visual_keep/review.json；mirror_shared2048/validate/validation.json]`

## 输入与路线选择

- 用户输入 `E:/Downloads/blue_dragon_3d_mode_partedl.fbx.glb`：9个对象、10,832三角面，没有UV／贴图；直接导入因3条同向邻边拒绝。独立split_fans变体保留逐面逐角位置，原输入保留。`[verified: artifacts/dragon_parts/round01/prepared/result.json；prepared_fans/sources/0/connectivity_variant.json]`
- 原分割网格的左右翼膜分别含119、135个原边连通片；全模型xatlas初始化664岛，512²排布覆盖11.744%。OptCuts试做单翼170→120岛，但存在未授权接触，未作为最终布局。`[verified: artifacts/dragon_parts/round01/component_audit.json；full512/inspect/result.json；wing_optcuts/reparameterization.json；opinion: 路线选择]`
- 最终以已有 `topology_restore_preflight01/semantic_patches/snapshot` 的带贴图规范化父资产作为制作表面，将新分割模型的位置迁移为原面标签，不焊接分割网格。面中心最近分割表面决定标签，三处内部采样交叉核对；均距0.001683、最大0.009828，200面存在标签边界分歧，没有超出2%包围盒对角线距离的面。这是候选分区，不是逐面身份恢复或全局正确性证明。`[verified: artifacts/dragon_parts/round02/labeled/segmentation_transfer.json；opinion: 证据边界]`
- 标签迁移后的部件边界使原121岛细分为191岛；这不是把原UV降为191岛。保留可用原UV；该父资产单翼OptCuts候选16→16岛且有数值错误，最终保留原布局。`[verified: artifacts/dragon_parts/round02/labeled/parts.json；labeled_wing_optcuts/reparameterization.json；opinion: 保留决策]`

## 四种配置与真实结果

全部2K配置采用Blender凹形排布、种子17、gap/border/padding各8px。原几何共享尝试被 `AFFINE_UV_OR_PACKING_REJECTED` 拒绝，按计划退回独占；该配置显式引用原几何无共享产物，没有伪造一次额外烘焙。`[verified: artifacts/dragon_parts/round03/summary.json natural_shared_attempt；round02/natural_wings/result.json]`

|配置|三角面|UV岛|排布代表图块|共享组|覆盖率|完整RGBA8 mip字节|
|---|---|---|---|---|---|---|
|保留原几何、无共享|10,832|191|191|0|55.449%|22,369,620。`[verified: round03/summary.json rows original2048]`|
|保留原几何、尝试共享|10,832|191|191|0，失败回退|55.449%|复用上一行产物。`[verified: round03/summary.json natural_shared_attempt]`|
|局部镜像、无共享|10,836|199|199|0|55.160%|22,369,620。`[verified: round03/summary.json rows mirror_unshared2048]`|
|相同局部镜像、共享|10,836|199|167|32|58.393%|22,369,620。`[verified: round03/summary.json rows mirror_shared2048]`|

表格的“代表图块”是每个精确成对共享组只排一个owner，不是将全局交叠连通域计作图块。实际授权UV交叠面积0.083789；两成员UV逐面对照最大误差为0，参与这些精确成对映射的表面积占25.084%。旧UV未授权交叠不算共享，明细保留。`[verified: artifacts/dragon_parts/round03/summary.json rows mirror_shared2048；mirror_shared2048/inspect/inspection.json geometry]`

共享带来的同几何覆盖增加为3.232个百分点，排布代表图块减少32，UV连通岛仍为199。两版2K字节相同；尚不能称为同画质包体节省。镜像将part5替换为part4的反射、part6替换为part7的反射，其余部件保留；几何改变与共享收益分别记录。`[verified: artifacts/dragon_parts/round02/mirror_labeled/parts.json；round03/summary.json；opinion: 收益边界]`

1K缩图另外生成了真实GLB和贴图，覆盖49.391%、完整RGBA8 mip为5,592,404字节。已观察全身预览与头部近景，近景更软；未做同等完整数值验收，作为诊断附件，主交付保持2K。`[verified: artifacts/dragon_parts/round03/summary.json rows mirror_shared1024；head1024/front.png；opinion: 观察与交付选择]`

## 验收与文件

2K三份模型已独立重新读取，逐面逐角位置与快照精确一致。LOD0/1/2的4采样/面、32像素数值渲染探针均执行：render子项通过，但原UV几何冲突、部分颜色覆盖与接缝子项使综合结果拒绝。共享版4个颜色失败面完整保留；没有修改阈值掩盖失败。`[verified: artifacts/dragon_parts/round03/*2048/preview.json；*2048/validate/validation.json]`

Agent已查看共享／不共享六视角，另看头部正侧、双脚正下、翼支撑正背近景；没有观察到明显特征损坏或大片错色，允许小幅采样差异。选区近景隐藏其他部件所露出的边缘不是整龙新增裂缝。2K LOD1/2正面也已观察，保留有限视角Agent结论，人工与真实游戏状态独立。`[opinion]`

- [交付目录](../../output/dragon-parts-20261125)：`model.glb` 是视觉保留模型，`model-parts.glb` 是保留9个具名对象的同图集版本，`texture/` 为完整mip，另附UV、预览、部件清单与回读核验。多对象版本逐部件XYZ、UV、贴图均重新读取一致。`[verified: output/dragon-parts-20261125/verification.json]`
- [可点击共享对照](../../artifacts/dragon_parts/round03/compare_shared/review.html)、[汇总JSON](../../artifacts/dragon_parts/round03/summary.json)和[Agent保留记录](../../artifacts/dragon_parts/round03/visual_keep/review.json)。`[verified: 对应实际文件]`
- 制作入口与可继续执行的快照见[分割部件流程](../segmented-parts-workflow.md)。本轮使用蓝龙案例中的保留原UV、按自然部件尝试共享及整组回退决策，区别是分区来自新的Tripo分割位置；分割网格拓扑和颜色投射路径均独立保留。`[opinion]`
