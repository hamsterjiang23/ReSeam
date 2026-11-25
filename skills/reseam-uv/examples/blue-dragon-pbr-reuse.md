# 蓝龙PBR投射排查与自身材质镜像复刻

## 适用条件与输入

适用于高低模PBR烘焙出现黑线或反光异常，以及已有直接低模PBR材质、需要制作镜像共享变体的任务。先确定当前任务的材质来源与几何路线，再选择案例阶段。`[opinion]`

高低模实验输入来自 `model/dragon/pbr_bake_test`，包含10,832面高模、3,113面主三角低模和4,487面补充低模。后续自身材质镜像复刻产生3,316面新几何。`[verified: docs/results/dragon-pbr-reuse.md L82–L88；docs/results/dragon-legacy-method-replay.md L3]`

## 高低模投射阶段

1. 通过独立拆连接副本处理非流形导入，保存源位置、UV和逐角法线来源。主低模另外修复10个面角法线，记录角度变化和原始直接材质参考。`[verified: docs/results/dragon-pbr-reuse.md L82–L90]`
2. 对照高模、直接低模、无共享和共享四版本，同原点、同镜头查看真实PBR及中性法线。原UV、OptCuts无共享、腿部共享与补充低模均保留实际导出或失败。`[verified: docs/results/dragon-pbr-reuse.md L11–L21、L29–L37]`
3. 修正后的投射轮次（round07）消除早期总览中的大片黑翼，2K头冠、嘴边和翼根仍有源高模没有的锯齿黑线。无共享路径存在同类缺陷，完整外观结论为EXPERIMENT_COMPLETED_QUALITY_NOT_ACCEPTED。`[verified: docs/results/dragon-pbr-reuse.md L5、L18–L29]`

腿部共享版65岛／57图块、约7.81%表面参与授权交叠，材质未验收。无共享为45岛／45图块。该实验最终保留负面结果及可打开的四版本对照场景。`[verified: docs/results/dragon-pbr-reuse.md L5、L18–L23]`

## 低模自身材质镜像阶段

1. 在新的授权变体中采用“整理一半→裁切镜像→共用UV→凹形排布→重烘焙”，使用低模自身Tripo PBR。负半边来源避开已定位的零切线源面，并按实际中线调整裁切。`[verified: docs/results/dragon-legacy-method-replay.md L9–L17]`
2. 保留来源逐角法线与镜像切线符号，使用同面角采样来源。建立同几何2K无共享60图块与共享30图块对照，保存实际模型和Blender场景。`[verified: AGENTS.md 开头“最新用户要求复刻历史”段；docs/results/dragon-legacy-method-replay.md L3–L13]`
3. Agent查看32组全身/重点近景后保留2K版本。1K另有有限范围VISUAL_KEEP、细节稍软，mip2无效探针427/13,264，2K同项1/13,264。`[verified: docs/results/dragon-legacy-method-replay.md L3、L13–L14]`

本阶段交付60岛／30图块，数值保持诊断，人工美术与游戏Shader待验收。同分辨率2K两版三张RGBA8图集完整mip均67,108,860字节，PNG和GLB文件体积另有记录。高低模投射阶段保留其原质量结论。`[verified: docs/results/dragon-legacy-method-replay.md L3、L19–L21]`

## 迁移到当前资产

先通过 `material.preflight` 与实际源/目标法线、UV、射线/Cage证据定位问题，再比较直接材质、无共享和共享的同镜头近景。分别核实颜色来源、切线框架及投射结果，并沿失败面追踪。`[opinion]`

用户选择自身材质镜像路线时，为新几何重建来源与目标，保存同几何无共享控制。PBR法线需随新UV和镜像切线变换重采样，参数按当前 `tools describe` 核实。沿两阶段各自的来源、验收范围和成本报告结果。`[opinion]`

## 证据入口

以下路径按 [索引的仓库定位方法](index.md) 读取。`[opinion]`

|用途|仓库根相对路径及读取位置|
|---|---|
|投射来源、失败与状态|`docs/results/dragon-pbr-reuse.md` 的“最新实际产物与结论”“来源、几何与法线版本”“保留的失败”。`[verified: docs/results/dragon-pbr-reuse.md L9–L29、L78–L92；标题检索输出 L127]`|
|投射同镜头黑线近景|`artifacts/dragon-pbr-reuse/round07/source-bake-closeup-comparison.png`。`[verified: docs/results/dragon-pbr-reuse.md L31–L33]`|
|投射四版本场景|`artifacts/dragon-pbr-reuse/round07/comparison2048/comparison.blend`。`[verified: docs/results/dragon-pbr-reuse.md L11–L13]`|
|自身材质复刻过程与取舍|`docs/results/dragon-legacy-method-replay.md`、`artifacts/dragon-pbr-reuse/round09_legacy_method/report.md`。`[verified: docs/results/dragon-legacy-method-replay.md L5–L21]`|
|复刻全身对照图|`artifacts/dragon-pbr-reuse/round09_legacy_method/pbr_comparison/hero.png`。`[verified: docs/results/dragon-legacy-method-replay.md L7]`|
|实际2K PBR模型|`artifacts/dragon-pbr-reuse/round09_legacy_method/pbr_delivery2048/asset.glb`。`[verified: docs/results/dragon-legacy-method-replay.md L5]`|
|复刻2K实际看图记录|`artifacts/dragon-pbr-reuse/round09_legacy_method/pbr_review2048_final/review.json` 的status与all_prepared_views_reviewed。`[verified: docs/results/dragon-legacy-method-replay.md L3]`|
