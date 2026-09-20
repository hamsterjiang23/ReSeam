# ReSeam 参考文献与实现索引

整理范围：原研究报告 R1–R8，以及本轮用户提供的 packing、约束优化、区域搜索和纹理复用资料。保留原始报告编号；新增文献另编编号。论文结论、我们的设计判断、代码接入状态分开记录。完整性仅指当前讨论范围，不是系统综述或新颖性证明。`[verified: goal/UV_展开方案_研究报告_v0.1.md“参考资料与核验范围”；本轮四份用户补充]`

核验日期：2026-09-18。引用优先作者、出版方和官方实现；“摘要/项目页已核验”不表示通读全文或成功复现。不批量复制第三方论文正文；保存链接及必要书目信息。`[opinion]`

## 切缝与参数化

### R1 · OptCuts（2018）

Minchen Li, Danny M. Kaufman, Vladimir G. Kim, Justin Solomon, Alla Sheffer. **OptCuts: Joint Optimization of Surface Cuts and Parameterization**. ACM TOG / SIGGRAPH Asia. DOI: [10.1145/3272127.3275042](https://doi.org/10.1145/3272127.3275042)。[作者项目页](https://www.cs.ubc.ca/labs/imager/tr/2018/OptCuts/) · [论文](https://www.cs.ubc.ca/labs/imager/tr/2018/OptCuts/doc/OptCuts_small.pdf) · [代码](https://github.com/liminchen/OptCuts) · [用户补充中的 Adobe 页面](https://research.adobe.com/publication/optcuts-joint-optimization-of-surface-cuts-and-parameterization/)。`[verified: 原研究报告 R1；作者项目页摘要]`

畸变约束下联合优化切缝和参数化；不能把“切缝与连续更新交替”本身当成 ReSeam 新意。当前用于 V1 强基线和独立连续适配器；本地快照与许可证见 [源码索引](README.md)。`[verified: 作者项目页摘要；src/reseam/solver.py；references/manifest.json]`

### R2 · PartUV（2025）

Zhaoning Wang et al. **PartUV: Part-Based UV Unwrapping of 3D Meshes**. SIGGRAPH Asia Conference Papers. [arXiv:2511.16659v1](https://arxiv.org/html/2511.16659v1) · [代码](https://github.com/EricWang12/PartUV)。部件树与展平反馈参照，V1 保留基线；不是本次固定岛 packing 后端。`[verified: 原研究报告 R2；references/README.md“来源与版本”]`

### R3 · AutoCuts（2017）

Roi Poranne, Marco Tarini, Sandro Huber, Daniele Panozzo, Olga Sorkine-Hornung. **Autocuts: Simultaneous Distortion and Cut Optimization for UV Mapping**. ACM TOG 36(6), Article 215. DOI: [10.1145/3130800.3130845](https://doi.org/10.1145/3130800.3130845)。[项目页](https://igl.ethz.ch/projects/autocuts/) · [论文](https://igl.ethz.ch/projects/autocuts/autocuts-SA-2017-poranne-et-al.pdf) · [代码](https://github.com/Roipo/Autocuts)。联合切缝与畸变优化的相关工作；本仓库未接入运行。`[verified: 原研究报告 R3；ETH 机构记录及论文摘要]`

### R4 · Ambrosio–Tortorelli UV（2023）

Colin Weill–Duflos, David Coeurjolly, Fernando de Goes, Jacques-Olivier Lachaud. **Joint optimization of distortion and cut location for mesh parameterization using an Ambrosio-Tortorelli functional**. Computer Aided Geometric Design 105, 102231. DOI: [10.1016/j.cagd.2023.102231](https://doi.org/10.1016/j.cagd.2023.102231)。[作者项目页](https://perso.liris.cnrs.fr/david.coeurjolly/publication/uv-at/) · [作者 PDF](https://perso.liris.cnrs.fr/david.coeurjolly/publication/uv-at/uv-at.pdf) · [代码](https://github.com/Lieunoir/UV-AT) · [出版方页面](https://www.sciencedirect.com/science/article/abs/pii/S0167839623000638)。`[verified: 原研究报告 R4；作者仓库 README]`

联合畸变与切缝的连续变分路线。将连续变量限制在 ReSeam 候选区域、阈值化后离散修复，是我们的待实验设计；用户补充中的逐边 z_e 公式不是已核对的作者实现接口。当前未接入。`[opinion]`

### R5 · SeamFlow（2026，预印本）

Yuming Zhao et al. **SeamFlow: Structure-Aware Flow Matching on Edge Probabilities for Artist-Like UV Unwrapping**. [arXiv:2609.04751v1](https://arxiv.org/html/2609.04751v1) · [项目页](https://meshy-dev.github.io/seamflow)。边概率空间中的 flow matching 接缝先验；当前仅相关工作，不把论文指标当作本项目实验结果。`[verified: arXiv 摘要；原研究报告 R5]`

### R6 · SeamGen（2026，预印本）

Hao Xu et al. **SeamGen: Artist-Aligned UV Seam Generation via Graph Flow Matching**. [arXiv:2607.12379](https://arxiv.org/abs/2607.12379)。学习式 artist 接缝生成参照；仅摘要级核验，未接入。`[verified: 原研究报告 R6；arXiv 检索记录标题]`

### R7 · SLIM（2017）

Michael Rabinovich, Roi Poranne, Daniele Panozzo, Olga Sorkine-Hornung. **Scalable Locally Injective Mappings**. ACM TOG. [作者项目页](https://igl.ethz.ch/projects/slim/)。候选连续求解后端；局部正 Jacobian 不等于全局无重叠。V2 固定岛对照不调用 SLIM。`[verified: 原研究报告 R7 及第 4.3 节]`

### R8 · SCAF（2017）

Zhongshi Jiang, Scott Schaefer, Daniele Panozzo. **Simplicial Complex Augmentation Framework for Bijective Maps**. ACM TOG 36(6), Article 186. DOI: [10.1145/3130800.3130895](https://doi.org/10.1145/3130800.3130895)。[作者入口](https://jiangzhongshi.github.io/publication/simplicial-complex-augmentation-framework-for-bijective-maps/) · [libigl 官方教程](https://libigl.github.io/tutorial/)。外部支架与双射映射路线；原报告 R8 引用的是教程章节，本次补齐作者书目，未复现完整论文。`[verified: 原研究报告 R8；作者页面 BibTeX 与摘要]`

## 排布、采样与纹理复用

### R9 · GFPack++（ICCV 2025）

**GFPack++: Attention-Driven Gradient Fields for Optimizing 2D Irregular Packing**. [CVF 论文页](https://openaccess.thecvf.com/content/ICCV2025/html/Xue_GFPack_Attention-Driven_Gradient_Fields_for_Optimizing_2D_Irregular_Packing_ICCV_2025_paper.html) · [作者代码](https://github.com/TimHsue/GFPack-pp)。作者代码包含学习权重、采样与碰撞/间隙处理；当前仅设计参照，未训练、下载权重或接入。不能把 Blender/xatlas 多起点包装称为 GFPack++ 复现。`[verified: 作者仓库 README；docs/v2-plan.md“后续路线”]`

### R10 · Texture Size Reduction（2025）

Julian Knodt, Xifeng Gao. **Texture Size Reduction Through Symmetric Overlap and Texture Carving**. ACM TOG 44(1), Article 11, 1–20, 2025-02-13. DOI: [10.1145/3714408](https://doi.org/10.1145/3714408)。[作者主页](https://www.julianknodt.com/) · [合作者主页](https://gaoxifeng.github.io/)。`[verified: ACM 出版元数据]`

输入已有网格、UV 和多通道纹理，先重叠相同 charts/折叠镜像区域，再共同调整 UV 与纹理以移除冗余和空白 texels。其 texture carving 的像素路径与 ReSeam 的网格切缝不同。`[verified: ACM 摘要及 Section 3 Method]`

ReSeam 已加入 [镜像整面配对与复用诊断](../docs/v2-reuse-results.md)，借鉴共享思想；未复现该论文的 OT 配准、连续折叠或 Texture Carving，不将叠加面积当唯一覆盖率。`[verified: ../src/reseam/symmetry.py 模块说明；复用诊断报告]`

### R11 · xatlas / xatlas-python（工程实现）

[xatlas 官方实现](https://github.com/jpcy/xatlas) · [Python 绑定](https://github.com/mworchel/xatlas-python)。已安装绑定版本 0.0.11；`Atlas.add_uv_mesh` 用于已有 UV 的 packing-only 路径，与重新参数化接口分开。输出须复核面身份、实际图集宽高、岛形及间距。`[verified: xatlas-python README“Repacking an existing UV atlas”；scripts/fixed_pack_worker.py]`

### R12 · Blender / UVAtlas（工程文档）

[Blender UV 操作 API](https://docs.blender.org/api/4.5/bpy.ops.uv.html) · [Microsoft Using UVAtlas](https://learn.microsoft.com/en-us/windows/win32/direct3d9/using-uvatlas) · [D3DXUVAtlasPack](https://learn.microsoft.com/en-us/windows/win32/direct3d9/d3dxuvatlaspack)。UVAtlas 文档将 gutter 定义为 texel 单位的 chart 最小距离。Blender 是当前 packing 候选后端；UVAtlas 仅口径参考，未接入。`[verified: D3DXUVAtlasPack“fGutter”参数；scripts/fixed_pack_worker.py]`

## 连续约束、区域搜索与可见性

### R13 · SQP / 信赖域（算法文档）

[NEOS: Sequential Quadratic Programming](https://neos-guide.org/guide/algorithms/sqp/)。SQP 以二次子问题近似局部约束问题，可结合线搜索或信赖域。用于后续设计，不等于固定岛原型已实现 SQP，也不提供全局最优保证。`[verified: NEOS SQP 说明]`

### R14 · Ipopt / 增广拉格朗日（候选基础设施）

[Ipopt 官方文档](https://coin-or.github.io/Ipopt/)。此处保留求解器入口；尚未确定接入，也未将非光滑岛间距离直接转换为符合其要求的问题。增广拉格朗日是补充中的算法路线，具体实现和文献尚未选定；Ipopt 不作为 ALM 的同义词。`[opinion]`

### R15 · Large Neighborhood Search / Destroy-and-Repair

本轮提出的区域删缝—重建搜索组织方式。当前未选定具体 LNS 论文，不填造作者、年份或 DOI；后续系统检索再补书目。与 ReSeam 的联系是设计类比，不是已有性能结论。`[opinion]`

### R16 · Semantic and Visibility Objectives（ICLR 2026）

Amirhossein Zamani, Bruno Roy, Arianna Rampini. **Unsupervised Representation Learning for 3D Mesh Parameterization with Semantic and Visibility Objectives**. [会议原文入口](https://proceedings.iclr.cc/paper_files/paper/2026/hash/e17afd5784c442d178744533c16d3c96-Abstract-Conference.html)。摘要使用 AO 作为 exposure proxy 并考虑语义/可见性。`[verified: 会议页面作者及摘要]`

供后续感知接缝代价参考；AO 不自动等于用户指定相机下的可见性，当前未接入。`[opinion]`

### R17 · Learning based 2D Irregular Shape Packing（2023）

Zeshi Yang, Zherong Pan, Manyi Li, Kui Wu, Xifeng Gao. **Learning based 2D Irregular Shape Packing**. [arXiv:2309.10329v1](https://arxiv.org/abs/2309.10329v1)。通过学习策略选择、组合接近矩形的 UV 子集并进一步联合优化排布；本次仅核验摘要，列为固定岛算法参照，未接入。`[verified: arXiv 作者信息及摘要]`

### R18 · Unreal Engine Lightmapping（工程文档）

[Epic: Understanding Lightmapping in Unreal Engine](https://dev.epicgames.com/documentation/en-us/unreal-engine/understanding-lightmapping-in-unreal-engine)。区分纹理 UV 与 lightmap UV，并说明 lightmap 的唯一空间及 padding 要求；用户补充中的工程采样参考。`[verified: 官方文档“Texture UV versus Lightmap UV”“Contiguous UVs and Padding”]`

本项目当前烘焙颜色与法线，不能把 lightmap 文档的全部规则或具体 padding 值直接推广为所有材质通道的游戏交付标准。`[opinion]`

## 阅读和实现优先级

当前：R11/R12 接口和采样口径 → R1/R3/R4 操作级对照 → R9 不规则 packing → R13/R14 连续约束。R10 受控复用与 R16 可见性单独立项；R5/R6 学习先验不成为当前原型依赖。所有新颖性主张须扩展检索并做同预算实验，目前不作“此前无人实现反馈闭环”的结论。`[opinion]`
