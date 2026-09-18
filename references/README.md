# 上游参考源码

用于 ReSeam 方法设计、源码阅读和后端适配参考。两个目录作为主仓库的 Git 子模块，保留独立 Git 元数据与官方 origin，由主仓库记录固定提交；此目录记录参考源码快照；ReSeam 通过独立适配器复用已有运行环境，实际构建、权重与实验状态见各轮 source.json 和 result.json。

## 来源与版本

- **OptCuts**：[官方仓库](https://github.com/liminchen/OptCuts)，本地 [README](OptCuts/README.md)，提交 `cd2302671af7954f263b0ea93d8419aa943d54be`。
- **PartUV**：[官方仓库](https://github.com/EricWang12/PartUV)，本地 [README](PartUV/README.md)，提交 `46bad07092a9396f00d7e04ea90369ded7de3d18`。

机器可读信息见 [manifest.json](manifest.json)。采用浅克隆，当前提交的受跟踪文件已全部检出，不包含完整提交历史。OptCuts 官方克隆的文件下载停滞后，使用旧实验同一提交的本地 Git 对象完成检出，未复制旧工作区修改；PartUV 直接从官方远程完成克隆。

## 阅读入口

- OptCuts：[主入口](OptCuts/src/main.cpp)、[优化器](OptCuts/src/Optimizer.cpp)、[网格表示](OptCuts/src/TriMesh.cpp)、[对称 Dirichlet 能量](OptCuts/src/Energy/SymDirichletEnergy.cpp)。重点核对候选切缝、连续更新和接受规则，作为 ReSeam 区域级重构与松弛策略的参照。
- PartUV：[Python 预处理](PartUV/partuv/preprocess.py)、[C++ 流程](PartUV/src/pipeline.cpp)、[展开与合并](PartUV/src/UnwrapMerge.cpp)、[畸变计算](PartUV/src/Distortion.cpp)、[配置说明](PartUV/doc/config.md)。重点核对分区、递归展开、合并及质量检查。

以上是后续阅读入口，不表示已经完成调用链分析或证实某个工程接口可用。

## 使用约定

- 保持上游快照干净；ReSeam 适配代码放在参考目录外。需要修改上游时建立明确的分支或补丁，并在实验配置中记录。
- 不在多轮实验之间自动拉取更新；升级必须记录新提交并新建实验轮次。每次运行以实际 HEAD 和工作区差异为准。
- 两份源码内自带的示例不替代 [V1 固定测试资产](../assets/v1/README.md)。
- OptCuts 保留 [MIT 许可证](OptCuts/LICENSE.txt)。PartUV 的 [LICENSE](PartUV/LICENSE) 区分项目 Apache 2.0 与 `preprocess_utils/partfield_official` 的 NVIDIA 非商业研究许可；复制或集成代码时按具体组件核对，不将整个目录统一视为 Apache 2.0。
- PartUV 的 `.gitmodules` 列出外部依赖，但本次未初始化或补齐构建依赖；依赖安装与实际可运行性需要后续单独验证。

获取检查：受跟踪文件存在、Git 工作区及暂存区干净、`git fsck --connectivity-only` 通过。浅克隆与额外复用的 Git 对象不影响本次源码版本标识。
