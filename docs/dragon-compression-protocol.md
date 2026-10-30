# 整龙最大限度压缩的执行口径

目标是在固定外观目标、保护区与质量协议下，选取已验收候选中的最小实际纹理字节；不声称全局最优。`[verified: 用户最新要求“经最大可能的压缩”；opinion: 有限搜索定义]`

## 同时比较尺寸和编码

枚举512、1024、2048、4096、8192，以及RGBA8、真实BC1 sRGB与BC7 sRGB。每个尺寸/路径只烘焙一次，编码变体共用同一源贴图，再独立验收解码结果。所有Baseline使用同样的候选编码与门限。`[verified: scripts/compare_dragon_reuse.py codecs/preparations]`

完整mip的RGBA8字节为 $\sum_l 4w_lh_l$；BC1字节为 $\sum_l 8\lceil w_l/4\rceil\lceil h_l/4\rceil$，BC7每块16字节，最后1×1、2×2也各占一个块。DDS DX10容器另加148字节头。`[verified: Microsoft DDS_HEADER与DDS_HEADER_DXT10结构；opinion: 存储推导]`

|尺寸|RGBA8完整mip|BC1完整mip|BC7完整mip|DDS头部|
|---|---|---|---|---|
|512²|1398100|174776|349552|各加148字节。`[verified: mip_dimensions公式计算]`|
|1024²|5592404|699064|1398128|各加148字节。`[verified: mip_dimensions公式计算]`|
|2048²|22369620|2796216|5592432|各加148字节。`[verified: mip_dimensions公式计算]`|
|4096²|89478484|11184824|22369648|各加148字节。`[verified: mip_dimensions公式计算]`|
|8192²|357913940|44739256|89478512|各加148字节。`[verified: mip_dimensions公式计算]`|

这些是布局字节，不是质量或实际节省结果；压缩后必须重新验收。原始JPG包体、PNG包体、DDS运行时纹理、解码预览与完整调试交付目录分列，不能混为同一指标。`[opinion]`

## 纹素预算重新分配

固定视角的4096诊断将主要残差定位在原面5383/5381/5379，对应原岛32的受保护眼睛。沿原面身份选中两侧眼部owner，只统一缩放其UV，保持几何、颜色和图块形状；接着恢复密度约束、排布并重新烘焙。`[verified: eye_density_ablation01/protocol.json；density.py scale_selected_owners]`

控制组与眼部1.5倍组从同一父状态出发，其他参数一致；若采用这一修正，四条正式路径都得到相同的公共预处理。不能把公共密度修正的收益归因于共享。`[verified: eye_density_ablation01/receipts；opinion: 正式比较约束]`

## 验收边界

BC1以真实块编码、逐mip解码结果接受检验；采样覆盖遮罩独立保存，不伪称BC1保留透明度。GLB是解码后颜色预览，DDS是单列纹理产物；目标引擎和GPU未执行。`[opinion]`

继续检查LOD0/1/2的颜色、接缝逐侧绝对误差及跳变、固定屏幕渲染，并保护眼睛和嘴。完整mip写出不代表全部LOD已验收。失败编码、尺寸、初始化及超时均进入矩阵。`[opinion]`

原始格式依据：[DDS_HEADER](https://learn.microsoft.com/en-us/windows/win32/direct3ddds/dds-header)、[DX10扩展](https://learn.microsoft.com/en-us/windows/win32/direct3ddds/dds-header-dxt10)、[DXGI_FORMAT](https://learn.microsoft.com/en-us/windows/win32/api/dxgiformat/ne-dxgiformat-dxgi_format)。`[verified: 本轮官方文档核对]`

BC7可选依赖固定为 `ispc-texcomp==1.0.1`，CPU编码配置固定为slow；运行时使用 `uv run --extra compression`。原始[API文档](https://k0lb3.github.io/ispc_texcomp_py/ispc_texcomp.html)和[包版本／许可](https://pypi.org/project/ispc-texcomp/1.0.1/)为接口依据。`[verified: encoding.py BC7_VERSION/BC7_PROFILE；本轮官方API与PyPI页面核对]`
