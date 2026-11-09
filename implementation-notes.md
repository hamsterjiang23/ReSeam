# Three-asset ReSeam implementation notes

## Portable Skill package and dependency setup (2026-11-06)

- Package the main reseam-uv and sibling reseam-hard-surface together. The package carries instructions/references, not project executors, native binaries, weights, personal runtime settings or model assets.
- Add first-use routing to environment-setup.md: the Agent installs missing selected-path dependencies and resumes the original task. Global doctor readiness includes unrelated historical backends, so it is diagnostic rather than a blanket gate.
- Installation guidance distinguishes Python/uv, Blender, WSL/OptCuts and optional CUDA research/encoding paths. Preserve existing environments; record real execution evidence and request only concrete missing access or system-level intervention.

## OptCuts-first Agent workflow (2026-11-06)

- User requested fewer moving parts: OptCuts proposes initial cuts; Agent reviews adjacent islands and edits only selected boundaries; existing packing/bake/validation tools remain. No new registry tool or Skill is introduced.
- Tripo smoke selected adjacent charts 10/12 on a continuous protruding patch; removing original edge377 reduced 19 charts to18. Every other OptCuts seam is retained; local maximum anisotropy is1.2612.
- The current mirror dragon has11338 faces and80 original-edge connected components. Fresh OptCuts also yields80 charts, so there is no adjacent-chart merge candidate; authorized left/right stacking is tested separately and cannot be labeled true island merging.
- Completed the two-asset three-seed matrix:12 real GLBs, each with9 mip PNGs and independent reimport checks. Dragon accepts37/40 mirror groups:80 islands/43 tiles,67.048% shared surface. Keep the existing80/40 higher-resolution delivery; this trial is diagnostic and is not a claimed improvement over it.
- Tripo's second local merge also produces18 islands but its best coverage is43.8425%, below OptCuts45.1728%; stop after the cheap packing screen rather than rebaking a second unpromising candidate. The first18-island candidate has three real baked exports; final Agent selection retains OptCuts.
- After archiving executed sources, fix three orchestration edge cases:missing OptCuts result must not crash target freezing, an earlier merge success must not suppress the current candidate's fallback, and an unseen local proposal must first render evidence and return for Agent review. These changes do not recompute or upgrade frozen experimental results.
- Report completeness checks initially mistook nested native backend command files for unfinished public tools. Restrict pending checks to public request/provenance directories, retain a regression test for native children, and require the exact method/seed matrix before packaging.

## Chloe hard-surface experiment (2026-11-06)

### Deviations

- The existing mirror geometry will be reused unchanged. A fresh opt-in FBX diagnostic import supplies split-normal evidence to both geometries through exact indexed geometry / recorded barycentric lineage; it does not alter historical snapshots or exported shading.
- Simple 30/45/60-degree cut sets retained same-chart overlaps. The original Chloe branch therefore uses bounded source-polygon fans with dual-tree openings, prioritized by 16-ray geometric AO. The mirror branch succeeded at 30 degrees and did not need the AO-prioritized fan fallback.
- The user added a Tripo spiked dagger and selected an existing 424-face retopology after the 1061-face mesh failed the unchanged importer's nonmanifold-edge check. It is a separate fixed-geometry experiment; no new retopology or mirror is created.
- Independent review found different projection normalization in Tripo normal cues versus projected BaseColor. Round02 is retained as development evidence; round03 reruns the affected paths with the original-low AABB diagonal for both and extracts the normal texture from the frozen reference.

### Discovered edge cases

