# MeshQuery 工具与三资产 UV 重构结果

已产生三资产的真实 UV、2048 BaseColor、完整 mip 和可查看模型：历史能量刀与龙翼有部件级共享，Tripo 项目路径达到117连通岛、99独立图块、18组共享。数值质量拒绝仍保留，当前产物是诊断／外观研究交付，不是完整PBR、GPU或人工美术通过。`[verified: ../../artifacts/meshquery_v1/history_report01/report.md 结果边界/真实2048贴图、模型与质量; ../../artifacts/meshquery_v1/tripo_project_color01/project_color/inspect/result.json; validate/result.json]`

方法与接口分别见[语义重构说明](../semantic-uv-reconstruction.md)、[MeshQuery工具说明](../meshquery-tools.md)及[逐工具验证](meshquery-tool-verification.md)。本项目实现的是论文公开机制的离散适配；工具可执行、Agent决策、复用收益与作者完整复现是不同结论。`[verified: ../meshquery-tools.md 评价边界; meshquery-tool-verification.md 公开入口与覆盖]` `[opinion]`

## 三资产结果

历史能量刀500面、龙翼2000面的输入低模没有UV，本轮共同起点为既有xatlas的68／177岛；Tripo为10832面独立资产，保留原121岛起点。三者不混算同一资产的收益，也不以历史修复父状态203岛代替Tripo原始基线。`[verified: ../../artifacts/meshquery_v1/history_report01/report.md 首段; ../../artifacts/meshquery_v1/tripo_report02/report.md 首段/路径表; ../../artifacts/meshquery_v1/tripo_reimport01/all_final_integrity.json exports.face_count]`

|资产／实际路径|起点→结果连通岛|独立图块／共享组|实际共享表面积|质量结论|
|---|---|---|---|---|
|能量刀：完整旧岛重展＋共享|68→62|59／3|13.2886%|颜色数值诊断未通过；已生成真实贴图与GLB。`[verified: ../../artifacts/meshquery_v1/history_bestshared01/energy_wholechart_shared/shared_uv/uv_metrics.json; ../../artifacts/meshquery_v1/history_report01/report.md energy_wholechart_shared/bake2048]`|
|历史龙翼：完整旧岛重展＋共享|177→173|171／2|0.9189%|颜色数值诊断未通过；第三对候选对应拒绝。`[verified: ../../artifacts/meshquery_v1/history_dragon_shared01/dragon_skill_wholechart/shared_uv/uv_metrics.json; ../../artifacts/meshquery_v1/history_report01/report.md 关键修正与消融/dragon_skill_wholechart]`|
|Tripo：项目规则与反馈后路径|121→117|99／18|43.1580%|限定颜色范围中geometry/material/seams通过，render拒绝，`quality_accepted=false`。`[verified: ../../artifacts/meshquery_v1/tripo_project_color01/project_color/inspect/uv_metrics.json; validate/result.json]`|
|Tripo：泛用规则与反馈后路径|121→117|102／15|36.6435%|同样render拒绝，`quality_accepted=false`；不能据当前差额归因于Skill。`[verified: ../../artifacts/meshquery_v1/tripo_generic_color01/generic_color/inspect/uv_metrics.json; validate/result.json]` `[opinion]`|

连通岛、独立图块与共享面积分别统计；共享成员面积不是节省的贴图字节。本轮代表产物均为2048 RGBA8，完整mip纹理载荷22,369,620字节，没有完成同质量缩图搜索；PNG文件、GLB包体与运行时纹理载荷也不混用。Tripo的几何配置显式关闭畸变质量约束并保留指定源冲突，不能把该geometry通过写成全部严格几何约束通过。`[verified: ../../artifacts/meshquery_v1/history_report01/report.md 结果边界/真实2048贴图、模型与质量; ../../artifacts/meshquery_v1/tripo_project_color01/project_color/validate/result.json payload_bytes/geometry_profile/strict_geometry_profile_applied]`

## 实际贴图与模型

下表链接的是工具实际产生的文件；均保留诊断身份。完整路径、失败、未运行项、步骤理由与费用分别见[历史两资产明细](../../artifacts/meshquery_v1/history_report01/report.md)和[Tripo明细](../../artifacts/meshquery_v1/tripo_report02/report.md)。`[verified: 所链接报告的产物表与失败章节]`

