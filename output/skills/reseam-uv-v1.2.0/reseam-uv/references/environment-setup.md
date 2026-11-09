# 环境预检与按需安装

当机器首次使用本Skill、`reseam`找不到、配置缺失或工具因依赖失败时，Agent执行本页，
解决环境后继续原任务。Skill提供决策指引；执行器、适配器和锁定依赖来自ReSeam仓库。
不假定其它机器有作者的盘符、模型目录、WSL发行版或Python环境。`[opinion]`

## 先确定仓库与所需路径

1. 在用户给定目录或当前工作区找同时包含 `pyproject.toml`、`src/reseam/`、
   `configs/runtime.example.json` 的ReSeam checkout，读取项目AGENTS.md和实际版本。
   没有源码时优先用用户已给的仓库地址获取；地址和访问权都未知时，只询问仓库位置/地址。
   不从PyPI安装同名包来猜测本项目，不把Skill目录当作源码仓库。`[opinion]`
2. 选择本次流程，再安装相应层级。保留原UV的检查/CPU颜色路径先验证基础环境；
   默认OptCuts流程需要原生OptCuts；LSCM/ABF/minimum-stretch、FBX及轮廓排布需要Blender。
   PartUV、PartField、FlattenAnything及可微优化只在实际选用时安装。`[verified: registry.py TOOLS；baseline/runner.py run_native]`
3. 日常任务授权包含普通本地依赖准备时，由Agent执行必要的用户级/项目级安装并记录命令、
   版本、退出码；不为每个包重新问用户。触及系统管理员操作、重启、访问凭证或受限下载时，
   说明具体阻碍并只请求必要协助。保留既有环境与配置，不默认升级全局Python、驱动或后端。
   `[opinion]`

## 基础环境：uv、Python与项目依赖

先运行 `uv --version`、`git --version`。已有可用uv就复用；缺失时按
[uv官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)为当前系统安装。
Windows且winget可用时可执行 `winget install --id=astral-sh.uv -e`；安装后重新定位可执行文件，
不要因当前进程PATH未刷新就重复安装。其它平台使用官方对应方法。`[verified: uv官方Installation WinGet节]`

在确认的仓库根目录执行：

```text
uv python install 3.11
uv sync --locked --python 3.11
uv run --locked reseam tools list
uv run --locked reseam tools describe uv.reparameterize
```

项目使用Python3.11及锁定numpy/scipy/trimesh/Pillow/Shapely/xatlas依赖；不需要先装GPU研究环境。
锁文件与项目不一致时报告实际错误，先核实checkout版本，不为让安装成功自动重写uv.lock。
`[verified: pyproject.toml project/dependencies；docs/running-v1.md 环境与依赖；uv官方Installing Python与Working on projects]`

配置仅写仓库的 `configs/runtime.local.json`：没有文件时从 `runtime.example.json` 复制，
只填本次实际用到的本机路径；已有文件先读取并只更新需要的键，不能整份覆盖。
模板的示例盘符和占位值不是安装位置，也不是依赖已存在的证据。不要把本机配置、访问令牌
或下载凭证写进可分享的Skill包。`[verified: runtime.py read_runtime；configs/runtime.example.json；opinion: 配置规则]`

`uv run reseam doctor`只作补充诊断：它检查旧固定资产和多个历史后端，全部ready=false不代表
当前选定路径不可用；即使ready=true也只表示文件/哈希存在，仍要实际冒烟。
`[verified: cli.py doctor资产/依赖检查与返回note]`

## Blender：局部展开、FBX与轮廓排布

