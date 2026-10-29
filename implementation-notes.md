# 实现记录

## Deviations

- Agent工具集验收：31项首轮相关测试与473项当时全仓测试通过；正式工具round01为2模型×3路径×2尺寸，共98次调用、12份真实纹理/诊断导出，0份质量通过，冻结代码manifest一致。后续边界修复和新龙测试另存，不覆盖这轮。
- 独立Skill正向测试仅凭Skill/describe完成18次CLI调用；默认LOD[0,1]通过，全LOD[0..6]拒绝，两类导出保留。反馈补充padding合法范围与导出报告LOD，Skill安装至本机技能目录；等尺寸共享没有自动省字节。
- 新龙tripo_p2含10832面、8192²颜色图；FBX原始非流形拓扑经GLB属性拆点形成独立规范化变体。原UV78张问题面隔离后结构合法，密度门限仍拒绝；源Specular扩展不支持，新增显式颜色诊断，未伪造其他贴图。法线兼容筛选修正了尖角最近面歧义；后续6对UV转移仍方向不一致而拒绝。三尺寸真实颜色图与失败指标见新龙报告。

- Agent工具集v1（2026-10-29）：先封装不可变快照与JSON CLI，再集成已有几何/材质内核；工具状态与质量通过分栏。FBX仅在独立Blender进程导入，保存原polygon/loop到规范三角面的对应；历史资产和上游源码不覆盖。

- V2 joint 开发冒烟：纯圆周初值在龙上出现多孔桥接失败及高畸变，新增合法旧UV初值对照与加权刚性预对齐；这不修改几何或放宽门禁。成对求解使用解析梯度的L-BFGS方向与正Jacobian回溯，仍保留200迭代/调用deadline、共同面积恢复和已验证档案。

- 用户确认 V2 扩大为近似复用＋接缝—排布联合优化，新增独立8小时预算，并用代理盲评避免等待人工。当前先落实目标与实施设计；保留旧固定岛/整面诊断协议和结果，联合代码及24项正式矩阵尚未启动。

- 对称复用首版采用完整三角形镜像配对与显式白名单，未复现论文 OT 或 texture carving。真实资产目前无连片候选通过小差异门限，先提供拒绝候选的对拍，不调松阈值制造收益；不继续自动 packing 或缩图。

- V2 固定岛对照先用 V1 seed17 已排布状态作为条件输入；不重新运行全部展开和烘焙。三种 UV 来源只回答给定岛形的排布空间，不构成全部方法或端到端排名。
- V2 Blender 独立 worker 首次出现陈旧 UV layer 引用崩溃，随后短预算 packing 超时；改复用已有 Blender worker 的 pack_fixed 分支，明确跳过 average_islands_scale，保留全部冒烟证据。
- V2 Blender 完整探测用时 19.453 秒，否定草案的 10 秒提案上限；正式运行前统一改为每次 90 秒、最多 4 提案，子进程 45 秒上限，全轮 3600 秒，未重置原 8 小时总预算。

- 用户在冻结实验期间提供 artist FBX 真值。新增独立只读检查轨道，不改 assets/v1 或运行中的配置；GT 拓扑、多个 UV 通道、辅助平面与复用关系单独报告。

- round02 在首个下游运行时失去进程与 pueue daemon，未留下精确终止时间；保留 66 个几何记录及未完成下游。按开始至恢复观察的上界向上取整计入 8000 秒，round03 仅用剩余 16720 秒，另保留环境／冒烟 3600 秒及 round01 480 秒。
- 收尾源码在独立工作树开发，旧轮冻结源码不变。round03 使用修复后的 deadline、完整提案失败日志和四种共同起点支持域诊断；新旧版本不合并排名。

- 正式 round01 首个多起点运行总耗时 185.672 秒，超过 180 秒协议；保留并中止该诊断轮。统一预留 8 秒清理／验证／导出，仍超时则标 BUDGET_EXCEEDED，不进入成功排名；修复后以新 round 和剩余总预算执行。

