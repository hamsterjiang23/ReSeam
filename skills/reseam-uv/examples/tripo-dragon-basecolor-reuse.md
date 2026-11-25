# Tripo蓝龙的原UV保留、镜像与继续复用

## 适用条件与输入

适用于保留可用原UV、让自然部件共享，或在已授权的镜像变体中扩大复用、减少碎片和重复角刺图块的任务。按当前几何路线选择下列阶段。`[opinion]`

原始 `tripo_p2` 有10,832面、121个UV岛和8192² RGB BaseColor。后续固定几何部件共享、镜像合岛及跨部位共享分别建立了独立实验。`[verified: docs/results/agent-tools-tripo-p2.md L9–L14；docs/results/dragon-raw-uv-reuse.md L3；docs/results/dragon-wholebody-refine.md L3–L13]`

## 固定几何部件共享

1. 从原始121岛出发，保留可用UV，跳过78面逐面隔离的旧修复。用原面集合定义翅膀、四肢和角刺，建立切口、共同域与锚点对应，失败优先整块修订。`[verified: docs/results/dragon-raw-uv-reuse.md L3–L10、L26]`
2. 固定父几何、目标与排布规则，比较保留原UV、OptCuts部件重展、自动几何复用、Agent锚点复用及解除共享控制。Agent路径得到117岛／98图块、19共享组，实际共享表面积44.6587%。`[verified: docs/results/dragon-raw-uv-reuse.md L14–L24]`
3. 目标先冻结，再看原色、目标和差异。白色特征错位的探索曾触发整块解除或修正，随后重新冻结，按同镜头像素与原面定位具体共享组。`[verified: docs/results/meshquery-uv-reconstruction.md L37；skills/reseam-uv/references/meshquery.md “从真实失败修订选区”中的白色边界定位段]`

高档颜色球仍有141个面包含范围外采样，保留整部件的外观取舍，原始和最终均3个单面岛。该路径颜色检查通过，旧渲染/接缝门禁拒绝，导出为diagnostic。`[verified: docs/results/dragon-raw-uv-reuse.md L24、L34–L36]`

## 镜像、真实合岛与中线薄条

1. 已授权的新镜像几何先建立134岛／67图块的整半共享，再处理头冠和脚部内部的多余接缝，得到92岛／46图块。脚底补必要切口后保留单脚一岛，并重新烘焙检查白爪。`[verified: README.md L39–L41]`
2. UV细线经展示编号到原面、三维来源与像素厚度核对，定位为6对裁切中线薄条。另建镜像变体调整平面，并沿源面重心坐标继承已有UV，得到80岛／40图块。`[verified: docs/results/dragon-midplane-cleanup.md L3–L15、L44–L46]`
3. 薄条修复后覆盖65.08%，颜色与接缝通过、严格渲染拒绝。该轮几何改变，来源与面身份随新变体重建。`[verified: docs/results/dragon-midplane-cleanup.md L24–L34、L46]`

## 跨部位角刺与排布取舍

在固定11,338面镜像几何上，将上背/中背、尾根/尾端的左右半边分别组合为四成员共享组，80岛保留，独立图块40→38。胸腹、腿、翼等局部展开试验仍保留未取得稳定优势的结果。`[verified: docs/results/dragon-wholebody-refine.md L3–L13]`

38图块版覆盖63.05%，同设置40图块最好对照64.53%。两版2K BaseColor完整mip均22,369,620字节，几何/颜色/接缝通过、严格渲染拒绝，Agent预览接受，完整PBR/GPU/人工待验收。此前1K预览有超阈值渲染探针，512近景模糊。`[verified: docs/results/dragon-wholebody-refine.md L5、L21–L30；README.md L43]`

## 迁移到当前资产

原UV保留任务先比较完整自然区域及未选区域，绑定当前源快照记录已有冲突。遇到白爪或翼缘错贴，先用同镜头像素拾取核实原面与共享组，调整整块对应或回退，再冻结目标。`[opinion]`

细线岛诊断同时查看UV和真实表面，并读取 `uv_label_mapping`、`midplane_strip_candidates`。按当前资产估计平面，依据实际问题选择重展开或已授权的几何变体。左右共享后可继续检查跨部位重复特征，分别评价图块数、覆盖、外观和字节。工具参数通过当前 `tools describe` 核实。`[opinion]`

## 证据入口

以下路径按 [索引的仓库定位方法](index.md) 读取。`[opinion]`

|用途|仓库根相对路径及读取位置|
|---|---|
|固定几何全过程与失败|`docs/results/dragon-raw-uv-reuse.md` 的“实测结果”“实际资产与外观”，明细 `artifacts/dragon_reuse_development/raw121_report01/report.md`。`[verified: docs/results/dragon-raw-uv-reuse.md L12–L36]`|
|固定几何真实模型/贴图|`artifacts/dragon_reuse_development/raw121_color02/agent_paired_2048_export/asset.glb`、`artifacts/dragon_reuse_development/raw121_color02/agent_paired_2048_bake/texture/BaseColor_mip0.png`。`[verified: docs/results/dragon-raw-uv-reuse.md L30–L31]`|
|合岛、脚底切口与镜像来源|`docs/results/dragon-final-workflow-retrospective.md` 的“真正合岛”“为什么合成一岛之后还要补脚底切口”。`[verified: docs/results/dragon-final-workflow-retrospective.md L78、L93]`|
|细线岛前后图与来源|`docs/results/dragon-midplane-cleanup.md`、`docs/assets/dragon-midplane-cleanup/uv_before_after.png`、`docs/assets/dragon-midplane-cleanup/dragon.glb`。`[verified: docs/results/dragon-midplane-cleanup.md L5、L28]`|
|38图块模型、贴图与对照|`docs/results/dragon-wholebody-refine.md`、`output/dragon-wholebody-refine/fewer-tiles-38/model.glb`、`output/dragon-wholebody-refine/before-after.png`。`[verified: docs/results/dragon-wholebody-refine.md L34–L35]`|
|完整制作图解|`output/pdf/dragon-wholebody-refine/镜像龙制作流程图解_含各UV实际渲染.pdf`，45页。`[verified: docs/results/dragon-wholebody-refine.md L36]`|
