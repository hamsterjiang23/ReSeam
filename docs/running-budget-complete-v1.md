# 新求解器 v1：完整预算搜索与对照入口

本入口是[推导](../derivation/v1.md)的实现工作区，完整性仍在核验。旧round05因缺少真正的式(19)恢复、同字节排序和先验收baseline而中止；原始matrix/summary保留中止前状态，终态以audit_interruption和matrix.after_audit为准。当前目标与完成条件见[执行 goal](../goal/budget-v1-implementation.md)，不能用局部测试或流程接通代替完整实现和真实对比。`[verified: ../artifacts/budget_v1/budget_v1_comparison_round05/audit_interruption.json reason；../docs/results/budget_v1_completion_audit_draft.md 结论]`

## 运行

先准备锁定的原生 MikkTSpace。已存在且哈希匹配时只读复用；源码来自官方仓库并校验固定 SHA，已有不匹配目录拒绝覆盖。构建需要本机 gcc，命令与源码/库哈希保存在 build_manifest.json。`[verified: scripts/build_budget_mikktspace.py build_native；tests/test_budget_mikk_build.py]`

```powershell
uv run python scripts/build_budget_mikktspace.py
uv run reseam budget-compare --config configs/budget_v1_restoration_smoke_round06.json --output artifacts/budget_v1/budget_v1_restoration_smoke_round06
```

输出目录必须不存在；改变算法、参数或协议时新建配置及 round_id。恢复算法round06先预定energy_blade、seed17、256px下仅烘焙/固定UV纹理恢复/联合恢复三项；每项90秒、公共准备120秒，只作新路径冒烟。旧round05配置仅用于追溯中止轮；新的完整冻结对照另行锁定。配置存在不表示已运行。`[verified: configs/budget_v1_restoration_smoke_round06.json models/methods/resolutions/options；budget_compare.py ComparisonConfig/run_budget_comparison]`

## 算法与状态

共同初始化使用 xatlas，并显式统一独立岛方向，将退化或超过各向异性上限的原始面切为等距独占面；修复对所有相关方法一致，原始面集合不增删。OptCuts只给单独的optcuts对照提供初值，不进入新方法。`[verified: budget_state.py repair_independent_initialization；budget_solver.py prepare_inputs/solve_budget_target]`

外层枚举冻结的有限图集预算，每档从共同只读初值独立复制，禁止跨预算冒充独立的热启动。内层保存几何合法的工作候选，允许它们暂时不降低本预算字节数；不依据一次失败跳过预算，也不使用未经证明的预算二分。`[verified: budget_compare.py 预定矩阵与 budget_solver.py 工作存档路径]`

联合候选包含独立展开起点、几何共享提案、整面独占例外、UV诱导曲面对应、沿原始边的删缝及开发违例重加权。对应保留源/目标原面及顶点身份、重心坐标、原始包围盒对角线归一化误差；最近点或有限往返证据不称全局双射。当前几何候选搜索使用一个已初始化的启发式区域对，尚不是全模型任意重复区域的完备发现器。`[verified: budget_solver.py 候选分支与 _association_records；budget_correspondence.py update_uv_correspondences]` `[opinion]`

连续恢复在原始面角仿射UV及有界纹理上构造归一化约束：逐面/LOD颜色、法线弦距代理、ORM、冻结灯光，以及形状均值/CVaR95、各向异性和邻面密度。局部子问题最小化公共非负松弛ξ加固定初始y0的二次trust项，使用矩阵自由算子、投影对偶迭代及原始—对偶间隙；它只认证局部凸模型。试步刷新TBN目标并重算非线性代理，满足硬安全H及代理充分下降才提交，拒绝完整回退。值计算与导数构造分开，试步不重复构造稀疏Jacobian。`[verified: budget_restore.py solve_epigraph_restoration；budget_constraints.py linearize/evaluate_material_constraints、linearize/evaluate_geometry_constraints；budget_restoration.py restore_budget_feasibility]`

每个排布组保留初始矩形范围，投影边界外推方向，再在首个边界/行列式退化点之前截步，提交前仍全量检查。原顶点身份、同一排布组、初始精确同UV共同授权运动等式，使同点接触副本统一位移；它们继续保留不同UV索引，不按近点焊接。`[verified: budget_state.py contact_motion_ties；budget_optimize.py motion_ties/uv_bounds；相关回归]`

拓扑、定向、成员单射、交叠授权、间距和边界属于硬安全H；内部允许形状/密度等质量违例，经恢复代理改进，最终仍全量检查。开发候选优先级可用面积平方正违例，但它不是式(19)目标。短迭代结束也对最终代理状态做DEV验收，再与不同的周期DEV存档比较，避免只保留初始纹理。`[verified: budget_state.py validate_budget_safety/validate_budget_geometry；budget_solver.py _development_feasibility/_optimize_candidate]`

搜索前先烘焙并完整验收独立baseline。工作池与交付池分开；预先冻结DEV候选池，最终对最多K项逐项检查，已验收集合按实际字节、带符号的最大归一化违例、预锁质量代价及接缝/岛开销排序。后续失败或超时不能删除已在期限内完整导出的合格baseline/候选。最终样本不反馈给后续优化，无合格输出不能称该预算数学无解。`[verified: budget_solver.py solve_budget_target baseline_validation/final_pool；budget_final.py validate_final_candidates；budget_delivery.py DeliveredArchive.offer]`