- 幻想龙原版 OptCuts 输出含 χ=-1、0 的带孔平面岛，实际无翻转／相交且平均畸变 0.02477。统一验证器允许流形、属零、带边界的带孔岛；新候选的圆周调和初始化仍只支持单边界圆盘，不把合法基线误判为失败。
- 合并候选出现多个边界时，先在当前 UV 拓扑的内部边图上用受约束最短路径连接边界，再初始化圆盘；这是明确的加缝步骤，与删缝一起评价，不使用未定义的分区器。
- 正式几何矩阵覆盖两模型、三个种子；昂贵烘焙／可微验收预先固定为所有方法的种子 17，其他种子下游不标已验证。

- Blender FRACTION=8/1024 的实测最短岛间距仅 15.652 像素。统一请求 9/1024，并仍以连续几何测量的间距至少 16 像素验收；烘焙扩边保持 8 像素，不按方法单独调参数。

- 连续后端先以 OptCuts 固定拓扑能量求解加 Python 全量单射验证实现；不得把只防翻转的求解器标为全局双射保证。
- Context7 缺少密钥，grep.app 技能脚本因 MCP 包接口变化不可用；第三方调用改核对官方文档及已安装版本接口。

## Discovered edge cases

- 联合基础审查C24–C26：补齐按每组双向对应计算的1px P95门禁；缩区后重新检查位置/法线/往返，排除超限约束并记录不完整区域；归档全量验证完成后再查deadline，超时不能用期限后新状态替换合法档案。

- 联合计划审查D1–D3：不同圆周采样不形成相同PL域，改为独立初值、自由边界及实际交集共享/并集排布；SD尺度保留为变量，明确定义耦合项和组级面积恢复；逐叠置单元位置界与逐成员TBN解码法线门禁单独锁定，不用整体P95或组ID代替。

- 龙的左右顶点最近距离掺入重拓扑采样差异；改为顶点投射到对侧三角形内部，再检查往返与法线。外侧翼掩码当前是几何启发式，双向通过的顶点查询数不等于独立一一配对数，也不证明全局双射。

- 镜像顶点接近不保证三角形匹配；拷贝对侧现有 UV 还会改变局部拉伸/密度。颜色兼容检查在 linear RGB，渲染差异单列 sRGB 图像 MAE，不能直接比较两个数值口径。

- xatlas 的矩形图集按宽高分别归一化，直接视为正方形会拉伸；先恢复像素宽高比。自动轴对齐/交换可能翻转岛方向，固定岛轨道关闭两项旋转选项，改由输入预旋转提供姿态。最终仍用原岛形和共同尺度重建。
- 凹岛的顶点均值不保证位于多边形核内，共同尺度可行性不能假设全局单调；有限采样与局部细化只报告找到的最好合法尺度。
- C23 审查发现后端身份记录不足：除包版本和源码哈希外增加实际 Blender 可执行文件、xatlas 二进制哈希及结束时复核；版本读取安装元数据，不硬编码。

- GT 龙主体的原始四边形 UV 中有 4 个投影多边形不能直接作有效 Polygon；按 Blender 实际三角化检查后 UV 三角形均非退化。统计并集不能先把这些原始面丢弃。

- 边界连接算法失败不证明带孔拓扑非法。后续日志将该类记为 INIT_FAILED；对冻结轮的离线汇总同时保留原始类别与更正解释，不改原始日志或接受结果。

- C21：匿名样例按成功集合重新洗牌会使评分对应到错误缓存模型。编号改为持久映射，Blender 场景缓存校验源状态、贴图和自身哈希；不接受身份缺失的旧缓存。
- C22：手工逐角 UV 与接缝声明冲突时，重建共享副本可能覆盖输入。归一化前逐角核对并拒绝冲突，不让技术验收静默修复提交。
- Windows 默认 GBK 读取中文 JSON 会失败，所有文本读取显式 UTF-8；配置兼容 UTF-8 BOM。评审页错误使用行内提示，避免模态对话框阻塞测试。
- 原生检查点过多时全量验证可超出求解预算；先保留已验证起点，为扫描分配时间，截止后停止扫描。幻想龙 60 秒预算冒烟实测 52.14 秒，输出合法且未取得目标改善。

- 高模自带法线贴图；颜色烘焙临时使用 Emission 后必须恢复原材质 Surface 连接再烘焙 Normal，避免丢失源法线细节。
- GPU 运行环境没有 SciPy；扩边索引在 ReSeam 主环境计算并通过 NPZ 传入，不修改旧项目环境。
- PartUV 预处理内部会使用固定采样 seed；适配器显式将请求种子传给 process_face，独立记录导致的不同分区结果。