- 优先定位已安装Blender，读取实际版本。当前项目实际对照使用4.5.11；可从
  [Blender官方版本归档](https://download.blender.org/release/Blender4.5/)取得相应构建，
  放到独立应用目录，不覆盖用户已有版本。其它版本必须先测所用API，不能默认最新版本兼容。
  `[verified: baseline/README.md Blender条目；opinion: 安装规则]`
- 在runtime.local.json中设置 `blender` 为真实可执行文件绝对路径；Blender是独立应用，
  安装主环境的Python包不等于安装Blender。先运行 `blender --version`，再以后台空场景启动
  检查 `bpy.app.version_string`，随后用临时小网格测本次LSCM/ABF/minimum-stretch或concave排布。
  所有测试使用新输出目录。`[verified: reparameterize.py _blender_unwrap；registry.py TOOLS[uv.pack]；opinion: 验证顺序]`
- **不要只写runtime配置：** FBX/非三角OBJ的 `asset.import` 以及 `backend=blender` 的
  `material.bake` 还必须在各次请求JSON中显式传 `"blender": "<已检测的可执行文件绝对路径>"`。
  这些分支未读取runtime.blender，省略时会落到作者机器默认路径；区域重展开和轮廓排布可读取
  runtime配置，也可显式传同一字段。不能把配置存在当作所有工具已使用正确路径。
  `[verified: assets.py L181—L216；material.py L406—L429；geometry.py pack_uv blender_concave分支]`
- 默认用户级便携安装；必须使用系统安装器、管理员权限或重启时再说明原因并请求相应协助。
  不因缺Blender就把用户要求的轮廓排布暗换成矩形排布。`[opinion]`

## OptCuts：默认初始切缝后端

Windows当前适配通过WSL运行原生程序。先检查WSL与实际发行版；配置 `wsl_distro` 为机器上
真实名称，不硬编码Ubuntu-22.04。未装WSL且需要系统特性/重启时，说明所需操作；不能因此
悄悄把OptCuts路线改成其它方法。纯Linux/macOS不是当前WSL适配的即用合同，需要单独验证
调用适配，而不是只把配置换成Unix路径就宣称支持。`[verified: runtime.py wsl_command；baseline/runner.py optcuts分支；opinion: 支持边界]`

1. 优先查找用户已有且版本匹配的OptCuts构建，记录提交、可执行文件哈希和真实运行结果。
   没有时，只初始化本路径的 `baseline/OptCuts` 子模块，使用checkout锁定提交，不拉取最新HEAD。
   旧连续求解器另依赖 `references/OptCuts`，两者不能不核对版本就互换。`[verified: .gitmodules；baseline/README.md；docs/running-v1.md 构建连续适配器]`
2. 读取该固定源码的README和CMake要求，在独立构建目录编译；上游build.py实际执行Release
   CMake与并行编译。Agent先检测编译器、CMake及报错涉及的系统库，按实际缺项补齐；
   可用 `cmake -S <源码> -B <新构建目录> -DCMAKE_BUILD_TYPE=Release` 和
   `cmake --build <新构建目录> --parallel 2` 控制资源。旧TBB/CGAL等不兼容必须保留错误、
   提交及外置补丁，不能把改过的上游伪装成干净官方版。`[verified: baseline/OptCuts/build.py；opinion: 可追溯构建方式]`
3. 设置 `optcuts_binary` 为该可执行文件的本机可访问路径，确认WSL映射与执行权限。
   用极小网格实际跑 `uv.baseline method=optcuts`，检查输出snapshot、面角身份与UV，
   再进入正式资产。源码存在、编译成功、进程启动成功都不替代这个冒烟。
   `[verified: baseline/runner.py optcuts分支；agent_tools/baselines.py run_baseline；opinion: 验证规则]`

## 仅在选择研究/附加功能时安装

|需求|安装与核对|
|---|---|
|PartUV / PartField|读取锁定 `baseline/PartUV/README.md`，在独立WSL/GPU环境安装匹配的PyTorch、torch-scatter及PartUV依赖；推荐用uv管理该环境并将上游pip命令转为目标环境的uv pip调用。设置partuv_python、partfield_checkpoint、partfield_sha256；实际适配器要求CUDA及带seed参数的PartField接口。权重按官方来源取得并校验固定SHA，缺访问权时只请求该资源；不能跳过哈希或把研究许可当商用授权。`[verified: baseline/PartUV/README.md Installation；baseline/adapters/partuv_worker.py main；references/README.md 许可说明]`|
|FlattenAnything|当前适配复用runtime.partuv_python所指WSL环境，需CUDA、官方FAM代码及针对该解释器重编译的kNN扩展，扩展置于baseline/runtime/fam-addons。官方预编译扩展面向Python3.9，不能直接塞入项目3.11环境；检查实际适配器，不用“torch可import”冒充后端已可运行。若需要隔离版本，核对并记录runtime解释器切换，保留原配置。`[verified: baseline/FlattenAnything/README.md Configuration；baseline/adapters/flatten_anything.py main；baseline/runner.py flatten_anything分支]`|
|BC7压缩|主环境执行 `uv sync --locked --extra compression`，保留原uv.lock；再用小图实际编码、解码并验收。BC1与BC7参数以tools describe material.encode为准。`[verified: pyproject.toml compression；registry.py TOOLS[material.encode]]`|
|旧连续求解器|仅在相应研究命令需要时依docs/running-v1.md和scripts/build_native.py构建continuous_binary；普通Agent UV整理不因缺此二进制停工。`[verified: docs/running-v1.md 构建连续求解适配器；opinion: 按需规则]`|
|预算PBR/MikkTSpace|按docs/running-budget-complete-v1.md运行scripts/build_budget_mikktspace.py，在独立输出中构建锁定官方桥接依赖；不把这项作为静态BaseColor路径的前置条件。`[verified: scripts/build_budget_mikktspace.py 模块说明；opinion: 路径隔离]`|
|历史可微贴图优化|仅该路径需要时配置differentiable_python与differentiable_renderer_root，依docs/running-v1.md核对PyTorch、nvdiffrast和实际渲染入口；当前普通Agent流程不要求全部配置。`[verified: docs/running-v1.md 环境与依赖；configs/runtime.example.json；opinion: 按需规则]`|

## 安装后的冒烟与失败处理

先执行工具发现/参数查询，再用临时构造的小网格或用户资产的一个小连通片测试选定后端；
需要交付颜色时，增加低分辨率无共享烘焙、指定LOD检查和诊断导出，并重新载入GLB核对。
小网格缺材质时只判断几何后端，不伪造颜色验收通过。默认单工具300秒；资源重任务先查
内存/磁盘/GPU并使用少线程，研究GPU后端不在未检查硬件时启动。`[opinion]`

将预检/安装/冒烟记录写进当前任务的新artifacts目录：实际系统、源码提交、安装版本、
运行时相关键、命令、退出码、错误日志及下一步。区分“缺依赖”“依赖已安装但执行失败”
和“工具执行成功但质量拒绝”；不能用放宽几何/颜色规则修复环境问题。同一构建失败最多
做三次有证据的修订，随后给出具体阻碍及可行路径；切换方法须符合原任务，不能静默替换
指定后端或把未跑写成成功。`[opinion]`
