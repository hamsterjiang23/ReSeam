# Chloe红刺的硬边、双UV与拉伸取舍

## 适用条件与输入

适用于尖端、棱线、凹槽较多的资产，需要保留纹理走向、解释少岛高拉伸取舍，或核实FBX多UV及静态颜色来源的任务。`[opinion]`

原始Chloe为450面／227顶点，镜像变体590面／368顶点。原FBX、作者UV和第二UV保留，静态颜色绑定指定图像与UVChannel_1。Unity动态材质仍有独立验收状态。`[verified: docs/results/legacy-assets-reseam.md L17–L21]`

## 历史过程与关键决策

1. 分别固定原始与镜像几何，读取导入逐角法线和原多边形身份。原始分支使用几何AO辅助侧面连接排序，镜像分支保留几何折角路线。本资产AO/normal/height贴图均未绑定。`[verified: docs/results/chloe-hard-surface.md L3–L5]`
2. 对原始模型比较OptCuts无共享、硬边无共享和硬边复用。OptCuts为3岛、拉伸P95约1.791，硬边无共享111岛、P95约1.171，近似共享111岛／108图块出现最大各向异性约75.323的退化。`[verified: docs/results/chloe-hard-surface.md L15–L25]`
3. 在固定镜像几何比较低拉伸硬边路线及少岛harmonic路线。硬边共享104岛／52图块、P95约1.000，harmonic共享2岛／1图块、P95约32.080。三种子与无共享控制均保留。`[verified: docs/results/chloe-hard-surface.md L33–L44]`
4. 检查红黑结构、尖端、背面细节与棋盘格。256像素硬边预览保留了主要结构，局部细节与接缝仍有质量问题。`[verified: docs/results/chloe-hard-surface.md L25、L46]`

## 失败、取舍与结果

少岛镜像展示曾选择2岛／1图块，后续硬边专项提供104岛／52图块对照，两者各自保留实际拉伸与质量状态。硬边专项表中数值质量均False、GLB回读均True，Agent意见限定实际预览范围。`[verified: README.md L23；docs/results/chloe-hard-surface.md L33–L46]`

本例范围为静态BaseColor，完整PBR、Unity动态Shader与人工状态独立。额外UV保留与指定活动UV绑定有对应记录。`[verified: docs/results/chloe-hard-surface.md L5；docs/results/legacy-assets-reseam.md L21]`

## 迁移到当前资产

按 [硬表面Skill](../../reseam-hard-surface/SKILL.md) 确定初始切缝与局部修改。分别记录自定义法线、sharp、几何折角和真实贴图线索，使用当前原多边形身份解释三角化内部边。`[opinion]`

对当前尖刺比较切缝、棋盘格、拉伸和真实颜色近景，再选择整块保留或共享。按实际UV通道与Shader用途建立静态目标，导出后重读额外UV。参数、通道支持及验收要求依当前工具和任务核实。`[opinion]`

## 证据入口

以下路径按 [索引的仓库定位方法](index.md) 读取。`[opinion]`

|用途|仓库根相对路径及读取位置|
|---|---|
|硬边路线、拉伸与失败|`docs/results/chloe-hard-surface.md` 的“原始450面模型”“固定590面镜像模型”。`[verified: docs/results/chloe-hard-surface.md L9–L46]`|
|双UV来源与早期展示|`docs/results/legacy-assets-reseam.md` 的“Chloe刺模型”。`[verified: docs/results/legacy-assets-reseam.md L7–L21]`|
|原始/镜像硬边对照图|`docs/assets/chloe-hard-surface/round01/original_comparison.png`、`docs/assets/chloe-hard-surface/round01/mirror_comparison.png`。`[verified: docs/results/chloe-hard-surface.md L11、L29]`|
|硬边实际模型与贴图目录|`artifacts/chloe_hard_surface/round01/mirror/evaluate/hard_shared/seed17`。`[verified: docs/results/chloe-hard-surface.md L39]`|
|请求、逐步记录与原始失败|`artifacts/chloe_hard_surface/round01`，汇总 `docs/assets/chloe-hard-surface/round01/summary.json`、`docs/assets/chloe-hard-surface/round01/failures.json`。`[verified: docs/results/chloe-hard-surface.md L50]`|
|少岛镜像展示GLB/贴图|`docs/assets/legacy-assets-reseam/round02/models/chloe/mirror_shared_final/256/asset.glb`、`docs/assets/legacy-assets-reseam/round02/textures/chloe/mirror_shared_final/256/BaseColor_mip0.png`。`[verified: docs/results/legacy-assets-reseam.md L19]`|