- OptCuts 命令行最后一项仅是输出后缀，不控制随机种子；多起点对照改为按种子选择并置前不同原始面和首顶点，恢复原始面顺序后统一验收。普通 OptCuts 重复标为确定性重复。

- 独立审查 C1–C4：改为按显式 UV 身份或后端接缝元数据导入；拒绝未实际断开的接缝；数值失败时读取检查点，按能量排序后恢复最好合法映射。

- 研究报告已由用户移入 goal/，内容哈希未变；保留移动并修复链接。
- OptCuts TriMesh 在原始顶点数与 UV 顶点数相同时忽略独立 UV 索引；适配入口直接接收按 UV 副本展开的 V_rest 与 FUV，消除歧义。
- 两模型是开发与回归集，不能以多次重复声称独立模型泛化。

## Questions for review

- 用户说明初步 3／4 分差异主要来自 ReSeam 岛间黑色空白过多。对应冒烟覆盖率 ReSeam 47.82%、OptCuts 48.77%；记录为排布后空间利用率反馈，新增报告诊断列，不改冻结 V1 目标，也不自动生成硬接缝约束。

- Artist 自动建议只有人工确认后才成为硬约束；人工盲评和修整计时不能由代理伪造。

## 交付摘要

偏差记录：10 项。
优先复核：几何目标改善与 artist 空间利用率要求之间的差距。
边界记录：14 项，包含 GT 复用、三角化与冻结证据保留。
后续先读：docs/v1-results.md，再查看原始 ledger 和分模型报告。
待完成：真人区域确认、限时编辑与视觉交付验收；未宣称 V1 artist 达标。

## V2 固定岛交付摘要

偏差记录：本次 3 项（条件化输入、复用 Blender worker、提高提案预算）。
优先复核：固定岛有限搜索不能定义 packing 上界；adaptive 最好种子与中位数有差距。
边界记录：本次 3 项（xatlas 图集/方向、凹岛尺度非单调、后端构建哈希）。
后续先读：docs/v2-results.md 与 references/literature.md，再看新轮源码快照和逐提案记录。
待完成：packing 引导切缝、AT/SQP、受控复用、重新烘焙与真人验收；36 个固定岛运行已完成。

## 对称复用诊断交付摘要

偏差记录：1 项，先完整三角形配对与诊断，不复现 OT/Texture Carving。
优先复核：几何相似、材质可共享及成对 UV 低畸变是三个不同条件。
边界记录：颜色空间差异、三角化不一致与小区域掩盖整体 MAE。
后续先读：docs/v2-reuse-results.md；用户确认的是颜色容忍度，不自动放宽畸变限制。
待完成：区域确认、成对参数化/曲面注册、复用组 packing、法线与 mip；没有生产批准。

## 联合目标与实施设计交付摘要

范围：先更新 goal/v2.md，再新增 docs/v2-joint-plan.md；本轮未实现联合求解器或运行正式矩阵。
设计澄清：3项，涉及不同边界采样、SD尺度和共享法线/交叠门禁；文档审查D1–D4已关闭。
协议：四轨道×两模型×三种子共24项，6个匿名包×3位评审；人工状态独立。
预算：用户新增v2_joint 28800秒账户，首次联合自动实验启动时扣费，旧账目保留。
下一步先读：goal/v2.md和docs/v2-joint-plan.md，从状态/验证器及不同三角化的成对UV构造测试开始。

## V2 联合原型实施（2026-09-20，正式冻结前）

### Deviations

- 成对圆盘/继承初始化在完整龙翼上遇到跨 chart 接触和对应失真，新增法线锥内连续近似平面小区域初始化；几何不修改，大小/角度作为候选生成参数，最终门禁不变。旧失败保存在 artifacts/v2_joint/smoke01–08 开发目录。
- 内层加入逐面相对面积 log 比例惩罚（权重10），以改善固定密度分配的可行性；没有放宽各项畸变、密度或1px耦合门限。只作为开发实现选择，正式配置冻结后不调整。
- 接缝反馈优先按包围盒浪费率生成保持逐角UV的岛内切分，再尝试原区域重构；边界/凹陷的完整几何代价仍是后续可替换启发式，不宣称最优切法。

