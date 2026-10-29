# 新求解器 v1：开发入口与首轮真实求解

本页保留首阶段开发入口及当轮结果；当前完整预算搜索与对照改见[完整运行说明](running-budget-complete-v1.md)。下文的“尚未实现”描述对应本页所列的早期诊断实现，不代表新入口的当前状态。`[verified: 本页首段范围与新 budget_solver.py/budget_compare.py 入口]`

本入口实现 [derivation/v1](../derivation/v1.md) 的第一阶段：独立成对几何提案、预算尺寸扫描和 Base Color 诊断。**不是完整 v1，也没有批准可交付的真实纹理复用。** 历史 V1/V2 代码、结果和预算账户保留。`[verified: src/reseam/budget_experiment.py 的 protocol/result 状态；derivation/v1.md 第11节]`

## 已实现的运行链

- 新 `BudgetPairProblem` 使用尺度归一化形状能量与重心对应耦合，固定一个共同坐标规范；guarded L-BFGS 在首个正行列式零点之前截步，并独立检查成员自交，不执行旧的求解后面积恢复。`[verified: src/reseam/budget_pair.py]`
- ROI 在固定几何候选池中按原始面积排序，先直接初始化，失败再有限缩区；保留删前/删后原始面 ID 与全部尝试。ROI 是几何启发式，不是人工确认的龙翼语义。`[verified: src/reseam/budget_experiment.py select_wing_pair]`
- 新几何验收器检查成员定向、单射、逐面各向异性、邻面密度、边界/间距，以及实际交叠单元的反射曲面关系；不套用旧 V2 的密度增益目标。`[verified: src/reseam/budget_geometry.py validate_pair_geometry]`
- 高模参考使用精确最近曲面点与保守空间筛选；颜色在线性 RGB 合成，真实量化为 RGBA8，生成完整 mip，再逐面比较。图像保持 uint8，不将 8192² 整图展开为 float64。`[verified: src/reseam/budget_texture.py ReferenceSurface、bake_atlas、evaluate_atlas]`
- 每轮独立输出协议、源码/配置快照、输入及源码哈希、预算账目、候选/矩阵、UV OBJ、图集和仅颜色的 ROI GLB；已有输出目录拒绝覆盖。`[verified: src/reseam/budget_experiment.py run_budget_experiment]`

## 启动方式

在仓库根目录运行。开发配置默认固定 dragon、种子17、每侧最多32面几何候选、最多15个初始化候选和300秒总上限；求解最多240次迭代或30秒，二者先到为止。改参数应复制配置并使用新的 round_id 和输出目录。`[verified: configs/budget_v1_development.json；BudgetExperimentConfig]`

```powershell
uv run reseam budget-solve --config configs/budget_v1_development.json --output artifacts/budget_v1/manual_new_round
```

分辨率诊断配置单独使用 128/256/512 和 mip0，不与开发轮 mip0/1/2 的质量均值混算。以下同样必须使用不存在的输出目录。`[verified: configs/budget_v1_resolution_diagnostic.json]`

```powershell
uv run reseam budget-solve --config configs/budget_v1_resolution_diagnostic.json --output artifacts/budget_v1/manual_new_resolution_round
```

当前种子只作为协议/来源标识；这一实现的 ROI、初始化、采样与优化路径确定性运行，不把重复 seed 配置当独立统计样本。`[verified: 新入口没有随机候选采样调用]`

## 首轮结果与证据

紧凑机器可读结果见 [development summary](results/budget_v1_development_summary.json)。完整输出位于本机忽略目录 `artifacts/budget_v1/`，不覆盖旧实验。以下结论只涉及缩减 ROI。`[verified: 对应本轮输出与 .gitignore]`