|资产／路径|2048 BaseColor|可查看模型|对应无共享控制|
|---|---|---|---|
|能量刀完整旧岛共享|[实际贴图](../../artifacts/meshquery_v1/history_bestshared01/energy_wholechart_shared/bake2048/texture/BaseColor_mip0.png)|[颜色GLB](../../artifacts/meshquery_v1/history_bestshared01/energy_wholechart_shared/bake2048/color_only_preview.glb)|[匹配unshare贴图](../../artifacts/meshquery_v1/history_bestunshare01/energy_best_matched_unshare/bake2048/texture/BaseColor_mip0.png) · [GLB](../../artifacts/meshquery_v1/history_bestunshare01/energy_best_matched_unshare/bake2048/color_only_preview.glb)。`[verified: history_report01/report.md 真实2048贴图、模型与质量]`|
|历史龙翼完整旧岛共享|[实际贴图](../../artifacts/meshquery_v1/history_dragon_shared01/dragon_skill_wholechart/bake2048/texture/BaseColor_mip0.png)|[颜色GLB](../../artifacts/meshquery_v1/history_dragon_shared01/dragon_skill_wholechart/bake2048/color_only_preview.glb)|[匹配unshare贴图](../../artifacts/meshquery_v1/history_dragon_unshare01/dragon_skill_wholechart_matched_unshare/bake2048/texture/BaseColor_mip0.png) · [GLB](../../artifacts/meshquery_v1/history_dragon_unshare01/dragon_skill_wholechart_matched_unshare/bake2048/color_only_preview.glb)。`[verified: history_report01/report.md 真实2048贴图、模型与质量]`|
|Tripo项目18组|[实际贴图](../../artifacts/meshquery_v1/tripo_project_color01/project_color/bake/texture/BaseColor_mip0.png)|[诊断GLB](../../artifacts/meshquery_v1/tripo_project_color01/project_color/export/asset.glb)|[匹配unshare贴图](../../artifacts/meshquery_v1/tripo_project_unshared01/project_same_layout_unshared/bake/texture/BaseColor_mip0.png) · [GLB](../../artifacts/meshquery_v1/tripo_project_unshared01/project_same_layout_unshared/export/asset.glb)。`[verified: tripo_report02/report.md project_color/project_same_layout_unshared; tripo_reimport01/all_final_integrity.json]`|
|Tripo泛用15组|[实际贴图](../../artifacts/meshquery_v1/tripo_generic_color01/generic_color/bake/texture/BaseColor_mip0.png)|[诊断GLB](../../artifacts/meshquery_v1/tripo_generic_color01/generic_color/export/asset.glb)|[匹配unshare贴图](../../artifacts/meshquery_v1/tripo_generic_unshared01/generic_same_layout_unshared/bake/texture/BaseColor_mip0.png) · [GLB](../../artifacts/meshquery_v1/tripo_generic_unshared01/generic_same_layout_unshared/export/asset.glb)。`[verified: tripo_report02/report.md generic_color/generic_same_layout_unshared; tripo_reimport01/all_final_integrity.json]`|

Tripo四份最终共享／匹配无共享GLB均已独立解析重新载入：10832面、位置误差0、面角UV最大误差约4.46–4.47e-8，嵌入2048纹理像素与导出贴图一致。此检查证明产物保存了记录中的几何、UV和图像，不替代渲染质量。`[verified: ../../artifacts/meshquery_v1/tripo_reimport01/all_final_integrity.json exports/all_passed/verification_scope]`

## 反馈实际改变了什么

- **选区要看全模型后果。** 能量刀113面自然选区在内部变整块，却把外侧旧岛切碎，使全局68→70；扩到所涉旧岛完整143面、保持OptCuts后端后得到62岛。历史龙翼也由自然选区的177→186，改为完整265面旧岛范围后的173岛。`[verified: ../../artifacts/meshquery_v1/history_report01/report.md 关键修正与消融]`
- **拓扑反馈与展开反馈分开。** 能量刀边界-only计划形成环带；补连续纵切得到单盘后，LSCM仍自交。保留切口、改正权调和才通过，不能把拓扑合法当UV合法。`[verified: ../../artifacts/meshquery_v1/history_report01/report.md 关键修正与消融]`
- **共享目标先看明显特征。** Tripo19组探索不是最终外观接受；白色特征错位触发整块解除／修正和重新冻结。项目18组目标记录翼尖白条修正，并保留局部爪边变暗取舍；泛用15组目标记录爪根与白尖边界修正。颜色球并未全通过，目标接受不改写烘焙数值拒绝。`[verified: ../../artifacts/meshquery_v1/tripo_setup01/project18_target_review.json observation/limitations/scope; generic_safe15_target_review.json observation/scope; ../../implementation-notes.md MeshQuery工具实现/Questions for review]`
- **研究分区成功不等于后续UV成功。** 500面能量刀的PartField真实推理产生8组，几何多尺度产生195候选；两种集合含义不同，不能用数量比较分区品质。PartField选区后续LSCM／ABF自交拒绝，预定烘焙未运行。`[verified: ../../artifacts/meshquery_v1/history_report01/report.md PartField与几何候选]`

## 数值、Agent外观与人工状态

