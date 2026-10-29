# 刺模型：模型、UV与贴图协同的参考

用户将这套资源指定为合理外观参考；本轮读取了原FBX和两张PNG，用独立Blender进程导入并导出实际UV。没有复现Unity材质及其平铺、偏移、旋转、扭曲和动态参数。`[verified: 本轮用户给定参考；../../artifacts/bake_topology/reference_chloe_spike01/uv.json unity_material_reproduced=false；metrics.json texture_assignment]`

![原贴图与实际两套UV](../../artifacts/bake_topology/reference_chloe_spike01/uv_layouts.png)

左侧为用户指定的256×256红色贴图；中间是第一套原始UV的叠图，右侧是第二套原始UV，不能把右侧自动解释为主颜色采样布局。第一套沿着刺形图案组织面片是从图中得到的设计观察；材质变换未解码，所以叠图不等于最终游戏外观。`[verified: ../../artifacts/bake_topology/reference_chloe_spike01/uv_layouts.png；uv.json objects[0].uv_layers]` `[opinion]`

|检查|实际结果|
|---|---|
|Blender导入|4.5.11 LTS；1个网格，227顶点、330多边形、450个导入三角形。`[verified: ../../artifacts/bake_topology/reference_chloe_spike01/uv.json objects[0]]`|
|第一套UV|UVChannel_1；被使用的逐角UV值去重为337，不等于原FBX完整UV表项数。`[verified: ../../artifacts/bake_topology/reference_chloe_spike01/metrics.json layers[0].unique_loop_uv_values]`|
|第一套覆盖|三角形面积和0.62199，并集面积0.50181；已用区域平均覆盖1.23948层。89对三角形交叠面积大于1e-12。`[verified: ../../artifacts/bake_topology/reference_chloe_spike01/metrics.json layers[0]]`|
|第二套UV|UVChannel_2；逐角UV值去重227，面积和/并集为2.54652；用途尚未核实。`[verified: ../../artifacts/bake_topology/reference_chloe_spike01/metrics.json layers[1]；uv.json unity_material_reproduced=false]`|
|第一套畸变诊断|实际逐三角形UV/3D Jacobian的各向异性中位数1.135、P95约4.480，47/450面超过2。不能因此直接否定用户认可的外观。`[verified: ../../artifacts/bake_topology/reference_chloe_spike01/metrics.json layers[0].anisotropy_median/anisotropy_p95/anisotropy_above_two_triangles]` `[opinion]`|

## 对新路线的约束

- 以用户指定参考为设计目标：连贯且便于编辑的纹理部件、明确的部件对应、合理复用和有意义的独占差异。不能只优化岛数量、几何均匀度或占用率。`[opinion]`
- 细长的刺需要细长结构；局部极小角只作为排查线索。要结合轮廓、真实投射、采样和最终外观判断是否需改拓扑，不能全模型强制“等边化”。`[opinion]`
- 先在独立拓扑variant上建立稳定无共享烘焙，再尝试对外观兼容的部件建立共同贴图块；形状不同不自动等于不能复用，外观或法线框架冲突也不能因同一组名而忽略。`[opinion]`
- 第一套UV已有实际重复覆盖，但交叠数不证明每个交叠都是有意共享，更不证明它符合旧ReSeam的镜像几何许可。参考外观验收和旧数学约束需分别记录。`[verified: ../../artifacts/bake_topology/reference_chloe_spike01/metrics.json layers[0]；其余为设计边界]`

原始[FBX](F:/MLBB_Test/haijiangli1_HAMSTERMLBB/Assets/Art/Prefabs/TempPrefabs/waipai/Effect/Hero/112_Chloe/skin07/Mesh/Temp_FX_Mod_112_chloe_YM_95.FBX)、[用户指定红色贴图](F:/MLBB_Test/haijiangli1_HAMSTERMLBB/Assets/Art/Prefabs/TempPrefabs/waipai/Effect/Hero/112_Chloe/skin07/Tex/Temp_FX_Tex_112_chloe_WWW_12_1.png)、[实际坐标](../../artifacts/bake_topology/reference_chloe_spike01/uv.json)、[统计](../../artifacts/bake_topology/reference_chloe_spike01/metrics.json)。`[verified: 本轮原文件读取及Blender导入结果]`
