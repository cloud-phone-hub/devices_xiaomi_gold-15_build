# gold-15 T3（真渲染）可行性矩阵与取证清单

> 定位：[gpu-hw-emu.md](gpu-hw-emu.md) §4 的 **G4（渲染门）** 展开。
> T1（保活）/T2（搬运填充）已在 [gpu-hw-emu-spec.md](gpu-hw-emu-spec.md) 规格化并判定 **GO**；
> 本文件回答最后一个问题：**T3 到底卡在哪、有没有公开资料、走哪条路**。
> 本地取证：[issue-gold15-gpu-jm-format](evidence/issue-gold15-gpu-jm-format/solution.md)
> （作业类型分布：[raw/ddk-job-mix.md](evidence/issue-gold15-gpu-jm-format/raw/ddk-job-mix.md)；描述符事实：[raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md)）

---

## 0. 结论速览

| 层 | 子问题 | 公开资料 | 参考实现 | 宿主 GPU 适配 | 判定 |
|---|---|---|---|---|---|
| **J-D 描述符** | 作业描述符布局（链头/Fragment/**Tiler/Vertex/Fused/Compute**） | ✅ **结构已在 panfrost 文档中定位**（[refs](gpu-hw-emu-refs.md)）；Fragment 布局获**逐字段外部确认** | ✅ panfrost（`v9.xml`/`decode_jm.c`） | 直接可用 | **可做（工程）** |
| **J-S 着色** | Mali Valhall v9 ISA 的**执行** | ⚠️ 只有**编码**（ISA.xml/文档），**无语义级成体系描述** | ❌ **无 ISA→宿主 翻译器** | **最大障碍** | **研究级（NO-GO 单独）** |
| **J-F 固定功能** | tiler / 光栅化 / ROP / 纹理 / 插值 | ✅ 架构文档 + panfrost 实现 | ✅ panfrost（tiler/ROP 语义） | 可用宿主图形管线近似 | **可做（工程）** |
| **J-M 内存格式** | AFBC、ZS/CRC、tile buffer | ✅ 部分（AFBC 文档/panfrost 实现） | ✅ panfrost | 需自写编解码 | **可做（按格式分档）** |
| **J-Y 同步** | tile 依赖、cache 语义、renderpass 四链 | ✅ kbase uapi 有 renderpass 语义；硬件语义靠 panfrost | ⚠️ 部分 | 语义可简化（整帧光栅化） | **可做（需仔细）** |

> **一句话**：T3 = **J-D/J-F/J-M/J-Y 四层"工程" + J-S 一层"研究"**。
> 而 **J-S 在"宿主 = 同族 Mali（路线 C）"下直接消失**——这正是路线 C 是"stock 栈 + 真出画"唯一高把握路径的根本原因。

---

## 1. 范围界定（本地取证，已确证）

| 事实 | 证据 | 对 T3 的意义 |
|---|---|---|
| 厂商 DDK 实测提交的**硬件**作业只有两类：`FS(0x1)` = **Fragment**、`CS\|T\|CF\|COHERENT_GROUP(0x4e)` = **Tiler/Compute** | [raw/ddk-job-mix.md](evidence/issue-gold15-gpu-jm-format/raw/ddk-job-mix.md) §3（样本：run81-83） | **T3 只需覆盖 Fragment + Tiler**，不必先解全部 9 类 |
| 软作业（`0x201`/`0x209`/`0x20a`）由 **kbase 在 guest 内实现** | `kbase/mali_kbase_softjobs.c:1476-1593` | **设备模型零工作量** |
| **JM 路径下 kbase 不管理 tiler heap**（只有 CSF 侧有 `mali_kbase_csf_tiler_heap*`） | 全树 grep；[raw/ddk-job-mix.md](evidence/issue-gold15-gpu-jm-format/raw/ddk-job-mix.md) §5 | **tiler 的堆/polygon list 语义全在 DDK + 硬件** → 设备模型必须自己实现（panfrost 是唯一公开参考） |
| 顶点阶段不单独成 job（未观测到 `V` 位） | 同上 §3 | **顶点着色落在 Tiler Job 内** → T3 的"顶点"工作属于 J-D/J-S 的 Tiler 分支 |
| 作业类型编号 = `JS<n>_FEATURES` 位号（FRAGMENT=9、TILER=7、COMPUTE=4…） | `kbase/mali_kbase_gpuprops.c:181-189`、`..._jm_macros.h:228-237`；**外部独立来源完全一致**（panfrost `enum mali_job_type`，[refs](gpu-hw-emu-refs.md) §0） | J-D 的"类型判别"已有**双源权威枚举** |
| **执行顺序由依赖 scoreboard 决定**（非 `Next` 链） | panfrost `pan_jc.h`（[refs](gpu-hw-emu-refs.md) §1） | 设备模型/桥接**必须实现依赖解析**（§2.5） |

**局限（诚实标注）**：样本来自限量探针（12 条 atom 摘要 + 4 条 raw）；`FS_AFBC`、`START/END_RENDERPASS` 未出现但**不代表不会出现**。
取得全集需要扩展探针打印"链上每种 job 的 Type"（**属代码改动，按用户指令暂缓**）。

---

## 2. 五层分解

### 2.1 J-D 描述符层：把链上每种 job 读懂

| 子项 | 需要什么 | 公开资料 / 参考 | 判定 |
|---|---|---|---|
| **链头（通用 32B）** | ✅ **已解**：`job_descriptor_size:1 @0x10:0`、`job_type:7 @0x10:1`、`job_barrier:1 @0x11:0`、`job_index:u16 @0x12`、`dep1:u16 @0x14`、`dep2:u16 @0x16`、`next_job_64 @0x18`；0x00-0x0F = `exception_status`/`first_incomplete_task`/`fault_pointer` | [外部权威](gpu-hw-emu-refs.md) §2（`mali_job_descriptor_header`） | **完成（并交叉验证）** |
| Fragment Job（64 B） | ✅ **已解**且**逐字段外部确认**：0x20-0x3F 是 payload = `mali_payload_fragment{min_tile_coord@0x20, max_tile_coord@0x24, framebuffer(FBD)@0x28}` | 项目实测 + [refs](gpu-hw-emu-refs.md) §0/§2 | **完成** |
| FBD → RT 数组 | ✅ **已解**（参数 64 B：w/h@0x20/0x22；RT 数组 64 B/项：Base@0x20、Row Stride@0x28、Surface Stride@0x2c、word1 的 WE/Format/BlockFormat）。结构名对齐 `bifrost_framebuffer` + `bifrost_fb_extra` + `bifrost_render_target[]`；**block format 编码随版本变化，以 v9 实测值为准** | 同上 + [refs](gpu-hw-emu-refs.md) §2/§3 | **完成（v9 位域以 v9.xml 复核）** |
| **Tiler Job** | **结构已定位**：payload = `bifrost_payload_tiler{prefix, bifrost_tiler_only{primitive_size, **tiler_meta**}, postfix}`；`bifrost_tiler_meta{hierarchy_mask, flags, width, height, **tiler_heap_meta**}`；`bifrost_tiler_heap_meta{heap_size, tiler_heap_start/free/end}`；FBD 内另嵌 `midgard_tiler_descriptor{polygon_list_size, hierarchy_mask, flags, **polygon_list**, polygon_list_body, heap_start, heap_end, weights[8]}` | [refs](gpu-hw-emu-refs.md) §2 | **结构已知；逐字段偏移按 `v9.xml` 落地** |
| **Vertex / Fused Job** | payload = `bifrost_payload_vertex{prefix, vertex, postfix}` / `bifrost_payload_fused{prefix, tiler, tiler_postfix, pad, vertex, vertex_postfix}`；状态尾巴 `mali_vertex_tiler_postfix{position_varying, uniform_buffers, texture_trampoline, sampler_descriptor, uniforms, **shader_meta**, attributes, attribute_meta, varyings, varying_meta, viewport, occlusion_counter, framebuffer}` | 同上 | **结构已知** |
| **Valhall 特有类型** | `MALLOC_VERTEX`、`FULLSCREEN`（v9 起"使用 tiling"的作业类型，见 `pan_jc.h` 的 `job_uses_tiling()`） | [refs](gpu-hw-emu-refs.md) §1 | **新增待解项（此前未列）** |
| **Compute Job** | payload 尾部为 `mali_compute_fbd`（16 B 未知域）+ `mali_vertex_tiler_prefix` 的工作组/下发维度；shader 经 `shader_meta` | 同上 | **结构已知** |
| Null(1) / Set-Value(2) / Cache-Flush(3) | `mali_payload_set_value{out, unknown}`；Cache-Flush 原文为 "unseen in the wild" | 同上 | **易（低风险）** |
| Renderpass 增量渲染（`END_RENDERPASS` 四链） | 结构在 uapi（`base_jd_fragment`，`mali_base_jm_kernel.h:475-523`）；**语义**（何时选哪条链）在 kbase（`mali_kbase_jm_hw.c:116-168`） | uapi + kbase 本地可读 | **可做** |

### 2.2 J-S 着色层：Mali Valhall v9 ISA 的执行（**唯一的研究级**）

| 问题 | 现状 |
|---|---|
| ISA **编码**（指令位域）有公开资料吗？ | ✅ 部分：Mesa `panfrost` 的 Valhall ISA 表（[ISA.xml](https://github.com/intel-lgci-fdo-gitlab-mirror/mesa.mesa/blob/93339463/src/panfrost/compiler/bifrost/valhall/ISA.xml)）与 [Valhall ISA 文档](https://people.collabora.com/~alyssa/Valhall-Documentation.pdf)；Collabora 的 [G78 反向工程文章](https://www.collabora.com/news-and-blog/news-and-events/reverse-engineering-the-mali-g78.html) |
| ISA **语义**（每条指令做什么）有成体系描述吗？ | ⚠️ 只能从 panfrost 编译器的**正向**实现反推（NIR→ISA 的每条生成规则）+ 反汇编器；**没有"ISA→高层 IR/宿主"的现成路径** |
| 有 **ISA → SPIR-V/NIR/LLVM** 翻译器吗？ | ❌ **无公开实现**（round 1 外部调研结论） |
| 宿主端能接受什么？ | Apple **Air 无公开汇编器** → 只能 **SPIR-V →（MoltenVK）→ Metal**；宿主 dist 里有 `libMoltenVK.dylib`，**没有** SPIR-V 工具链（需自备 SPIR-V 生成器） |
| 结论 | **研究级**：需要（a）ISA 反汇编/解码器（可基于 ISA.xml 写），（b）控制流/寄存器/SSA 重建，（c）类型与内存模型恢复，（d）SPIR-V 生成。工作量按人年计；**无先例** |
| **路线 C 下的变化** | **同一 ISA 家族（v9）→ 二进制可直接执行/重编码**，J-S 从"翻译"降为"搬运" → **研究级消失** |

### 2.3 J-F 固定功能层

| 子项 | 硬件做什么 | 宿主近似方式 | 参考 | 风险 |
|---|---|---|---|---|
| **Tiler**（图元装配/裁剪/分块/polygon list） | 顶点变换后把三角形写入 tile 列表 + tiler heap | 可选：(a) 真做 tiler（写 heap/polygon list，宿主再消费）；(b) **跳过 tiler，直接整帧光栅化** | **字段落点**：`bifrost_tiler_meta`（hierarchy/width/height/heap 指针）+ `midgard_tiler_descriptor`（polygon_list/heap_start/end/weights）；**分块粒度 16×16**（[refs](gpu-hw-emu-refs.md) §2/§3） | 中（(b) 丢分块顺序/内存峰值语义，但**屏幕结果通常一致**） |
| 光栅化（viewport/scissor/MSAA/coverage） | 固定功能 | 宿主 Vulkan/Metal 管线直接给 | **字段落点**：`mali_viewport`（clip min/max + viewport0/1）；MSAA 在 FBD `mfbd_flags`/`bifrost_fb_extra` | 低 |
| ROP（blend / depth / stencil / CRC / 读写掩码） | 固定功能 + **blend shader**（Mali 支持在 ROP 阶段跑小 shader） | 宿主 blend state / depth-stencil state；blend shader 需转成宿主可执行 | **字段落点**：`mali_shader_meta`（`depth_units/depth_factor`、`alpha_coverage`、`stencil_mask_front/back`、`mali_stencil_test stencil_front/back`、`union midgard_blend`（方程或 **blend shader 指针**））；CRC 在 `bifrost_fb_extra.checksum`（每 tile 8 B） | 中（blend shader 又回到 ISA；CRC 影响像素一致性） |
| 纹理单元（采样/过滤/寻址/mip/格式解包/sRGB） | 固定功能 | 宿主采样器 + 格式转换 | **字段落点**：`mali_vertex_tiler_postfix.{texture_trampoline, sampler_descriptor, uniform_buffers}` + `mali_texture_descriptor`（layout：TILED/LINEAR/**AFBC**）+ `mali_sampler_descriptor`（filter/wrap/compare/LOD/border） | 中 |
| Varying 插值 + 系统值 | 固定功能（重心/透视校正）+ 系统值预载（R55-R62） | 宿主管线插值；系统值需按 `preload_regs` 置位 | **字段落点**：`postfix.{varyings, varying_meta, position_varying}` + `mali_shader_meta.preload_regs`（[refs](gpu-hw-emu-refs.md) §3） | 低 |

### 2.4 J-M 内存格式层

| 子项 | 事实 | 处置 |
|---|---|---|
| **AFBC**（Block Format 12/13） | 实测 RT 为 AFBC（run74 "含 AFBC solid 头"）；FBD RT 项的 `Block Format` 字段可直接判别 | **分档**：T2 只做"solid/clear"即可被 guest 正确解读；任意搬运/渲染需完整 AFBC 编解码（参考 panfrost 实现） |
| Linear / Tiled（BlockFormat 1/2） | FBD 字段可判别 | 直接按 stride/pitch 处理 |
| ZS / CRC 扩展 | FBD 指针 bit1 指示存在 ZS CRC 扩展块（64 B） | 深度/模板测试相关；先"实现但不测试" |
| tile buffer / MSAA 解析 | Mali 分块渲染的中间缓冲 | 整帧光栅化路线下可省（RT 是最终结果） |

### 2.5 J-Y 同步层

| 子项 | 事实/要求 | 处置 |
|---|---|---|
| **job 依赖（scoreboard）** | ⚠️ **执行顺序 ≠ `Next` 链顺序**：每个 job 可设 **2 个依赖**（`dep1/dep2`），执行由图依赖解析决定 | **设备模型/宿主桥接必须实现依赖解析**，否则 read-before-write 竞争（[refs](gpu-hw-emu-refs.md) §1） |
| tiler 严格有序 | 每个 tiler job 必须依赖前一个 tiler job（硬件 tiler 非线程安全） | 依赖解析中承载 |
| renderpass 四链 | kbase 依 tiler OOM 状态选链（`mali_kbase_jm_hw.c:116-168`） | 设备模型按 chain 内容执行即可（选择逻辑在 guest） |
| cache flush/invalidate | `GPU_COMMAND=CLEAN_CACHES/CLEAN_INV_CACHES` + `JS_CONFIG` 的 flush 位 | 设备模型需保证"宿主写完的 RT 对 guest 可见"（缓存一致性，见 [gpu-hw-emu.md](gpu-hw-emu.md) §5.4） |
| fence/完成回执 | `JOB_IRQ_RAWSTAT` 的完成位必须**在宿主渲染真正完成后**置位 | **T2/T3 的正确性关键** |

---

## 3. 三条 T3 落地路线（与 [gpu-hw-emu.md](gpu-hw-emu.md) §4 G4 一致）

| 路线 | 做法 | J-S 是否消失 | 工作量 | 风险 | 建议 |
|---|---|---|---|---|---|
| **T3-C（路线 C）宿主 = 同族 Mali 板** | 描述符重写 + 同族 ISA 直发/重编码；tiler/ROP 由**真 Mali 硬件**做 | **是**（同家族二进制直接可用） | 控制面（同 T1/T2）+ 桥接 1–2 人月 | 中 | **首选** |
| **T3-S ISA → SPIR-V** | 自写 Mali ISA 解码 + SSA/类型恢复 + SPIR-V 生成 → MoltenVK | 否（要写翻译器） | **人年级** | **高（无先例）** | 不建议作主线 |
| **T3-CPU ISA → 宿主 CPU** | ISA 解释器/JIT 到 arm64，软件光栅 | 否（但要写执行器而非翻译器） | 人年级 | 高 | 仅作**正确性参照**（golden image），不满足"桥接宿主 GPU" |

---

## 3.5 ISA 翻译的性能剖析（"翻译"其实是两种东西，结论相反）

| 含义 | 做法 | 稳态性能量级（估计） | 瓶颈在哪 |
|---|---|---|---|
| **T3-S 编译式翻译（AOT/缓存）** | Mali ISA → 反编译 → LLVM IR/SPIR-V → 宿主 GPU 机器码，**编译一次、按 shader hash 缓存** | **宿主原生的 0.3–0.8×**（宽松估计；宿主 M4 Pro 16 核 GPU 对 Mali-G57 MC2 有巨大算力余量，**即使 2–4× 损失也可能比原机快**） | ① 翻译覆盖/正确性；② **没有宿主等价物的特性**：tile buffer / 可编程混合（Mali 的 ROP blend shader、"framebuffer fetch"）——Metal 侧只能靠 imageblock/光栅顺序组部分映射；③ AFBC/CRC（事务消除）语义；④ 驱动路径的 **CPU 开销**（描述符解析、页表遍历、逐 job 提交） |
| **T3-CPU 解释执行** | Mali ISA 在宿主 CPU 上解释/JIT（软件光栅或逐 shader 解释器） | **差 1–2 个数量级**（1080×2400 的 UI 通常只有个位数 fps） | 失去 GPU 并行度；只适合做**正确性 golden**，不适合交付 |

**支撑判断的既有事实（本会话已确证）**：

1. **翻译目标端只能是 SPIR-V**：Apple Air 无公开汇编器（[refs](gpu-hw-emu-refs.md)），所以必须走 SPIR-V → MoltenVK → Metal；
2. **Apple GPU 也是 TBDR**：Mali 的"分块"语义在 Metal 上由硬件自带（Metal 内部就是 tile-based）→
   "跳过 tiler、整帧光栅化"这条简化路线在 Apple 宿主上**仍能享受 tile 带宽优势**，这比在桌面式 GPU（IMR）宿主上做要划算；
3. **Android UI 是 draw-call 密集型**：性能风险不只是 shader 执行，而是**每帧数千次小绘制**的 CPU 侧翻译/提交开销
   → 必须做 shader 缓存 + 批量提交，否则 CPU 端先饱和；
4. **参照物（同类问题的公开结果，仅作量级参照，非 Mali 实测）**：API/IR 级翻译（Zink、DXVK/Proton、MoltenVK、SPIRV-Cross）
   普遍能到宿主原生的 0.5–1.0×——**说明"翻译到同一执行模型"本身不必然慢**；
   真正昂贵的是**跨执行模型 + 需要反编译硬件二进制**这一类（本项目面对的情况），公开**没有** Mali 的可比数据。

**结论**：**"ISA 翻译性能差"这个判断要分开看** ——
**编译式翻译（T3-S）不是性能问题、是正确性/覆盖与特性等价问题**（且有宿主余量兜底）；
**CPU 解释执行（T3-CPU）才是真慢**（1–2 个数量级，只作参照）。
而**路线 C（同族 Mali 宿主）根本没有 ISA 翻译这一层** → 性能即宿主原生，这也是它作为首选的原因之一。

---

## 4. 取证清单（下一轮可执行，**全部只读**；标 🔒 者需用户授权才动代码）

| # | 要拿到什么 | 怎么拿 | 判据 |
|---|---|---|---|
| 1 | 链上每种 job 的 **Type 分布**（全集而非样本） | 🔒 在现有 16d 探针里加一行打印 job Type（不改逻辑） | dmesg 里出现 type ∈ {1,2,3,4,5,6,7,8,9} 的清单 |
| 2 | **Tiler Job 的前 256 B** 原始字节 | 🔒 同探针扩展（当前只 dump Fragment 链） | 与 panfrost genxml 的 tiler 结构逐字段对照成功 |
| 3 | **FBD 里的状态字段全集**（blend/depth/MSAA 等） | 已有 FBD dump（16d 已取前 128 B）；扩到完整 FBD | 与 panfrost 的 `framebuffer` genxml 字段对齐 |
| 4 | ZS CRC 扩展块是否存在 | FBD 指针 bit1 统计 | 统计值（0/1）分布 |
| 5 | 是否出现 `FS_AFBC` / `START/END_RENDERPASS` | 同 #1 的 atom 摘要统计 | core_req 直方图 |
| 6 | 纹理/采样器状态与 shader 描述符指针位置 | 需要更深的链遍历 + panfrost 对照 | 能在链上定位到"shader 二进制指针" |
| 7 | panfrost 各描述符结构的**权威字段表** | 外部取证（Mesa 源码/genxml 文档） | 字段 × 偏移 × 语义表 |

> #1–#6 属"探针增强"，全部是**加打印不改逻辑**；按用户"只出方案文档、不改代码"的指令，**暂缓执行**，等授权。

---

## 5. go/no-go 与建议

1. **在 Apple Silicon 宿主上证**：T3 **NO-GO**（J-S 无公开工具链、无先例、无 oracle 三重叠加）。
2. **在"宿主 = 同族 Mali"（路线 C）下**：T3 退化为 J-D/J-F/J-M/J-Y 的**工程问题**（因为真硬件承担 tiler/ROP/ISA），**GO**。
3. **中间档（可交付）**：T2（clear/fill/copy/搬运）在两条宿主上都是 **GO**，且能覆盖相当一部分"UI 可见"内容（纯色/清屏/图层搬运）；
   要把**文字/图标/图片**也出对，必须进入 T3 的 J-S，或走路线 C。
4. **建议顺序**：先按 [gpu-hw-emu-spec.md](gpu-hw-emu-spec.md) 落 **T1→T2**（QEMU 设备模型骨架 + 宿主 fill/copy），
   同时做 §4 的**只读取证**（尤其 #1/#2/#7），**再**决定是否投入路线 C 或 T3-S。

---

## 附录 · 外部来源（已核实 URL）

| 主题 | 来源 |
|---|---|
| Valhall ISA 文档（反向工程） | <https://people.collabora.com/~alyssa/Valhall-Documentation.pdf> |
| Mesa Valhall ISA 表（编码） | <https://github.com/intel-lgci-fdo-gitlab-mirror/mesa.mesa/blob/93339463/src/panfrost/compiler/bifrost/valhall/ISA.xml> |
| Collabora 反向工程 Mali-G78 | <https://www.collabora.com/news-and-blog/news-and-events/reverse-engineering-the-mali-g78.html> |
| Mesa Panfrost 支持矩阵（v9 无 Vulkan） | <https://docs.mesa3d.org/drivers/panfrost.html> |
| Apple Air 无公开汇编器 / metal-shaderconverter 入口 | <https://github.com/o3de/sig-graphics-audio/blob/main/rfcs/AppleShaderConverterIntegration/AppleShaderConverterIntegration.md> |
| 宿主桥接踩坑（venus/AHBF/fence） | <https://glasshaven.github.io/Haven/gpu-acceleration.html> |
| 无真硬件做 oracle 的风险 | <https://www.collabora.com/news-and-blog/blog/2022/01/27/writing-an-open-source-gpu-driver-without-the-hardware/> |

> 说明：Mesa panfrost genxml 的**具体文件清单与字段表**（Tiler/Compute Job、状态描述符位置）正在外部取证中，
> 结论到齐后补入 §2.1/§2.3；本文件的**分层判定**不依赖该细节（J-S 的结论来自"无 ISA 翻译器"这一已确证事实）。
