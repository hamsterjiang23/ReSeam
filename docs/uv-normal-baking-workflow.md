# UV 与法线烘焙：ReSeam 工作流程与预检

本流程服务于切线空间法线烘焙：先确认实际使用的几何、法线与 UV，再检查投射，最后看实际贴图与渲染。`material.preflight` 只输出证据，不改 UV、不修模型、不授予导出或外观通过。`[verified: src/reseam/agent_tools/preflight.py preflight、normal_uv_audit]`

用户提供的 27 步教程是问题来源，其中的经验参数不作为项目默认规范。[原文摘录与截图索引](assets/uv-normal-baking-reference/README.md)保留原始表述；[实际运行结果](results/normal-baking-preflight.md)独立记录本机验证。`[opinion]`

## 按生产目的选择工具

| 环节 | 要确认什么 | 工具与证据 | 出问题后如何处理 |
|---|---|---|---|
| 输入检查 | 变换、面顺序、原始身份、材质来源与法线是否可用 | `asset.import collect_normal_evidence=true`；导入日志、不可变快照与输入哈希 | 非流形/退化输入先定位，保留失败；修复必须使用独立变体。`[opinion]` |
| 法线与切缝 | 相邻面实际法线是否断开，UV 是否沿该边拆点 | `material.preflight checks=[normal_uv]`；`mesh.hard_edges` 提供候选依据 | 用 `seam.select/preview` 审阅，再用现有改缝/展开工具；不把候选边自动全部切开。`[opinion]` |
| 展开与排布 | 畸变、原边连通、许可共享、实际留边 | `uv.inspect`、`view.render`、预检 `uv_sampling` | 原 UV 可用时保留；局部 `uv.reparameterize`，之后 `uv.pack`，重新测量。`[opinion]` |
| 投射检查 | 射线能否命中预定高模，Cage 是否有问题 | 预检 `projection`；逐样本 JSONL、问题面、射线图 | 检查部件配对、空间关系及前后距离；不得把最近点近当成射线命中。`[opinion]` |
| 烘焙验收 | 实际贴图、法线约定、接缝、所需 mip 和目标渲染表现 | `material.bake`、`quality.validate`；实际模型/贴图与同视角图 | 数值诊断、Agent 目视保留、人工、PBR、GPU 分别记录，继续沿用项目现有验收策略。`[opinion]` |

## 教程需要纠正的地方

### 法线、平滑组与 UV 是不同数据

