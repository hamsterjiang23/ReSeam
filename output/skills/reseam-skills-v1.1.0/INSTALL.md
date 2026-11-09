# ReSeam Skills v1.1.0

本包包含 `reseam-uv` 主Skill与 `reseam-hard-surface` 硬表面专项，以及全部Skill内引用文档。
依赖准备入口为 `reseam-uv/references/environment-setup.md`；Agent首次使用或发现依赖缺失时先读它。

## 安装Skill

将解压后的 `reseam-uv/` 和 `reseam-hard-surface/` 两个目录一同放入Codex的Skill目录：

- 设置了CODEX_HOME时，使用 `<CODEX_HOME>/skills/`。
- 未设置时，使用当前用户的 `~/.codex/skills/`；Windows通常位于用户目录的 `.codex/skills/`。

最终目录应为 `.../skills/reseam-uv/SKILL.md` 和 `.../skills/reseam-hard-surface/SKILL.md`。
已有同名Skill时先比较内容、保留本地修改，再合并或备份替换，不要批量覆盖其它Skill。
让新会话发现这两个Skill，然后调用 `$reseam-uv`；硬表面专项也可用 `$reseam-hard-surface`。

## 给Agent的首次运行请求

> 使用 $reseam-uv 处理我的模型。先读取环境安装指引，检查本机ReSeam仓库及本次路线的依赖；
> 缺少普通本地依赖时请安装、配置并做小型冒烟，再继续模型任务。不要默认装全部研究后端。

若Agent尚不知道ReSeam源码的位置，请在请求中附上现有仓库路径或可访问的仓库地址，以及模型路径。
包内是Skill指令和引用文档；实际执行需要ReSeam源码、项目锁定依赖及选定后端。
不携带原生二进制、WSL发行版、GPU模型权重、用户资产、runtime.local.json或访问凭证。

## 安装依赖时的行为

1. 定位正确仓库，读取项目约束，按uv.lock安装Python环境。
2. 按本次路线补齐Blender/OptCuts；PartUV、FlattenAnything等只在实际选择时安装。
3. 写本机运行时配置并测试真实小网格；工具执行成功与几何/颜色质量通过分别记录。
4. 缺仓库地址、管理员权限、重启或受限权重访问时，只询问具体缺失项。

Windows是当前项目后端适配的主要执行环境；原生OptCuts等通过配置的WSL发行版调用。
Linux/macOS不能仅凭Python依赖安装成功就宣称全部后端即用，需验证调用适配。

MANIFEST.json记录包内Skill文件SHA-256，可用于核对来源和解压完整性。
本包验证了文件结构、引用和Skill格式；没有在一台全新机器上重新安装WSL/GPU研究环境。