### Discovered edge cases

- 裁剪区域可把同一原始顶点/旧UV身份拆成多个扇区，允许其零面积端点接触必须携带身份来源；仍拒绝无关近点接触和正面积自交，不能按坐标焊接。
- 无像素中心覆盖的小面需补采样；颜色先在线性空间解码整图再双线性滤波，否则可能漏判局部误差。C28已补回归。
- 非无法判断的成对偏好必须引用双方可比较的图像，只有一方图不能产生有效胜负票。C29已补回归。
- 正式行需完整预算预约，不允许把阶段余量当缩短的成功运行；每行复核源码、资产、实际后端路径与哈希。C30/C31已修复并补回归。
- 现有 draw_uv 自动缩放会改变视觉占用率；新 atlas 图直接使用[0,1]坐标，保留边界像素尺度。

### Questions for review

- 能量刀 controller_smoke01 通过搜索与烘焙；G约1.0301、4次接受、无复用组。这是开发冒烟，不能作为四轨道排名。龙翼当前局部候选仍受耦合/各向异性等门禁拒绝。
- GLB 导出增加实际 NORMAL/TANGENT 属性与烘焙逐角帧核对；能量刀500面已通过。镜像法线必须另外用共享构造例验证，不拿无复用案例代替。
- 视觉证据边界：controller_smoke 冒烟复用了现有六个回归相机，未按计划另建开发相机。正式轮仍统一这些已知视角；最终报告不得称它们为从未观察过的独立测试视角，代理盲评也不提供模型/视角泛化证据。
- Windows 后处理采用显式 UTF-8（python -X utf8，或进程级 PYTHONUTF8=1）；本机默认CP936，直接按默认编码读取中文评审会失败。此为读写启动选项，未修改冻结算法/门禁/评分规则。
- 评审执行偏差：原180秒窗口中，两份首轮评审缺失、一份及时完成；仅依据执行状态将后续统一上限改为420秒（含协调的预约450秒），采用2+1编排。保留旧有效评分和失败，最多重试一次；rubric、图片、匿名映射、算法、技术门禁、总28800秒和review阶段3600秒均不变。
- 最终交付：24项搜索/下游与24项附加相机检查完成，18份有效代理评分（20次新上下文尝试）；真实复用0组，均NEEDS_WORK，人工未复核。68项测试、ruff、basedpyright通过。HTTP预览服务及file URL预览分别被自动审批/浏览器策略拒绝；保留文件交付，完成584张页面资源与JS语法静态检查，未声称浏览器视觉QA通过。

## 归档同步与方向更新

用户要求同步全部改动，并确认后续借鉴OptCuts高层思路、重新设计独立求解器。保留历史OptCuts依赖、子模块和实验协议；新方向记录在goal/solver-redesign.md，尚未实现新求解器。手写后处理脚本复制到scripts，历史artifact原件保留；约1.98GB完整运行继续按gitignore排除，约3.56MB紧凑证据（含194个归档文件，其中69个为冻结源码文件）随Git同步。


## 新求解器 derivation/v1 开发（2026-10-28）

### Deviations

- 首轮先实现独立几何求解与RGBA8 BaseColor诊断，不将未实现的法线/ORM、真实ASTC或完整模型验收标为完成。RGBA8为实际可编码采样的诊断codec；未来更换codec须另建round。
- 首个ROI采用固定几何启发式外翼patch，不冒充人工确认；借用旧region模块仅构造独立planar/harmonic初值及重心对应，新内核不调用OptCuts或历史solve_pair。

### Discovered edge cases

- 共享组须固定一个共同相似规范避免scale-invariant形状能量与coupling同时缩小造成塌缩；不做旧面积恢复。每面对应覆盖与二维sample rank只是入口检查，不是共享有效性证明。
- 原始high有8192²颜色、法线及金属度/粗糙度贴图；保持uint8以限制内存，初版颜色检查不能代替未测材质。
- CodeGraph原未初始化；用户确认后已建立索引，自动创建.cursor/rules/codegraph.mdc；.codegraph由现有gitignore排除。

### Questions for review