- Existing FBX imports did not retain corner-normal evidence. `crease_select` uses geometric dihedrals, so that output cannot be labeled authored hard normals. Internal polygon triangulation diagonals must be excluded before seam proposals.
- The original Chloe's unchanged UV outside a local smoke region has mixed orientations. Native local Blender outputs are audited independently; local backend success is never relabeled as full-snapshot acceptance.
- Scalar I;16 height images must not pass through RGB8 conversion. Regression coverage checks a midrange 16-bit height step; normal-map data are linear, and GLB/internal V-axis conversion is recorded explicitly for Tripo cues.
- The CLI orchestration scripts are invoked with `uv run python -m scripts.<module>` so shared audit helpers resolve. The first Chloe evaluation invocation failed after export on a module import; the exact request/output was resumed without rerunning or rewriting completed tools.
- Two additional Tripo diagnostic views ran alongside evaluation and were omitted by the serial ledger writer. Their immutable result/request/provenance files survived; their runner wall times were reconciled with explicit unknown outer overhead. After experiments completed and executed code was archived, a per-geometry lock was added to reject simultaneous writers. This orchestration-only fix did not regenerate UVs, bakes or scores; final kernels remain frozen in the report packages.

### Questions for review

- None. Both geometry versions and appearance-first acceptance were selected by the user. Existing dirty edits are preserved.

### Implementation summary

- Four conservative deviations above; no historical input or model was overwritten.
- Revisit: the 256-pixel tradeoff between reduced stretch, extra seams and texel budget.
- Four added edge-case notes cover inherited UV rejection, 16-bit maps, projected cue scale and module invocation.
- Chloe original approximate reuse has a local maximum anisotropy regression; it remains diagnostic rather than a final-quality success.
- Next read: `docs/results/chloe-hard-surface.md`, `docs/results/tripo-spike-hard-surface.md`, and each round's failure/provenance files.

## Deviations

- The 45-metric supplement reuses the previous benchmark definitions and retained color validation. Original low meshes without UV remain N/A; rejected packing is measured only in its retained native layout, never relabeled as a final atlas.
- Fresh-eye review caught the legacy label "new 3D seams": the implementation measures all explicit state.seams, including authored seams. New tables say "recorded seam length" and retain the legacy field names and formulas; the first metric round is retained and the corrected 45-field presentation is regenerated in a new round.
- LSCM and ABF++ on the uncut retained halves produced mixed orientation. Positive-weight harmonic was tried as a distinct existing backend: it produced one master chart for Chloe/energy; dragon failed its disk Euler condition and retained OptCuts' three charts. No face isolation was used.
- A single sharing group spanning multiple OptCuts charts cannot use the contour packer. Sharing was regrouped per actual master chart, without adding cuts or changing geometry.
- Chloe's initial Y-plane candidate had the lowest point-cloud residual but visibly changed the crest arrangement. It was rejected after rendering; a separate X-plane candidate preserves the vertical feature layout. Both candidates and real textures remain recorded.
- The 2048 projected xatlas bake timed out in round01. The existing restrict_bounds broad phase now provides per-original-low-face ROI acceleration with exact full-source fallback. Affected evaluations move to round02, retaining round01. 2048 projected bake/validation explicitly request up to900s each, still capped by the 2400s whole-path budget; ordinary tools remain300s.

## Discovered edge cases

