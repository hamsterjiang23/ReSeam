# 整龙UV复用进度

## 最新：完整部件共享与颜色球

修订后统一使用uniform排布并重跑受影响的自动候选：无共享203图块、OptCuts无共享169、纯几何157、Agent150；Agent保留19组完整部件共享，实际共享覆盖全模型44.6587%。同布局解除共享对照为169图块。`[verified: components_auto_uniform02 各方法 inspect/uv_metrics.json；states.json]`

新增低／中／高颜色球（默认半径5／10／20，可自定义）：高档有99.24%的修改区域采样面积在球内，仍保留球外位置，不写成全部通过。用户允许小幅颜色及明暗差异；预览选择高档，颜色球、真实烘焙误差和Agent外观判断分别报告。`[verified: components_color_high02/color_check.json presets；用户最新颜色取舍；opinion: 预览选择]`

真实512与2048矩阵及实际PNG/GLB在[完整部件报告](../../artifacts/dragon_reuse_development/components_report02/report.md)，报告注明运行中／完成项。接口与参数说明见[部件工具及颜色球](../dragon-component-reuse.md)。下文保留早期轮次，不能与最新uniform结果混排。`[verified: components_color02/matrix.json；opinion: 报告口径]`

## 2026-10-30 部件重展开

新轮次已完成19组成对自然部件的几何比较：原无共享203图块，OptCuts重展开169图块，自动复用159图块，锚点部件复用152图块。锚点路径接受17组，实际共享覆盖全模型37.5053%；手臂及侧角两组拒绝保留，正在按完整区域修正。颜色目标、真实贴图与外观结论另行记录，本段不将几何完成当作烘焙完成。`[verified: components_round01/summary.json；geometry_auto_inspect/uv_metrics.json；agent_paired_inspect/uv_metrics.json；matrix.json]`

本轮接口与操作见[部件重展开](../dragon-component-reuse.md)，原始调用、配置、父子快照和失败见[步骤记录](../../artifacts/dragon_reuse_development/components_round01/steps.md)。下文保留此前整脚严格实验，不能与新配置混算收益。`[verified: components_round01/protocol.json、steps.json；opinion: 报告边界]`

## 历史整脚严格实验

整脚UV已通过完整几何检查；当前8K共享贴图仍因7处LOD2接缝被拒绝，尚未交付整龙已验收复用结果。编码支线暂停，主线继续处理实际区域共享与独占例外。`[verified: geometry_coherent_release01/release_trace02/result.json；restored_whole_foot_color_pair03/comparison_metrics.json；用户最新纠正]`

## 当前6552顶点变体的几何对照

以下使用同一资产、10832原三角形和共同父状态05b281ac…；统一1024、gap16、border8及几何门限。旧7007顶点变体不参加本表收益比较。`[verified: geometry_restored_baselines02/README.md、config.json、face_corner_equivalence.json]`

|路径|UV连通岛|实际独立图块|共享组|实际共享表面积占全模型|
|---|---:|---:|---:|---:|
|无共享|203|203|0|0% `[verified: geometry_restored_baselines02/comparison_metrics.json no_share]`|
|双翼膜OptCuts无共享|203|203|0|0% `[verified: comparison_metrics.json optcuts_no_share]`|
|旧21候选自动策略|203|201|2|3.1626% `[verified: comparison_metrics.json geometry_auto]`|
|整脚连贯独占策略|227|200|13|8.5765% `[verified: comparison_metrics.json coherent_agent]`|

实际图块按岛间已授权的正面积交叠连通分量计算。整脚有199个packing owners，但有200个实际图块；两脚区域实际交叠覆盖86.1192%，声明共享成员面积92.9266%，不能混用。上述面积口径为1024几何状态，不能未经核对套到8K状态。`[verified: geometry_restored_baselines02/README.md、comparison_metrics.json；geometry_coherent_release01/alias_coverage.json]`

同父固定UV、同代码的策略消融中，112面最小面积独占有61个例外片、得到255图块；190面连贯独占有6片、得到200图块。两者均几何通过，仅更换例外面，不重新投影。自动21候选与Agent跨旧岛整脚提案属于端到端比较；不能写成固定候选池上的LLM优势。OptCuts只重展77+77面翼膜，不代表整龙OptCuts最优。`[verified: geometry_restored_baselines02/controlled_ablation.json、comparison_scope.json、README.md；解释为opinion]` `[opinion]`

[四路径与策略消融完整报告](../../artifacts/dragon_reuse_development/geometry_restored_baselines02/README.md) · [固定UV解除共享原理](../uv-fixed-map-exceptions.md) · [UV、同色组及逐步前后记录](../../artifacts/dragon_reuse_development/geometry_coherent_release01/steps.md)。`[verified: 对应文件]`

