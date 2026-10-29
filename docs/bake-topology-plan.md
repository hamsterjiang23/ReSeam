# 烘焙优先的拓扑变体

状态：实现与诊断中。用户已确认“加入拓扑优化，先做好烘焙”；当前优先级扩展为先建立可用的无共享烘焙输入。原[推导v1](../derivation/v1.md)仍保持固定原始几何的数学合同，不事后将拓扑变化计作该算法收益。`[verified: 本轮用户选择回复；derivation/v1.md §1]`

## 已观察的问题

用户随后提供[刺模型的合理外观参考](results/chloe-spike-reference.md)。其第一套UV沿用户指定红色贴图中的刺形图案组织面片，并有实际重复覆盖；新阶段需以模型、UV和纹理部件的协同设计为方向，最小角改善只作诊断，不能将所有细长结构当作错误。`[verified: ../artifacts/bake_topology/reference_chloe_spike01/uv_layouts.png；metrics.json layers[0]；设计解读为opinion]` `[opinion]`

固定dragon有2000个三角形，其中257个最小角小于5°，最小角约0.001594°，最长边/对应高最大约69720.56。energy_blade对应为14/500，最小角约2.52°。这些是探索诊断，不是烘焙失败的因果证明。`[verified: ../artifacts/budget_v1/topology_input_diagnostic_round01/metrics.json models[*].minimum_corner_angle_degrees、faces_min_angle_below_5_degrees、longest_edge_over_its_altitude]`

此前固定UV的法线框架诊断中，energy_blade近边世界法线均值由23.946°降至21.812°，两种框架仍有500个面未通过完整门禁；颜色/ORM不变。dragon的平滑Mikk变体初始化失败。只修平滑法线还未解决现有合同下的全模型烘焙。`[verified: ../artifacts/budget_v1/normal_frame_diagnostic_round04/diagnostic_summary.json models.energy_blade.variants、models.dragon.variants.B_smooth_mikk]`

## 执行顺序

1. 保留父高模、历史500/2000面低模及哈希；所有修整输出进入独立variant，重新建立面身份、UV和烘焙对应。不能继承旧拓扑上的硬约束或把旧面角映射冒充仍有效。`[verified: assets/v1/README.md 多轮测试规则2、9；其余为本轮设计]`
2. 先尝试确定性的局部边翻转：保护明显折痕，保持顶点位置和面数，每次改善局部最差三角形角度，检查定向、退化和流形边，失败完整回退。非共面区域的曲面会变化，必须记录；没有全局自交验证时明确标注。`[opinion]`
3. 若剩余极瘦面由近重合顶点造成，再单列短边合并、顶点重分布/回投或重新减面的变体；面数变化与曲面变化分别记录。`[opinion]`
4. 每个固定拓扑先做无共享、足够分辨率的真实BaseColor/Normal/ORM烘焙；统一目标法线、切线空间、UV间距和投射/cage参数，保存真实贴图、源/目标渲染及近景。先在能量刀冒烟，再在龙上验证。`[opinion]`
5. 分别验收曲面/轮廓误差、投射命中与错部件风险、解码材质与mip表现、实际视图效果。失败按来源分类，不用最小角改善或流程SUCCESS替代烘焙验收。`[opinion]`
6. 无共享烘焙可用后，才在每个固定variant内部比较缩图与共享。跨拓扑结果只称整个资产方案的比较，不能把收益归因于UV求解器；相同质量条件下报告最小字节、实际网格和包体总成本。`[opinion]`

Blender官方说明中，Selected to Active由低模向高模投射，cage及射线距离影响采样，烘焙与使用时法线空间应一致，mip过滤需要岛边距。这些设置会与拓扑共同影响烘焙，需作为实验变量记录。[Blender Render Baking](https://docs.blender.org/manual/en/3.6/render/cycles/baking.html) `[verified: 官方手册 Selected to Active、Normal Space、Margin]`

## 当前边界

已生成两模型的100提案局部边翻转探针，保留原始顶点位置与面数；最差小角仍未消除。它们是几何质量诊断，实际烘焙与外观验收均为NOT_RUN。正式无共享烘焙及跨拓扑效果仍待执行；旧UV轮未产生合格压缩收益。完整UV对照推迟到烘焙输入得到验证后。`[verified: ../artifacts/bake_topology/topology_flip_probe_round01/summary.json models[*].quality_before/quality_after/bake_status；../artifacts/budget_v1/budget_v1_complete_development_round03/summary.json validated_rows=0]`
