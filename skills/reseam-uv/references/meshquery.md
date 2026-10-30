# 查询、切缝计划与反馈

在原 UV 明显碎片化、需要重新安排整块部件切缝时使用本流程；原 UV 已经好用的部件继续保留，直接进入成对共享比较。不要先把异常面全部拆成独立三角岛。`[opinion]`

1. 读取 `tools describe mesh.index/mesh.query/region.propose`，为当前快照建立索引。用查询摘要定位几何特征；需要边或面 ID 时，在 SQL 中写 `id AS edge_id` 或 `id AS face_id`。完整结果在 selection.json，不能把响应前20行当作全部选择。`[verified: mesh_query.py query_mesh/query_selection]`
2. 用 region.propose 生成候选，再通过 regions、wireframe、normals、basecolor 多视图理解部件。先命名与保护区域，再用 region.define 固定原面集合。几何聚类不是语义真值。生产默认 geometry；PartField 只走独立研究对照。`[opinion]`
3. 用 seam.select 试选，seam.preview 查看实际边。三角网格不要强行调用需要四边形证据的 topological_loop；优先 region_boundary、thin_loop、geodesic 或 seamcut_seam。路径转弯／断裂时补充锚点与视角。`[opinion]`
4. 用 seam.submit_plan 保存带理由的 program，再检查切割拓扑。只对需要修改的完整部件提交 faces；保护区排除在重展开范围之外。查看 input_edge_ids、edge_ids 与 refinements，确认自动修正符合意图。`[opinion]`
5. seam.apply 使用明确后端。读反馈后调整一个主要机制，保存每次计划；连续三次同类失败改用其它路径或整块保留原 UV。seam.plan select 选取原计划及父快照，不会直接恢复一份已烘焙候选。`[opinion]`
6. 左右部件计划一致后再调用 reuse.check_pair/apply；镜像切缝不等于共享。材料目标、颜色球、真实烘焙及导出沿用主 Skill。每次 UV 修改后重新执行相关验收。`[opinion]`

## 部件判断

如果用户明确允许改变几何以获得完全对称模型，可使用 `asset.mirror_variant`，显式设置 `allow_geometry_change=true`，从一侧裁切并镜像建立新asset。先查看中线、脸部和明显色界；该入口返回同一新几何的共享／无共享快照，二者必须同设置烘焙对照。原资产和历史结果保留。它属于上游变体，不能归为固定几何UV修复收益；中线当前保持显式双侧身份，不按坐标焊接。`[verified: mirror_asset.py mirror_asset；symmetry_variant.py build_symmetry_variant；opinion: 工作流]`

默认四个水平镜头看不到脚底、顶部和被遮挡的翼膜。`view.render` 与 `seam.preview` 可显式传 `cameras`（完整参数以 `tools describe` 为准），例如 `[{"name":"underside","azimuth":180,"elevation":-65},{"name":"oblique","azimuth":45,"elevation":30}]`。这会替换默认镜头；原始、冻结目标、实际烘焙及无共享对照必须使用同一列表。角度按Y轴向上，elevation ±90为正顶／底视。`pick_pixels` 使用列表中的镜头名称，读取原面和附近边；不可把背景或未看见的面当已检查。`[verified: views.py _cameras/render_views；opinion: 制作规则]`

局部重展开后，公共排布可能平移或统一缩放未选图块；“保留原UV”指保留未选区域的内部连接与局部形状，不承诺绝对图集坐标不动。每次都保留父快照，重新烘焙并对照。`[verified: reparameterize.py reparameterize 的 _packed 调用；opinion: 检查规则]`

- 翼膜：先确认是否有正反面；沿膜边缘或翼骨边界分割，避免把每根细节划成小岛。`[opinion]`
- 翼骨与四肢：沿背面或相对隐蔽面布置连续纵向口；关节处先看拉伸反馈，不能仅因形状细长便拆碎。`[opinion]`
- 角刺：根部边界＋根尖路径；尖端保护纹样，比较左右完整部件映射。`[opinion]`
- 头脸与脚爪：眼、嘴、爪子的明显颜色边界优先保护；轻微颜色或明暗差异按已授权颜色容忍度处理，超出颜色球保留证据和外观判断。`[opinion]`

## 小型可执行计划

以下结构示例中的 face_ids 需替换为当前已观察部件的真实原面 ID；先 preview，不盲目执行示例编号。`[opinion]`

```json
{
  "snapshot": "<当前 snapshot.json 的绝对路径>",
  "faces": [0, 1],
  "program": [
    {
      "name": "part_border",
      "operator": "region_boundary",
      "arguments": {"face_ids": [0, 1]},
      "comment": "保留此自然表面的外轮廓，移除旧内部 UV 碎片"
    }
  ],
  "refine": false
}
```

索引因快照变化而过期时重新生成，不能修改 index.json 的哈希绕过。AO／厚度为 NULL 表示未测或不适用；不能解释为无遮挡／零厚度。成本记录所有失败和反馈调用；实际模型标识未知时写 unknown，不猜测型号。`[opinion]`

SQL 表名为单数 `face`、`edge`；完整结构可查询 `SELECT name,sql FROM sqlite_master WHERE type='table'`。
例如 `SELECT id AS edge_id,v0,v1 FROM edge WHERE boundary=1` 取得真实边界原边。
`seam.select` 的 `operator_parameters` 按算子列出参数：特别注意 `crease_select` 用 `edge_ids` 过滤，不能用 `face_ids` 代替。
查询得到完整集合后再选择有顺序的路径锚点；`waypoint_edges` 需要 2–8 个有序原边，不能把大型无序选择集直接作为路径。

## 从真实失败修订选区

重展开前检查选区切穿了哪些旧岛。不能只看选区内部从多少块变成一块，还要比较全模型岛数：外部残片可能抵消收益。
可用 `SELECT id AS face_id FROM face WHERE chart_id IN (SELECT DISTINCT chart_id FROM face WHERE id IN (<已观察选区的原面ID>))`
获得完整旧岛闭包候选，再检查是否扩大到保护面或不应改动的部件。不得自动覆盖保护区；闭包不合适时保留原状态。
真实能量刀中，直接自然选区使68岛增至70，完整旧岛闭包在同OptCuts后得到62；这是一个案例，不是普遍保证。

冻结目标出现明显白色边界／爪子错位时，先用同分辨率的像素拾取或 face_rasters 取得实际原面，再定位所属共享组。
部件名称可能覆盖比名称暗示更长的表面，不凭名称猜测问题归属。颜色球通过比例不替代这种特征检查。
优先修正整块锚点、切口或展开；时间内无法修复时解除整个自然组并重新冻结目标，保留失败目标图。
不要只把目标颜色改回原样却继续强制共享冲突 texel，这不能修复真实烘焙。
