# 防御塔的共享、合岛与PBR交付

## 适用条件与输入

适用于已有PBR材质的重复装饰或对称建筑，需要比较原形整理、镜像共享、继续合岛与交付展示的任务。镜像阶段以当前资产的明确授权为前提。`[opinion]`

本例原材质含8192² BaseColor和2048² Normal，Roughness固定0.6、Metallic固定0、AO取1。最终镜像版7,650三角形，48连通岛／24独立图块，另有相同几何48图块无共享对照。`[verified: docs/assets/tower-pbr-process/round01/README.md L9；docs/assets/tower-uv-comparison/README.md L15]`

## 历史过程与关键决策

1. 保存原UV参考，比较六种基线、原形接缝整理及原形共享，再从已授权的半边镜像路线继续合岛。制作过程按输入、基线、整理、镜像、合岛和近景六部分保留。`[verified: docs/assets/tower-uv-comparison/README.md L5；docs/assets/tower-uv-comparison/report/report.md L9–L28]`
2. 镜像保留负X侧，记录源三角形和重心坐标，反射法线与切线并修正切线方向符号。颜色与法线沿同表面面角来源重采样，法线先恢复表面空间方向，再转换至目标切线坐标系。`[verified: docs/assets/tower-pbr-process/round01/README.md L10–L11]`
3. 共享成员先独立采样，再在一致目标方向下合成。写出2K BaseColor、Normal、ORM，并将AO、Roughness、Metallic拆分展示。12级mip中的Normal逐级重归一化。`[verified: docs/assets/tower-pbr-process/round01/README.md L11–L13]`
4. 最终共享与无共享对照绑定同一镜像几何和2K尺寸。实际整理了全方法PBR对照、关键部位近景、线框与模型/贴图包。`[verified: docs/assets/tower-uv-comparison/README.md L3–L17；README.md L3–L7]`

## 失败、取舍与结果

- FlattenAnything保留UV重叠结果，OptCuts保留原生失败及修复后超时，归档含ReSeam失败候选与修正步骤。`[verified: docs/assets/tower-uv-comparison/README.md L19–L25]`
- 最终48岛／24图块、覆盖56.82%，Agent结论VISUAL_KEEP，数值DIAGNOSTIC，人工和游戏Shader状态NOT_RUN。`[verified: docs/assets/tower-uv-comparison/README.md L15–L17]`
- 同尺寸共享与无共享完整mip解码字节均为67,108,860。粗糙度、金属度和AO的展示值沿用本例固定材质参数。`[verified: docs/assets/tower-uv-comparison/README.md L15；docs/assets/tower-pbr-process/round01/README.md L9–L15]`

## 迁移到当前资产

先用 `uv.inspect`、真实材质视图与来源记录选择原形整理或已授权的镜像路线，共享后检查连续表面的多余接缝。通过当前 `tools describe` 核实 `asset.mirror_variant`、`uv.reparameterize`、`uv.pack` 和材质工具的参数，使用新快照重烘焙。`[opinion]`

沿每个通道的真实来源处理色彩空间与切线转换，分别记录纹理载荷、文件体积及目视/数值状态。交付时保留同几何无共享版本、通道图和实际近景。`[opinion]`

## 证据入口

以下路径按 [索引的仓库定位方法](index.md) 读取。`[opinion]`

|用途|仓库根相对路径及读取位置|
|---|---|
|过程、归档与恢复方法|`docs/assets/tower-uv-comparison/README.md` 的“完整实验归档”“还原方法”。`[verified: docs/assets/tower-uv-comparison/README.md L21–L61]`|
|方法结果与失败|`docs/assets/tower-uv-comparison/report/report.md` 的逐方法表。`[verified: docs/assets/tower-uv-comparison/report/report.md L9–L28]`|
|PBR来源、图表、贴图包|`docs/assets/tower-pbr-process/round01/README.md` 的“这次贴图怎样得到”“来源索引”。`[verified: docs/assets/tower-pbr-process/round01/README.md L3–L39]`|
|实际模型|`artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_exhaustive_minimum_shared_2048/export/asset.glb`。`[verified: docs/assets/tower-uv-comparison/README.md L59]`|
|真实BaseColor与Normal|`docs/assets/tower-pbr-process/round01/maps/mirror_negative_exhaustive_minimum_shared`。`[verified: docs/assets/tower-pbr-process/round01/README.md L22–L25]`|
|制作图解|`output/pdf/tower-making-process/防御塔制作过程图解.pdf`，六页制作与近景。`[verified: docs/assets/tower-uv-comparison/README.md L5]`|
|八视角线框|`docs/assets/tower-wireframe/round01/README.md`。`[verified: README.md L3]`|

全量请求、日志、快照与19模型Blender场景的恢复入口在上述分卷归档说明中。`[verified: docs/assets/tower-uv-comparison/README.md L23–L25]`
