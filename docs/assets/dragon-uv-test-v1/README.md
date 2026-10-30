# 新测试龙：独立三视图生成参考

用途：用户生成一个新的龙模型，再交给ReSeam做UV复用测试。按用户要求交付三张独立图片，正面、左侧面、背面各一张；这是图像生成参考，不是从同一个已建模网格渲染的严格投影。`[verified: 用户本轮“三视图”“拆成三张独立的图”请求；生成方式记录于generation.json]`

- [正面 front.png](front.png)
- [左侧面 left-side.png](left-side.png)
- [背面 back.png](back.png)

造型来源是用户提供的[正面](../llm-uv-reference/front.webp)、[背面](../llm-uv-reference/back.webp)、[侧面](../llm-uv-reference/side.webp)截图中的灰模；随机分组色与红色接缝线只用于诊断，不作为新模型的材质图案。冰蓝色身体、浅色翼膜和腹部是本次生成设计选择，不冒充原作者实际贴图。`[verified: 所链接用户截图；配色为opinion]` `[opinion]`

图像使用内置image_gen生成；提示词和文件校验记录见[generation.json](generation.json)及其引用的prompt文件。三张图要求相同角色、尺度、张翼姿势及配色；生成图仍可能存在跨视图细节差异，模型接收时需检查真实结构。`[opinion]`

建议生成时保持双侧主体相似，保留清晰翼膜、翼骨、脚爪、头冠和尾部；不添加文字、随机伤痕或半透明发光效果。回传原始生成模型、原UV和全部实际材质贴图，有独立重拓扑低模时一并保留，并记录生成设置。随后另建资产身份/哈希、无共享烘焙基线，再比较几何复用与LLM辅助复用；历史测试输入保持冻结。`[opinion]`
