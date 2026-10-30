# Agent UV 工具集 v1

颜色容忍度由 `reuse.check_color` 设置低／中／高球半径（默认中），检查原始颜色到冻结共享目标的Oklab距离；支持自定义半径与球外区域定位。调用前已有 `material.target`，具体参数见 `tools describe`，解释和真实三档结果见[颜色球](dragon-component-reuse.md#颜色球容忍度)。这是源外观变化检查，与烘焙重建验收分别报告。`[verified: color_tolerance.py check_color；opinion: 报告方式]`

部件切缝、面角锚点和 `paired_domain` 的参数与流程见[部件重展开](dragon-component-reuse.md)。真实龙的部件范围为双翼、四肢和角刺；每组记录 OptCuts 实际切口和共同域回退，图块减少与外观结论分别报告。`[verified: 用户接受的部件计划；opinion: 报告方式]`

当前新增的[整龙 LLM 复用执行合同](dragon-llm-reuse-plan.md)正在实施：四条 Baseline、逐步修正证据、可控颜色对称化及独立 `basecolor_reuse` 验收范围。下文接口登记、单项测试和历史结果均不能代替本轮整龙完成判定。`[verified: 用户接受的整龙计划；dragon-llm-reuse-plan.md 完成条件]`

统一入口为 `uv run reseam tools list`、`tools describe <name>` 与 `tools run <name> --request request.json --output <新目录>`；旧实验命令保留。请求与返回为 JSON，大数组保存在快照/贴图文件中。`[verified: src/reseam/cli.py tools 分支；agent_tools/runner.py run_tool]`

```powershell
uv run reseam tools list
uv run reseam tools describe asset.import
uv run reseam tools run asset.import --request import.json --output artifacts/my_asset/import
```

导入无 UV 低模的请求示例，文件引用用绝对路径：`[verified: agent_tools/assets.py import_asset]`

```json
{
  "path": "E:/ReSem/assets/v1/energy_blade/low.obj",
  "reference": "E:/ReSem/assets/v1/energy_blade/high.glb",
  "initialize_uv": "xatlas",
  "repair_degenerate_faces": true,
  "timeout_seconds": 300
}
```

后续请求引用返回的 `snapshot`，可附 `state_hash` 防止选错父状态。使用 [reseam-uv Skill](../skills/reseam-uv/SKILL.md) 选择工具和处理拒绝；参数详情以当前 `tools describe` 为准。`[verified: agent_tools/state.py require_snapshot；skills/reseam-uv/SKILL.md 执行路线]`

|工具|职责|
|---|---|
|asset.import|冻结输入与哈希，保留面/面角身份；缺失 UV 显式初始化。`[verified: assets.py import_asset]`|
|uv.inspect / view.render|编号岛、邻接、密度、真实三角覆盖/交叠；z-buffer 四视图、真实源颜色或当前贴图、棋盘格、组色、接缝和错误面。`pick_pixels` 返回原面 ID／chart ID／重心坐标，`selected_faces` 或 `region_id` 查看局部；预览不是质量渲染。`[verified: geometry.py inspect_uv；views.py render_views/_texture]`|
|region.define|从原面 ID 或 chart ID 定义可跨旧岛的语义区域，保存保护面、角色、理由和证据；不修改几何与 UV。`[verified: regions.py define_region]`|
|uv.reparameterize|新增区域展开入口，显式选 harmonic／OptCuts 后端；面角映射、后端冒烟和真实结果需单独核验，不以入口登记当算法完成。`[verified: registry.py TOOLS[uv.reparameterize]；实施状态为opinion]` `[opinion]`|
|reuse.find_candidates|按岛的面积与镜像质心筛选 X/Y/Z 反射候选；只是候选，不授权交叠。`[verified: geometry.py find_candidates]`|
|reuse.check_pair|精确镜像复制、不同三角划分曲面转移、可选成对求解；全交叠单元检查后输出绑定父哈希的提案。`[verified: geometry.py check_pair]`|
|reuse.apply / uv.pack|共享、整面独占、解除共享；统一排布并验证。合法但质量尚不合格的中间状态可保留。`[verified: geometry.py apply_reuse / pack_uv]`|
|material.target|新增源信号目标冻结入口，供已授权颜色对称化及同目标无共享对照；不能从待验收贴图生成目标。接口以 describe 为准，结果需绑定来源及对应证据。`[verified: registry.py TOOLS[material.target]；后半为执行约束]` `[opinion]`|
|material.bake|标准 PBR 路径经 CPU 或隔离 Blender 烘焙实际 BaseColor／Normal／ORM；新颜色复用路径只声明 BaseColor 范围，不能借模式切换补造其他通道。具体参数与支持范围以 describe 和返回记录为准。`[verified: material.py bake_material_tool / _blender_atlas；registry.py TOOLS[material.bake]；后半为执行约束]` `[opinion]`|
|quality.validate|几何、逐面/局部尾部、实际 texel、指定 mip、固定视角 CPU 渲染；独立于烘焙成功。`[verified: material.py validate_quality]`|
|asset.export|accepted 或 diagnostic；GLB、真实贴图、快照、来源哈希、报告与可选同协议基线对照。`[verified: material.py export_asset]`|

## 状态与失败

每个修改写入新目录；原始几何与面集合不得变化。快照、源文件、已烘焙图像均验证 SHA256；UV 改动会清除旧贴图/证据，过期提案与旧验收拒绝。`[verified: state.py save_snapshot/load_snapshot；geometry.py apply_reuse；material.py _load_bake/export_asset]`

`status` 是操作状态，`execution_success` 是是否完成检查/执行，`quality_accepted` 是最终验收；完成一次质量拒绝可以是 `execution_success=true`。`TIMEOUT` 保留日志，不覆盖父快照；新调用使用新目录。`[verified: worker.py execute；runner.py run_tool；material.py validate_quality]`

新增颜色复用路径的通过必须连同验收范围读取：UV＋BaseColor 通过不等于完整 PBR 通过。原 `basecolor_diagnostic` 继续只作诊断，不因为新增路径而升级历史结果。`[opinion]`

每个 CLI 操作默认 300 秒，独立子进程且数值线程设为 1；Windows 超时只终止本次子进程树。Blender 使用 `--background --factory-startup --threads 1`，不操作用户已有窗口。`[verified: runner.py run_tool；assets.py import_asset；bake_variant_bake.py subprocess 参数]`

## 输入、材料与验收边界

- 几何输入为静态三角 GLB、三角 OBJ、经隔离 Blender 导入的 FBX。GLB 动画、骨骼、morph、非三角 primitive、混合有/无 UV 对象会明确拒绝。OBJ 完整材质由显式参考 GLB 提供。`[verified: assets.py _glb_arrays/import_asset]`
- FBX 记录原对象/polygon/loop 到规范三角面的祖先关系及实际使用的 UV 层；Blender 不暴露原始 FBX UV 索引，因此保守保留 polygon 边界，不能声称恢复全部原始 UV 岛连接。额外 UV 层保存在原始冻结文件，不作为本轮共享与材质验收通道。`[verified: scripts/agent_asset_worker.py main；assets.py identity_policy]`
- 极小三角形被 xatlas 返回全零 UV 时，`repair_degenerate_faces=true` 才会隔离这些原面、生成内禀等距三角 UV；随后必须排布，原面不删改。`[verified: assets.py uv_repair / repaired]`
- 完整材质目前限单实例、单 primitive、单材质、单 UV 的标准不透明 PBR；保留源法线/切线、材质因子和采样环绕模式。非法/不支持的扩展、发光、顶点色等明确拒绝。缺贴图但有合法 glTF 常量时使用定义中的常量，不伪造高模纹理。`[verified: material.py _reference / _wraps]`
- 目标导出使用显式逐面法线与 glTF TBN，源法线保持；Blender 正反镜像切线转换经过构造测试，输入自定义切线不能在 Blender 保持时拒绝。`[verified: material.py _blender_atlas；tests/test_bake_variant_bake.py 镜像测试]`
- 完整 mip **交付**与指定 LOD **验收**分别记录，默认验证 `[0,1]`。请求全 LOD 可能暴露小岛消失、padding 或多岛混色问题；不能把通过较少 LOD 写成全部通过。`[verified: material.py validate_quality mip_levels；Skill 正向测试 acceptance-summary.json]`
- 标准 PBR 路径的实际输出编码为 RGBA8，payload 统计 BaseColor／Normal／ORM 三张图及全部 mip；颜色路径只统计实际颜色图，PNG 包体另计。未执行 BC/ASTC、GPU 或人工验收的结果不得宣称相应质量或收益。`[verified: material.py _save_bake / export_asset；budget_material.py MaterialAtlas.payload_bytes；颜色范围与执行约束为opinion]` `[opinion]`

## 上游参考与许可

固定提交和许可文件哈希见 [references/manifest.json](../references/manifest.json)。工具层复用 ReSeam 的对应、共同域、独占、合法性与材质内核；未把以下 Blender 插件安装为默认后端，也未复制其实现源码。`[verified: agent_tools/geometry.py imports；agent_tools/material.py imports；references/manifest.json repositories]`

|参考|固定提交|本轮用途|
|---|---|---|
|[UniV](https://github.com/Oxicid/UniV)|8790a8ae99f1df0ab34e521780ffb7329430101b|Stack 机制参考；GPL-3.0，源码保留上游目录。`[verified: references/manifest.json UniV；references/UniV/LICENSE]`|
|[Magic UV](https://github.com/nutti/Magic-UV)|cc0635a7fc3ada7cf3d9f1a19cf8fd0b01c1af60|拓扑/堆叠分组参考；GPL-2.0-or-later。`[verified: references/manifest.json Magic-UV；references/Magic-UV/LICENSE “either version 2 ... any later version”]`|
|[UVgami](https://github.com/DanielBoxer/UVgami)|0cb3826b3d831688d9791633350b097ce3f2064b|重复部件流程参考；GPL-3.0。`[verified: references/manifest.json UVgami；references/UVgami/LICENSE；git remote get-url origin 输出]`|
|[xatlas](https://github.com/jpcy/xatlas)|f700c7790aaa030e794b52ba7791a05c085faf0c|源码参考；运行使用锁定的 Python xatlas 0.0.11，不能混同参考提交与 wheel 内部版本；MIT。`[verified: references/manifest.json xatlas；pyproject.toml xatlas==0.0.11；references/xatlas/LICENSE]`|

## 对照运行

整龙新轮必须独立运行原 UV 必要修复无共享、OptCuts 区域修复无共享、纯几何复用、Agent 迭代复用。共同父资产、预处理、候选池和 UV 起点用于受控机制比较；允许改变区域／外观目标的端到端结果单列，并补齐同一冻结对称化目标的无共享对照。单次或最好候选不能替代完整矩阵。`[opinion]`

每个关键步骤记录父子快照与哈希、同视角图、相同口径指标及变化量、Agent 理由、工具输入、耗时、失败面和接受／拒绝。消融固定父状态，只改变研究变量；源码修正影响路径或验收时新建冻结轮次并重跑，不与旧代码 Baseline 混比。最终必须附实际贴图、模型，并分别报告连通岛、独立图块、共享组、共享面积、密度、质量、实际 mip 字节、包体及失败成本。`[opinion]`

本轮允许 OptCuts 作为显式实验后端；这条授权仅适用于整龙工具工作流，不修改历史自研求解器的初始化和后端范围。OptCuts、其他后端和共享机制的贡献须通过对照分开。新龙颜色协议要求 LOD `[0,1,2]`；下面旧 PBR 工具验证的默认 `[0,1]` 不追溯改写。`[verified: 用户接受的整龙计划“几何与工具实现”“颜色验收”；其余为执行约束]` `[opinion]`

新龙 [tripo_p2 实验](results/agent-tools-tripo-p2.md)补充了 FBX 侧车贴图冻结、惰性图片加载检查及尖角法线对应修正。完整 PBR 不支持源扩展时，可显式使用 `material.bake mode=basecolor_diagnostic` 输出真实颜色与专用预览；该模式始终 `quality_accepted=false`，不能进入 accepted 导出。`[verified: assets.py FBX sidecar；agent_asset_worker.py image_sources；geometry.py _surface_constraints；color.py bake_color_diagnostic]`

先保存 Agent 选择的具体面 ID、理由与来源；`scripts/compare_agent_tools.py` 只执行这些外部提案，不模拟 LLM 做选择。真实两模型的三路径、128/256 有限预算及全部失败见 [本轮报告](../artifacts/agent_tools_v1/round01/report.md)。这份工具验证不替代完整求解器/历史正式 baseline 目标。`[verified: scripts/compare_agent_tools.py protocol / attempts；goal/budget-v1-implementation.md 完成条件]`

```powershell
uv run python scripts/compare_agent_tools.py --output artifacts/agent_tools_v1/another_round --agent-proposals artifacts/agent_tools_v1/agent_proposals.json
```
