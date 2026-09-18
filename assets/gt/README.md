# Artist 真值参考

用户提供的两份 FBX 及两张贴图保存在此处，哈希与关联见 [manifest.json](manifest.json)。FBX 原文件保持不变；贴图是用户提供 PNG 的字节相同副本。

- 能量刀：`Temp_FX_Mesh_115_skin06_YY_1005.FBX`，111 顶点、186 多边形，Blender 三角化后 218 面，含两套 UV。
- 幻想龙：`Temp_FX_Mesh_25_skin11_CLW_182.FBX`，主体 `tripo_node_` 为 1504 顶点、2528 多边形，三角化后 2754 面；另外含 `fz` 与 `Plane003` 两个平面网格。主体有两套 UV。

这些是独立 artist 参考，不替换 [固定 V1 低模](../v1/README.md)，也不并入同拓扑、同预算的方法排名。按原始对象、材质和 UV 通道分别计算指标；利用率取图集内的 UV 面积并集，重复覆盖不重复计数。存在镜像／复用时，不直接按 V1 的非重叠装箱约束判 artist 结果失败。

能量刀 FBX 未提供材质绑定，龙主体引用的 `long.png` 不在 FBX 内。展示时将用户提供的参考图与第一 UV 通道关联，须与原游戏着色器的实际 UV 使用方式区别。第二 UV 和辅助平面不自动作为主颜色图集。

复现只读检查：

```powershell
uv run python scripts/inspect_artist_gt.py --input assets/gt --manifest assets/gt/manifest.json --output artifacts/artist_gt/<新的编号>
```

输出包含源文件哈希、未变检查、Blender 导入版本与未变形几何、各 UV 通道、原始 FBX UVIndex 拓扑、独占覆盖与重复覆盖，以及几何畸变和密度变化。带骨骼模型使用导入后的未变形网格，不能据此宣称动画姿态均已验证。