- The historical low meshes have no source UV and their material references have different triangulations. The existing same-corner BaseColor path cannot be used without explicit projected source lineage.
- FBX import currently preserves polygon-loop identity conservatively; Chloe requires raw UVIndex recovery and a preserved second UV channel to avoid counting importer splits as authored islands.
- Chloe FBX includes animation metadata. The explicitly requested static-color import disables animation loading and records that omission; ordinary imports retain their existing animated-input rejection.
- Projected targets cannot use the old high==low original_topology witness. They preserve current-low adjacency and explicitly reject that incompatible optional witness instead of accepting a mismatched topology.
- Fresh-eye review found that mirrored/source-only views reset an inherited projection bound to .02. Fixed to inherit the frozen source value unless explicitly overridden; the integration test now exercises a .1 inherited bound.
- Whole-pair exact coordinate matching can fail at a nonzero mirror plane after float32 export. The existing topology path succeeded for that retained experiment; no global geometric matching tolerance was silently raised.
- Original-versus-mirror preview cameras use the same angles with per-mesh automatic fit. Their images are qualitative geometry references, not pixel-aligned cross-geometry error evidence; shared/unshared controls retain identical bounds.
- The installed skill validator requires PyYAML, absent from the project environment. It passed via an ephemeral uv --with environment; no new project dependency was added.
- A report read-only review found one completed inspection omitted by the concurrent calls.json writer. The report now compares actual tool directories to ledger keys and refuses missing, duplicated, unknown-cost or unfinished calls. Final reconciliation preserves missing-call runtime from its result.json wall_seconds, with its different timing scope recorded.
- After all evaluations ended, two external Blender/NumPy typing annotations needed explicit runtime-no-op casts for the expanded basedpyright check. The executed 117-file kernel is retained under round02/frozen_code; AST equality after erasing casts and an actual Chloe reimport showed identical geometry, UV0 identities, UV1 and texture arrays. No score was recomputed under mixed code versions.
- Packaging exposed a display-measurement error: the orchestrator inspected every packed result with a 256-pixel gap contract, hiding valid 2K sharing metrics. It now passes each candidate's actual resolution. All 30 completed candidates were re-inspected with the original frozen kernel and proper packing profile; the original inspections and numerical validation remain archived. UV state hashes and packing-selection coverage values are unchanged, so there is no new UV/bake/quality comparison mixed into this measurement revision.

## Questions for review

- No unresolved user decision. Original assets and historical experiment outputs remain immutable; the user authorized separate mirror variants and selected static Chloe evaluation.

## Final implementation summary

- Added explicit original-high projected targets and retained-side source lineage, with fixed original-face ROI acceleration.
- Added explicit FBX static color binding, raw UVIndex recovery and auxiliary UV export/inheritance.
- Completed three-asset mirror/merge/packing and resolution matrices; rejected fixed-geometry and raw-packing attempts remain visible.
- Recorded Agent preview acceptance separately from numerical rejection, with matched unshared controls and actual texture/model outputs.
- Verification: 116 related tests passed; expanded Ruff/basedpyright and real post-typing Chloe import parity passed. Packaging and file-hash checks are retained with the final report.

## 45-metric supplement summary

- Measured 39 retained original/method/resolution cases with 45 requested fields. Added geometry/shape/AO/distortion metrics, retained all 30 historical color validations, and recorded per-field N/A reasons without new solving or baking.
- Preserved the first run, corrected the legacy "new seam" label to recorded explicit-seam length, and retained the five executed source modules and their exact hashes. The corrected-label run has identical numerical values; formatting-only edits have identical Python ASTs.
- Appended three searchable vector metric pages to a new nine-page PDF. The old six-page PDF is unchanged; all six original images remain pixel-identical in the new PDF.
- Verified 130 source hashes, all 30 mip0 sizes/hashes, the three original source-image sizes, and three matched-geometry shared/unshared pairs. Existing composite quality rejections remain unchanged.
- Verification: 8 metric tests passed; Ruff and basedpyright passed. Data and PDF check receipts are stored next to their outputs; all nine pages were rendered and visually reviewed.

## 镜像龙通俗制作流程 PDF（2026-11-09）

### Deviations

- 正文把多轮重复的颜色生成集中在一页解释，逐步页面仍标明何时重新上色和检查；原始请求和返回作为 PDF 内嵌附件及旁存 JSON 提供。原因是面向外行的28页篇幅，全部132次已收录调用单独分页会淹没制作主线。
- 新绘图仅增加讲解高亮、编号与局部展开图。原有脚底切线颜色偏暗，按原图已有红色像素加亮并向外扩一像素，明确标注；历史模型、UV、烘焙和结果保持原证据。

### Discovered edge cases

