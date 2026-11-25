# ReSeam Skill包与全量安装

## 一句话交给Agent

将ZIP或解压目录提供给目标Agent，发送下面的指令。`[opinion]`

> 安装这个ReSeam Skill包，并按INSTALL.md完成全量依赖与工具安装。由你检测机器、获取锁定源码、安装配置所有可用组件、执行小型冒烟并持续修复可恢复问题。最后给我每个组件的实际状态、日志和使用方式。

包内 `install.py` 安装两项Skill。随后由执行Agent按本页和 `skills/reseam-uv/references/install-manifest.json` 完成运行环境安装。选择全量安装表示处理清单全部组件。`[opinion]`

## 安装Skill本身

在解压目录读取 `bundle-manifest.json`，核对文件哈希。已有Python时执行下列命令，安装器默认选择 `$CODEX_HOME/skills`，未设置时使用用户目录的 `.codex/skills`。已有Skill会先合并到暂存区，再备份原目录并发布，失败回滚已发布目录。`[opinion]`

```text
python -X utf8 install.py
python -X utf8 install.py --dest <目标Agent实际使用的skills目录>
```

缺Python时由Agent先按官方方法准备uv，再执行 `uv run --no-project --python 3.11 python -X utf8 install.py`。Windows可下载官方PowerShell安装脚本到临时目录，先读取再执行，随后重新定位uv。`[opinion]` [uv官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)

安装后读取目标目录中的 `reseam-uv/SKILL.md`，继续下述全量安装。Agent按实际宿主的Skill加载机制刷新或在后续请求中使用该目录。`[opinion]`

## 源码与安装顺序

1. 先读取Skill目录下的 `assets/runtime-manifest.json` 和解压包根目录的 `bundle-manifest.json`，运行工作流启动器 `--check --repo <候选checkout>`。只有执行源码文件的内容哈希匹配时复用该checkout；仅HEAD相同不证明工作树匹配。不匹配时由启动器校验Skill内的 `assets/runtime-source.zip` 并解到独立用户缓存，不覆盖已有checkout。仓库内不保存生成的运行时ZIP，开发时可在仓库使用 `uv run python -m reseam.workflows`。读取所选源码的AGENTS.md，记录提交、源码内容哈希和实际运行目录。`[opinion]`
2. 有Git元数据的checkout可用 `git submodule update --init --recursive -- <需要的子模块路径>` 获取锁定后端，核对 `baseline/sources.json`；独立源码缓存没有完整第三方源码时，按清单记录的上游仓库与固定提交单独获取，再写本机配置。先完成核心、Blender、OptCuts、MikkTSpace和压缩，再完成GPU研究后端、连续求解器与可微渲染。每项安装独立记账，已可用组件实际冒烟后复用。`[opinion]`
3. 读取 [环境预检与安装细节](environment-setup.md) 补齐本机配置。ZIP根目录的INSTALL.md链接到本页，相邻reference从包内或已安装的 `reseam-uv/references/` 读取。`[opinion]`

需要完整Git checkout时可使用下面的结构，目录和提交取实际值；随后仍以运行时清单核对执行源码内容。运行时快照只分发当前执行源码、适配器、锁文件与配置模板，不含模型、历史实验归档、后端二进制或权重。仓库访问失败时记录具体认证需求，并继续独立可安装的本机组件。`[opinion]`

```text
git clone --filter=blob:none --no-checkout https://github.com/hamsterjiang23/ReSeam.git <新工作目录>
git -C <新工作目录> checkout --detach <bundle-manifest.json的source_revision>
uv python install 3.11
uv sync --locked --python 3.11 --all-extras
```

## 全量组件执行要点

