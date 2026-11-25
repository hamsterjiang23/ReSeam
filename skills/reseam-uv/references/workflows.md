# 可复用制作工作流

这四个入口用于重复的部件制作、回读和报告任务；底层仍调用 `reseam tools`。先根据 [案例索引](../examples/index.md) 选择制作路线，接口参数以包内请求模板和当前入口帮助为准。工具完成、数值验收、Agent目视、人工与PBR/GPU状态分别记录。`[opinion]`

## 启动与依赖

安装版Skill携带 `assets/runtime-source.zip` 和 `assets/runtime-manifest.json`。启动器比较源码文件内容哈希，匹配时复用所选checkout，否则在独立用户缓存中准备匹配源码；不覆盖用户checkout。`--repo` 指向候选源码目录，不表示忽略源码不匹配。仓库内不保存重复的生成ZIP，开发时直接使用下面的模块入口。`[opinion]`

```text
python -X utf8 <已安装Skill>/scripts/reseam_workflow.py --check --repo <ReSeam源码目录>
python -X utf8 <已安装Skill>/scripts/reseam_workflow.py rebuild --request <绝对路径.json> --output <新目录> --repo <ReSeam源码目录>
uv run python -m reseam.workflows rebuild --request <绝对路径.json> --output <新目录>
```

`--check` 检查源码与可用依赖，不能替代后端运行冒烟。运行需要uv、Python3.11及匹配源码的锁定依赖；`report` 使用reports可选依赖，执行 `uv sync --locked --extra reports`，启动器按需使用 `uv run --extra reports`。Blender与OptCuts沿 [环境预检](environment-setup.md) 和 [全量安装合同](full-installation.md) 配置，不能把源码解包成功写成全部后端可用。所有文件引用填写绝对路径，输出必须是尚不存在的目录。`[opinion]`

|入口|最小请求|主要产物|
|---|---|---|
|`rebuild`|[重展开模板](../requests/rebuild.json)|`rebuild.json`、逐部件请求与结果、新快照、失败及父状态回退记录。`[opinion]`|
|`preview`，`stage=prepare`|[准备模板](../requests/preview-prepare.json)|`preparation.json`、统一排布、UV检查、部件编号、冻结目标和原始／目标／差异六视角。`[opinion]`|
|`preview`，`stage=bake`|[烘焙模板](../requests/preview-bake.json)|`preview.json`、实际BaseColor、实际六视角、诊断GLB和独立回读检查。`[opinion]`|
|`collect`|[交付模板](../requests/collect.json)|`model-parts.glb`、`model.glb`、`texture/`、`parts.json`、`visual-review.json`、`verification.json`。`[opinion]`|
|`report`|[报告模板](../requests/report.json)|四张总览PNG、`comparison-book.pdf`、`index.html`、`evidence.json`。`[opinion]`|

模板中的示例目录、部件ID和哈希均须替换。空的 `reviewed_cameras` 和提示性notes不能作为已看图记录；先观察真实图片再填写。`[opinion]`

## 逐部件重展开：`rebuild`

输入绑定 `snapshot` 与当前 `state_hash`。省略 `parts` 时处理快照元数据中的全部部件；指定时使用 `[{id,backend?,seam_edges?,faces?}]`，`faces` 若提供必须等于当前部件的面集合。默认后端OptCuts；公共 `resolution`、`profile`、`blender`、`timeout_seconds` 按当前任务填写。小样模板显式设置 `resolution=512`，不要只在profile中写尺寸。`[opinion]`

每个部件的 `seam_edges` 使用当前原顶点ID对，显式空列表表示重建旧内部连接；自动开口只在该部件显式提供 `auto_opening={axis,side,longitudinal_axis?}` 时尝试。坐标轴为x/y/z，side为positive/negative，方向由当前资产决定，不照搬恐龙。旧哈希、未知面或范围不匹配先拒绝；后端失败保留日志并退回该部件父状态。`[opinion]`

结果中的 `snapshot` 和 `state_hash` 供下一步使用；`decisions`、`failed_parts` 记录实际操作。`requires_pack=true`、`requires_rebake=true`、`visual_accepted=false` 表示只是暂存部件UV。随后统一排布、烘焙和目视检查，不把工具返回OK当作成品认可。`[opinion]`

## 小样与目标检查：`preview`

准备阶段输入 `stage=prepare`、`snapshot`、`state_hash`。`resolution` 默认512，`padding_pixels` 默认8，`packing_backend` 默认blender_concave、`packing_seed` 默认17；`profile`、`blender`、`cameras`、`size` 可显式配置。六视角默认front/back/left/right/upper/lower，当前任务可指定镜头与近景。`[opinion]`

读取 `preparation.json`，查看其部件编号、UV和原始／冻结目标／差异图。若明显特征改变，回到父状态修订；若保留，记录实际 `target_review={target_hash,decision:"keep",reviewer:"agent"|"human",reviewed_cameras:[实际名称],notes:实际观察}`。Agent不能代填人工判断。`[opinion]`

烘焙阶段输入 `stage=bake`、`preparation` 和上述 `target_review`，`reviewed_cameras` 必须覆盖准备记录的全部实际镜头。它使用准备记录中冻结的配置，除 `timeout_seconds` 外不能在此阶段替换分辨率、镜头、目标或排布策略；需要改变时新建准备阶段。读取实际烘焙图与回读结果后，外观评审仍需独立执行；诊断导出不自动变成 `artist.review` 或完整PBR认可。`[opinion]`

## 具名多对象交付：`collect`

显式提供 `snapshot`、`state_hash`、`model`、`export_manifest`、`parts`、`bake`、`review`。`validation`、`uv_image`、`preview_image` 可选，路径来自当前结果，不按目录名称猜测。先以 `artist.compare/review` 生成实际整模型保留评审，要求其 `scope=basecolor_reuse`、`whole_surface_views=true`、`all_prepared_views_reviewed=true`；目标预检的 `target_review` 不能替代这份烘焙后评审。`[opinion]`

交付核对模型与导出／烘焙／评审记录的哈希、部件完整且无重复的面覆盖，以及多对象GLB独立载入后的逐角位置、UV和贴图。记录保留实际评审者、数值状态和材质范围；只有BaseColor的结果不补称PBR，缺数值验收也不补造通过。`[opinion]`

## 历史与baseline报告：`report`

`cases` 为有序记录列表，每项至少提供 `id` 与 `title`。按实际可用证据补 `snapshot`、`bake`、`validation`、`texture`、`images={basecolor,checker,uv,texture}`、`evidence`、`record_path`、`status` 和 `notes`。`record_path` 是在证据JSON中逐级取记录的字符串／整数数组，指标取自该实际记录；未知值保留未知。`[opinion]`

`overview` 和 `history` 分别指定case ID顺序，省略时使用全部；不写死列数。`render_missing` 默认false，仅用已有图片；显式true时可调用 `view.render`／`uv.inspect` 补图，仍不重新展开、排布、烘焙或验收。`cameras` 可分别配置basecolor和checker镜头，`font` 可指向可用中文字体。`[opinion]`

报告分别输出 `all-methods-basecolor.png`、`all-methods-checker-uv.png`、`history-basecolor.png`、`history-checker-uv.png`，并写PDF、HTML与证据JSON。从实际快照、烘焙和验收记录读取几何／状态／目标哈希及协议，标明同几何、跨几何和不同协议。失败路径显示原始状态与原因；没有合格贴图时不补造成功图。生成前后核对源文件哈希，历史结论保持原身份。`[opinion]`
