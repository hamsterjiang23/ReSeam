# ReSeam 制作案例索引

需要选择路线、复用历史做法或诊断下表问题时，读取匹配案例的相关阶段，并在任务记录中写明借鉴依据与当前资产差异。参数查询沿用 `tools describe`，接口与通用操作参见主 [Skill](../SKILL.md) 和各专项 references。`[opinion]`

## 按当前问题选择

|当前问题或目标|优先案例与阅读阶段|
|---|---|
|Tripo分割结果、按Part合岛、逐部件镜像与统一图集|[恐龙分割部件](segmented-dragon-parts.md) 的来源选择、逐部件重建与同几何对照。`[opinion]`|
|小碎片归位、删翼片露洞、腿一岛后白爪丢失|[恐龙分割部件](segmented-dragon-parts.md) 的小片分类和失败回退；先看实际前后图。`[opinion]`|
|图块减少但覆盖率或纹理字节未改善|[恐龙分割部件](segmented-dragon-parts.md) 的18→11图块对照与验收范围。`[opinion]`|
|保留可用原UV，尝试翅膀、四肢、角刺共享|[Tripo蓝龙](tripo-dragon-basecolor-reuse.md) 的固定几何阶段。`[opinion]`|
|自然选区重展开后，全模型碎片反而增加|[能量刀](energy-blade-reuse.md) 的完整旧岛闭包与开口修正，另看 [历史龙](legacy-dragon-reuse.md)。`[opinion]`|
|白爪、眼嘴或翼缘出现明显错贴|[Tripo蓝龙](tripo-dragon-basecolor-reuse.md) 的目标预检、像素定位与整组回退。`[opinion]`|
|已有镜像授权，希望扩大共享或继续真实合岛|[Tripo蓝龙](tripo-dragon-basecolor-reuse.md) 的镜像与合岛阶段，或 [防御塔](tower-pbr-reuse.md)。`[opinion]`|
|镜像UV出现细线岛、空白薄条|[Tripo蓝龙](tripo-dragon-basecolor-reuse.md) 的中线薄条诊断。`[opinion]`|
|已有左右共享，继续寻找跨部位重复角刺|[Tripo蓝龙](tripo-dragon-basecolor-reuse.md) 的40→38图块阶段。`[opinion]`|
|高低模PBR烘焙有黑线、黑翼或法线异常|[蓝龙PBR](blue-dragon-pbr-reuse.md) 的高低模投射阶段，结合 [防御塔](tower-pbr-reuse.md) 的来源与切线处理。`[opinion]`|
|无UV低模需要高模颜色及共享对照|[历史龙](legacy-dragon-reuse.md) 或 [能量刀](energy-blade-reuse.md)。`[opinion]`|
|尖刺、硬边、AO/normal线索与低拉伸展开|[Chloe红刺](chloe-hard-surface.md) 和 [Tripo匕首](tripo-spike-hard-surface.md)。`[opinion]`|
|OptCuts初始展开后是否值得继续合岛|[Tripo匕首](tripo-spike-hard-surface.md) 的19→18岛尝试与保留决策。`[opinion]`|
|整理PBR通道、制作过程图解与交付证据|[防御塔](tower-pbr-reuse.md) 的PBR处理与交付阶段。`[opinion]`|
|历史／baseline对比出图、具名GLB交付回读|[工作流工具](../references/workflows.md) 的 `report`／`collect`；制作决策继续查匹配案例。`[opinion]`|

## 八项案例与历史结果

|案例|历史结果与状态|
|---|---|
|[恐龙分割部件](segmented-dragon-parts.md)|真实合岛与小片清理后18岛／11图块，7共享组，2K UV＋BaseColor验收与Agent目视通过。`[verified: docs/results/dragon-parts-clean-final.md “逐部件结果”“验收、交付与继续处理”]`|
|[防御塔](tower-pbr-reuse.md)|最终镜像版48岛／24图块，Agent VISUAL_KEEP，数值DIAGNOSTIC。`[verified: docs/assets/tower-uv-comparison/README.md L15–L17]`|
|[Tripo蓝龙与镜像BaseColor分支](tripo-dragon-basecolor-reuse.md)|保留原UV路线117岛／98图块，镜像继续整理80岛／38图块。两条路线保留各自诊断状态。`[verified: docs/results/dragon-raw-uv-reuse.md L14–L34；docs/results/dragon-wholebody-refine.md L3–L30]`|
|[蓝龙完整PBR与镜像复刻](blue-dragon-pbr-reuse.md)|高低模投射外观未接受，自身材质镜像2K版60岛／30图块，Agent保留、数值诊断。`[verified: docs/results/dragon-pbr-reuse.md L3–L5；docs/results/dragon-legacy-method-replay.md L3–L21]`|
|[历史2,000面龙](legacy-dragon-reuse.md)|固定几何龙翼路线173岛／171图块，独立镜像展示版6岛／3图块，综合数值拒绝。`[verified: docs/results/meshquery-uv-reconstruction.md L14；README.md L23]`|
|[历史能量刀](energy-blade-reuse.md)|固定几何路线62岛／59图块，独立镜像展示版2岛／1图块，综合数值拒绝。`[verified: docs/results/meshquery-uv-reconstruction.md L13；README.md L23]`|
|[Chloe红刺](chloe-hard-surface.md)|少岛镜像对照2岛／1图块，硬边镜像共享104岛／52图块，数值均拒绝。`[verified: docs/results/chloe-hard-surface.md L36–L46]`|
|[Tripo带刺匕首](tripo-spike-hard-surface.md)|424面固定几何，19→18岛候选已试，最终保留OptCuts。`[verified: docs/results/tripo-spike-hard-surface.md L3；docs/results/optcuts-agent-uv.md L13–L24]`|

## 定位原始证据

案例正文与内部链接随Skill安装。各页“证据入口”中的 `docs/`、`artifacts/`、`output/` 路径均从ReSeam仓库根目录解析。先按 [环境预检](../references/environment-setup.md) 定位含 `pyproject.toml`、`src/reseam/`、`configs/runtime.example.json` 的checkout，再以该checkout为根读取实际文件。`[opinion]`

每例保留原报告、视图、真实贴图/模型及请求/失败入口。需要观察外观时打开对应图片或模型并记录实际看过的范围，文字摘要只用于定位。历史报告中的状态是对应轮次的记录，当前任务另行生成证据。`[opinion]`

先检查证据文件是否存在。归档型产物按原交付说明核对清单后恢复。文件缺失时标明“当前checkout缺失”，沿已读案例的决策经验继续处理可完成部分。复用历史请求前重新获取资产路径、面/边ID、状态哈希、平面及参数。镜像、颜色修改和验收要求依据当前任务确认。`[opinion]`
