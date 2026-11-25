# 分割部件参考与统一 UV 图集

入口为 `uv run reseam tools describe asset.prepare_parts`。详细参数与制作步骤见 [Skill 部件流程](../skills/reseam-uv/references/segmented-parts.md)。几何镜像与 UV 共享分别配置。`[verified: src/reseam/agent_tools/registry.py asset.prepare_parts]`

## 两种输入路线

直接制作分割结果时，`sources` 提供多对象模型或多个已定位文件。缺 UV／贴图时显式 `material_scope=geometry`，另选 `projected_reference` 可以取原模型颜色；投射记录不代表逐面身份。默认有纹理的输入采用 `material_scope=basecolor`。`[verified: src/reseam/agent_tools/parts.py prepare_parts]`

分割导出破坏连通性，或用户只希望使用分割位置作参考时，可将标签迁移到带原 UV／贴图的快照：

```json
{
  "sources": [{"id": "original", "snapshot": "E:/project/original/snapshot.json"}],
  "placement": "source_world",
  "segmentation_reference": "E:/project/parted.glb",
  "timeout_seconds": 180
}
```

这条路径只给原面赋予语义标签：按三角面中心最近分割表面决定标签，另取三处内部采样记录边界分歧；超过原模型包围盒对角线2%的距离拒绝。`segmentation_transfer.json` 保留争议面和距离。形状差异显著时先对齐或重选输入。`[verified: src/reseam/agent_tools/part_labels.py transfer_labels；opinion: 拒绝后的处理]`

`parts.json` 保存稳定ID、输入/输出面集合、连通片及策略；`part_lineage.npz` 保存新面角到来源的映射。`snapshot` 带显式镜像共享组，`unshared_snapshot` 是同几何无共享控制，均需后续排布、烘焙与检查。`[verified: src/reseam/agent_tools/parts.py prepare_parts]`

## 镜像与受控比较

`mirror_half` 用部件内部一半生成另一半，`mirror_from` 用主部件完整反射替换目标。平面在组装坐标中，最终GLB的float32变化另记。镜像需 `allow_geometry_change=true`，保护面冲突时拒绝。封口只标记保留，缺乏分割前身份时新增面审计为 `not_checked`。`[verified: src/reseam/agent_tools/parts.py _mirror/prepare_parts]`

保存原几何无共享、原几何尝试共享、局部镜像无共享、相同局部镜像共享四种配置。失败允许保留独占；没有变化的配置可以引用同一产物，但注明复用而非重跑。共享比较绑定同几何与颜色来源，跨几何变化单列。`[opinion]`

先512²小样，正式2048²、种子17、gap/border/padding 8px，`acceptance_policy=visual`。比较可用旧UV与OptCuts局部重展候选，再看源色、冻结目标、实际烘焙与六视角／接合处近景。分别记录数值诊断、Agent观察与人工认可。`[opinion]`

辅助命令通过已有工具生成真实图集、模型与视图，并独立回读导出面角位置：

```text
uv run python -m scripts.run_parts_preview --snapshot <snapshot.json> --output <全新目录> --resolution 512
```

脚本不自动认可外观，`preview.json` 保留 `visual_review=pending`。逐部件重展开可用 `scripts/rebuild_parts_uv.py`，其结果也只是待看图的候选；全部展开后统一排布。失败保留每步请求／结果，从最后有效快照在新目录继续。恐龙真实合岛、小片清理与失败见 [最终记录](results/dragon-parts-clean-final.md)。`[verified: scripts/run_parts_preview.py run_preview；scripts/rebuild_parts_uv.py rebuild；opinion: 继续方式]`