- `budget_v1_development_round01`：面积最大的53面候选在区域对应构造阶段失败，未进入新内核。失败、协议与耗时保留。`[verified: ../artifacts/budget_v1/budget_v1_development_round01/result.json]`
- `roi_probe01`：相同候选按原门槛缩为16+20面，保留原候选面积65.9771%；116条对应覆盖36面，未按材质选择区域。`[verified: ../artifacts/budget_v1/roi_probe01/result.json]`
- `budget_v1_development_round02`：新内核完成240次迭代，状态为 `ITERATION_LIMIT`，不是收敛证明。6项几何检查通过，共享方案69个几何许可交叠单元、重复面积因子约1.4359；32/64/128三档的两轨颜色诊断全部拒绝。源码和输入结束复核未改变。`[verified: ../artifacts/budget_v1/budget_v1_development_round02/solver.json、matrix.json、result.json]`
- `budget_v1_resolution_diagnostic_round03`：在 mip0 单独比较，独立轨128/256/512的面积加权颜色误差约0.04109/0.02440/0.01088，共享轨约0.05858/0.04934/0.04295。512时仍分别有2/25面未通过既定逐面门禁；不能只用全局均值称合格。输入/源码未改变。`[verified: ../artifacts/budget_v1/budget_v1_resolution_diagnostic_round03/result.json 与各 material.json]`
- `matched_raster_diagnostic_round04`：保持共享候选的原始逐角 UV、128²栅格和576个 mip0 参考采样完全相同，逐成员各烘焙一张图，与融合为一张图比较。面积加权颜色误差从0.0379546增至0.0585668，36面中28面的均值变差；两侧单独图也只有3面通过原门槛。这隔离了当前 UV 下的融合增量，不能把全部误差归为共享冲突。两张图共174760字节，融合图87380字节，均含完整 RGBA8 mip；这是隔离控制，不是同质量压缩成功或等存储对照。CPU耗时1.921875秒，原138个文件及输入/源码哈希未改变。`[verified: ../artifacts/budget_v1/matched_raster_diagnostic_round04/result.json L8-L47、L132-L135；budget_account.json]`

这些结果支持继续研究外观引导的 UV/对应更新：仅优化形状与几何耦合虽然找到合法共享候选，却没有解决当前贴图冲突。它们不证明该区域换对应后仍不能复用，也不证明新方法优于旧方法。`[opinion]`

## 如何阅读产物

- `roi.json`、`face_mapping.json`：解释实验实际覆盖哪些原始面、移除了哪些不支持面；ROI 文件不包含全模型面集合，不能作为完整低模交付。`[verified: run_budget_experiment 的 face_mapping 输出]`
- `solver.json`：记录几何迭代；`geometry.valid` 仅表示当前几何协议合格。`full_v1_validated`、完整材质接受组数始终不被几何结果置真。`[verified: solver 与 result 字段构造]`
- `matrix.json` 和各 `record.json`：保留全部目标尺寸及失败。`BASE_COLOR_DIAGNOSTIC_PASS` 也只是颜色诊断，法线/ORM与渲染状态仍为未测。`[verified: run_budget_experiment 与 evaluate_atlas 状态构造]`
- `texture/BaseColor_mip*.png`：真实量化的颜色 mip；`base_color_only.glb` 仅预览颜色，缺少高模法线及金属度/粗糙度表现。`UV.png` 使用真实 `[0,1]` 排布坐标，无自动铺满缩放。`[verified: bake_atlas、export_glb调用及 _draw_packed]`
- `base_color_payload_bytes`：只计 RGBA8 颜色图全部 mip 有效载荷，不是完整多通道资产大小。PNG 文件字节和 GPU 有效载荷分开。没有调用 ASTC 编码器，不宣称 ASTC 画质已验证。`[verified: protocol codec 与纹理字节计算路径]`

## 尚未实现与验收边界

当前先求一个几何提案，再扫描图集尺寸；尚未实现每个预算内 UV+纹理的联合迭代、式(19)完整质量可行性恢复、自动独占例外搜索或最好完整材质存档。它是预算驱动方法的开发底座，不能称为已经解完式(18)。`[verified: 新入口的 solve-before-resolution-loop 路径；derivation/v1.md 第7–9节]`

高模确实带 normal 和 metallicRoughness 图，但本入口尚未测这两个通道，也未做真实引擎光照、过滤足迹或 artist 验收。当前 mip 诊断比较高模点信号，不是匹配过滤足迹的参考；固定2px padding 也未被证明足以覆盖所测 mip。`[verified: evaluate_atlas 的 source_channels_present、required_channels_not_measured、mip_reference；round02/analysis/mip_face_summary.json]`

相同 UV/相同栅格的逐成员控制已完成，当前仍无合格的完整材质候选。下一步加入颜色/法线内容引导，并在不放宽几何门禁的前提下选择独占面；完整材质可行后，再报告同质量下的最小实际贴图尺寸。`[verified: matched_raster_diagnostic_round04/result.json L27-L47、L82-L88]` `[opinion]`

## 神经网络辅助求解：候选设计，尚未实现

用户提出可考虑用神经网络处理难解方程或参数。当前优先探索的是**外观感知的对应与 UV 提案**：减少两侧纹理在当前共享域中的错位。网络的作用、结构和收益均待实验；本轮未安装神经网络依赖、未训练网络，也没有把网络当成已实现的 v1 组成部分。`[opinion]`

