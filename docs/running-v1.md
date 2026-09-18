# V1 运行与复现

## 环境与依赖

主程序使用 Python 3.11、uv.lock 和 `uv sync`。原生连续求解与 OptCuts／PartUV 在 WSL Ubuntu-22.04 运行，Blender 与可微渲染在 Windows 运行。所有路径通过 `configs/runtime.local.json` 配置，该文件不提交；模板为 [runtime.example.json](../configs/runtime.example.json)。

已安装的后端环境可以复用，但不得修改旧项目代码或将其模型输出冒充本仓库新运行。`reseam doctor` 只检查存在性与资产哈希，运行成功须检查实际 result.json。

构建连续求解适配器需要一个已经成功编译的、与 references/OptCuts 同提交的 OptCuts 构建目录：

```powershell
uv run python scripts/build_native.py --optcuts-build <构建目录> --optcuts-source <对应源码目录>
```

脚本核对上游提交和各编译单元源码，编译独立 JSON 入口并复用上游对象与依赖，输出 `artifacts/runtime/reseam-continuous` 和 build.json。它不是从零安装 OptCuts 全部依赖的脚本。连续入口禁用内部拓扑更新；Python 对所有候选作完整合法性检查。

PartUV 需要本地 PartField 权重，运行时校验 SHA-256 并禁用在线下载。可微环境需要 PyTorch、nvdiffrast、trimesh 和 Pillow，以及配置中指定的已有 `unlit_mesh_render.py`；主环境负责生成扩边索引，GPU 环境不要求安装 SciPy。

## 命令与产物

```powershell
uv run reseam doctor
uv run reseam unwrap --model energy_blade --method optcuts --output artifacts/manual/baseline --seconds 180
uv run reseam unwrap --model energy_blade --method reseam_adaptive --output artifacts/manual/reseam --seconds 180
uv run python scripts/evaluate_run.py artifacts/manual/reseam --seconds 600
uv run reseam benchmark --config configs/v1_round01.json
uv run reseam report --round artifacts/v1/v1_round01
```

`unwrap` 生成原生 OBJ／GLB、完整 state.json、UV 图、配置、逐候选日志和 result.json；输出已存在时拒绝覆盖。`--annotations` 接受标注文件，确认后的标注必须匹配输入网格身份。`--island-weight 0.01` 开启低碎片轨道；正常几何轨道为 0。

单次上限默认 180 秒，包含重新运行初始化。多起点 OptCuts 在同一总预算内使用三个明确不同的输入面起点；普通 OptCuts、xatlas 和 Smart UV 的重复不声称由种子驱动。PartUV 显式传递 PartField 采样种子。

`benchmark` 固定两模型 × 11 个方法配置 × 3 个种子，共 66 个几何运行。昂贵下游预先选择种子 17 的 22 个组合；仅超过畸变阈值但映射合法的输出也可烘焙作诊断，不能计作合格展开。运行期间冻结源码；不同版本新建 round_id。

几何阶段最多 4 小时，artist 软偏好与下游阶段最多 2 小时，总上限 8 小时，预留环境检查与汇总时间。完整矩阵保存在 ledger.json；失败、未运行、候选池未满和待人工验收分别标识。

## 几何与支持域验收

原始模型几何固定，UV 顶点身份独立于坐标。所有方法使用相同 SD 定义、平均阈值 0.025、非翻转和连续三角形相交检查。支持合法带孔平面岛；新的调和初始化只支持单边界圆盘，不能初始化时明确拒绝，不宣称数学不可行。

每个复合提案均从原始三维邻接构造，允许删旧缝和加新缝。支持域为 1 环、3 环、整个受影响岛；固定值来自同一候选起点。原生进程保留迭代检查点，Python 从中选择最好合法结果，求解失败不得覆盖已有合法存档。

完整搜索的 small／large／adaptive 轨迹会分叉。独立的 support_audit 才用于共同候选、共同起点的范围消融；报告救回数量和分母，不将整场搜索的差异直接归因于支持域。

## Artist 标注与盲评

实验目录的 `annotations/<model>/annotation.blend` 包含稳定边／面 ID 和自动建议。打开后在 Text Editor 运行内嵌的 `reseam_annotation_addon.py`，3D 视图侧栏出现 ReSeam 面板。

编辑模式选择边后标记必须切、禁止切、偏好切或清除；选择面后标记保护区域。填写实际确认者，导出到新的 JSON 文件。只有该显式确认会产生硬约束；测试目录中 `AUTOMATED_TEST_NOT_HUMAN` 的模拟导出不属于真实标注。

报告的 `blind/index.html` 使用匿名样例，不含方法名称或方法路径。可下载匿名贴图低模，记录限时修整和评分，导出评审 JSON；样例映射仅存于评审包之外的 blind_key.private.json。未实际完成评审时，artist_assessment 保持 PENDING_HUMAN，V1 不标完成。

## 下游质量与结果解释

统一 Blender packing 请求 FRACTION=9/1024，以连续几何测量确认岛间至少 16 像素；图集 1024²、烘焙扩边 8 像素。保留原生 UV 的质量指标，打包缩放不混入原生 SD 评价。

Cycles Selected to Active 烘焙真实 Base Color 与切线空间 Normal；法线烘焙前恢复高模原始材质连接。重新导入烘焙 GLB 后核对几何、逐角 UV 与接缝身份。每面四个投射探针记录命中率和最近表面距离，这只是抽样诊断，不等于每个烘焙 texel 的射线都已验证。

独立 Blender 视角分别评价全前景、接缝带及探针通过区域。可微优化固定 128 步，训练六视角、验证两视角、测试六视角互不重用；按验证集选择检查点，加入初始贴图锚定与岛内 TV。重新扩边并量化后再次验证，退化时保留初始化。初始与优化后贴图都检查 mip。

报告同时保留失败、代价、全局与局部指标，不自动给出综合冠军。两个模型为反复使用的开发集，所有结果仅限这些资产和设置，不能推断独立模型泛化或已达到艺术家交付标准。
