# 对称复用与真实合岛 workflow

用于“左右部件共享纹理”“整半镜像复用”“共享后继续减少碎片”“紧凑排布与缩图”。仅做接缝规划时使用同目录的 [MeshQuery 流程](meshquery.md)；本流程不要求每个资产都改模或重新展开。`[opinion]`

阅读顺序：先按授权选择路线，再执行共享、合岛、外观修正和交付。操作参数通过 `uv run reseam tools describe <工具名>` 读取；下面的表格是决策流程，不是另一套接口定义。`[opinion]`

## 输入与启动判断

输入为 ReSeam 工作目录、资产或不可变快照、真实材质来源、当前任务授权、保护特征及质量/预算要求。缺少源颜色不能完成真实颜色验收；来源身份不支持 `basecolor_reuse` 时回到主 Skill 的投射烘焙路径，不伪造贴图。先运行 `tools list`，按需查询接口与后端依赖。`[opinion]`

|输入/情况|判断与下一步|
|---|---|
|未明确允许改几何，或只说“模型看起来对称”|保持几何，检查精确/近似对应；不调用镜像变体。旧资产授权不继承给新资产。`[opinion]`|
|当前资产明确允许保留一半并镜像|观察中线和保留侧，建立新镜像父资产；原模型保留。左右颜色统一属于此变体的外观改动，单列与原模型比较。`[opinion]`|
|已经共享，但连通岛没有减少|先测实际堆叠与独立图块；共享可能已成功，再检查自然表面内部的多余接缝。`[opinion]`|
|只要求更紧凑排布|直接进入排布步骤，不因本 workflow 而额外改模或合岛。`[opinion]`|

默认单个重操作 300 秒、每条真实路径总预算 2400 秒；任务已有预算时沿用任务设置。记录失败调用。同一假设最多修订三次，随后换机制或整块回退；预算耗尽时交付已有证据，不声称数学不可行。`[opinion]`

## 执行：输入 → 判断 → 工具 → 结果 → 下一步

### 观察原资产与建立对照

|输入|判断|工具操作|必须读取|下一步|
|---|---|---|---|---|
|原模型/快照、原材质|哪些 UV 已可用，哪些特征不能破坏|必要时 `asset.import`；`uv.inspect`；`view.render` 原色、棋盘格、接缝和局部图|父状态哈希、原面身份、全模型岛/图块/共享面积、已有冲突、相机列表|保留原始状态；只提出完整区域改动。`[opinion]`|
|候选部件/旧岛|观察是否遗漏背面、脚底、翼膜反面|`mesh.index/query`、`region.propose`；补充相机与 `pick_pixels`；`region.define` 固定原面集合|完整选择集、实际原面、保护面；不能把截断摘要当完整集合|冻结区域与理由；无可追溯身份时先解决来源问题。`[opinion]`|
|统一父资产与来源目标|对照必须与候选在同一几何/目标范围内|用 `uv.pack`、`material.target/bake`、`quality.validate` 建无共享对照|真实烘焙、失败面、相同相机与质量设置|基线失败保留原因，先修复或注明范围，不能伪称已通过。`[opinion]`|

不先逐面隔离异常面。按主 Skill 的显式 `preserve_source_uv` 策略保留来源冲突时，绑定源快照和哈希，另存严格结果；原有重叠不算新复用。选区跨旧岛时检查完整旧岛闭包与保护面，比较全模型变化，避免选区内合并、选区外碎裂。`[opinion]`

### 选择几何路线并建立实际共享

|输入|判断|工具操作|必须读取|下一步|
|---|---|---|---|---|
|已授权镜像的资产|确定坐标轴、平面、主侧，中线/眼嘴/爪子是否适合镜像|`asset.mirror_variant allow_geometry_change=true`；显式选择 `axis/offset/keep_positive`|新资产、来源面角/重心映射、同一新几何的共享与无共享快照、中线连接策略|比较原始/新参考外观，再在新父几何上建立成对对照。`[opinion]`|
|固定几何的部件对|是否有精确拓扑/曲面对应|`reuse.check_pair` 精确/拓扑或近似路径；不同划分需共同域时使用主 Skill 的 `paired_domain`|方法、方向、失败面、畸变、真实覆盖、父哈希|失败先修正整块对应/锚点；不凭同语义或约70%的探索评分直接授权重叠。`[opinion]`|
|当前状态的有效提案|检查通过是否只针对几何而非最终材质|`reuse.apply action=share`；`uv.inspect`；必要时局部 `view.render`|子快照、实际成员、图块、重叠面积；颜色验收尚未完成时明确记待验收|保存共享检查点，再评估是否需要真实合岛。`[opinion]`|