3ds Max 的相邻面只要共享至少一个平滑组就会平滑，单个面可以属于多个组；不能仅看两面的组列表是否完全相同。预检使用导入后的逐角法线，不猜组编号。参见 [Autodesk：Viewing and Changing Smoothing](https://help.autodesk.com/cloudhelp/2023/ENU/3DSMax-Modeling/files/GUID-1DB2E3C2-CE68-4AF7-899E-01B90F7EB320.htm)。`[verified: 官方文档“Viewing and Changing Smoothing”正文相邻面共享组规则]`

切线法线烘焙中，硬边应有 UV 接缝；UV 接缝不要求同时设硬边。六个面均硬的箱体应拆成六个面岛；给整个把手一个平滑组并不会自动让把手内部所有折角变硬。参见 [Marmoset：Hard Edges and UVs](https://marmoset.co/posts/toolbag-baking-tutorial/)。`[verified: 官方教程“Hard Edges and UVs”；opinion: 工具箱应用例]`

ReSeam 历史默认目标使用 `frame_arrays` 生成逐面平面法线；蓝龙 PBR 新路径显式 `target_normal_policy=imported` 保留逐角法线并按 Blender 本地切线约定生成当前 UV 的切线。报告同时列出 `normal_uv.source` 与实际选定的 `normal_uv.target`。历史 flat 圆柱失败不能直接解释为源模型做错，旧预检报告仍保留。`[verified: src/reseam/budget_material.py frame_arrays 导入法线分支；src/reseam/agent_tools/preflight.py normal_uv_audit]`

`mesh.hard_edges` 为选缝候选过滤可靠的原多边形内部对角线；预检不会用这条规则遮蔽真实法线断开。sharp 标记、几何二面角、导入逐角法线各自保留来源。`[verified: src/reseam/agent_tools/hard_edges.py classify_edges；preflight.py normal_uv_audit]`

下面是两种不同法线合同下的箱体展开示意；这是预期行为说明，实测硬边工具箱见结果报告。六面硬边时每个方格是一个独立岛；如果相邻边实际保持软法线，可以保留五条面间连接并沿其余七条箱体棱切开，形成连续十字形展开，而不是只剪一条棱就变成矩形。软法线立方体只用于解释数据关系，不代表推荐把金属箱的所有棱平滑。`[opinion]`

```text
六面硬边： [左]  [前]  [右]  [后]  [上]  [下]   六个岛

软边连续展开：        [上]
               [左][前][右][后]              一个连通岛
                   [下]
```

### 拆点、间距、扩边和 mip 分别检查

UV 索引不同只表示已经拆开；两侧 UV 坐标仍可贴在一起。`gap_pixels` 是岛间距，`border_pixels` 是图集边界，`padding_pixels` 是烘焙扩边，三者分别记录。`[verified: preflight.py normal_uv_audit、uv_sampling_audit]`

在 mip 层 L，预检按原像素距离除以 2^L 显示有效间距。小于一个像素或间距小于两侧扩边之和会产生提示，但这些提示不构成过滤质量证明。“1 像素足够”“手游不留空隙”“必须把所有 UV 打直”均不作为通用验收条件。实际过滤仍要看目标引擎、压缩和 mip。参见 [Blender：Bake Margin](https://docs.blender.org/manual/en/4.5/render/cycles/baking.html)。`[verified: preflight.py uv_sampling_audit；opinion: 项目验收取舍]`

### 高低模、名称与 Cage

高低模需要适合投射的空间对应；不要求高模处处包住低模。Cage 控制投射起点和方向，扩大 Cage 也可能打到邻近错误部件。参见 [Marmoset：Low Poly Size / What Is A Cage?](https://marmoset.co/posts/toolbag-baking-tutorial/)。`[verified: 官方教程同名小节]`

Painter 需要启用 `Match → By Mesh Name` 才按名过滤；`_low/_high` 是可配置默认后缀，一个低模可匹配多个高模。例如 `body_low` 可以匹配 `body_high`、`body_high_1`。ReSeam 预检使用显式 `part_pairings`，不会凭命名自动宣称配对成功。参见 [Adobe：Matching by Name](https://experienceleague.adobe.com/en/docs/substance-3d/bakers/features/matching-by-name)。`[verified: 官方文档“How Matching By Name Works / Enabling Matching By Name”；preflight_projection.py projection_audit]`

Painter 的 `Missing seams on hard edges` 显示硬边缺少 UV 接缝，`Matching error` 显示高模落在 Cage 壳外的区域；显示颜色可改，不能只写“紫灯/红灯”。参见 [Adobe：Baking visualization settings](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/baking/baking-visualization-settings)。`[verified: 官方文档“High definition mesh / UV seams”]`

原教程第 25 张的 Bake Project、High/Low 文件夹和 Moderate Padding 是 Toolbag 界面，不是 Painter；第 5 张“存储/切换变换目标”对应 ZBrush Morph Target，不是 Transpose Target。Turn to Poly 的 Max Size 限制输出多边形边数，不能当作自动删除废面的命令。参见 [Toolbag 烘焙设置](https://docs.marmoset.co/docs/baking-attributes/)、[ZBrush Morph Target](https://help.maxon.net/zbr/en-us/Content/html/reference-guide/tool/polymesh/morph-target/morph-target.html)、[Autodesk Turn To Poly](https://help.autodesk.com/cloudhelp/2023/ENU/3DSMax-Modifiers/files/GUID-3BCBC9E5-8516-4282-BDB3-5DB2B587D16D.htm)。`[verified: 用户截图05、25；所链官方文档同名设置]`

## 调用方式

参数以 `uv run reseam tools describe material.preflight` 为准。每次运行使用新输出目录，下面路径为填写示例。`[verified: src/reseam/agent_tools/registry.py material.preflight；opinion: 示例路径]`

```json
{
  "snapshot": "E:/ReSem/artifacts/my_asset/low/snapshot",
  "state_hash": "填写低模当前哈希",
  "high_snapshot": "E:/ReSem/artifacts/my_asset/high/snapshot",
  "high_state_hash": "填写高模当前哈希",
  "checks": ["geometry", "normal_uv", "uv_sampling", "projection"],
  "bake_config": {
    "resolution": 1024,
    "gap_pixels": 8,
    "border_pixels": 8,
    "padding_pixels": 4,
    "mip_levels": [0, 1, 2]
  },
  "projection": {"front_fraction": 0.02, "back_fraction": 0.02, "timeout_seconds": 240},
  "blender": "E:/Blender 4.5.11/blender.exe",
  "timeout_seconds": 300,
  "views": true
}
```

```powershell
uv run reseam tools run material.preflight --request E:/ReSem/artifacts/request.json --output E:/ReSem/artifacts/preflight01
```

高低模分别通过 `asset.import` 创建快照；FBX 导入需显式 `collect_normal_evidence=true` 才有逐角法线证据。GLB/旧快照未记录该信息时源法线项是 `not_checked`，不代表模型光滑。`[verified: assets.py import_asset；hard_edges.py source_fields]`

需要显式 Cage 时，在导入 Cage 的请求中设置 `cage_for_snapshot` 和 `cage_for_state_hash`，声明保持低模原顶点顺序。导入器验证顶点数量与三角面索引/顺序，预检再核对绑定哈希；它不从空间最近点恢复身份，也无法仅凭相同数组形状证明用户未重排顶点。随后把得到的快照作为 `cage_snapshot`。`[verified: assets.py import_asset 的 cage_for_snapshot 分支；preflight_projection.py projection_audit]`

部件隔离使用 `part_pairings=[{"low_faces":[0,1],"high_objects":["body_high","bolt_high"]}]`；名称来自高模快照 `metadata.objects[].node`，未分配的低模面仍查询全部高模。默认不按名字猜配对。`[verified: preflight_projection.py _objects、projection_audit]`

## 实际射线做了什么

默认每个低模三角形固定取 4×4 个内部重心样本。自动 Cage 沿原始顶点邻接上的面积加权几何法线外移；前、后距离均以低模包围盒对角线归一化，默认各 2%。这是一组可复现诊断参数，不是资产通用最佳值。`[verified: preflight_projection.py projection_audit；preflight_projection_worker.py barycentric_samples；opinion: 默认值用途]`

射线从对应 Cage 点射向低模点，继续向内至声明后距离。结果区分：范围内命中、背面命中、超出射程、未命中、零方向。范围内未命中时额外延长两倍对角线只用于定位 `out_of_range`，不会把它计为成功。命中面 ID 属于导入后的高模三角形索引。`[verified: scripts/preflight_projection_worker.py run]`

Cage 报告退化、相对低模的局部方向反转、非相邻三角形相交；正常共享顶点的面对应被排除。显式部件过滤互不相交时，跨组 Cage 交叠列入 `isolated_by_disjoint_part_filters`，保留记录但不误判为相互投射污染。`[verified: scripts/preflight_projection_worker.py run、intersects]`

此检查不验证连续曲面覆盖、不保证内部拓扑不自交，也不声称与 Cycles 每个像素的采样相同。背面、接触处及稀疏细节仍要看实际烘焙。`[opinion]`

## 输出与能力边界

| 输出/范围 | 含义 |
|---|---|
| `preflight.json`、`report.md` | 配置/状态哈希、各项状态、执行是否完整；最终验收仍为 null/NOT_RUN。`[verified: preflight.py preflight、write_report]` |
| `projection/samples.jsonl` | 每条射线及命中对象/面/距离/夹角；最近点距离单列。`[verified: preflight_projection_worker.py run]` |
| `projection/geometry.npz`、`provenance.json` | 实际低/高/Cage 世界坐标数组与后端/源码哈希；不二次归一化。`[verified: preflight_projection.py projection_audit]` |
| `views/`、`normal_vectors.png`、`projection/rays.png` | 现有可见性正确的错误面/接缝图，以及明确标为 X-ray 的法线/射线示意；镜像继承证据仅保留同侧源空间夹角，不画未经变换的源法线箭头。线图不作为着色质量证明。`[verified: preflight.py normal_vector_picture；preflight_projection.py _ray_picture]` |
| 超时 | 保留已写样本和阶段报告；`complete=false`，不完整项不得通过。最外层进程截止时读取已有 preflight.json 与 JSONL。`[verified: preflight_projection.py projection_audit；runner.py run_tool]` |
| UDIM / 多材质路由 | 首版仅完整检查单个 0–1 材质图集；超出该范围返回 not_checked。几何射线仍可独立运行。`[verified: preflight.py uv_sampling_audit]` |
| 自定义法线导出 / 外部引擎 | 当前目标审计限定 ReSeam 的 flat frame；不自动修改导出法线，不模拟 Max/SP/Toolbag 界面。`[verified: preflight.py normal_uv_audit；opinion: 范围]` |

## 复现示例

在已配置 Blender 的环境运行以下命令。脚本生成硬边工具箱、光滑圆柱、故意错位高模，调用正式 JSON 工具，并执行两份 128² 法线烘焙与同镜头对照；每次指定新目录。`[verified: scripts/preflight_examples.py main]`

```powershell
uv run python scripts/preflight_examples.py --output artifacts/normal-preflight/my_round --bake
uv run pytest tests/test_material_preflight.py tests/test_agent_tools_hard_edges.py -q
```

实际产物及失败历史见[验证报告](results/normal-baking-preflight.md)。新的检查是反馈入口，不替换 `material.bake`、真实 mip/PBR 验收或艺术家判断。`[opinion]`