- 原型仅输出ROI诊断，独占例外自动搜索、UV与纹理联合优化、多通道最终验收、整模型预算搜索尚未接通；以结果中的独立状态判断，不把入口执行完成叫v1完成。

- 开发round01完整保留入口失败：原53面ROI存在source对应缺失；round02改为固定geometry候选池direct/refined初始化。缩为16+20面，保留65.9771%候选面积，不冒充原ROI已解决。
- round02新核240次迭代后几何通过（69许可交叠cell），但32/64/128颜色全拒。round03只mip0诊断128/256/512，仍全拒；高分辨率独立轨降低误差，共享轨保留额外冲突，不能归因于单一因素或证明复用不可行。
- review C1：启动/结束hash异常须确保结果与账目落盘；已保护setup和逐文件finally，并补故障注入。review C2：失败的refined候选亦须保存实际face IDs；已补日志及回归。修复不覆盖旧round。
- matched_raster_diagnostic_round04已完成相同UV/128²栅格/576采样控制：逐成员图误差0.0379546，共享融合0.0585668；28/36面的均值变差。单独图仍仅3面过关，不能把全部误差归为共享冲突；此为双图隔离控制，不是等存储压缩成功。`[verified: artifacts/budget_v1/matched_raster_diagnostic_round04/result.json L8-L47、L132-L135]`
- 用户提出神经网络辅助：记录离线坐标MLP提案与直接UV变量的等预算对照设计。所有损失经最终原面角的仿射UV求值，网络不代替合法性或真实材质验收；尚未新增网络依赖或训练。先检验外观引导更新，跨资产预训练不在当前已实现范围。`[opinion]`
- 神经提案审查：θ空间的直线步不是UV空间直线，不能直接复用式(25)的首根公式。设计改为网络提出UV端点，再在UV空间截步；已接受UV、网络端点和截步比例分别记录。`[opinion]`

### 新求解器开发交付摘要

偏差记录：先几何+RGBA8颜色诊断，采用明确标注的启发式缩减ROI。
优先复核：外观引导的直接UV更新与离线MLP提案是否改善已验收候选；相同UV的逐成员控制已有round04结果。`[opinion]` `[verified: artifacts/budget_v1/matched_raster_diagnostic_round04/result.json L8-L47]`
边界记录：完整mip块成本、原始面内对应、TBN/ORM未测、点信号mip与真实足迹差异、source冻结异常落盘。
后续先读：derivation/v1.md、docs/running-budget-v1.md、docs/results/budget_v1_development_summary.json。
未完成：完整v1、真实多通道材质、整模型缩图和artist验收；无可接受的存储收益结论。


## 新求解器完整实现与对照 goal（2026-10-28）

### Deviations

- 用户要求补全算法与 baseline 报告，并持续使用原固定模型调优；已创建 active goal，执行条件见 goal/budget-v1-implementation.md。CPU RGBA8 合同与 GPU/artist 批准分别记录。`[verified: 用户本轮指令；create_goal active 返回]`
- 全模型共同初始化显式统一 xatlas 岛方向；dragon 的两张零UV面及其余超过固定各向异性界限的面共33张沿原边切为等距独占面，所有方法同处理，原几何与面集合保持。`[verified: artifacts/budget_v1/full_mesh_probe02/result.json]`

### Discovered edge cases

- 64²完整冒烟四项均未找到当前8px间距下的排布，未进入材质，不能归因为贴图不兼容；原轮完整保留，下一轮扩大图集预算，不放宽质量门槛。`[verified: artifacts/budget_v1/budget_v1_complete_smoke_round01/summary.json 与 candidate_log.json]`
- C1：UV更新后不得复用旧覆盖/投射证据；增加完整UV状态指纹与最终栅格支持刷新，保留已优化纹理值。C2：初始化后的几何拒绝不能在汇总时改写为初始化失败。`[verified: 本轮审查与对应修补；tests/test_budget_material.py、test_budget_solver.py]`
- C3：独立面内/近边探针及四视角可漏掉个别错误texel；最终增加每原面在所测LOD的实际texel中心编码误差检查，开发代理与最终验收范围分开。`[verified: 本轮独立审查C3构造反例；完成状态以新测试及运行结果为准]`

### Questions for review

