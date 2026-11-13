# ReSeam 十页项目汇报

新增[防御塔制作过程图解](tower-making-process/防御塔制作过程图解.pdf)，共6页，展示输入与基线、原形合岛、半边镜像、62→24图块整理及颜色／法线近景。完整实验与还原方法见[防御塔交付包](../../docs/assets/tower-uv-comparison/README.md)，来源哈希和逐页检查见[tower-making-process/verification.json](tower-making-process/verification.json)。`[verified: tower-making-process/evidence.json pages；verification.json pages/source_hashes_match]`

新增[三模型 UV、模型与45项指标 PDF](legacy-assets-with-metrics/reseam-three-assets-with-45-metrics.pdf)：共9页，前6页保留原有真实图像，后3页分别列Chloe红刺、历史能量刀和幻想龙的完整指标。全部尺寸39条数据另见[补测报告](../../docs/results/legacy-assets-45-metrics.md)。`[verified: legacy-assets-with-metrics/evidence.json pages/metric_fields/all_cases]`

九页版的[总览](legacy-assets-with-metrics/preview.png)、[来源与哈希](legacy-assets-with-metrics/evidence.json)及[检查记录](legacy-assets-with-metrics/verification.json)一并保留。已检查9页渲染、中文文本提取、原6页图像像素一致和旧PDF未改；粗体只表示同镜像几何的共享／解除共享两列数值比较，不代表跨几何或质量排名。`[verified: legacy-assets-with-metrics/verification.json；evidence.json bold_scope]`

生成入口为`uv run --with reportlab --with pypdf python -m scripts.build_legacy_metrics_pdf --output output/pdf/legacy-assets-metrics-new`；新输出目录必须不存在。该入口读取补测结果，保留旧图，仅追加表格，不调用UV求解、改模或烘焙。`[verified: scripts/build_legacy_metrics_pdf.py build/main]`

新增[三模型 UV 与模型渲染对照 PDF](legacy-assets-comparison/reseam-three-assets-uv-model-comparison.pdf)：共6页，每个模型按“UV与指标、模型渲染”两页排列，顺序为Chloe红刺、历史能量刀、历史幻想龙。六张原图按原比例无裁切嵌入；原实验说明与拒绝状态保持原样。`[verified: legacy-assets-comparison/evidence.json pages/page_count/embedded_pixels_exact]`

六页版的[总览](legacy-assets-comparison/preview.png)、[源图哈希](legacy-assets-comparison/evidence.json)和[检查记录](legacy-assets-comparison/verification.json)一并保留。生成脚本为 `scripts/build_legacy_comparison_pdf.py`，依赖ReportLab、pypdf、Pillow和微软雅黑；只组合已有图像，不重新求解、改模或烘焙。`[verified: build_legacy_comparison_pdf.py build；verification.json]`

更新版见 `with-baselines/reseam-uv-workflow-baselines-10-pages.pdf`，保留十页，前四页归纳原始输入到最新80岛结果的制作流程，后六页加入论文基线实验与两份ReSeam对照。原版及其预览保持不变。`[verified: with-baselines/workflow-pdf-evidence.json pages；with-baselines/verification.json original_pdf_unchanged]`

更新版内容：

- 六项方法：FlattenAnything、OptCuts、xatlas、Blender Smart UV、PartUV、MeshQuery项目实现；另列ReSeam共享和解除共享对照。`[verified: with-baselines/baseline-assets/measurements.json rows]`
- 相同镜头的棋盘格、彩色UV、三条代表路径的真实模型与BaseColor；全部八条路径的岛数、独立图块、共享表面、重复占位节省、复用倍数和覆盖率。`[verified: with-baselines/workflow-pdf-text.txt 第6—9页]`
- 颜色、接缝、渲染的分别验收结果，保留原生UV失败；MeshQuery项目实现不冒充作者代码复现。`[verified: with-baselines/workflow-pdf-text.txt 第5、10页]`
- 历史精调覆盖率65.08%与本轮共同排布62.93%分别标明；基线未追加ReSeam共享后处理。`[verified: with-baselines/workflow-pdf-text.txt 第5、9页]` 0%不代表无法组合共享，共享占位节省不等于同质量纹理字节节省。`[opinion]`

更新版复现：

```powershell
uv run --with reportlab --with pypdf python scripts/build_uv_baseline_pdf.py
pdftoppm -png -r 90 output/pdf/with-baselines/reseam-uv-workflow-baselines-10-pages.pdf output/pdf/with-baselines/pages/page
```

`with-baselines/`保留源素材、SHA-256清单、文本提取、验证记录、逐页预览及总览图。生成脚本不执行求解、改模或烘焙，只读取已保留的测量结果和实际图像。`[verified: scripts/build_uv_baseline_pdf.py BaselineReport；with-baselines/verification.json]`

## 原版

- `reseam-uv-workflow-10-pages.pdf`：十页、16:9横版，首页为原始输入与最新80岛输出的模型/UV/数据对照。
- `reseam-uv-workflow-preview.png`：十页总览；`pages/`：逐页1600×900预览。
- `source-assets/`：本次实际使用的图片、UV数组和指标；来源与SHA-256见 `workflow-pdf-evidence.json`。
- `verification.json`：页数、字体嵌入、中文提取、指标/UV面板断言和代码检查结果。

生成脚本位于仓库根目录下的 `scripts/build_uv_workflow_pdf.py`。在仓库根目录运行：

```powershell
uv run --with reportlab --with pypdf python scripts/build_uv_workflow_pdf.py
```

需要Windows微软雅黑字体。脚本优先读取仓库内原始证据，缺失时使用输出目录的 `source-assets/`；保留这些文件即可继续排版。源码中的几何数据处理只用于绘图，不执行新求解、改模或烘焙。PDF中的跨几何比较与外观预览状态按原始报告保留。
