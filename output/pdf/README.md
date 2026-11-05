# ReSeam 十页项目汇报

- `reseam-uv-workflow-10-pages.pdf`：十页、16:9横版，首页为原始输入与最新80岛输出的模型/UV/数据对照。
- `reseam-uv-workflow-preview.png`：十页总览；`pages/`：逐页1600×900预览。
- `source-assets/`：本次实际使用的图片、UV数组和指标；来源与SHA-256见 `workflow-pdf-evidence.json`。
- `verification.json`：页数、字体嵌入、中文提取、指标/UV面板断言和代码检查结果。

生成脚本位于仓库根目录下的 `scripts/build_uv_workflow_pdf.py`。在仓库根目录运行：

```powershell
uv run --with reportlab --with pypdf python scripts/build_uv_workflow_pdf.py
```

需要Windows微软雅黑字体。脚本优先读取仓库内原始证据，缺失时使用输出目录的 `source-assets/`；保留这些文件即可继续排版。源码中的几何数据处理只用于绘图，不执行新求解、改模或烘焙。PDF中的跨几何比较与外观预览状态按原始报告保留。