镜像变体已生成精确共享时不重复应用同一组。重新展开主侧之后，旧提案已过期，须以新状态重新 `reuse.check_pair mode=exact`，再复制到对侧。精确反射只适用于真实对应，不能用于强迫一般资产改成对称。`[opinion]`

镜像后先读 `mirror_variant.json` 的 `midplane_review_required/midplane_strip_candidates`，并看编号UV。该诊断按来源模型尺度标出紧贴裁切平面的整岛薄条，只提示复核，不自动删面。默认 `offset=0` 不是对真实对称中线的估计；发现细线岛时检查来源坐标与近中线顶点带，在当前资产授权的新变体中调整平面后比较轮廓和颜色，不能靠隐藏轮廓线或放宽门限宣布修复。真实薄翼/鳍可能应保留，必须看图。`[verified: symmetry_variant.py report.midplane_strip_candidates；opinion: 制作流程]`

### 细线岛诊断与中线修复

|观察/输入|操作与证据|下一步|
|---|---|---|
|UV中出现孤立折线、近乎空白图块|读取 `view.render` 返回的 `views.json → uv_label_mapping` 定位实际面；结合三维近景和来源对应检查，避免把显示编号当原面ID。|普通描边属于展示；真实面则继续区分几何与UV问题。`[opinion]`|
|三维表面合理，只有UV过窄/拉伸|检查原展开、切口、畸变和纹素分配，保留几何，修正完整区域。|回到部件展开分支，不因细线外观随意删面。`[opinion]`|
|完整图块挤在裁切中线附近|读取镜像响应的 `report` 路径，再读 `midplane_strip_candidates`；该字段给出新变体保留半侧面ID，不是原Tripo面ID。|观察来源顶点带、平面坐标和真实薄鳍，决定保留或提出几何修正。`[opinion]`|
|确认是裁切残条，且当前资产允许新镜像变体|检查当前最好布局能否作为父状态保留其UV；显式设置轴、offset和保留侧调用 `asset.mirror_variant`。少量平面候选分别建新输出，退化/失败保留，不在旧状态上覆盖。|重新测条带、轮廓与来源对应，选择可用新父资产。`[opinion]`|
|真实薄翼/薄鳍，或无改模授权|保留表面；有必要时改UV切口或密度，注明尚存的细条和外观范围。|不自动删除，不把旧资产授权扩展到当前任务。`[opinion]`|
|新几何已建立|面ID、资产哈希、候选及验收均以新输出为准；通过来源面/重心对应追溯旧状态，并为同一新几何保留共享/无共享分支。|重新排布、冻结目标、烘焙和验收，交付同镜头对照。`[opinion]`|

几何诊断条件是整岛最大平面距离除以 `source.mesh.scale` 小于 `1e-3`，不是“少于1像素”；像素厚度随分辨率和密度变化，需另看实际UV与纹理。候选为空不能替代轮廓/颜色检查，诊断为真也不代表应该删除。不要照搬案例中的offset或用更宽的检查阈值掩盖薄条。`[verified: symmetry_variant.py band/depth判定；opinion: 解释与制作规则]`

来源若跨多次变体，保留每层 `source_correspondence.npz` 和父状态身份。复查“旧问题面是否消失”与“总面数净变化”是两项记录：重新裁切可能新增三角形，不能用净面数差冒充已移除问题面数。修复成功要求实际面/UV问题及同镜头外观得到证据；数值拒绝继续保留原身份。`[opinion]`

### 真正合岛与切口反馈

|输入|判断|工具操作|必须读取|下一步|
|---|---|---|---|---|
|共享检查点、原边邻接和 UV 岛|同一自然连通表面是否还有多余切缝|通过网格查询、区域与 UV 检查定位整块选区|原边身份、全局前后岛数、跨旧岛边界；不按坐标焊接|已是一岛的表面跳过；不连接空间不相邻部件来降计数。`[opinion]`|
|整块选区与受影响组|重展开前是否需要解除共享|`reuse.apply action=unshare` 指定组；`uv.reparameterize` 指定 `faces/seam_edges`|未共享子状态、实际切缝、面角副本、方向与岛数|保留未选区域，得到主侧新布局。`[opinion]`|
|主侧新布局|是否可迁移到另一侧|重新 `reuse.check_pair`，通过后 `reuse.apply`|新提案绑定状态、真实共享面积、全模型岛数、外侧残片|合法候选进入排布和真实颜色预览。`[opinion]`|
|棋盘格拉伸、白爪模糊或明显错贴|是目标改色问题、映射问题还是展开切口问题|原始/目标/烘焙同镜头定位；用原面拾取及 `seam.select/preview` 修正锚点、隐蔽纵切或分支切口|实际选边、目标变化、修正前后图、实际连通岛数|从父状态整块重试并重新烘焙；仍失败则整块保留或解除共享。`[opinion]`|

