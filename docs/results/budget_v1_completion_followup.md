# 预算求解器：完整性修复与当前状态

本记录承接[中止轮审计草案](budget_v1_completion_audit_draft.md)。旧round05仍是中止诊断；本轮代码修复不追溯改写其源快照、状态和性能结果。当前实验优先级已按用户确认改为[先做好拓扑与无共享烘焙](../bake-topology-plan.md)。`[verified: ../../artifacts/budget_v1/budget_v1_comparison_round05/audit_interruption.json reason；本轮用户选择回复]`

|审计项|当前实现与验证范围|
|---|---|
|式(19)恢复|局部公共非负ξ与固定初始y0 trust的真实epigraph子问题，矩阵自由联合U/T步、对偶下界/间隙、非线性代理复算、H检查及回退。法线弦距、CVaR95、冻结TBN和shader有限差分都是显式代理，最终编码另验收。`[verified: budget_restore.py solve_epigraph_restoration；budget_constraints.py linearize/evaluate_material_constraints；budget_restoration.py restore_budget_feasibility]`|
|短迭代输出|最终代理状态总会做DEV检查，再与不同的周期DEV存档比较，避免少于检查间隔的运行丢弃全部新纹理更新。`[verified: budget_solver.py _optimize_candidate final_proxy_state_development_evaluated；test_budget_solver.py 短迭代回归]`|
|硬安全和可恢复质量|H只含拓扑/定向/单射/重叠授权/间距/边界；最终另检查均值、CVaR95尾部、逐面各向异性与原邻面密度。正面积交叠不再沿用旧V2的面积跳过阈值，浮点数值合同公开。`[verified: budget_state.py validate_budget_safety/validate_budget_geometry]`|
|baseline与排序|搜索前完整验收baseline；冻结DEV top-K后逐项验收全部可完成项，交付档按实际字节、带符号的最大归一化违例、质量代价与接缝/岛开销排序。后续失败不得删除及时合格基线。`[verified: budget_solver.py solve_budget_target baseline_validation；budget_final.py validate_final_candidates；budget_delivery.py DeliveredArchive]`|
|对应证据事务|整面独占/共享组解散同步清理两种schema的对应记录；未知证据保守删除并记录原因，原始状态不变。`[verified: budget_state.py L469-L479；test_budget_state.py L292-L329]`|
|截止时间|完整行检查和深复制后、插入前重读同一时钟；超时、逆行或非有限时刻不入档。103秒与131秒/130秒截止的构造回归覆盖时间传播和旧基线保留。`[verified: budget_delivery.py L249-L278；budget_final.py L398-L427；test_budget_final.py L241-L291]`|
|实际贴图报告|终态自动生成真实BaseColor/Normal/ORM附件、原PNG及全mip清单，固定seed17预览；来源区分最终、待验收及baseline诊断。单列后处理成本。`[verified: budget_compare.py build_terminal_texture_gallery；build_budget_texture_gallery.py source_directories/build_gallery]`|
|协作停止|STOP_AFTER_CURRENT_TARGET让当前目标结束后正常保存终态，预定剩余行保留NOT_RUN_STOP_REQUESTED；不依赖强停后虚构完成记录。`[verified: budget_compare.py stop_requested 与对应comparison测试]`|

本轮根进程运行预算相关与新拓扑测试：374 passed in 19.47s；对应源码/测试Ruff通过，BasedPyright 0 errors。可选绘图脚本在实际Matplotlib临时环境的显式类型路径下单独检查0 errors。测试范围不包含真实新烘焙或完整新baseline矩阵。`[verified: 本轮pytest、ruff、basedpyright及PLOT_TYPE_CHECK_EXIT=0输出]`

尚未完成：新恢复器的完整冻结真实对照、仅烘焙/纹理优化真实消融、拓扑变体的实际烘焙和视觉验收。若声明推导§11实验全部完成，还需要仅重排、严格镜像与四组共同候选池机制消融；现有主方法入口不能代替这些结果。P2/P3/P4按可运行条件补充；GPU、artist和未知模型泛化独立。目标保持ACTIVE，不把上述374项测试当作正式性能结论。`[verified: derivation/v1.md §11.1–§11.3；goal/budget-v1-implementation.md 完成条件；新轮未执行状态]`
