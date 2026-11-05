# MeshQuery：项目实现与官方源码状态

论文：MeshQuery: Agentic Seam Planning for UV Parametrization，arXiv:2610.02507v1。`[verified: 论文摘要页]`

2026-11-05检索作者主页及GitHub，未发现可核实的官方实现链接；作者主页该条目仅链接paper。本目录不声称包含作者源码或精确重现作者实验。`[verified: https://marcoschouten.github.io/ MeshQuery条目；本轮定向检索]`

本轮使用现有ReSeam查询网格、DSL选边、计划预览、展开反馈和版本回退工具，当前Agent提供决策，不新增模型API。原文公开算子已在项目工具层实现；三角网格适配、后端版本、当前Agent与论文不同。正式标签为 `MeshQuery (project implementation)`。`[opinion]`

入口：`uv run python baseline/run_dragon.py meshquery --output <本轮目录>`；完整请求、源代码哈希和反馈保存在该轮meshquery目录。源码快照由本轮归档脚本保存，不复制作者不存在的实现。`[opinion]`