- 真实材质当前采用官方Mikk源高模帧、显式flat低模NORMAL/TANGENT、三张RGBA8完整mip、CPU双线性/整数LOD合同；导出GLB仅LOD0预览，完整mip PNG侧文件独立交付。真实GPU足迹、引擎和artist状态不得自动置真。`[verified: src/reseam/budget_material.py 模块合同及 export_material_glb]`
- 正式矩阵需在修补与审查后冻结：两模型×四方法×三种子×至少两档预算；全部失败、未运行和实际开销保留，不能用源码存在或局部冒烟代替完整对比。`[verified: src/reseam/budget_compare.py ComparisonConfig formal 条件]`


### 持续开发轮与诊断

- complete_development_round02：两模型×independent/joint×256²共4项，253.344秒，源/输入未变，全部材质拒绝；round03扩展五方法×512²共10项，783.859秒，4质量拒绝/2初始化失败/4超时，无合格输出。已有中文report与完整矩阵，不把流程完成当算法效果完成。`[verified: 两轮summary.json、matrix.json]`
- 原生OptCuts两模型实际返回12.953/37.906秒；接入时只为optcuts方法提供初值，并使用共同repair、编码与验收合同。新求解器仍独立初始化；公共准备成本不用于宣称独立部署效率优势。`[verified: optcuts_reference_probe01/result.json；budget_optcuts.py；budget_solver.py prepare_inputs/solve_budget_target]`
- UV步长归档重放明确：owner33内原vertex159的不同UV副本396/450初始精确同点，独立方向分离导致正面积自交，κ/density/gap/border未触限。下一轮加入有身份授权的运动等式，不按接近坐标焊接或放宽自交容差。`[verified: uv_step_diagnostic_round04/post_texture_contact_result.json]` `[opinion]`
- flat框架对照：energy未切边平均切空间目标跳变18.3185°，smooth Mikk为0°；近边normal误差23.9460→21.8119°，完整材质仍拒。dragon smooth初始化出现5面6零切线角点，保留失败，不更改当前冻结合同。`[verified: normal_frame_diagnostic_round04/diagnostic_summary.json、mikk_failure_probe.json]`
- 审查新增C4(非默认投射参数漏传)与C5(仅单DEV候选完整验收，缺少完整验收回退)，round03默认.02不受C4影响，但该轮仍为开发诊断；修复后必须新round，尚未关闭goal。`[verified: 本轮独立审查C4/C5与受控反例]`


### 活动正式轮交接（2026-10-28 19:04 +08:00）

- Goal仍ACTIVE：完整实现与正式baseline报告均为完成条件，不以开发轮结果关闭。正式矩阵 artifacts/budget_v1/budget_v1_comparison_round05 正在执行，不启动重复轮；unified exec session_id=43042，可用write_stdin轮询。`[verified: create_goal/get_goal与启动session输出]`
- 代码、配置、native已冻结；仅更新文档和读取产物，直到正式轮结束。模型两种、方法五种、种子17/29/43、预算512/1024、每target120秒、公共setup180秒，共60行。19:03:57快照5项已结束，energy_blade/independent/seed17/1024运行中。`[verified: configs/budget_v1_comparison_round05.json；matrix.json快照]`
- 本轮检查：239项pytest、ruff、basedpyright通过；独立代码审查及文档审查均无遗留问题。C1-C5已关闭；motion_ties实模12块均0 backtracks，总22.578秒，画质仍拒。`[verified: 本轮命令stdout；final_solver_review/complete_docs_review；motion_smoke_round04]`
- 后续先监控当前60行直至完成，复核summary/integrity所有预定行及源码/输入哈希；源码不变时不要重跑整套测试。随后用scripts/plot_budget_comparison.py生成正式图、检查图片、将紧凑结果和中文对比报告落docs/results及docs，更新README/goal状态。无合格输出须如实报告负结果，不能从均值宣布压缩成功。`[opinion]`
- 原生构建可复现脚本、完整运行说明、10项开发对比报告均已存在；原始input/历史实验/旧账户未覆盖。正式报告交付且目标条件核对完成后才update_goal complete，不能因本轮转交/等待而结项。`[verified: scripts/build_budget_mikktspace.py；docs/running-budget-complete-v1.md；complete_development_round03/report.md]` `[opinion]`


## 烘焙优先与刺模型参考：本轮继续

