# ReSeam

[仓库内龙模型交付包](docs/assets/dragon-uv-final/README.md)包含UV数据对照图、模型前后渲染、最终GLB、真实BaseColor完整mip及核验摘要。当前模型为外观预览，严格渲染探针未全通过。`[verified: docs/assets/dragon-uv-final/manifest.json、quality-result.json]`

最新[镜像龙真实合岛](docs/results/dragon-uv-island-merge.md)：保持镜像变体的几何不变，头冠每侧16→1岛、脚部每侧7→1岛，整龙134→92连通岛、67→46独立图块，均减少31.3%；所有面仍参与左右共享。脚底补切口后仍为单岛，已输出2048真实贴图及GLB。当前为Agent看图接受的预览，严格渲染探针仍拒绝，原已验收版本保留。`[verified: island_merge01/final_uv_audit.json、summary.json、appearance_review.json；opinion: 外观判断]`

此前[整龙镜像共享与紧凑排布](docs/results/dragon-whole-half-reuse.md)：用户授权的新镜像模型包含134个连通UV岛，67对实际堆叠后只排67个独立图块；同几何无共享为134图块。2048轮廓排布占用率由矩形排布的27.98%提高至57.95%，已写出真实贴图与GLB并通过当前UV＋BaseColor有限探针协议。原模型和固定几何结果保留，人工／PBR／引擎验收独立。`[verified: whole_half_formal_shared01 两个pack/geometry.json及shared_blender_concave/validate/validation.json；whole_half01/independent_audit/results.json]`

镜像龙另附1024节省型预览，RGBA8完整mip为5,592,404字节，较2048的22,369,620字节减少75%；Agent四视角初筛可接受，两个mip渲染探针仍超原阈值，不视为完整数值通过。512近景模糊和1536后端不支持均保留记录。`[verified: whole_half01/appearance_review.json selected_small_preview；whole_half_sizes01/shared_compact_1024/validate/validation.json；whole_half_sizes01/matrix.json]`

固定几何路线的[保留原始121岛的部件复用结果](docs/results/dragon-raw-uv-reuse.md)：跳过异常面逐面隔离，得到117连通岛、98独立图块、19组真实共享；原始与最终均3个单面岛。五条2048对照与两项512冒烟、实际GLB／贴图和全部拒绝均保留，当前交付为Agent看图接受的UV＋BaseColor预览。`[verified: raw121_report01/report.md、preservation.json及appearance_review.json；opinion: 外观判断]`

[部件复用实测结果](docs/results/dragon-component-reuse.md)已附完整龙GLB、真实2048贴图和五路径×两尺寸对照：固定修复父资产203图块降至150，保留19组共享；低／中／高颜色球及全部球外、数值拒绝项分别记录。当前交付为Agent看图接受的UV＋BaseColor预览。`[verified: components_report02/report.md、export_integrity.json；opinion: 交付口径]`

2026-10-30按[部件重展开路线](docs/dragon-component-reuse.md)推进双翼、四肢和角刺：逻辑区域保留原面身份，显式切口重建 UV，锚点共同域完成堆叠；真实结果、失败和外观预检在新轮次单独记录。`[verified: 用户接受的部件实施计划；opinion: 实施记录方式]`