- 旧 view_component36/front.png 和 view_component18/front.png 是平面区域视图，不适合解释三维位置。改用修改前完整模型的实际面编号图和区域面集合加亮定位。
- 最终截图沿用80片几何并重新排布，覆盖率62.93%；制作阶段精调65.08%单独展示。两状态顶点与面数组逐项相同。
- 最终40组逐面回溯到11450面父状态及原始Tripo面。38组的旧形状经裁切插值后与最终图块可用整体相似变换重合，误差约4e-15；另两组分别对应头冠16→1和脚部7→1。
- 中文字体按字符集拆成多个子集嵌入。最终检查按两种字重核对全部五个子集的字体数据与Unicode映射，避免把合法子集数量当作缺字问题。

### Questions for review

- 无需新增用户决定。部位白话名称按实际高亮图命名，标记为讲解用名称；操作理由按历史记录整理，不作为原始思维逐字记录。

### Implementation summary

- 交付28页中文横版图解PDF，含12个制作步骤、40块逐一定位和132次已收录调用附件。
- 两项展示取舍是集中解释重复上色、加亮原有切线；后者如需原图可沿来源索引回查。
- 发现四项边界情况，已分别处理三维定位、两轮覆盖率、片形来源和字体子集核验。
- 全部28页已渲染并目视检查；40组覆盖11338面无遗漏，中文文本、书签、附件、全部来源哈希核验通过。
- Ruff与指定内置运行时的basedpyright通过。下次先读 output/pdf/dragon-making-process/verification.json 和 evidence.json。

## 镜像龙全身继续整理（2026-11-09）

### Deviations

- round01胸腹重展开成功，但坐标近邻精确复制、拓扑复制及近似转移三条旧路径均拒绝。保留round01，新增显式mirror_source使用镜像创建时的哈希绑定面角对应；代码变更后的真实实验在round02重新建立对照。

### Discovered edge cases

- 胸腹每侧485个顶点，最近坐标匹配只有480个不同命中。保存的791对面可恢复485个一一对应顶点；坐标反射有约1e-9的float32舍入差，旧精确坐标门限约1.58e-10。
- 来源复制检查同一资产与逐项相同几何、来源文件哈希、全表面配对、顶点双射和分量ULP舍入界。普通坐标路径保持原门限；错误平面、过期来源、改几何、遗漏面和未绑定文件仍拒绝。
- round02共同目标为source_original，工具报告modified_faces=0，四视角原始/目标像素逐项相同。控制组烘焙先于Agent目视预检完成，时序如实记入target_review.json；大腿候选尚未开始烘焙时已完成目视接受。后续编排增加当前target_hash绑定的人工式Agent记录检查。

### Questions for review

- 当前固定几何继续试胸腹、腿、尾巴、翅膀与角刺。新跨部位共享须重新检查目标外观，不把已有左右共享冒充新增收益。

### 全身试验与四成员共享补充

- round02完成16次单部位展开及2次组合。大腿在seed0/17较对照好，seed29较差；旧65.08%排布相对密度与截图版几乎一致，不能用截图62.93%冒充此前最高覆盖。
- 4组角刺进行9次跨部位UV尝试，3组找到两成员可用方案。拆掉原左右关系再配对会变成41图块，不能算新增节省。
- 为保留两个镜像伙伴，ReuseGroup显式增加member_maps，记录同一个主表面到每个完整成员的相似变换与方向。新增组合选项沿用reuse.check_pair，独立验证全部正面积交叠，包括从成员到另一个成员的组合映射；旧两成员格式默认行为保留。
- 颜色目标允许显式选择需要统一的组，其他部位保留来源颜色。新多成员类型限定在UV与BaseColor路径，完整PBR入口保持明确拒绝。
- 新鲜上下文审查发现释放无关面会丢失新成员记录。已用dataclasses.replace保留未改变组，并补充两种公开释放入口的保存/重读回归；复审CLEAN，审查者独立7项新测试通过。
- round02三个执行器均完成后才改生产代码，104份原源码哈希再次核验无变化，并封存closure.json。四成员真实实验另开round03，重新冻结源码并建立对照。

### 完成交付