已有原始工作 [Neural Surface Maps](https://arxiv.org/abs/2103.16942) 用神经网络表示、组合并优化曲面映射，支持将它作为参数化研究方向；该论文不证明 ReSeam 的固定原始三角形、部分共享及纹理预算约束会因此得到解决。作者的 [Joint UV Optimization and Texture Baking 说明](https://www.julianknodt.com/papers/joint_uv_optimization_and_texture_baking.html) 还描述了图像误差引导的 UV 调整及受控碰撞步长，可作为直接优化方案的参考。`[verified: Neural Surface Maps Abstract；Knodt 作者页 “Poor UV of small resolution textures”]` `[opinion]`

建议按以下顺序做等预算消融；先保留一个直接优化 UV 与纹理的基线，避免把新损失、增加迭代或更多初始化带来的收益归给网络。`[opinion]`

1. **直接变量基线**：固定接缝/共享成员，直接更新原始 UV 副本坐标与纹理；先在构造问题验证采样梯度，再到同一真实 ROI。对应更新另作完整候选，不随优化暗中重定义许可。`[opinion]`
2. **单资产坐标 MLP 提案**：使用下面的残差形式输出每个 UV 副本的位移，锚点固定。输入描述 $z_j$ 必须保留成员、接缝副本及初始 UV 的区别，不能因原始三维位置相同而自动焊接；$\alpha$ 是冻结的探索半径，不是通过放宽质量阈值获得的自由度。`[opinion]`

   $$U_j(\theta)=U_j^0+\alpha\tanh f_\theta(z_j),\qquad
   U_f(b;\theta)=\sum_{k=1}^{3}b_kU_{\chi(f,k)}(\theta).$$

   材质代理始终用右式的**最终逐三角形仿射 UV**采样，再计算颜色与重建法线损失、形状和对应正则；不能在面内直接查询 MLP 的非线性 warp 后，以角点导出冒充同一个映射。纹理张量可以直接优化，不必另用网络表示。代理损失权重需在新轮协议冻结，并报告各项违例，不能靠权重隐藏硬约束。`[opinion]`
3. **分开验证网络的两个用途**：一次性拟合当前资产的 MLP 只检验该参数化的优化效果；学习跨资产初值/候选排序需要额外数据与独立资产评估。当前两个固定资产不能支持预训练泛化结论。`[opinion]`

MLP 的参数线段 $\theta+\eta d_\theta$ 通常产生非线性 UV 路径，不能直接套用式(25)的线性 UV 行列式二次式。首版令网络只提出端点 $U_{\rm nn}$，从已接受的 $U$ 沿 **UV 空间线段** $U(t)=(1-t)U+tU_{\rm nn}$ 在首个退化步长之前截步，再独立检查成员单射、交叠许可与对应、畸变、密度、间距及边界。截步后的 UV 是独立的已接受状态，保存网络提案及截步比例，不能把它误认为当前网络的直接输出；后续求值以实际候选 UV 为准。`[opinion]`

每次验收重烘焙、重建 TBN、真实编码并测试 mip 和实际材质通道；失败回退完整候选及其优化器/网络状态。最终导出普通 UV 和贴图，运行时不依赖网络或新增解码 Shader。加入离线 MLP 不改变当前运行时合同，连续网络本身也不提供双射、合法性或画质证明。`[opinion]`

最小下一实验先冻结当前36面 ROI 与对应，直接变量和 MLP 使用相同纹理变量、锚点、位移半径 $\alpha$ 及活动区域；优化采样与独立检查采样分开。随后再单独测试对应更新，避免把新增自由度当作网络收益。`[opinion]`

对比记录直接优化与 MLP 各自的最优已验收字节数、逐面误差尾部、合法候选率、全部初始化失败和总耗时；同起点、同参考、同质量门槛、同存储预算与同时间预算。只有网络产生更多或更好的已验收候选，才有保留它的实测理由；单独降低训练 loss 不算收益。若两侧真实信号在可行对应范围内仍冲突，应尝试完整面独占例外；不能通过学习补画参考来消除验证误差。`[opinion]`

## subagent 讨论与审查修订

数值与策略 subagent 比较后选择 guarded L-BFGS，暂不把成员碰撞和真实材质验收强塞进黑箱 SQP；纹理与几何 subagent 共同限定了“几何通过、颜色诊断通过、完整材质通过”三种状态。该选择是当前可观测原型的工程判断，不是算法优越性的实验结论。`[opinion]`

独立审查 C1 已修复启动/结束哈希读取异常造成账目与结果无法落盘的问题，并补删除配置和初始输入读取失败两项回归；C2 补齐初始化失败时缩区前后的面 ID 记录。旧轮证据保持不变。`[verified: tests/test_budget_experiment.py 对应回归与 run_budget_experiment/select_wing_pair 修订]`
