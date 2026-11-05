# MeshQuery 全基线源码与运行入口

本目录统一保存官方上游源码、项目适配器和实验入口；原有 references/ 保留。固定版本与许可见 sources.json。`[verified: 本轮用户源码归档要求；opinion: 组织方式]`

|目录|来源与作用|
|---|---|
|FlattenAnything/|官方 keeganhk/FlattenAnything；直接调用官方模型与train_fam训练循环。`[verified: 上游README与adapters/flatten_anything.py]`|
|OptCuts/|官方 liminchen/OptCuts固定源码；实际调用已配置的编译二进制并记录哈希。`[verified: runner.py optcuts分支]`|
|xatlas/、xatlas-python/|官方算法及Python绑定；运行包版本与源码提交分别记录。`[verified: 本轮官方源码克隆；runner.py xatlas分支]`|
|Blender/|官方v4.5.11源码；Smart UV及展开/排布使用本机Blender可执行文件。LFS笔刷资源保留指针，非构建资源完整包。`[verified: 本轮git版本及LFS获取记录]`|
|PartUV/|官方EricWang12/PartUV固定源码；调用已安装0.1.2研究环境及PartField权重。`[verified: 本轮PartUV backend.json；adapters/partuv_worker.py]`|
|MeshQuery/|本轮未找到官方代码；目录明确区分论文机制的项目实现与作者代码，并保存项目源码快照。`[verified: MeshQuery/README.md；opinion: 实现定位]`|
|adapters/、runner.py、run_dragon.py|ReSeam新增IO/调用适配及实验脚本，不修改上游算法源码。`[verified: 对应文件]`|

## 运行

官方源码以固定提交的Git子模块提交；获取核心源码时使用下面的明确路径。Blender二进制笔刷LFS资源与各平台预编译构建库不属于本次运行必需的源码，按需另行获取。`[verified: .gitmodules；sources.json Blender记录；opinion: 获取顺序]`

```powershell
$env:GIT_LFS_SKIP_SMUDGE = '1'
git submodule update --init baseline/FlattenAnything baseline/OptCuts baseline/xatlas baseline/xatlas-python baseline/PartUV baseline/Blender
git -C baseline/xatlas-python submodule update --init --recursive
```

```powershell
uv run reseam tools describe uv.baseline
uv run python baseline/run_dragon.py init --output artifacts/dragon_paper_baselines/round02
uv run python baseline/run_dragon.py smoke --method optcuts --output artifacts/dragon_paper_baselines/round02
uv run python baseline/run_dragon.py algorithm --method optcuts --output artifacts/dragon_paper_baselines/round02
uv run python baseline/run_dragon.py meshquery --output artifacts/dragon_paper_baselines/round02
uv run python baseline/run_dragon.py finish --method optcuts --output artifacts/dragon_paper_baselines/round02
```

每次使用新目录。原生算法参数按MeshQuery附录A.4；后续2048贴图、8px生产留边另列，与论文.001排布间距有区别。`[verified: run_dragon.py protocol与runner.py设置；论文附录A.4]`

复用率必须拆开报告：表面参与有效共享比例、去除有效重复占位的比例、复用倍数。未经对应与外观确认的交叠不计有效共享；零面积、翻转、自交、超时与依赖失败均保留，不自动拆小岛修复。`[verified: 用户要求对比UV复用率；opinion: 统计口径]`

上游目录为独立Git工作树，按 sources.json 固定。当前未创建新的远程仓库或推送；若后续将其并入主仓库，需要保留其上游身份与许可。`[verified: 本轮Git克隆记录；opinion: 后续交付方式]`