后端优先沿用当前任务选择；未指定时先试 `blender_lscm`，方向/展开失败后可显式尝试 `blender_abf`。先看错误而非机械换后端。OptCuts 是显式可选路线，读取其实际 `result_seam_edges` 传给对应步骤；后端缺失就记录依赖问题，不暗换算法冒充成功。`[opinion]`

`seam_edges: []` 表示删除选区内部旧 UV 切分，不是“自动找到好切口”。有孔、封闭或复杂部件需要明确开口，失败不证明整块不可展开。切口增加不必然增加连通岛，读取实际结果；不要照搬龙的52条边或任何固定ID。`[opinion]`

`seam.select operator=geodesic` 的锚点是 `arguments.waypoint_edges` 中2–8个有序原边ID；切口传入 `uv.reparameterize.seam_edges` 时使用原顶点ID对。读取返回选择和网格索引建立转换，不把顶点、边ID、面角ID混用。`[verified: tools describe seam.select operator_parameters.geodesic；uv.reparameterize request.seam_edges]`

### 排布、真实贴图与交付

|输入|判断|工具操作|必须读取|下一步|
|---|---|---|---|---|
|已共享或合岛快照|固定密度规则与留边，避免重新分配密度混淆收益|`uv.pack backend=blender_concave`，显式指定 `density_normalize`|每组只排主图块并回放相同变换；实际 `unique_coverage/minimum_gap_pixels/minimum_border_pixels`|与同设置矩形或旧布局比较，不把请求 margin 当实测。`[opinion]`|
|新布局、源颜色/保护区|源/冻结目标有无不可接受的特征变化|`material.target`；`view.render mode=basecolor,target=...` 查看原始/目标/差异；按需 `reuse.check_color`|颜色球超限面、保护特征、Agent外观判断；不能从待验收图生成目标|目标被接受后才烘焙；目标改变重做预检。`[opinion]`|
|预检后的目标|当前输入是否满足同身份颜色合同|`material.bake mode=basecolor_reuse`；`quality.validate`；`view.render texture=...`|真实纹理/mip、数值质量、同镜头外观；目标与实际图分别看|保存最好已验收与最好外观预览两个状态。`[opinion]`|
|最终候选和对照|数值结果与Agent外观判断分别是什么|`asset.export mode=accepted` 或 `diagnostic`；重新导入导出模型核对几何/UV/嵌入图片|实际模型、贴图、前后图、逐步记录和独立回读范围|按下面模板交付；不能把BaseColor结果升级为PBR/GPU/人工通过。`[opinion]`|

排布使用本机 Blender；`density_normalize=false` 保留岛间相对纹素分配，`true` 用统一密度，两者不得混作同设置基线。不通过逐岛缩小制造高覆盖；显式密度调整另做同父消融。`[opinion]`

需要多起点搜索时，`uv.pack backend=blender_concave` 可显式设置 `packing_seed`：0保留旧初始化，非零种子可复现地打乱主图块顺序并旋转完整图块；不改变岛形状或共享关系。各候选从同一父快照独立派生，固定密度规则，分别测实际覆盖和间距；重复同输入同种子不算新增搜索。按间距分别选最好合法候选，再烘焙看图，不只选代理覆盖率。`[verified: tools describe uv.pack request.packing_seed；opinion: 搜索与验收规则]`

缩图是可选步骤。优先完成UV复用；有缩图要求时逐个枚举支持的尺寸并重新烘焙、验收，不假设通过情况单调。间距、边界和padding分别记录，不能默认都同比缩小；完整mip写出不等于所有LOD验收。`[opinion]` 当前 `basecolor_reuse` 只支持二次幂尺寸，1536的排布完成不能算贴图完成。`[verified: src/reseam/agent_tools/color_reuse.py L579–580 BASECOLOR_REUSE_REQUIRES_POWER_OF_TWO_RESOLUTION]`

## 最小请求示例与状态传递