## 8K真实贴图：共享拒绝、同目标控制通过

两路使用相同父资产、同一个源贴图与固定对应冻结的目标、相同8192尺寸和LOD0/1/2协议。共享候选的几何、面颜色、渲染通过，7处LOD2接缝单侧重建拒绝；同目标无共享控制完整通过并accepted导出。9次调用工具合计756.390秒，墙钟769.828秒，额外17.032秒外观诊断并行单列。`[verified: restored_whole_foot_color_pair03/comparison_metrics.json、candidate_validate/validation.json、round_cost.json]`

冻结目标还存在白爪颜色向蓝色趾根扩张，外观修改标记NOT_ACCEPTED。这与实际贴图对冻结目标的重建误差分开判定。下一步先查看新目标与原外观，再决定是否进行大图烘焙；不合格目标不自动进入烘焙。`[verified: restored_whole_foot_color_pair03/comparison_metrics.json appearance_edit_status、appearance_diagnostic/outer_comparison.png；opinion: 下一步]`

![原始外观与冻结共享目标的脚部近景](../../artifacts/dragon_reuse_development/restored_whole_foot_color_pair03/appearance_diagnostic/outer_comparison.png)

[真实贴图、GLB及完整对照报告](../../artifacts/dragon_reuse_development/restored_whole_foot_color_pair03/report.md)。`[verified: 对应报告及导出文件]`

## 历史7007顶点变体的几何准备

本轮四条路径已完成相同父状态的几何准备；颜色、mip、实际存储和整龙有效复用尚未验收。`[verified: branches.json run_status、各path final_quality_passed=null]`

|路径|连通岛|独立排布对象|共享组|共享成员面积比例|独立准备秒|
|---|---|---|---|---|---|
|no_share|203|203|0|0.0000%|0.000。`[verified: preparation/branches.json paths.no_share]`|
|optcuts_no_share|203|203|0|0.0000%|15.610。`[verified: preparation/branches.json paths.optcuts_no_share]`|
|geometry_auto|203|201|2|3.1628%|342.922。`[verified: preparation/branches.json paths.geometry_auto]`|
|agent|205|202|3|6.9579%|43.906。`[verified: preparation/branches.json paths.agent]`|

共同预处理42.735秒，全部89次调用，工具合计384.121秒；拒绝候选保留。`[verified: branches.json summary]`

原资产121岛带已有交叠；合法修复父状态203岛。当前Agent方案共享翼膜、踝部和翼尖，仍只有3组，独立对象仅少1个。共享成员面积含两侧，不能当成减少的纹理字节。`[verified: agent metrics；geometry_agent_best03/checkpoint.md；opinion: 指标边界]`

OptCuts仅重展左右翼膜，没有共享，不能代表整龙全局OptCuts最优。自动选择和Agent使用同一Agent提供的21组语义候选池，属于受控决策比较，不能称为完全自动的语义发现。`[verified: preparation/protocol.json；branches.json comparison_scope]`

用户最新明确以UV岛复用为重点。后续继续改进部件对应和碎片问题；编码实验暂停。编码诊断不会作为UV复用收益。`[verified: 用户“关键是UV岛复用啊，贴图压缩不是很关键”；opinion: 后续执行约束]`

[全部逐步回执及前后指标](../../artifacts/dragon_reuse_formal_round01/preparation/steps.md)；[冻结输入与四路径状态](../../artifacts/dragon_reuse_formal_round01/preparation/branches.json)。`[verified: 以上文件]`

## 原始连接恢复后的独立变体

上表属于7007顶点的规范化输入。新变体恢复8个流形语义部件的原始索引连接，几何顶点表为6552，仍为10832个原三角形；逐角XYZ、UV数组和UV副本ID逐字节相等。原始UV仍121岛、0共享；恢复连接本身不计作UV复用收益。`[verified: topology_restore_preflight01/independent_geometry_witness.json；verification.json]`

完整脚从7个几何连通片恢复成1个，侧冠15／16→1，背刺2→1；这使跨旧岛的共同参数化可以初始化。已有新变体203岛无共享修复通过几何检查，完整脚与背刺的共享尚未通过；圆边界调和初值仍产生超限畸变。`[verified: topology_restore_preflight01/verification.json regions；geometry_restored_regions01/steps.md]`

本轮接着改进按主UV图块重建目标切缝的近似对应；四路Baseline将在同一新变体内重新比较，跨变体不能归因于共享。`[opinion]`

相似变换支持已通过12项回归和16个独立缩放／镜像／面序探针；真实旧岛整区仍拒绝，不能把新接口等同真实复用成功。`[verified: test_agent_tools_similarity.py与独立review输出；geometry_similarity_round01/summary.json]`