- 全部40个来源部位均有最终处理或保留依据。16次局部展开、2次组合、9次跨部位尝试及3个四成员候选保留真实请求与失败。
- round03最终80片／38图块，固定11338面。两组四成员来自原9+10和13+14，其余36组仍是左右两成员；原主侧轮廓仅10、14改变，38份原轮廓保持整体相似形。
- 最少图块优先的选择得到63.05%覆盖。同设置40份对照最好64.53%，历史40份65.08%，因此没有取得更紧凑的结果。2048 RGBA8完整mip运行时字节相同，字节节省0。
- 最终两份对照均先冻结并目视审查同一目标，再烘焙。保护面颜色变化0，角刺231面有意调整；实际四方向和两局部视图由Agent接受，严格渲染继续拒绝，几何／颜色／接缝在显式外观优先设置下通过。
- 两份GLB独立重读，几何误差0，UV最大舍入误差约4.47e-8，嵌入颜色像素完全相同，24张mip尺寸检查通过。40页新版PDF包含历史28页与新增12页，附160次本轮调用及132次历史调用；40页渲染，历史页逐像素一致，新页已目视检查并修正表格间距。
- 204项相关回归通过，新增7项最后重跑通过，Ruff／basedpyright通过，107份生产源码冻结后再核对。交付与核验入口见docs/results/dragon-wholebody-refine.md、round03/delivery_verification.json和output/pdf/dragon-wholebody-refine/verification.json。
- 清理本轮临时审阅图与中间PDF的命令被自动审批拒绝，仅返回blocked by policy。保留临时文件，不换机制重试删除；正式交付完整。

### 最终38份展示补充

- 按用户要求追加两页，42页新版的第41页展示最终模型、完整38份展开图及真实颜色图，第42页展示十列全方法对照，最右列标为ReSeam进一步优化。
- 原九列卡片逐像素一致，新增列来自最终状态8ebd5185…的真实checker视图。所有38个owner覆盖11338面，对照图4992×1460原分辨率嵌入PDF，原有四份证据附件完整保留并新增展示证据附件。
- Windows以WinError 5拒绝原PDF路径覆盖，另存最终38份对照版本。42页已渲染，新增2页与最后一列已目视检查，原40页内容流与像素一致，Ruff与basedpyright通过。

### 各UV实际颜色渲染补充

- 用户要求实际颜色结果，新增十列实际BaseColor模型与贴图对照，并在45页新版追加总览一页、放大两页。可用8组包含原始8K参考、6条2K基线及最终38份2K版本。
- FlattenAnything和xatlas在各自冻结排布阶段失败，未生成颜色图，保留明确失败标记。其它7个镜像龙输出逐项核对实际UV状态、数组哈希、烘焙记录及texture渲染请求，六份基线渲染使用旧冻结实际输出，38份按同一1024尺寸及35/15角度补渲染。
- 45页完成渲染，新增3页已目视检查，前42页内容与像素一致，原始及六条有效基线卡片像素一致。4992×1460对照图原分辨率嵌入PDF，六份证据附件保存，Ruff与basedpyright通过。


## 2026-11-09 美术工具与目视验收

用户授权七类工具全部补充，以肉眼没有明显差别为保留依据。实现十个 artist.* CLI 入口及离线工作台，显式 visual 路径保留数值证据为参考。实际 GLB 使用 Blender CPU 重新导入渲染。详见 docs/artist-tools.md、docs/results/artist-tools.md。

验证：107 项相关测试通过，Ruff/basedpyright/diff check 通过，真实龙 11 次操作调用与独立双方案试做成功，原快照不变；浏览器定位、共享高亮、固定点、闪烁、缩放及复制请求通过；16 张实际龙渲染与五材质通道冒烟成功。独立复查六项问题已修复并加回归。早期自动下载被浏览器取消，最终界面采用已验证复制请求，无下载按钮。证据在 output/artist-tools/dragon-38/verification.json 与 artifacts/artist_tools/。