|组件|Agent执行与完成依据|
|---|---|
|核心CLI与所有内置工具|安装Git、uv、Python3.11和锁定依赖及全部extras。运行 `reseam tools list`，逐项查询 `tools describe`，保存当次实际工具目录。使用构造小网格测试导入、检查、CPU颜色烘焙和诊断导出。`[opinion]`|
|Blender|优先检测现有版本并验证所用API，必要时从官方归档下载4.5.11便携构建并核对官方校验文件。设置 `blender`，用后台小场景验证展开、轮廓排布与实际GLB渲染。FBX导入和Blender烘焙请求需显式传本机blender路径，详见环境reference。`[opinion]`|
|OptCuts|Windows检测实际WSL发行版，在独立构建目录编译锁定 `baseline/OptCuts`。按实际CMake错误补编译器和系统库，保存必要外置补丁。设置 `wsl_distro`、`optcuts_binary`，用小连通网格跑 `uv.baseline method=optcuts` 并读取输出UV。`[opinion]`|
|MikkTSpace与PBR|准备当前宿主可用的GNU兼容编译器，运行 `uv run --locked python scripts/build_budget_mikktspace.py`，保留官方源码哈希和符号载入结果。用小型PBR材质运行预检、烘焙与实际法线渲染。`[opinion]`|
|BC1／BC7|`uv sync --locked --all-extras`包含compression依赖，使用小贴图实际编码及解码，记录通道、尺寸与结果。`[opinion]`|
|PartUV／PartField|初始化锁定PartUV，按README在独立WSL Python3.11环境准备PyTorch、torch-scatter及PartUV/PartField，下载公开checkpoint并核对清单SHA-256。确认项目适配器要求的带seed接口，运行真实小型推理及展开。设置partuv相关三个路径/哈希键。`[opinion]`|
|FlattenAnything|按锁定README准备FAM依赖。当前适配器复用 `partuv_python`，为同一实际解释器重编译kNN并放入 `baseline/runtime/fam-addons`。核实与PartUV环境兼容后用64点、10步跑适配器，保留训练及UV产物。`[opinion]`|
|历史连续求解器|核对 `references/OptCuts` 与prepared build的源码提交和对象文件，运行 `scripts/build_native.py --optcuts-build ... --optcuts-source ... --distro ...`，设置 `continuous_binary` 并测试小网格入口。`[opinion]`|
|可微贴图优化|从清单renderer_source的仓库获取固定commit，核对 `diffRender2/unlit_mesh_render.py` SHA-256，设置renderer_root为该checkout的diffRender2目录。准备兼容的PyTorch、PyTorch3D与nvdiffrast，设置两个differentiable配置键并通过现有worker运行小型渲染/优化冒烟。`[opinion]`|
|制作图解与报告|当前 `report` 使用锁定reports extra，运行 `uv sync --locked --extra reports`；字体与历史证据按目标机器定位，用一页图解或小图实际验证字体、渲染和输出。历史PDF脚本若额外导入reportlab，按所选脚本另行准备并记录，不把历史脚本依赖混作当前report必需项。`[opinion]`|
|四项制作工作流|检查 [辅助工具清单](../scripts/tools.json) 与 [调用说明](workflows.md)。用非恐龙双部件小网格验证 `rebuild`、`preview prepare/bake`、`collect`，再用含成功／拒绝／缺图的记录验证 `report`。512小样及实际回读通过才记录相应入口可用。`[opinion]`|

PartUV锁定README列出Python3.11、PyTorch2.7.1/cu128和torch-scatter组合。FAM上游列出Python3.9预编译kNN，项目适配器加载当前解释器重编译的 `.so` 并使用CUDA。安装Agent应结合所选checkout和实际硬件核对版本，使用uv管理对应环境。`[verified: baseline/PartUV/README.md L55–L74；baseline/FlattenAnything/README.md Configuration；baseline/adapters/flatten_anything.py L25–L29、L49–L65]`

可微渲染器的获取地址、提交与模块哈希列于清单renderer_source，需要目标机器具备该内部Git仓库访问权限。模块顶层导入PyTorch3D，nvdiffrast后端也使用其相机变换。清单中的observed_package_versions记录打包机器已安装版本，目标机器通过实际冒烟确认兼容性。`[verified: renderer_source commit a620ad647468b2b75b6be6184e7b40b9a68eb0c0 的 diffRender2/unlit_mesh_render.py L3–L17、L239–L249；本次环境包元数据输出 torch=2.11.0+cu128、pytorch3d=0.7.9、nvdiffrast=0.4.0]`

Blender使用官方发布包，OptCuts构建以锁定源码README/CMake为依据。第三方参考插件的集成状态按checkout文档逐项确认，使用现有ReSeam工具所需的真实依赖集合。`[opinion]` [Blender官方版本归档](https://download.blender.org/release/Blender4.5/) [OptCuts官方源码](https://github.com/liminchen/OptCuts)

## 配置、资源与恢复

合并 `configs/runtime.local.json` 的实际本机值，保存已有配置副本。Windows与WSL路径分别验证，Blender便携目录、WSL发行版和Python环境根据机器选择。配置模板中的盘符只作格式示例。使用Python输出中文时显式启用UTF-8。`[opinion]`

安装请求包含普通用户级依赖准备、源码获取、独立环境、构建和临时冒烟。Agent主动完成这些步骤。实际需要管理员操作、WSL启用/重启、仓库或权重访问时，说明缺少的动作并记录该项状态，继续独立可完成的组件。CUDA硬件或renderer来源缺失时记录实际阻碍与已有进度。`[opinion]`

构建和GPU冒烟前检查内存、磁盘、GPU与当前负载。先小型冒烟，编译从少线程开始。同一构建问题按日志修复，复用已成功步骤并保留失败记录。上游补丁写独立构建副本，记录提交与差异。`[opinion]`

## 安装报告与验收

在新建 `artifacts/setup/<本次唯一目录>/` 写入安装报告和命令日志，逐项记录清单component ID、版本/提交、路径、安装命令、退出码、冒烟输入/输出及状态。状态使用 `READY`、`BLOCKED_HARDWARE`、`BLOCKED_ACCESS`、`NEEDS_SYSTEM_ACTION`、`FAILED`、`NOT_RUN`。所有组件实际冒烟READY后报告全量完成，存在剩余项时报告PARTIAL并给出具体原因和恢复入口。`[opinion]`

`reseam doctor`提供存在性补充诊断。最终可用状态依据各组件实际执行结果和当次工具目录，几何/材质质量状态随制作任务单独验收。安装报告注明目标机器及实际测试范围。`[opinion]`

包内四项工作流的安装检查还要核对 `.py`、请求JSON、案例PNG和源码快照均已安装且哈希一致。安装Skill、源码准备、Python依赖可导入、后端小样可执行分别记录；未执行的研究后端不得因核心工作流可用而标为READY。`[opinion]`