颜色复用支持[低／中／高颜色球容忍度](docs/dragon-component-reuse.md#颜色球容忍度)：`reuse.check_color` 检查原色到冻结共享目标的Oklab距离，球内满足容忍度，球外区域交给Agent看图判断；默认中档，支持自定义半径。`[verified: 用户新增颜色球要求；color_tolerance.py check_color/tolerance；opinion: 球外判断方式]`

当前正在实施[整龙 LLM UV 复用与 Baseline 对照](docs/dragon-llm-reuse-plan.md)：在 `tripo_p2` 固定规范化几何上，由 Agent 看图选区域、解释失败并修订，允许细小颜色差异对称化，保护头脸等特征。无共享修复、OptCuts 无共享修复、纯几何复用和 Agent 迭代复用均须独立记录；关键步骤前后证据和真实贴图是必交付项。此入口表示实施目标，不表示整龙共享、颜色验收或存储节省已经完成。`[verified: 用户接受的整龙计划及 Baseline／修正记录要求]` `[opinion]`

本轮交付范围为 UV＋BaseColor；新增 `region.define`、区域展开、源颜色目标冻结及颜色复用验收流程，参数以 `tools describe` 为准。用户明确允许 OptCuts 作为本轮实验展开后端，此例外不改写历史自研求解器、V1/V2 或完整 PBR 的验收口径。`[verified: 用户本轮允许 OptCuts 等几何工具及“先完成 UV＋颜色复用”选择；registry.py 新工具登记]`

Agent UV 工具集入口：`uv run reseam tools list`。使用 [运行说明](docs/running-agent-tools.md)与[reseam-uv Skill](skills/reseam-uv/SKILL.md)完成导入、候选、共享、排布、真实烘焙和独立验收导出；[真实两模型报告](artifacts/agent_tools_v1/round01/report.md)附实际贴图。工具闭环、复用收益与艺术验收分别判定，不替代下述完整求解器目标。`[verified: src/reseam/cli.py tools 分支；docs/running-agent-tools.md 对照运行]`

新增用户提供的 [tripo_p2 龙实验](docs/results/agent-tools-tripo-p2.md)：原始资产独立保留，已测试 UV 问题面隔离、镜像对应与 256/512/1024 真实颜色贴图；完整材质与缩图质量未通过，不计入已验收收益。`[verified: artifacts/agent_tools_v1/tripo_p2_audit.json color_experiments/full_material_supported]`

用户已将下一阶段明确为[加入拓扑优化，先做好无共享烘焙](docs/bake-topology-plan.md)。历史500/2000面低模保持原样，独立变体先检查面形、法线与投射，再测缩图和区域复用；不能将拓扑变化归为固定几何UV算法的收益。`[verified: 本轮用户选择“加入拓扑优化，先做好烘焙”；assets/v1/README.md 多轮测试规则2]`

当前执行目标：[完整实现新求解器 v1 并与 baseline 对比](goal/budget-v1-implementation.md)。继续使用固定 energy_blade 与 dragon 调优；完整实现与实际对比报告均为完成条件。`[verified: 用户2026-10-28本轮授权；goal/budget-v1-implementation.md 完成条件]`

60项旧对照已因审计缺项中止，原始记录与[实际贴图](artifacts/budget_v1/budget_v1_comparison_round05/texture_gallery_after_audit/gallery.md)保留，仅作诊断。式(19)恢复、完整排序、baseline保护及对应记录/截止时间问题已修复，状态见[完整性后续](docs/results/budget_v1_completion_followup.md)；尚未完成新冻结对照，也没有合格缩图收益。`[verified: artifacts/budget_v1/budget_v1_comparison_round05/audit_interruption.json reason；docs/results/budget_v1_completion_followup.md 审计项与实际检查]`

早期几何内核与 BaseColor 开发诊断见[首阶段开发说明](docs/running-budget-v1.md)：真实龙翼缩减区域几何通过、颜色拒绝。该页和紧凑摘要保留对应早期实现状态，不用它们代表当前完整矩阵的结果。`[verified: docs/results/budget_v1_development_summary.json scope 与 full_v1_implemented=false]`

## 对话要点：LLM 辅助 UV 复用修复

用户给出的业务需求是优化 AI 生成游戏资产的贴图清晰度与存储成本：**只研究同一模型内部的区域复用**，例如左右翅膀、四肢、角和重复装饰。既比较相同贴图预算下的有效纹素密度，也比较满足相同画质时的最小实际纹理字节数；岛少、重叠多或图集看起来更满都不能单独证明收益。默认输出普通材质 UV，不依赖特殊解码 Shader。`[verified: 本轮用户“只关注同一模型内的区域复用”；derivation/v1.md §1–§3；用户粘贴的《LLM的UV复用修复实验》业务描述]`

用户粘贴的文章展示了重复部件识别、UV 岛减少和复用标记，但未给出可核对的作者算法、代码与完整实验配置。两份补充对话中的流程是可实施的推测方案，不能写成已确认的作者原始实现，也不能承诺任意 AI 模型都可完整复现。下面的设计与上游参考不是作者实现证据；ReSeam 的工具调用、Agent 实际决策和真实模型复用结果须分别用各轮产物证明。`[verified: 本轮粘贴文章及第一份附件“根据结果反推……不是确认过的作者原始实现”；其余为opinion]` `[opinion]`

### 怎么实现：语义提案与几何执行分工

**LLM/VLM 提出哪些部件值得复用，几何求解器验证并生成 UV，实际烘焙与渲染决定是否接受。** 不把 LLM 直接输出的大量浮点 UV 坐标作为默认方案，也不把“左右翅膀”“同组”或模型自报置信度作为重叠许可。`[opinion]`

1. **准备资产与烘焙基线**：保存原模型、原始 UV、全部实际材质及哈希。低模可来自 Tripo 等上游重拓扑，也可在有必要时本地生成独立 variant；不要求无条件再减面。按用户最新选择，先检查拓扑、法线和高低模投射，建立可用的无共享烘焙，再做缩图/复用。拓扑改变后重建面身份和对应，不迁移旧硬约束冒充仍有效。`[verified: 用户“加入拓扑优化，先做好烘焙”；assets/v1/README.md 多轮测试规则2、9；其余为设计opinion]` `[opinion]`
2. **给模型可追溯的信息**：提取原面/面角 ID、连通区域、现有 UV 岛、邻接、面积、空间位置与材质统计，生成多视角图、带区域编号的模型图和 UV 图。语义区域可以跨越当前碎片岛；不能让既有碎片划分永久限制候选。`[opinion]`
3. **生成结构化复用提案**：LLM/VLM 返回源/目标区域 ID、mirror/repeated/approximate 关系、语义、证据图、置信度以及必须独占的纹样。记录模型版本、提示词、输入与输出、调用耗时和费用；置信度只是提案信息，不是经过标定的可行性概率。人工指定区域也必须标明来源。`[opinion]`
4. **验证曲面与外观兼容**：严格同拓扑复制只作便宜基线；近似部件使用配准和曲面对应，保存目标面及重心坐标，允许不同顶点数和三角划分。最近点、ICP 或有限往返误差只作证据，还要检查区域连续性、映射折叠、最终逐面仿射 UV 的表达误差及真实交叠单元；颜色、法线和 ORM 的兼容分别验收。`[verified: AGENTS.md 实现不变量；derivation/v1.md §4–§6；其余为设计opinion]` `[opinion]`
5. **修复接缝、共享与排布**：对通过验证的区域建立共同参数域；处理必要边界切缝、连贯岛与整面独占例外。仅给兼容组许可重叠；打包时将整组视为一个对象，保留成员的相对映射，复查畸变、纹素密度、间距和边界。候选失败恢复完整状态，不能只恢复 UV 忘掉组、对应或纹理。`[opinion]`
6. **重新生成纹理并验收**：UV 改变后，原纹理须按原始外观重新烘焙/重采样。共享成员先分别烘焙与比较，再形成共同纹理或退回独占，避免多个成员往同一图块写入而产生顺序覆盖。检查镜像 TBN、法线约定、AO 差异、颜色空间、过滤和 mip，最后用独立视图/灯光及目标引擎检查。输出模型、真实贴图、UV/接缝/复用标记、质量与成本报告。`[opinion]`

### 作者回复与最小复现路线

2026-10-29用户补充的[作者回复截图](docs/assets/llm-uv-reference/author-reply.png)写明：“基本靠llm自己的判断，人工干预不太多”。这支持作者自述由LLM主导判断，但未公开具体模型、提示词、程序、对应算法或烘焙验收。截图作为用户提供的来源保留，[清单](docs/assets/llm-uv-reference/manifest.json)记录原文件与SHA256；不能据此声称已独立复现作者系统。`[verified: author-reply.png 作者回复文字；manifest.json]`

[UV前后图](docs/assets/llm-uv-reference/uv-comparison.webp)将左侧标作82个UV连通岛，右侧标作42个独立纹理区；[正面](docs/assets/llm-uv-reference/front.webp)、[背面](docs/assets/llm-uv-reference/back.webp)与[侧面](docs/assets/llm-uv-reference/side.webp)展示同色复用组及灰模接缝。组数、独立图块数和拓扑连通岛数须分别统计，不能将堆叠后的视觉减少直接当作删缝合岛；这些展示也没有给出原始彩色材质、法线和mip的保真对照。`[verified: 所链接截图的标题、图例与展示内容；统计解释为opinion]` `[opinion]`

**优先验证的推测流程是“已有UV岛 → LLM分组和操作决策 → 几何对应与堆叠 → 按组重排 → 看图迭代”。** LLM可同时负责语义判断、选择操作、生成或调用脚本以及读反馈修订；不将其作用限缩为给部件命名。以下是本项目拟实施路线，不是作者已披露的实现。`[opinion]`

1. **保留起点并编号**：保存输入几何、原UV、材质与面角身份，提取岛/区域的面集合、邻接、面积、位置、边界及拓扑统计，输出带编号的多视图。先从已有UV岛构造候选，再按需跨岛组合，不把岛编号等同于语义部件。`[opinion]`
2. **输出可执行提案**：LLM提出共享成员、代表区域、镜像/重复关系、独占与保护区、操作次序和依据。实际脚本也要保存；记录模型、提示词、工具输入输出及人工修订，区分Agent决策、程序计算和人工指定。`[opinion]`
3. **先验证便宜的堆叠基线**：精确重复先尝试拓扑匹配/UV复制；镜像候选处理方向与切线基。不同拓扑转入曲面对应与逐面UV拟合，或明确拒绝当前候选，不能凭同名、同色、最近点或图同构失败作最终可行性判断。`[opinion]`
4. **整组排布并检查反馈**：代表区域参与打包，同组成员应用一致的图集变换，保留相对对应。输出编号UV、实际接缝、同色组、畸变及错误定位，供LLM提出下一次修改；执行前后全量验证，失败完整回退。`[opinion]`
5. **接回真实材质验收**：无共享烘焙先达标，共享成员分别烘焙/取样后再形成共同贴图，检查BaseColor、Normal、ORM、过滤和mip；最终比较同画质实际字节与同预算清晰度。先做这条小流程的可运行对照，不要求先求完完整预算优化问题；它也不能替代完整算法和baseline的最终交付。`[opinion]`

新增测试龙由用户使用[三张独立建模参考图](docs/assets/dragon-uv-test-v1/README.md)生成。它作为单独的新资产接收、记录生成来源/设置/哈希并建立自己的原UV与无共享烘焙基线；保留历史energy_blade与dragon及既有实验。新图是参考设计，不能作为实际3D拓扑、严格几何一致性或可复用性的证明。`[verified: 用户“我去生成的一个模型让你进行测试”及“拆成三张独立的图”；接收与验收规则为opinion]` `[opinion]`

### 已核对的工程参考

以下于 **2026-10-29** 核对上游文档或指定源码；“有此功能”不等于本机已安装、已跑通或满足 ReSeam 的近似复用合同。第三方工具用于模块参考和显式对照，不自动替换当前自研求解器或初始化。`[verified: 本轮上游页面与源码读取；goal/solver-redesign.md 已确定的方向]`

上述新增8个GitHub仓库已克隆并作为子模块固定在本地[references](references/README.md)，与原OptCuts、PartUV共10项；完整提交、origin及许可文件哈希见[清单](references/manifest.json)。源码获取不代表插件安装或功能验证。`[verified: references/manifest.json new_reference_names/repositories；本轮获取核验]`

|参考|可借鉴内容|必须保留的边界|
|---|---|---|
|[UVgami](https://github.com/DanielBoxer/UVgami) / [官方指南](https://uvgami.com/docs/)|Stack Similar 检测重复网格部件，只展开一份再复制 UV；另有局部展开、松弛与合岛工具。`[verified: 官方指南 Stack Similar、UV Editor Tools]`|可作精确重复部件流程对照；上述文档不是 LLM 语义提案或任意近似曲面的成功证明。`[opinion]`|
|[UniV](https://github.com/Oxicid/UniV)|Stack 叠放相似几何岛；Stitch、Relax、纹素密度等操作可供参考。`[verified: README Operators，Stack/Stitch/Relax/Adjust & Normalize]`|先核实实际匹配条件、操作上下文与版本，不把“similar”解释为任意非同构部件。`[opinion]`|
|[Magic UV](https://github.com/nutti/Magic-UV) / [Pack UV 源码](https://github.com/nutti/Magic-UV/blob/master/src/magic_uv/op/pack_uv.py)|先按 UV 中心、尺寸和 UV 数筛选；Accurate Island Copy 路径建立 UV 图、检测图同构，再复制对应坐标。`[verified: pack_uv.py _group_island 与 execute 的 accurate_island_copy 分支]`|这是已有 UV 候选匹配/复制参考，不是任意三维语义区域检测器；同构基线失败不能否定近似复用。`[opinion]`|
|[UV Toolkit](https://github.com/pedcorr/uv-toolkit)|文档列出岛提取、Stack/Unstack、MaxRects/BSSF、密度及重叠检查。`[verified: README Transform、Texel Density、Architecture]`|形状哈希 StackIslands 仍列在 V2 Roadmap；README 的部分功能描述与规划交叉，须以具体源码及冒烟核验，不引用其利用率宣传值作为本项目结果。`[verified: README Roadmap V2；其余为opinion]` `[opinion]`|
|[UV Mechanic](https://github.com/theanine3D/uv_mechanic)|按拓扑选相似岛，以及岛间重叠、岛内自重叠和密度诊断。`[verified: README Select、Texel Density]`|README 的岛间 Overlap 是包围盒检测；不能直接替代本项目逐三角形交叠许可/单射验收。`[verified: README Select Overlap；其余为opinion]` `[opinion]`|
|[xatlas](https://github.com/jpcy/xatlas)|ComputeCharts/PackCharts 与已有 UV 的 AddUvMesh/PackCharts 分工，可作无共享展开/重排对照。`[verified: README Generate an atlas、Pack multiple atlases into a single atlas]`|展开/打包库的存在不证明具有语义复用；原面角身份与排布结果仍要核验。`[opinion]`|
|[dcc-mcp-blender](https://github.com/dcc-mcp/dcc-mcp-blender)|官方工具表有 get_uv_islands、unwrap_uvs、pack_uvs、validate_mesh、烘焙及导出接口。`[verified: README Available MCP Tools，blender-uv-ops/validation/texture-bake/interchange]`|它提供执行通道，不能据此声称内置文章的复用算法；接入前核对实际工具 schema、运行实例和可回退范围。`[opinion]`|
|[blender-asset-mcp](https://github.com/yi00it/blender-asset-mcp)|文档提供实际预览渲染、资产预算检查、LOD 与 GLB/FBX 导出。`[verified: README Checks its own work、Game-ready output]`|本次读到的 Requirements 为 Blender 5.2+；不能直接假定兼容此前使用的 4.5.11，也不证明内置语义共享。`[verified: README Requirements；其余为opinion]` `[opinion]`|
|[OptCuts / PartUV 固定参考](references/README.md)|展开、切缝、分区与合并机制参考；本轮整龙工作流另允许显式调用 OptCuts。`[verified: references/README.md 阅读入口；用户接受的整龙计划]`|历史新求解器默认后端/初始化限制保持；本轮实验例外见[执行合同](docs/dragon-llm-reuse-plan.md)。PartUV 项目与 PartField 依赖许可分开核查。`[verified: goal/solver-redesign.md；references/PartUV/LICENSE；整龙执行合同“工具与判断”]`|

NumPy/Trimesh 可承载已有数值与网格处理，Open3D/ICP 可作为配准候选；工具选择不替代曲面双射、材质和烘焙验证。“Symmetry-Aware Mesh Processing”作为附件提出的研究方向保留，未给出准确原文前不编造书目。参数化、联合 UV/贴图优化、重复内容去除与 texture carving 的既有来源见[文献索引](references/literature.md)和[推导参考](derivation/v1.md#12-参考来源与新意边界)，不将语义提案、堆叠或 LLM 控制 Blender 本身宣称为新算法。`[verified: 两份用户附件的技术建议；references/literature.md；derivation/v1.md §12；设计取舍为opinion]` `[opinion]`

### 对话确定的验收与下一步

- **先可用烘焙，再缩图和复用**。允许上游重拓扑输入；新增拓扑单列 variant，保留固定能量刀/龙和全部失败记录。当前拓扑/烘焙顺序见[执行计划](docs/bake-topology-plan.md)，固定几何算法仍按[完整实现与对比目标](goal/budget-v1-implementation.md)验收。`[verified: 用户明确选择“加入拓扑优化，先做好烘焙”；assets/v1/README.md 多轮规则]`
- **以实际制作参考理解“合理 UV”**。用户提供的[刺模型拆解](docs/results/chloe-spike-reference.md)包含实际两套 UV 与256×256贴图：部件、UV 和纹理图案需协同，保留连贯区域和有意义的独占差异，不能将细长结构一律当错误。第二套 UV 的用途与 Unity 材质变换尚未核实，不自动称为 Lightmap 通道。`[verified: docs/results/chloe-spike-reference.md 检查表与边界；设计目标为opinion]` `[opinion]`
- **复用不等于无损**。左右伤痕、文字、颜色、法线或 AO 不同时，需明确共同纹理如何取舍/优化，允许部分共享与独占；不得默认复制一侧覆盖另一侧。材质共享 UV 与业务要求非重叠的 Lightmap UV 分开处理。`[opinion]`
- **面积收益与字节收益分开**。固定尺寸/格式/通道/mip 下，重叠本身不会改变既定纹理布局字节数；收益可能是同预算更清楚，也可能是经最终验收后减小图集。报告全部实际通道和 mip 的 payload、文件包体及网格副本成本，不用覆盖率或共享率替代。`[verified: derivation/v1.md §2 存储定义；其余为验收设计opinion]` `[opinion]`
- **检验 LLM 的额外价值**。先用精确重复/纯几何模块建立对照，再测几何+LLM 提案的端到端效果，计入全部提案、拒绝、调用与验证成本。另用共同候选池、UV 起点及预算比较提案排序/语义保护机制；不能把不同搜索量造成的改善全归给 LLM。`[opinion]`
- **神经网络可作为辅助消融**。用于语义分割、匹配提案、难参数预测或初始化；学习输出和训练 loss 不能绕过最终几何、编码、多通道与视觉验收，不因优化困难就自动增加训练流程。`[verified: 用户“难以求解……考虑神经网络”；其余为设计opinion]` `[opinion]`
- **报告必须附实际产物**。给出原始/修改 UV、接缝与复用组同色标记、独占例外、真实 BaseColor/Normal/ORM、完整 mip、近景和渲染对照；明确已接受、被拒绝及未运行状态。源码/文档核对、流程成功、实际复用、存储改善和 artist 验收分别记录。`[verified: 用户“报告里附带上实际的产生的贴图”；AGENTS.md 实验与证据；其余为交付设计opinion]` `[opinion]`

附件中的 Blender 5.2 API 示例和“UVgami 新版移除 Symmetry”等版本性说法不直接转成运行指令：执行前查看所用版本的 RNA/接口与变更记录。外部工具先单模型冒烟，锁定版本、源码和许可后再纳入对照；本次整理不代表安装或自动切换工具。`[verified: 第一份附件对copy_mirrored_faces的版本说明；第二份附件版本性断言；执行规则为opinion]` `[opinion]`

**Reversible Seam Restructuring with Adaptive Relaxation for UV Unwrapping**  
**基于可撤销接缝重构与自适应松弛的 UV 展开**

**独立求解器方向：[重新设计求解器](goal/solver-redesign.md)。** 该路线借鉴 OptCuts 高层思路、自研 ReSeam，OptCuts 保留为参考、对照和历史复现。用户本轮授权的[整龙工具实验](docs/dragon-llm-reuse-plan.md)允许显式使用 OptCuts 后端，两者分别归因；下面 V1/V2 结果继续保持历史口径，不代表新求解器或整龙目标已完成。`[verified: 用户历史求解器方向与本轮接受的整龙计划]`

ReSeam 面向 AI 生成高模经过重拓扑后的低模，研究如何切缝和展开，使 UV 便于艺术家编辑、支持可靠烘焙，并适合后续可微贴图优化。在映射合法、畸变受控的前提下，改善接缝位置、区域连续性与碎片结构，减少人工修整成本；接缝短、岛少是评价的一部分，不代表已经达到 artist 目标。

状态：V1 原型与自动 A/B 已交付：66 个 UV 配置完成记录，49 个通过、17 个拒绝；17 个下游流程完成。能量刀接缝改善但覆盖率下降，幻想龙未取得 ReSeam 改进，artist 人工验收尚未完成。详见 [实际结果与边界](docs/v1-results.md)。ReSeam 是工作名称，由 Re- 与 Seam 组成，不要求逐字母展开；正式投稿前需核验论文和软件项目重名。

固定开发与回归模型已迁入 [assets/v1](assets/v1/README.md)：能量刀 `energy_blade`（500 面低模）与幻想龙 `dragon`（2000 面低模），各包含真实材质参考高模、无 UV 低模 GLB 和 OBJ。资产来源、哈希及检查范围见 [manifest.json](assets/v1/manifest.json)；旧实验源文件保留。

用户提供的 [artist 真值参考](assets/gt/README.md) 单独保存。它们保留原始 UV、不同拓扑及复用关系，用于生产目标对照，不替换固定低模实验输入。

当前阶段：[V2 联合原型](goal/v2.md) 已完成24项正式搜索、24项下游验证与18份有效代理盲评。能量刀唯一覆盖率中位数从固定岛47.04%到联合49.14%，成对纹素密度增益中位数为2.497%；龙仍为28.21%，真实复用组为0。全部代理评价为NEEDS_WORK，尚未达到artist交付目标。见 [实际结论](docs/v2-joint-results.md)、[运行说明](docs/running-v2.md) 与 [联合计划](docs/v2-joint-plan.md)。`[verified: docs/results/v2_joint_round01_summary.json groups/paired_comparisons/proxy]`

V2 前期固定岛原型完成36项对照：能量刀adaptive的Blender排布中位覆盖率从47.36%到48.52%，幻想龙仍约28.20%。这是联合轮之前、不同配置的历史阶段，详见 [前期报告](docs/v2-results.md)，不与新轮次混合计算收益。`[verified: docs/results/v2_fixed_uv_round01_summary.json“groups”]`

既有 [对称 UV 复用诊断](docs/v2-reuse-plan.md) 检查可配对区域、颜色与拉伸差异并输出对拍。新联合流程允许代理盲评推进自动实验，标记 AGENT_EVALUATED，人工复核独立记录；技术门禁不因代理高分放宽。`[verified: goal/v2.md“Subagent 代理盲评”]`

[复用诊断结果](docs/v2-reuse-results.md)：两模型各 4 个几何候选、0 个通过全部暂定门限，已输出拒绝候选的六视角及局部对拍。当前不是生产可用复用结果。`[verified: docs/results/v2_reuse_preview_round01_summary.json“passed_preview_gate_counts”]`

[龙翼近似曲面对应](docs/dragon-approximate-symmetry.md)：放开严格三角形配对后，外侧翼区候选中 427/478 个顶点通过 0.5% 对角线位置/往返误差及 45° 法线门限，其中 418 个目标位于对侧三角形内部或边上，而非现成顶点。它是下一步区域配准的几何候选，尚未改 UV 或批准纹理复用。`[verified: docs/results/dragon_surface_match_round01_summary.json“sweep”，tolerance_fraction=0.005]`

获取项目与固定版本的参考源码：

```sh
git clone --recurse-submodules git@github.com:hamsterjiang23/ReSeam.git
```

已有克隆可执行 `git submodule update --init --recursive` 补齐参考源码。模型资产直接随主仓库跟踪，实验输出放在已忽略的 `artifacts/` 中。

## 运行原型

使用 Python 3.11 和 uv：执行 `uv sync`，按 [运行说明](docs/running-v1.md) 配置本地后端路径并构建固定拓扑适配器。

```powershell
uv run reseam doctor
uv run reseam unwrap --model energy_blade --method reseam_adaptive --output artifacts/manual/energy --seconds 180
uv run reseam benchmark --config configs/v1_round03.json
uv run reseam report --round artifacts/v1/v1_round03
uv run reseam pack-benchmark --config configs/v2_fixed_uv_round01.json
```

已有输出不会覆盖。每轮记录源码与外部程序哈希、配置、全部失败及未完成项。正式配置包含 66 个几何运行；种子 17 的 22 个方法／模型组合承担下游验收，另两个种子不宣称已验证烘焙。

## 我们要做什么，为什么做

目标生产流程为：单视角输入与多视角图生成 → Tripo 等 AI 3D 生成工具产出高模 → 各类重拓扑算法产出低模 → **ReSeam UV 展开** → Blender 贴图烘焙 → 可微贴图优化。这是项目的应用场景，不是对某个生成工具内部步骤的假定。

我们负责 UV 环节：输入固定低模，以及可选的高模材质、部件信息和高低模对应关系；输出接缝、UV、岛 ID、对应关系及质量记录。上游生成和重拓扑作为数据来源，下游烘焙与贴图优化作为效果验收流程，不在 V1 中重建整条资产流水线。

artist 目标需要同时考虑：重要纹样和部件的连续性、接缝位置是否合适、岛是否便于识别和编辑、局部拉伸是否可接受，以及烘焙后是否出现明显接缝。通过艺术家盲评与修到约定交付标准的用时验证，不能用岛数、畸变或图像 MAE 的单一排名代替。

## V1：展开与切缝

输入为固定的、非退化且可定向的三角流形网格；沿原始网格边修改接缝，优化切割后的 UV 坐标。原始几何与面集合保持不变。

两项核心机制：

- **可撤销接缝重构**：初始 split 只提供起点，不构成永久边界。允许跨初始分区删除旧接缝、添加新接缝，并将区域级复合修改作为整体评价。
- **自适应松弛**：根据候选的求解情况，逐步扩大允许移动的 UV 顶点范围，减少因局部优化范围过小造成的候选误拒。扩大的是坐标活动范围，不是放宽畸变或合法性约束。

设计流程：

```text
初始展开 → 区域级接缝提案 → 候选拓扑与 UV 初始化
        → 自适应扩大 UV 松弛范围 → 全量质量与合法性检查
        → 接受改进或完整回退 → 在预算内继续搜索
```

OptCuts 是优先接入的强基线与初始展开来源，也是可替换、可修改的求解后端，不是方法名称的一部分。候选构造、连续求解与最终验证应分离；既有其他项目的对比结果不计作 ReSeam 实验结果。

V1 优先实现语义区域保护下的碎片修复与接缝重构：支持必须切、禁止切和偏好切约束，保护重要纹样及部件连续性，避免只为降低岛数而合并难编辑的区域。高模信息仅在有可靠对应关系时用作先验；缺失或不可靠时回退到几何与人工标注，不把 AI 纹理瑕疵自动当作真实材质边界。

### 目标与质量边界

纯几何机制验证沿用研究报告的受约束目标：

$$
C(S)=\frac{\sum_{e\in S}\ell_e}{\sqrt{A_{\mathcal M}}}+\lambda_K K(S).
$$

其中，接缝集合为 S，原始表面积为 A，UV 岛数量为 K；每条新增接缝边只计一次，原始网格边界不计为新增接缝。

- 在统一的畸变阈值与合法性要求下比较接缝长度和岛数量；同时报告各原始指标，不只报告加权分数。
- 机制验证先使用 λ_K = 0；低碎片实验再使用预先固定的 λ_K > 0，避免混淆目标变化与搜索收益。
- 验证面覆盖、索引与拓扑一致性、非退化、非翻转及岛内无自重叠；仅检查三角形方向不能替代完整重叠检查。
- 保存 packing 前 UV，并在统一几何尺度下计算平均、尾部和最大畸变。
- 未找到合格候选、数值失败或超时，不等于证明该切法数学上不可行。

artist 约束实验单独定义接缝偏好、语义保护和碎片诊断，约束、指标和接受规则在实验前锁定。报告小岛面积占比、狭窄区域与边界曲折程度、部件碎片化、人工编辑用时和盲评结果；不把目标偏好变化解释为搜索机制收益，不预设未经验证的 artist 综合分数。

V1 不以贴图空间利用率为优化目标。为烘焙验收或展示使用外部 packing 时，所有对照采用相同的排布器、分辨率、纹素密度规则与像素间距，参数敏感性另列；不得以更改排布器解释切缝机制的收益。

## V1 A/B 测试

先用小规模构造网格验证不变量，再用固定能量刀资产做流水线冒烟，随后扩展到幻想龙。正式模型清单固定为上述两套，质量阈值、预算和后端版本在每轮运行前锁定。自动对照结果已记录；真人评审、确认约束与派生重拓扑实验的完成状态另列，不因自动矩阵结束而一并标为完成。

按 [多轮测试规则](assets/v1/README.md) 保留原版 OptCuts、xatlas、Blender Smart UV、PartUV 和 ReSeam 消融对照。每次方法或参数修改新建 round_id，随机方法默认以 17、29、43 三个种子独立重复；输入不变、输出不覆盖、失败不剔除。固定历史低模是跨轮基准，其他重拓扑版本只能作为这两个模型的显式派生组。

1. **接缝重构对照**：A 保留初始分区边界，B 允许跨分区区域级复合重构；两组使用相同初始状态、后端、松弛策略、质量要求和总预算。
2. **松弛范围对照**：在固定候选池和共同 UV 起点上，比较固定小支持域与自适应支持域，并加入始终使用大支持域的对照。设置等时间或等预算对照，分离支持域扩大与额外计算的作用。
3. **完整方法对照**：纳入原版 OptCuts、同预算多起点 OptCuts 和固定分区组合基线，检验收益是否仅来自初始化或更多尝试。
4. **artist 约束对照**：在相同搜索机制下比较纯几何与 artist 约束版本；在相同 artist 约束下比较不同搜索机制，区分偏好和搜索的作用。使用匿名结果开展编辑任务与盲评。
5. **重拓扑稳定性**：同一高模生成不同重拓扑方法及面数档位的低模；每个低模内部固定几何做 UV 对照，跨低模分层报告，不混淆几何差异与 UV 收益。

记录完整有效输出率、首次可行解耗时、归一化接缝长度、岛数量、畸变分布和质量随时间变化。计时包括初始化、失败候选、求解和验证；报告实际硬件与资源配置。

逐候选记录删缝与加缝、活动范围、求解状态、耗时、接受或拒绝原因，以及回退结果。支持域救回率必须附带分母和审计抽样方式；失败与超时必须进入汇总。

下游验收分别记录固定 Blender 烘焙结果和固定预算可微优化后的结果。检查接缝附近误差、纹样连续性及多级 mip 表现，先验证 Base Color，再补充切线空间法线验证；可微结果使用未参与优化和调参的视角评价。固定 cage、渲染与采样设置并校准颜色空间，单列高低模对应和投射失败，不把所有误差归因于 UV，也不预设可微优化必然改善结果。

V1 A/B 完成标准：预定样本全部完成或有明确失败记录，产物与配置可追溯，形成包含负结果的对照结论。冒烟通过不等于正式基准完成，收益为正也不是实验完成的必要条件。

两套资产属于反复使用的开发与回归集，不能用多轮、多种子或未见相机视角宣称独立模型泛化。

## V2：近似复用与接缝—排布联合优化

V2已接通区域对应、成对参数化、组级排布和接缝反馈，构造例可合法共享并提交；真实模型本轮的有效改动仅来自排布和切分。找到近似对应与得到可用共享UV是两个验收层次，详见 [实际结论](docs/v2-joint-results.md)。`[verified: tests/test_joint_material.py 正向提交测试；v2_joint_round01/audit.json accepted_candidate_kinds]`

V2 在1024²纹理预算、畸变和采样安全约束下提高表面有效纹素密度；复用组间距至少16px、图集边界至少8px。唯一覆盖率、复用比例、密度与外观误差分别记录，不把重复重叠面积冒充唯一覆盖率。固定岛配置仅是对照轨道，不限制完整方法的切缝或共享操作。`[opinion]`

每轮预先锁定假设与A/B协议，保留逐候选日志、完整矩阵、代理评分与图像依据，以及正负结果。技术完成、实验完成、代理评价与人工复核分别记录；预算不足只交付有缺口的报告，不标完整验收。`[opinion]`

### 近似对称复用的实践经验

AI 生成模型与重拓扑低模应以**近似对称区域**为研究入口，不要求左右严格镜像、顶点数相同或三角形划分一致。严格顶点／完整三角形配对只保留为基线；该基线没有找到候选，不能据此判断模型不对称或无法复用。`[opinion]`

龙翼实验采用“源顶点 → 镜像位置 → 对侧曲面最近点”，用**目标面 ID＋重心坐标**表示对应，并检查位置误差、往返误差和法线一致性。曲面对应可以消除要求目标必须是现成顶点所引入的离散限制，整个过程不改变原始几何或 UV。实现与复现命令见 [近似对应报告](docs/dragon-approximate-symmetry.md)。`[verified: src/reseam/surface_match.py“match_surfaces”；docs/dragon-approximate-symmetry.md“这次如何匹配”]`

本次门限属于探索配置，不是通用 artist 标准；外侧翼掩码是几何启发式，双向顶点查询数也不是独立的一一配对数。后续应检查区域连续性、对应折叠与边界，再做成对参数化，分别验收拉伸、密度、颜色、法线和采样安全。允许小幅外观差异不等于允许牺牲几何质量；代理盲评可推进实验，但真实人工复核仍独立记录。`[opinion]`

## 文档与实施入口

- [研究报告 v0.1](goal/UV_展开方案_研究报告_v0.1.md)：数学定义、算法草案、实验设计与参考资料。保留原文件及历史标题；当前名称与版本范围以本 README 为准。
- [AGENTS.md](AGENTS.md)：项目协作与实现约束。
- [V1 目标](goal/v1.md)：应用目标、交付清单与验收要求。
- [固定测试资产与多轮协议](assets/v1/README.md)：两套模型、输入完整性与运行目录规范。
- [上游参考源码](references/README.md)：OptCuts 与 PartUV 官方源码、固定提交及阅读入口；实际执行环境另行记录。
- [运行说明](docs/running-v1.md)：环境、命令、标注和结果解释。
- [V1 实际结果](docs/v1-results.md)：正式矩阵、负结果、空间利用率与尚未完成的 artist 验收。
- [V2 运行说明](docs/running-v2.md)：联合搜索、独立预算、匿名评审与UTF-8后处理命令。
- [V2 联合结果](docs/v2-joint-results.md)：24项对照、代理评分、未成功的真实复用与证据边界。
- [V2 紧凑证据归档](docs/results/v2_joint_round01/README.md)：随Git同步的矩阵、原始评分、执行记录和预览；完整大体积产物保留在本地artifacts。
- [求解器重新设计](goal/solver-redesign.md)：独立求解器方向与边界；整龙工具实验的 OptCuts 后端例外见[执行合同](docs/dragon-llm-reuse-plan.md)。
- [Artist 参考与质量目标](docs/artist-quality-reference.md)：用户提供的成品贴图参考、初步反馈及覆盖率／密度／安全间距的边界。
- [人工编辑任务](docs/artist-review-task.md)：匿名场景、限时修整、UV 导出、技术复核与重烘焙。
- [实现记录](implementation-notes.md)：偏差、审查修复和已知边界。

人工步骤使用 [统一编辑任务](docs/artist-review-task.md)：匿名 Blender 场景、15 分钟修整、逐角 UV 导出、固定几何与约束复核、重新烘焙及真实偏好记录。自动验证不会将人工验收标为完成。V1 保留原人工验收口径；V2 的技术/实验完成及代理评价按 [新目标](goal/v2.md) 分开记录，人工缺席不阻塞自动阶段。

本项目不预设全局最优、同预算必胜或艺术家级布局结论；效果与适用范围由实验决定。


最大限度压缩继续按[尺寸、编码与纹素预算协议](docs/dragon-compression-protocol.md)执行：四条路径统一比较RGBA8和真实BC1／BC7解码验收，公共眼部密度修正单独消融；不得把编码收益归因于UV共享，或把运行时纹理字节当成整个调试交付包体。`[verified: 用户最新压缩要求；opinion: 统一执行约束]`


当前优先级以用户最新纠正为准：重点是 **UV岛复用**。继续扩大部件对应与实际共享、减少共享后的碎片；编码压缩暂作支线。四路径几何检查点见[UV复用进度](docs/results/dragon-uv-reuse-progress.md)，尚未宣称整龙有效复用完成。`[verified: 用户最新纠正；opinion: 执行优先级]`

用户进一步要求以实际外观为主要验收标准，不以严格的几何审计作为目标。后续另建外观优先实验，允许近似对应和较宽松的几何参数，保留UV有限、非退化和模型可正常显示等基本条件；对照图和人工／艺术验收单列，不能将宽松结果写成已通过旧严格协议。`[verified: 用户“只要外观上可以通过就行”；opinion: 实验执行方式]`

候选部件相似度约70%即可尝试合并，低于70%也可以尝试；相似度的定义须明确，最后仍根据合并后的实际外观判断。`[verified: 用户对“70”的澄清；opinion: 评估方式]`

整脚候选已在同资产几何对照中将实际独立图块203→200；8K真实贴图仍有7处LOD2接缝拒绝，同目标无共享控制通过。源外观对照发现白爪向趾根扩张，已保留失败与真实贴图，不能将该候选称为有效整龙复用。`[verified: artifacts/dragon_reuse_development/geometry_restored_baselines02/README.md；restored_whole_foot_color_pair03/comparison_metrics.json]`

对称化目标先用 `material.target` 冻结，再用 `view.render mode=basecolor,target=...` 查看原始／目标／差异与具体原面，经过Agent外观预检才进入烘焙。高差异的明显特征保留独占；新目标不得跳过这一步。`[verified: src/reseam/agent_tools/views.py；views_frozen_target_smoke_01/render/views.json；opinion: 执行准入规则]`

## Training-Free 语义引导 UV 重构

三资产实际结果见[MeshQuery UV重构报告](docs/results/meshquery-uv-reconstruction.md)：能量刀62岛／59图块、历史龙翼173岛／171图块，Tripo项目路径117岛／99图块、18组共享，附真实2048贴图与GLB。数值拒绝、Agent目标／外观初筛与人工状态分别记录；统一canonical受控比较已完成，两路同为117岛／102图块／15组，未观察到项目切口的图块收益，不据端到端路径差额宣称Skill因果收益。`[verified: artifacts/meshquery_v1/history_report01/report.md 结果边界; artifacts/meshquery_v1/tripo_project_color01/project_color/inspect/result.json; validate/result.json]` `[opinion]`

当前新增 MeshQuery 风格网格查询、程序化选边、切缝计划与反馈工具，详细接口和实现差异见 [工具说明](docs/meshquery-tools.md)。采用通用预训练 VLM 决策与确定性几何执行，不新增训练或微调；主流程使用多视图与几何候选，PartField 单列研究适配。`[opinion]`

重构范围是切缝与 UV 组织，保持原几何、面及身份；已有可用 UV 优先保留。依次验证无共享展开、真实原贴图重采样及部件共享，连通岛减少与独立图块减少分开报告。具体决策链见[方法说明](docs/semantic-uv-reconstruction.md)，逐工具证据和独立 Skill 实操见[工具验证](docs/results/meshquery-tool-verification.md)。不凭入口或构造测试宣布工业生产可用。`[opinion]`

用户新增整半镜像变体：明确允许改变几何时，通过 `asset.mirror_variant` 保留一半并镜像另一半，生成同一新几何的UV共享/无共享对照。原资产和固定几何结果保留，结果及来源见 `artifacts/meshquery_v1/whole_half01`；该变体不归为固定几何算法收益。`[verified: 用户最新镜像授权；mirror_asset.py；opinion: 结果边界]`
