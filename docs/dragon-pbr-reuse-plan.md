# 蓝龙完整 PBR 与共享执行记录

## 最新调整：复刻历史 40 图块路线

用户要求“之前龙(40岛的)那个不是挺好的吗，那个怎么做的，你这个就怎么做”。本轮据此在当前主低模另建镜像变体：保留半边、精确镜像并共享 UV、检查整块表面的多余接缝，再使用轮廓排布。原输入与此前固定几何实验不覆盖。`[verified: 本轮用户最新请求；opinion: 新变体实施范围]`

实际执行保留两条独立几何：BaseColor 冒烟使用正半边，完整 PBR 使用负半边并把当前模型的裁切平面设为 x=-0.001，避开源正侧唯一零切线角及已定位的中线薄条。PBR 同几何无共享／共享为 60／30 图块，使用低模自身材质、同面角对应；两个半边分支不能用作纯材质消融。最新图、场景与验收见[旧方法复刻结果](results/dragon-legacy-method-replay.md)。`[verified: round09_legacy_method/source_frame_choice_audit.json；mirror_pbr_clean/mirror_variant.json；pbr_control_pack17/packing.json；pbr_pack17/packing.json]`

历史成品实际为 11,338 三角形、80 连通岛／40 独立图块，已看图范围是 BaseColor；不能把它当作 3,113 面低模或完整 PBR 已验收证据。新路线先沿用当前低模自身的 Tripo 材质，复现同模型 UV 整理与共享，并单独检查 PBR。此前高模到低模投射的局部缺陷仍保留为独立未解决问题，不因材质来源改变而改写结论。`[verified: docs/results/dragon-midplane-cleanup.md 修复后的UV与外观；opinion: 比较边界]`

新请求、参数、来源和结果保存于 `artifacts/dragon-pbr-reuse/round09_legacy_method`；中线由当前几何检查确定，不沿用旧模型的偏移值，不预设新模型必须恰好得到 40 图块。`[opinion]`

用户于 2026-11-11 接受完整 PBR 烘焙、UV 整理及共享计划，验收环境为 Blender；游戏 Shader 与人工验收独立待完成。`[verified: 本轮用户接受计划]`

## 冻结合同

- 来源为 `model/dragon/pbr_bake_test/high/source_pbr`；主低模为 `low/pbr_tri_3113`，补充为 `low/pbr_mixed_4487`。原始输入不改写，哈希按资产 manifest 校验。`[opinion]`
- 正式输出使用全新 `artifacts/dragon-pbr-reuse/round01`；代码修正后的受影响实验在新轮次重跑，不覆盖已有证据。`[opinion]`
- 先修复异常连接并保留逐面位置、逐角 UV/法线与原面来源，再冻结共同低模。flat 法线为历史默认，新实验显式保留导入法线。`[opinion]`
- 四条受控路径：原 UV 必要修复、OptCuts 无共享整理、几何选择共享、Agent 选择共享。共用修复父几何、来源、候选、后端、预算和灯光。原 Tripo 低模 PBR 单列直接生成参照。`[opinion]`
- 先 128 冒烟，正式 2048 与 1024；间距16、边界8、扩边8像素；实际测量及 mip 0/1/2 结果分别报告。每操作300秒、每路径2400秒，同一假设最多三次修订。`[opinion]`
- 先腿脚，再翼和角刺；保护头脸、眼嘴、白爪。固定几何，不镜像改模，不强迫共享。共同目标必须先冻结，改色与重建误差分列。`[opinion]`
- 完整 PBR 对照同时看实际着色和各通道，使用八方向及特征近景、soft/side 灯光；Agent 目视与数值、人工及项目引擎验收独立。`[opinion]`

## 资源与执行边界

启动时可用内存约34 GiB，E盘剩余约948 GiB。Blender 实验每次一个进程、4线程；先小模型探测耗时和内存，再扩大。`[verified: 本轮 Win32_OperatingSystem FreePhysicalMemory=35619844 KB；Get-PSDrive E Free=1018060189696]`

各路径保存真实输入/输出、失败、耗时、代码和状态哈希。支持接口不等于真实资产完成；不接受仅 BaseColor 结果替代 PBR，不凭图块减少宣称节省。`[opinion]`

## 执行调整与最终口径

- 原 UV 2048² 在初始 300 秒预算下超时；正式 `round07` 烘焙预算改为 900 秒，同一轮八份 1K／2K 烘焙使用相同上限。旧超时保留。`[verified: artifacts/dragon-pbr-reuse/round04/tri_original2048/bake/result.json reason；round07 八份 */bake/request.json timeout_seconds=900]`
- 渲染任务独立运行，每个 Blender 进程限制 4 线程；部分独立任务并行执行，没有使用 GPU。初始单进程约束属于启动时的资源安排，最终耗时按实际每操作记录，不把并行总用时等同于单路径计算成本。`[verified: scripts/artist_render_worker.py Cycles CPU；src/reseam/agent_tools/artist_review.py preview 启动参数；opinion: 成本口径]`
- 主无共享与腿部共享均使用统一密度矩形排布。冻结 2048² UV 后直接在 1024² 重烘焙，实际间距约由 16 像素变为 8 像素，边界由 8 变为 4；扩边仍为 8。并非每个尺寸重新排布到相同像素间距。原 UV 的零间距冲突另列保留，未声明严格通过。`[verified: round07/final_matrix.json rows[].measured_actual_resolution_geometry、actual_pixel_settings 与 geometry_profile_audit]`
- 1K／2K 真实贴图与完整 mip 已写出，mip 0／1／2 为数值采样诊断；Blender 读取 GLB 的 LOD0 贴图，不把缩小屏幕预览声称为显式 mip 或游戏采样验收。`[verified: round07/final_matrix.json；scripts/artist_render_worker.py rendered.distance_mode]`
- 实际局部外观检查未通过，终态记录实验执行完成及质量未接受，保留未达到的计划条件，不把诊断输出交付为生产通过版本。`[verified: artifacts/dragon-pbr-reuse/round07/main_appearance_review.json status；opinion: 状态含义]`
