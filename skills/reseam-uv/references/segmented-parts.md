# 分割部件输入、局部镜像与统一图集

用户提供 Tripo 等工具的分割结果时，先查 `asset.prepare_parts`。部件色是编号展示，不代表 BaseColor；先检查对象、原 UV 和实际贴图。支持一个多对象 GLB/FBX，或多个有明确组装位置的文件。`[opinion]`

## 准备合同

只用分割位置作参考时，对唯一来源传入 `snapshot`（可绑定 `state_hash`），顶层提供 `segmentation_reference`，省略 `parts`。按面中心最近分割表面赋予标签，三处内部采样记录边界分歧；超过原模型对角线2%的距离拒绝。读取 `segmentation_transfer.json` 的实际距离、争议面和空标签，再看编号图决定调整。标签是语义候选，不是原面角来源身份，不能用它自动焊接。`[verified: src/reseam/agent_tools/part_labels.py transfer_labels；opinion: Agent复核]`

`sources` 每项提供唯一 `id` 和 `asset.import` 参数，额外 `transform` 为导入世界坐标到组装坐标的可逆仿射矩阵。必须写 `placement="source_world"`，表示已经核对组装位置；归零部件没有变换时先解决来源。省略 `parts` 时每个导入对象成为一个部件。`parts` 每项以 `source` 选择来源，再以 `objects` 或 `faces` 选取面；默认要求完整、不重复覆盖输入。`[verified: src/reseam/agent_tools/parts.py prepare_parts/_extract]`

每个部件可写 `name`、`rationale`、`protected_faces`、`cap_faces`（均为该来源的面 ID）。`geometry` 可为 `{mode:"keep"}`、`{mode:"mirror_half",axis:"x",offset:0,keep_positive:true}`，或 `{mode:"mirror_from",master_part:"left",axis:"x",offset:0}`。平面在组装坐标中，`mirror_from` 的主部件必须保留几何；它替换所选目标部件，不额外保留旧目标。镜像需要顶层 `allow_geometry_change=true`，保护面冲突时拒绝。`[verified: src/reseam/agent_tools/parts.py _mirror/prepare_parts]`

`uv_policy=exclusive|candidate|shared` 独立控制复用：默认独占，candidate 仅是待研究的意图；shared 只为本次实际生成的精确镜像面建立权限。保留几何的近似部件通过 `reuse.check_pair/apply` 再授权。输出 `snapshot` 与同一几何的 `unshared_snapshot`，两者均未排布／未验收。`[verified: src/reseam/agent_tools/parts.py _groups/prepare_parts]`

`material_scope=basecolor` 保留有 UV 的来源并将不透明、0–1 UV 的贴图整理到单一来源图集；共享源图去重，同步保留不同对象的面角 UV。不同材质因子会显式转换到 RGBA8，记录在 `source_tiles`，不能声称颜色信号无量化。其他 PBR 通道不在这份参考内，原输入另存。`[verified: src/reseam/agent_tools/parts_material.py source_tiles/atlas_tiles]`

缺 UV／纹理时可显式 `material_scope=geometry`，以 xatlas 初始化缺失 UV。需要从另一个有贴图模型恢复颜色时，另传 `projected_reference`（单一纹理 GLB、已对齐组装坐标）及 `projection_max_fraction`；这采用冻结原部件表面的最近点投射，保留来源面／重心坐标和采样误差，不冒充原始逐面身份或外观通过。没有该字段则只输出几何能力，不生成假贴图。`[verified: src/reseam/agent_tools/parts.py projected_reference 分支；src/reseam/agent_tools/projected_source.py ProjectedSource]`

## Agent 执行

1. 看编号区域、实际来源颜色和接合处；记录各部件镜像／独占／共享理由。分割边界是范围参考，内部允许另加切口，一个部件可包含多个岛。`[opinion]`
2. 按part整理UV时，必须逐部件删除多余旧接缝、规划必要开口并重展开，不能只识别部件和保留旧UV后就宣称完成。使用 `uv.reparameterize pack_after=false` 暂存各部件UV，全部处理后再统一 `uv.pack`；暂存状态没有全局排布/交叠验收。默认OptCuts，必要时比较ABF/LSCM；记录每部件前后岛数、实际切口、拉伸、失败与回退，保留真实断开的部件片。`[verified: 用户“我的意思就是分part整理UV”；src/reseam/agent_tools/reparameterize.py pack_after；opinion: 工作流]`
3. 原几何与局部镜像几何各比较共享／不共享。共享控制从同一组 UV 解除共享，沿 `material.target` 的同一冻结目标采样；不要重新选择颜色目标后归因于 UV 共享。`[opinion]`
4. `uv.pack backend=blender_concave`，种子17，正式2048²、gap/border/padding 8px，外观策略 visual；冻结目标、看图、烘焙、检查 LOD0/1/2。所有修改新目录，失败保留。`[opinion]`
5. 逐部件及全模型查看六视角与接合处、眼嘴爪子近景，记录实际观察角度。独立回读真实导出 GLB。报告 UV 岛、独立图块、覆盖、共享面积和真实字节。`[opinion]`

`parts.json` 保存策略和来源，`part_lineage.npz` 保存每个输出面的来源文件、来源面、逐角重心坐标及反射标记。镜像后来源位置须再做声明平面反射才是实际新几何。未给分割前对应证据时，封口／新增面审计明确为 `not_checked`，不能据部件名称自动删面。`[verified: src/reseam/agent_tools/parts.py Part/prepare_parts]`

批量处理可用 [工作流工具](workflows.md) 的 `rebuild`、`preview`、`collect`、`report`。先 `preview stage=prepare` 查看冻结目标和差异图，记录实际 `target_review` 后再以 `stage=bake` 烘焙；诊断GLB和回读不自动记录visual keep。完整制作经验及失败图见 [恐龙部件案例](../examples/segmented-dragon-parts.md)。旧 `scripts.run_parts_preview` 保留历史复现用途，当前制作优先两阶段入口。`[opinion]`