- 用户确认“加入拓扑优化，先做好烘焙”；保持历史输入及固定几何v1，独立variant先验证无共享烘焙，后做缩图/复用。docs/bake-topology-plan.md与README/AGENTS/goal已同步。
- 完整性缺项现已修复为实际Eq19 epigraph恢复+固定y0 trust、H/质量分离、baseline先完整验收、signed交付排序；两项新审计P2（对应记录失效、ranking/deepcopy跨deadline）也有回归。根检查374项通过、ruff通过、basedpyright0；新真实正式矩阵仍未执行，Goal ACTIVE。
- round05进程43042已终止；raw matrix/summary保留中止前状态，终态在audit_interruption/matrix.after_audit。报告附实际三通道贴图与完整mip。不得按旧19:04记录误认为仍在运行。
- 新低成本几何诊断：dragon257/2000面最小角<5度，最小约0.001594度；局部100提案翻边探针两模型均保持顶点和面数，最差小角未消除，烘焙NOT_RUN。不是拓扑导致全部失败的证明，也不将细长刺一律视为坏面。
- 用户给定Chloe刺FBX+256x256红色贴图作为合理设计参考。独立Blender4.5.11导入450三角形/两套UV，原始第一套UV重复覆盖平均1.23948层，图在artifacts/bake_topology/reference_chloe_spike01/uv_layouts.png；Unity二进制材质及动态参数未复现。目标是拓扑/UV/纹理部件协同，不能只追最小角或填满图集。
- BlenderMCP只读查询未返回，本轮终止等待cell226；已核实本机CLI E:/Blender 4.5.11/blender.exe，可用独立headless进程，不操作用户现场。新三通道bake adapter子代理开发中，未运行资产；RAM最近可用9.66/63.78GiB，大任务启动前重查。

本轮偏差：新增用户授权的拓扑/烘焙阶段；旧固定几何结论独立。
最需再看：刺模型纹理部件设计与最终外观，而非全模型等边化。
边界：局部翻边未修近重合顶点、未验全局自交、尚无合格新烘焙。
下一步先读：docs/bake-topology-plan.md、docs/results/chloe-spike-reference.md、bake_variant_bake接口。
继续条件：先小规模真实无共享烘焙，保留失败与实际PNG，再比较固定variant内缩图复用。

## 整龙 LLM 复用实施（2026-10-29）
### Deviations
- 暂无；本轮允许OptCuts作为区域展开后端，历史solver路线保持独立。
### Discovered edge cases
- 六对候选顶点/面数相同，但镜像最近顶点不为双射；属性拆点不能按位置焊接。对应、共同域与颜色验收分开实现。
### Questions for review
- 完成必须包含四条Baseline与逐步前后证据，不能以工具接口或单对成功结项。


### 本轮实际修正与仍未验收项
- OptCuts真实77面翼膜原生展开成功；导入器误把scientific默认6位小数当17位，修正为先验证原始F0面角索引和序列化舍入、保留输入几何。旧拒绝与新18.719秒通过分别保存optcuts_dragon_baseline01/02；回归包含真实舍入和故意改几何拒绝。`[verified: optcuts_dragon_baseline02/wing_optcuts/result.json；test_agent_tools_optcuts_identity.py]`
- 选中开发分支包含翼膜、踝部、翼尖三组：203→205连通岛，203→202独立排布对象。另有10组合法但更碎的探索分支，未按组数选择它；颜色和存储尚未验收。`[verified: geometry_agent_best03/checkpoint.md 与 replay_plan.json]`
- 颜色开发检查暴露长薄三角形尖端无纹素中心覆盖、原始属性拆点接缝漏检及不支持的顶点颜色被忽略；独立审计反例与修复测试保留，修复后必须新建正式轮。原始FBX身份NPZ可提供拆点前邻接，禁止按空间近点焊接推断。`[verified: scripts/agent_asset_worker.py imported.npz/ancestry输出；tests/test_agent_tools_color_reuse_audit.py；opinion: 正式执行约束]`
- 基线脚本冻结源、目标、状态、后端和依赖；成对消融逐个嵌套参数校验。准备失败、超时和导出失败保留，不能把成功调用计为已验收最小存储。`[verified: agent_tools/experiment.py changed_fields；scripts/compare_dragon_reuse.py]`