|证据层|已记录结论|没有证明的内容|
|---|---|---|
|数值质量|历史代表路径颜色诊断拒绝；Tripo代表路径材料、接缝通过但渲染拒绝，导出diagnostic。`[verified: history_report01/report.md 真实2048贴图、模型与质量; tripo_project_color01/project_color/validate/result.json; tripo_generic_color01/generic_color/validate/result.json]`|完整数值质量通过、同质量最小字节。`[opinion]`|
|历史两资产Agent初筛|已有非盲评记录：已查看水平视角未见新增大片错贴，`initial_visual_acceptance=true`，仅限明显外观破坏初筛。`[verified: ../../artifacts/meshquery_v1/history_report01/appearance_review.json status/observations/acceptance_scope]`|盲评结论、未看视角、细节全面保真。`[opinion]`|
|Tripo目标预检|项目18组与泛用15组冻结目标均有Agent接受记录，允许记录中的局部明暗取舍；范围明确排除最终贴图／数值／PBR接受。`[verified: ../../artifacts/meshquery_v1/tripo_setup01/project18_target_review.json; generic_safe15_target_review.json scope]`|完整外观统一签收；补充视角初筛见下文，人工仍未验收。`[opinion]`|
|人工／引擎|`human_accepted=false`、`gpu_verified=false`；没有以Agent判断替代人工。`[verified: ../../artifacts/meshquery_v1/tripo_reimport01/all_final_integrity.json; ../../artifacts/meshquery_v1/history_report01/appearance_review.json]`|工业美术接受、目标引擎完整PBR。`[opinion]`|

## 受控比较与补充视角

[受控对照](../../artifacts/meshquery_v1/controlled_report01/report.md)统一了LSCM批次、调和调用顺序、15对候选、原色目标和2048质量设置，只改变研究切口。相同切口区域归一化UV形状误差最大6.913e-15；大腿及一侧背刺的对应输入切口随该研究变量变化，其余参数相同。两路径均为117连通岛、102独立图块、15组，颜色面积加权均值0.0175963／0.0175909，均数值拒绝，没有观察到项目切口的图块收益。旧混杂轮次保留，canonical累计2117.986秒，generic累计1328.692秒；三个导出均独立重导入通过。`[verified: controlled_report01/report.json rows/candidate_invariants_equal_excluding_research_seams；controlled_reimport01/integrity.json]`

无反馈／有反馈对照见历史报告的选区闭包、环带补切口及Tripo的LSCM→整块调和反馈；通用／项目规则的严格机制比较以上述固定候选轮次为准；geometry／PartField语义对照见历史报告，同一500面资产真实运行，PartField后续展开拒绝，依赖烘焙保留NOT_RUN。最终18组与15组属于目标及反馈历史不同的端到端结果，不能替代受控消融。`[verified: history_report01/report.md 关键修正与消融/PartField与几何候选；controlled_report01/report.json；opinion: 比较边界]`

可配置顶视、底视和斜视已补齐，实际运行见[camera_extension01](../../artifacts/meshquery_v1/camera_extension01/appearance_review.json)。Agent查看最终2048共享图、同布局无共享图与原色的顶部、仰视、斜视和后下方视角：未见新增大片错贴，保留可见蓝色明暗改动，限定初筛接受；不改写数值拒绝。默认四视角及UV五张PNG与冻结版本逐字节相同。`[verified: camera_extension01/appearance_review.json；regression.json]` `[opinion]`

最终数值实验源码已保存为[冻结归档](../../artifacts/meshquery_v1/frozen_source01/manifest.json)。后续改动仅扩展诊断镜头和报告状态：未执行验收的行改为NOT_EVALUATED/null，真实拒绝仍为REJECTED。旧history_partfield01等冻结报告曾默认显示False，应按对应调用解释为“未评估”；新汇总不沿用这一默认值。`[verified: frozen_source01/source.zip；tripo_report02/report.json quality_status；scripts/run_meshquery_experiments.py quality_status]`

用户随后授权整半几何镜像作为独立变体，已产出[整龙共享与紧凑排布结果](dragon-whole-half-reuse.md)：134连通岛保持，独立图块134→67，2048轮廓排布占用57.95%，当前限定BaseColor协议通过。它改变几何，不能计为上述固定几何方法的收益。`[verified: 用户“允许，新建镜像模型，优先实现大面积复用”；opinion: 比较边界]`

历史高低模没有相同面身份，匹配unshare控制使用各成员原色，不能称为“同一冻结对称化目标”的保真比较。Tripo候选沿用历史19组已审阅区域，不能写成这一轮从零自动发现全部语义部件。`[verified: ../../artifacts/meshquery_v1/history_report01/report.md 结果边界; ../../artifacts/meshquery_v1/tripo_report02/report.json candidate_regions]`

逐工具测试、三个原生Blender后端、Skill独立前向失败与后续修正统一见[工具验证](meshquery-tool-verification.md)。本报告不覆盖历史V1的冻结实验结论，不以本轮部件进展替代完整求解器、完整整龙有效复用或工业可用性的交付条件。`[verified: meshquery-tool-verification.md 公开入口与覆盖/独立Skill前向测试及反馈修正]` `[opinion]`
