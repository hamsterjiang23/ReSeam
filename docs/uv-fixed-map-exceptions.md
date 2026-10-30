# 固定UV映射后处理独占例外

目标是修复已经大部分合法的共享候选。先固定对应与逐角UV，再选择整面独占；不在删除主面后自动重新投影全部剩余目标顶点。`[opinion]`

## 当前观察

完整脚的候选在20个例外面后已满足翻转、拉伸与密度门限，但仍有243个正面积单元的法线对应冲突。按固定单元图求得112面、3.1073%区域面积的加权覆盖；若删面后重做最近点投影，会改变主域并产生新的塌缩三角形。`[verified: geometry_chart_normal_groups01/foot_cell_cover_diagnostic.json；foot_cell_cover_final/check.json]`

删除后重算与固定映射后限制不等价：$P_{D\setminus E}(x)$ 一般不等于 $P_D(x)$ 在剩余成员上的限制。孔洞会让最近点移到边界，多个顶点可能被投向同一条边。`[opinion]`

## 固定单元图

把主面与目标面作为二部图两侧；某一对原面在当前UV中有不兼容的正面积交叠，就连一条冲突边。变量 $z_f\in\{0,1\}$ 表示将原面f独占，权重为原始3D面面积，求

$$\min_z\sum_f a_f z_f,\qquad z_i+z_j\ge1\quad\forall(i,j)\in E_{bad}.$$

翻转、单面各向异性等一元失败要求对应面必须被处理；邻接密度约束另检查。固定二部冲突图的加权顶点覆盖可用LP／最小割求解；这不代表最终UV优化的全局最优。`[opinion]`

## 限制映射的理由

若其余UV完全不动，保留下来的每个面之间的交叠单元仍相同；覆盖集移除了每条坏边的至少一个端点，因此原来的坏单元不再由两个共享成员覆盖。把释放面放到独占图块后，再对每个实际UV共享组共同施加统一缩放和平移，原交叠中的重心坐标、3D对应及法线关系保持不变。`[opinion]`

剩余面的各向异性不因统一缩放改变；独占面的内禀三角形可做到等距。面角连接、相邻密度、图集边界、间距、组内实际交叠及浮点运算仍必须全量复核；组被清空或顶点扇断开也可能需要进一步处理。上述推理只针对固定候选，不能跳过最终门禁。`[opinion]`

## 工具顺序

1. `reuse.check_pair` 按需保留带哈希、明确拒绝状态的诊断候选。
2. Agent读取具体坏面和交叠单元，保存整面例外提案及选择依据。
3. 对诊断候选使用 `reuse.apply action=exclusive`，保留其余映射后重新排布与全量几何检查。
4. 只有通过后再重新烘焙、独立颜色／接缝／mip验收；不能把图覆盖解或成功调用算作复用收益。

## 连通例外与实际图块

仅最小化释放面积会产生许多碎片。设原始网格边及旧UV边连接组成面邻接图，$C(z)$为被释放面在此图中的连通片数，可以用 $\sum_f a_fz_f+\lambda C(z)$ 指导例外提案；这是设计代理，最终仍须计算实际交叠连通图块和完整几何质量。新增边只沿原始网格边，不能通过相近坐标合并UV。`[opinion]`

本次Agent从同一固定脚候选选择全部冲突边的second端点，再在同一旧UV图内用面积加权的原面邻接最短路径连接例外。132个端点、24个例外连通片变为190个面、6个连通片；覆盖全部243条冲突边且不包含保护面。面积最小LP的112个面则有61个旧UV连通片。两个提案使用同一父快照，没有重做对应。`[verified: geometry_frozen_uv_release01/coherent_second_cover.json independent_cover_witness；released_patch_counts.json]`

`reuse.apply action=exclusive, exclusive_layout=preserve_patches`保留释放片的原UV形状、片内旧副本身份及未释放对应，沿原边分离独占片；整个负向释放片统一反射。旧`intrinsic`策略仍为默认，不能把两种策略的不同结果写成同一次实验。组解散时沿用旧独占操作的成员方向规范化，额外改变也必须记录。`[verified: src/reseam/agent_tools/release.py release_patches；opinion: 记录约束]`

## 整脚几何检查点

新候选完整几何通过：独立排布owner为199，但按实际正面积交叠图计算是200个独立图块，较无共享203减少3；UV连通岛为227。一个共享组中保留的两个源岛没有实际交叠链连接，因此不能把owner数当作图块数。`[verified: geometry_coherent_release01/metrics.json；alias_coverage.json]`

原语义范围2396个面中，2186个面仍属于13个共享组，覆盖92.9266%的两脚表面积；按每面实际UV交叠并集换算，实际被复用覆盖的两脚表面积为86.1192%。前者是成员范围，后者才是本次实际交叠覆盖，均不是全模型比例。`[verified: geometry_coherent_release01/metrics.json；alias_coverage.json]`

最大法线对应角44.7363°、位置误差0.0020642、最大各向异性1.9999533及邻接密度比1.99999998，均通过原门限；原几何与原面保持相等。本次6片排布直接合法，`packing_roundoff.attempts=[]`，没有把数值修正机制计作收益。颜色烘焙仍需另验。`[verified: geometry_coherent_release01/release/geometry.json；release/operation.json；metrics.json original_geometry_bit_identical]`

[共享前UV](../artifacts/dragon_reuse_development/geometry_coherent_release01/before_view/uv.png) · [共享后UV](../artifacts/dragon_reuse_development/geometry_coherent_release01/after_view/uv.png) · [逐步记录](../artifacts/dragon_reuse_development/geometry_coherent_release01/steps.md)。`[verified: 对应view.render产物及步骤文件]`
