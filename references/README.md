# 上游参考源码

后续采用 [独立求解器路线](../goal/solver-redesign.md)：OptCuts用于高层思路研究、对照及历史复现，不作为新求解器后端或默认初始化；保留源码快照以便核查原方法。`[verified: 用户本轮方向更新]`

论文、算法及工程文档统一列于 [参考文献与实现索引](literature.md)，包括原研究报告 R1–R8 和 V2 新增参考；源码快照信息仍以本页为准。

用于 ReSeam 方法设计、源码阅读和后端适配参考。当前10个目录作为主仓库的 Git 子模块，保留独立 Git 元数据与官方 origin，由主仓库记录固定提交。2026-10-29新增下表8个仓库，原OptCuts与PartUV提交保持不变；本次完成源码获取与完整性检查，未安装插件、依赖或运行新后端。实际构建、权重与实验状态见各轮 source.json 和 result.json。`[verified: manifest.json repositories 与 source_acquisition_verified_at_utc；本轮10项获取核验输出]`

## 来源与版本

- **OptCuts**：[官方仓库](https://github.com/liminchen/OptCuts)，本地 [README](OptCuts/README.md)，提交 `cd2302671af7954f263b0ea93d8419aa943d54be`。
- **PartUV**：[官方仓库](https://github.com/EricWang12/PartUV)，本地 [README](PartUV/README.md)，提交 `46bad07092a9396f00d7e04ea90369ded7de3d18`。

本次新增的源码参考如下；作用与功能边界见[主README参考表](../README.md#已核对的工程参考)。`[verified: manifest.json new_reference_names]`

|仓库|本地入口|固定提交|
|---|---|---|
|[Oxicid/UniV](https://github.com/Oxicid/UniV)|[UniV/README.md](UniV/README.md)|`8790a8ae99f1df0ab34e521780ffb7329430101b` `[verified: manifest.json repositories[name=UniV].commit]`|
|[nutti/Magic-UV](https://github.com/nutti/Magic-UV)|[Magic-UV/README.md](Magic-UV/README.md)|`cc0635a7fc3ada7cf3d9f1a19cf8fd0b01c1af60` `[verified: manifest.json repositories[name=Magic-UV].commit]`|
|[pedcorr/uv-toolkit](https://github.com/pedcorr/uv-toolkit)|[uv-toolkit/README.md](uv-toolkit/README.md)|`3eba2c837f1c0e318b6ff1c311d0164d51fb595a` `[verified: manifest.json repositories[name=uv-toolkit].commit]`|
|[theanine3D/uv_mechanic](https://github.com/theanine3D/uv_mechanic)|[uv_mechanic/README.md](uv_mechanic/README.md)|`046b465e533463ae4293665c2caefb4b61eafd73` `[verified: manifest.json repositories[name=uv_mechanic].commit]`|
|[DanielBoxer/UVgami](https://github.com/DanielBoxer/UVgami)|[UVgami/README.md](UVgami/README.md)|`0cb3826b3d831688d9791633350b097ce3f2064b` `[verified: manifest.json repositories[name=UVgami].commit]`|
|[dcc-mcp/dcc-mcp-blender](https://github.com/dcc-mcp/dcc-mcp-blender)|[dcc-mcp-blender/README.md](dcc-mcp-blender/README.md)|`8d8027fe4377b4d399d545869fc11b80e04ff785` `[verified: manifest.json repositories[name=dcc-mcp-blender].commit]`|
|[yi00it/blender-asset-mcp](https://github.com/yi00it/blender-asset-mcp)|[blender-asset-mcp/README.md](blender-asset-mcp/README.md)|`0fb15af9243c529c8d67e3f88fee67772af78a8d` `[verified: manifest.json repositories[name=blender-asset-mcp].commit]`|
|[jpcy/xatlas](https://github.com/jpcy/xatlas)|[xatlas/README.md](xatlas/README.md)|`f700c7790aaa030e794b52ba7791a05c085faf0c` `[verified: manifest.json repositories[name=xatlas].commit]`|

机器可读信息见 [manifest.json](manifest.json)。新增仓库采用depth=1、blob过滤请求的浅克隆，当前提交受跟踪文件全部检出，无未下载的Git LFS指针；未递归初始化额外依赖。完整历史不在本次获取范围。原OptCuts的获取沿用同提交本地对象补齐方式，PartUV沿用原官方克隆记录，不改写历史获取过程。`[verified: manifest.json acquisition/tracked_files/missing_tracked_files/git_lfs_pointer_files；本轮verification.json全部10项检查]`

## 阅读入口

- OptCuts：[主入口](OptCuts/src/main.cpp)、[优化器](OptCuts/src/Optimizer.cpp)、[网格表示](OptCuts/src/TriMesh.cpp)、[对称 Dirichlet 能量](OptCuts/src/Energy/SymDirichletEnergy.cpp)。重点核对候选切缝、连续更新和接受规则，作为 ReSeam 区域级重构与松弛策略的参照。
- PartUV：[Python 预处理](PartUV/partuv/preprocess.py)、[C++ 流程](PartUV/src/pipeline.cpp)、[展开与合并](PartUV/src/UnwrapMerge.cpp)、[畸变计算](PartUV/src/Distortion.cpp)、[配置说明](PartUV/doc/config.md)。重点核对分区、递归展开、合并及质量检查。

以上是后续阅读入口，不表示已经完成调用链分析或证实某个工程接口可用。

## 使用约定

- 保持上游快照干净；ReSeam 适配代码放在参考目录外。需要修改上游时建立明确的分支或补丁，并在实验配置中记录。
- 不在多轮实验之间自动拉取更新；升级必须记录新提交并新建实验轮次。每次运行以实际 HEAD 和工作区差异为准。
- 两份源码内自带的示例不替代 [V1 固定测试资产](../assets/v1/README.md)。
- 新增源码只作为参考或显式基线；安装、构建、实际API兼容与烘焙/复用有效性仍需单独冒烟，不能由clone成功推出。`[opinion]`
- OptCuts 保留 [MIT 许可证](OptCuts/LICENSE.txt)。PartUV 的 [LICENSE](PartUV/LICENSE) 区分项目 Apache 2.0 与 `preprocess_utils/partfield_official` 的 NVIDIA 非商业研究许可；复制或集成代码时按具体组件核对，不将整个目录统一视为 Apache 2.0。
- 新增仓库许可文件及SHA-256记录在manifest的license_files中；uv-toolkit没有独立LICENSE文件，其[扩展清单](uv-toolkit/blender_manifest.toml)声明`SPDX:GPL-3.0-or-later`。xatlas随附第三方代码的许可另有文件，不用根LICENSE代替整个依赖树的核对。`[verified: manifest.json repositories[*].license_files；uv-toolkit/blender_manifest.toml license；xatlas/source/thirdparty许可文件清单]`
- PartUV 的 `.gitmodules` 列出外部依赖，但本次未初始化或补齐构建依赖；依赖安装与实际可运行性需要后续单独验证。

获取检查：全部10项origin符合记录、受跟踪文件存在、各子仓库工作区及暂存区干净、`git fsck --connectivity-only`通过。核验原始记录路径见manifest的verification_evidence。主仓库本次登记8个新gitlink与.gitmodules，未提交；文档/manifest修改与其他已有工作分开保留。`[verified: 本轮verification.json与git submodule status输出；主仓库登记命令输出]`