## 实际编码与验收合同

冻结合同为三张实际RGBA8图：BaseColor按sRGB存储、采样在线性RGB比较，Normal/ORM为线性数据；所有图保存完整mip，法线mip重新归一化。有效载荷由全部实际通道与完整mip逐层字节累加，PNG包体、GLB与顶点/索引字节另计。没有调用ASTC编码器，不把RGBA8结果称为ASTC画质。`[verified: budget_material.py atlas_from_arrays/save_material；budget_solver.py payload_bytes/mesh_attribute_bytes]`

源高模以原始glTF UV方向建立官方MikkTSpace；目标低模显式写出flat NORMAL/TANGENT。固定两份low.glb仅有POSITION属性，无可继承的NORMAL/TANGENT；这是本实例的共同框架合同，不代表它适合所有资产。平滑框架改造单列为后续变体，不能默默改变冻结轮。`[verified: artifacts/budget_v1/low_attribute_inspection01.json；budget_material.py frame_arrays/load_material_reference/export_material_glb]` `[opinion]`

每次最终验收同时要求下列检查通过；任何单项通过都不能替代其他项。`[opinion]`

- 全部原面、稳定身份、定向、成员单射、显式正面积交叠单元许可、形状均值与CVaR95尾部、邻面密度、间距与边界。报告数值协议及名义/实际交叠组数。`[verified: budget_state.py validate_budget_safety/validate_budget_geometry]`
- 最终UV全部实际栅格投射参考状态刷新；覆盖证据绑定原始几何、UV/面角/接缝、分辨率与投射参数，禁止复用旧UV证据。优化纹理值保持原样，不用重烘焙覆盖它们。`[verified: budget_material.py refresh_material_support 与C1回归]`
- 独立固定面内与近边探针，以及每原面/所测mip的实际纹素中心，检查颜色L2、世界法线角度、ORM与固定灯光误差的逐面均值、P95和最大值。无中心的小面仍有固定探针。`[verified: budget_material.py prepare_material_samples/evaluate_material/evaluate_raster_material 与C3回归]`
- 四个冻结正交视角真实zbuffer、两灯光、整数mip的解码材质渲染检查；参考在同一低模可见点投射高模信号，轮廓几何差不冒充纹理误差。`[verified: budget_render.py prepare_render_reference/evaluate_render]`
- 全量验收、原始贴图/状态/GLB/来源证据导出在目标期限内完成，源码、实际原生依赖与原始输入哈希不变。期限后的失败清理可保留该已存档结果，但执行器必须复核完成时间及全部存档文件哈希；总墙钟和超时清理成本照实报告。`[verified: budget_final.py 完成时间与provenance；budget_compare.py _timely_delivery_proof/target_budget_overrun]`

参考投射是精确最近曲面点与冻结距离/有效性规则；覆盖100%表示此协议下命中、alpha及框架有效，不提供可见性、cage或语义正确性的证明。mip参考目前为高模点信号，整数LOD软件测试不等同于匹配积分足迹、GPU自动LOD或目标引擎。GLB是LOD0材质预览；验收用的完整mip PNG作为侧文件保存。`[verified: budget_material.py MaterialReference.sample/evaluate_material/export_material_glb；budget_render.py 协议字段]` `[opinion]`

## 对照与产物

independent允许同一连续UV/纹理恢复但不引入共享；old_pair保留历史成对目标及面积恢复；geometry_only保留新几何提案并只恢复纹理；joint同时搜索结构与连续变量；optcuts使用其独立展开及相同后续恢复。新增bake_only只交付共同初值的实际烘焙，texture_only固定同一UV运行纹理恢复，隔离纹理更新附加收益。每种方法都保留预先完整验收的共同baseline回退，并报告是否实际使用回退。`[verified: budget_baselines.py solve_roi_baseline；budget_solver.py 方法分支/baseline_validation/delivery_fallback_used]`

每轮生成protocol/source_snapshot/source/integrity、完整matrix、候选日志、逐项原始报告、最终验收顺序、UV/贴图/GLB以及中文report.md；通过时另保存已验收存档。可从实际合格候选比较最小payload；若任一方法没有合格输出，存储收益记为不可比较，不用最低均值或复用率代替。`[verified: budget_compare.py run_budget_comparison；budget_solver.py 最终产物路径]`

终态报告自动嵌入seed17的实际BaseColor/Normal/ORM拼图，并链接逐项原始PNG、完整mip及哈希清单；失败产物保留诊断状态。贴图附件成本在账户中单列。需要协作停止时建立该轮目录中的`STOP_AFTER_CURRENT_TARGET`文件：当前目标结束后保存终态，剩余项记NOT_RUN_STOP_REQUESTED。`[verified: budget_compare.py build_terminal_texture_gallery/STOP_AFTER_CURRENT_TARGET；scripts/build_budget_texture_gallery.py build_gallery]`

```powershell
uv run --with matplotlib --no-project python scripts/plot_budget_comparison.py --round artifacts/budget_v1/my_new_comparison --output artifacts/budget_v1/my_new_comparison/plots
```

图中叉号表示未完整验收；曲线为有原始指标的种子行中位数，缺失指标不绘制但完整矩阵仍保留，不能将重复的确定性初始化当作模型泛化证据。当前两资产始终是调优/回归集。`[verified: scripts/plot_budget_comparison.py；assets/v1/manifest.json scope]`