以下是待填写的结构示例，不可直接运行。面/组ID和路径必须取自当前资产；生产调用加入本轮冻结的 `profile`（颜色工具使用 `geometry_profile`），避免默认值与对照不一致。每次从上一步成功响应取得新 `snapshot/state_hash`；失败不推进当前状态。`[opinion]`

解除需要重新展开的组；`group_ids` 必须来自当前快照。`[opinion]`

```json
{
  "snapshot": "<当前快照绝对路径>",
  "state_hash": "<当前状态哈希>",
  "action": "unshare",
  "group_ids": ["<实际共享组ID>"]
}
```

重展开请求示例仅演示一个面选择的结构；执行时换成完整自然区域的实际面ID集合，并按曲面要求提供切口。空切口不是封闭曲面的通用默认值。`[opinion]`

```json
{
  "snapshot": "<解除共享后的子快照绝对路径>",
  "faces": [0],
  "backend": "blender_lscm",
  "seam_edges": [],
  "timeout_seconds": 300
}
```

重展开成功后，使用其子快照检查对应；精确镜像例的平面须以实际资产坐标替换。`[opinion]`

```json
{
  "snapshot": "<重新展开后的子快照绝对路径>",
  "first_faces": [0],
  "second_faces": [1],
  "mode": "exact",
  "plane_normal": [1, 0, 0],
  "plane_offset": 0
}
```

接下来 `reuse.apply` 读取该检查返回的 `proposal`，输入仍是该提案绑定的快照；`uv.pack` 读取应用后的子快照。`material.target → material.bake → quality.validate → asset.export` 分别传递实际返回的 target、bake、validation 文件引用，不凭目录名猜测成功。`[opinion]`

## 每步记录与交付模板

每条记录保存：父子快照/哈希、实际原面与切口、工具请求和结果、配置、观察事实、修正假设、前后同镜头图、全模型指标及差值、耗时、接受/拒绝原因与回退位置。观察与假设分开；未知token/费用填unknown。`[opinion]`

|比较项|固定条件|报告结果|
|---|---|---|
|堆叠|同父几何、展开、目标、密度与质量设置|连通岛、独立图块、共享组/实际共享面积。`[opinion]`|
|合岛|同父几何与目标；旧接缝对新展开，后续设置一致|全局岛数、选区外碎片、接缝、畸变、颜色/渲染变化。`[opinion]`|
|排布|同UV形状、共享关系、尺寸、留边与密度规则|实际面积并集覆盖率、间距、边界；不能只看矩形面积。`[opinion]`|
|缩图|同目标与质量规则，记录每个尺寸的布局/留边变化|实际完整mip字节、包体、逐尺寸失败；同质量最小值只取已验收候选。`[opinion]`|

|交付路径|几何版本/父哈希|岛/图块/组|实际共享面积/覆盖率|纹理尺寸/mip字节/文件字节|数值质量|Agent外观|人工/GPU/PBR|模型/贴图/前后图/耗时|
|---|---|---|---|---|---|---|---|---|
|原始参考、对应无共享、共享旧缝、合岛候选、最好预览分别填|不得跨几何归因|使用实际测量|注明分母和定义|存储口径分列|通过/拒绝/未运行|接受/拒绝/未检查|分别记录|真实产物与失败链接。`[opinion]`|

没有已验收候选时明确写“未获得已验收结果”，不要虚构最好状态。外观接受但数值拒绝可以诊断导出，保留通过对照；用户认为可以并不改写数值结果。全局岛数下限只在已验证原边连通分量和固定拓扑条件下讨论。`[opinion]`

## 案例与使用边界

仓库案例按机制定位：总过程在 `docs/results/dragon-final-workflow-retrospective.md`，固定形状排布在 `docs/results/dragon-packing-search.md`，中线薄条修复在 `docs/results/dragon-midplane-cleanup.md`。对应数据分别位于 `docs/assets/dragon-uv-final/`、`dragon-packing-search/`、`dragon-midplane-cleanup/`。这些是仓库根目录相对定位提示，不是相对本机安装目录的链接；缺少案例文件时本workflow仍应独立可用，不能依赖作者本机历史路径。`[opinion]`

龙案例说明：精确镜像建立共享后，ABF++部件重展开与脚底切口改善了碎片和局部外观；不是所有资产都应优先ABF++，也不是固定岛数或固定切口可迁移。现有工具没有输出的分量统计、独立回读或图像比较不能凭空填充：先查询现有工具输出与项目核验入口，仍缺失则记录证据缺口，不声称已验证。`[opinion]`
