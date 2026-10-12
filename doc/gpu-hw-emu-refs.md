# gold-15 GPU 仿真：外部权威资料索引与关键结构事实（round 7 有界补齐）

> 用途：为 [gpu-hw-emu-t3.md](gpu-hw-emu-t3.md) 的 J-D（描述符）与 J-F（固定功能）两层提供**公开、可引用**的资料基座，
> 并对 round 2/3 的**本地实测结论做交叉验证**。
> ⚠️ 版本口径：下列"老版 Mesa `panfrost-job.h`"是 **2019 年前后**的 JM 描述符文档，
> **结构名/语义**至今有效，但**具体位域在 v9 有演进**（例如 render target 的 block format 编码）；
> 逐字段的当前权威以 Mesa `src/panfrost/genxml/v9.xml`（75 KB，Valhall）为准。

---

## 0. 交叉验证结论（最有价值的部分）

| 本地实测（round 2/3） | 外部权威 | 结论 |
|---|---|---|
| 作业类型编号 = kbase `JS<n>_FEATURES` 位号（NULL=1、SET_VALUE=2、CACHE_FLUSH=3、COMPUTE=4、VERTEX=5、GEOMETRY=6、TILER=7、FUSED=8、**FRAGMENT=9**） | panfrost `enum mali_job_type`：`NOT_STARTED=0, NULL=1, SET_VALUE=2, CACHE_FLUSH=3, COMPUTE=4, VERTEX=5, GEOMETRY=6, TILER=7, FUSED=8, FRAGMENT=9` | ✅ **两套独立来源完全一致** |
| Fragment Job：`Type u7 @ (0x10:1)`、`Next u64 @ 0x18`、FBD 指针 `u64 @ 0x28` | `mali_job_descriptor_header`：`job_descriptor_size:1 @0x10:0`、`job_type:7 @0x10:1`、`job_barrier:1 @0x11:0`、`job_index:u16 @0x12`、`dep1:u16 @0x14`、`dep2:u16 @0x16`、`next_job_64 @0x18`；随后 0x20-0x3F 是 **payload**（`mali_payload_fragment{min_tile_coord@0x20, max_tile_coord@0x24, framebuffer@0x28}`） | ✅ **我们的实测偏移被逐字段确认**（0x28 的 "FBD 指针" 就是 `mali_payload_fragment.framebuffer`） |
| 可执行缓冲不能跨 **16 MB**（宿主 panfrost 的约束，PC 24 位） | `panfrost-job.h`：*"the program counter for shaders is actually only 24 bits, so shaders cannot cross the 2^24-byte boundary… The blob handles this by allocating a 2^24 byte pool for shaders"* | ✅ **本地约束得到外部独立印证**（路线 C §3 第 2 条） |
| RT 的 block format 实测值 12=AFBC/13=AFBC Tiled（来自 genxml v9） | 老版 `enum mali_mfbd_block_format`：`TILED=0, UNKNOWN=1, LINEAR=2, AFBC=3`（2 位字段） | ⚠️ **编码随版本变化** → 以 v9 实测值为准 |

---

## 1. 作业链（job chain）语义：**scoreboard，不是简单顺序表**

来源：Mesa `src/panfrost/lib/pan_jc.h`（注释即规格）：

- **依赖**：链上每个 job 可设 **最多两个依赖**（`dependency_1`/`dependency_2`），指向**更靠前**的 job 序号；
  形成一个 **scoreboard**：job B 依赖 job A ⇒ B 在 A 完成前不会开始。
- **关键**：*"Jobs linking via next_job has no bearing on order of execution… just establishes the linked list…
  the execution order is from resolving the dependency fields instead."*
  → **设备模型/宿主桥接必须实现依赖解析，而不能只按 `next` 顺序执行**（否则 read-before-write 竞争）。
- **规则（Tiler 相关，权威）**：
  - 有 tiler job 时**必须有** WRITE_VALUE job，且 tiler 依赖它（**仅 arch ≤5 / Midgard**；
    `pan_jc_initialize_tiler()` 在 `PAN_ARCH >= 6` 时提前返回 → **Bifrost/Valhall 不发 WRITE_VALUE**，
    与我们在 gold-15 样本中"未观测到该类型"一致）；
  - vertex 与 tiler job **相互独立**；
  - **tiler job 必须严格有序**（每个依赖前一个 tiler）；
  - tiler job 依赖其数据源（若有 vertex job 则依赖它）。
- **作业类型语义**（原文）：WRITE_VALUE=通用写入（用于清零 tiling 字段）；VERTEX=跑顶点着色器；
  TILER=分块并为 fragment shader 做准备；FRAGMENT=跑片元着色器并写出；COMPUTE=跑计算着色器；
  FUSED=vertex+tiler 融合（Bifrost，隐式内部依赖）；GEOMETRY=几何着色器（**未实现**）；
  CACHE_FLUSH="unseen in the wild"。
- **Valhall（v9）新增**：`job_uses_tiling()` 在 `PAN_ARCH >= 9` 时把 **`MALLOC_VERTEX`** 与 **`FULLSCREEN`**
  也视为使用 tiling 的作业类型（另有 `INDEXED_VERTEX`，`PAN_ARCH >= 6`）→ **gold-15 的链上可能出现这两类**，
  是 R4 待解清单里此前未列出的项。
- **`next` 指针的写法**：`pan_jc` 直接改**前一个 job 的 `opaque[6..7]`**（= 0x18-0x1F，即 `next_job_64`），
  依赖写 `opaque[5]`（= 0x14-0x17，dep1/dep2） → 与上表布局一致。

---

## 2. 结构速查（J-D / J-F 层）

| 结构 | 作用 | 关键字段（节选） |
|---|---|---|
| `mali_job_descriptor_header` | 链上每条 job 的 32B 头 | `job_type`、`job_barrier`、`job_index`、`dep1/dep2`、`next_job_64`、`exception_status`、`fault_pointer` |
| `mali_payload_fragment` | **Fragment job 的 payload（0x20 起）** | `min_tile_coord`、`max_tile_coord`、**`framebuffer`（= FBD 指针，我们实测的 0x28）** |
| `bifrost_payload_tiler` / `_vertex` / `_fused` | Tiler/Vertex/Fused job 的 payload | `prefix` + (`bifrost_tiler_only` \| `bifrost_vertex_only`) + `mali_vertex_tiler_postfix` |
| `bifrost_tiler_only` | tiler 专属 | `primitive_size`、**`tiler_meta`**、`gl_enables` |
| `bifrost_tiler_meta` | **tiler 描述符** | `hierarchy_mask`、`flags`、`width`、`height`、**`tiler_heap_meta`** |
| `bifrost_tiler_heap_meta` | **tiler heap** | `heap_size`、`tiler_heap_start`、`tiler_heap_free`、`tiler_heap_end` |
| `midgard_tiler_descriptor`（内嵌于 FBD） | **分块中间产物** | `polygon_list_size`、`hierarchy_mask`、`flags`、`polygon_list`、`polygon_list_body`、`heap_start`、`heap_end`、`weights[8]` |
| `mali_vertex_tiler_postfix` | vertex/tiler 的"状态尾巴" | `position_varying`、`uniform_buffers`、`texture_trampoline`、`sampler_descriptor`、`uniforms`、**`shader_meta`**、`attributes`、`attribute_meta`、`varyings`、`varying_meta`、**`viewport`**、`occlusion_counter`、`framebuffer` |
| **`mali_shader_meta`** | **着色器 + 图形状态**（J-F 的答案） | **`mali_ptr shader`**、`sampler_count/texture_count/attribute_count/varying_count`、`uniform_count/work_count`、`depth_units/depth_factor`（polygon offset）、`alpha_coverage`、`stencil_mask_front/back`、`mali_stencil_test stencil_front/back`、`preload_regs`（系统值预载位图）、**`union midgard_blend blend`（blend 方程 或 blend shader 指针）** |
| `mali_viewport` | 视口/裁剪 | `clip_min/max x,y,z`、`viewport0/1` |
| `mali_payload_set_value` | SET_VALUE job | `out`、`unknown` |
| `bifrost_framebuffer`（FBD） | **片元输出描述（粒度=帧）** | `scratchpad`、`sample_locations`、`width1/height1`、`width2/height2`、`rt_count_1/2`、`clear_stencil`、`mfbd_flags`、**`midgard_tiler_descriptor tiler`**，可选 `bifrost_fb_extra`，随后 `bifrost_render_target[]` |
| `bifrost_render_target`（每 RT 64B） | 单个渲染目标 | `mali_rt_format format`（block/nr_channels/flags/swizzle/no_preload）、**`afbc{metadata, stride, unk}`**、`framebuffer`、`framebuffer_stride`、`clear_color_1..4` |
| `bifrost_fb_extra`（可选） | 帧级附加 | `checksum`+`checksum_stride`（**事务消除/CRC**）、`flags`、深度/模板（linear 或 AFBC 两种形态） |
| `mali_texture_descriptor` / `mali_sampler_descriptor` | 纹理/采样器 | 尺寸、format（swizzle/srgb/type/**layout：TILED=1, LINEAR=2, AFBC=0xC**）、`payload[mip][face][2]`；采样器：filter/wrap/compare/LOD/border color |
| `mali_attribute` / `attribute_meta` / `mali_uniform_buffer_meta` | 顶点属性与 UBO | 由 `mali_shader_meta` 的计数界定 |

**T3 的 J-F 层因此有了明确落点**：
- **着色器二进制指针** = `mali_shader_meta.shader`（经 vertex/tiler postfix 或 FBD 路径可达）；
- **blend/depth/stencil/alpha-coverage/polygon-offset/系统值预载** 全在 `mali_shader_meta`；
- **viewport/scissor** = `mali_viewport`；
- **tiler heap / polygon list** = `bifrost_tiler_heap_meta` / `midgard_tiler_descriptor`；
- **MSAA/clear/CRC/AFBC** = FBD + `bifrost_fb_extra` + 每 RT 的 `afbc{}`。

---

## 3. 其它有用事实

| 事实 | 来源/说明 |
|---|---|
| **分块粒度 16×16**（`MALI_TILE_SHIFT 4`），tile 坐标打包成 u32（各 12 位，`MALI_X_COORD_MASK 0xFFF` / `MALI_Y_COORD_MASK 0xFFF0000`） | 分块渲染几何 |
| **每 RT 64 B**（`bifrost_render_target`）、**FBD 头 + 可选 extra + RT 数组**的排布 | 与我们实测 "FBD = 参数 64B + padding 64B + [ZS CRC 64B] + RT 数组 64B/项" 结构一致（v9 字段名/位域以 v9.xml 为准） |
| **AFBC metadata**：每 tile 16 B 元数据，stride 以 tile 为单位；AFBC 开启时 `framebuffer_stride` 为 0 | 解释了"RT 含 AFBC solid 头"的实测现象 |
| **事务消除（Transaction Elimination）**：`bifrost_fb_extra.checksum` + `checksum_stride`（每 tile 8 B） | T3 若要求像素完全一致，需要处理 CRC（否则可能出现"应写未写"） |
| **深度/模板可 AFBC**：`ds_afbc{}`（仅 24/8 组合深度模板） | J-M 层 |
| **blend shader**：`union midgard_blend` 可以是 blend 方程**或** blend shader 指针；`MALI_HAS_BLEND_SHADER` | ⚠️ blend shader 又回到 ISA 问题（J-S） |
| **程序计数器 24 位**、blob 用 **2^24 B 的 shader 池**，内核保证分块对齐 | 与宿主 panfrost 的 16 MB 约束互相印证 |
| **系统值预载**：Bifrost+ 在 R55-R62 预载 `gl_LocalInvocationID`/`WorkGroupID`/`GlobalInvocationID`/`PrimitiveID`/`FrontFacing`/`FragCoord`/`SampleMask` 等，由 `preload_regs` 位图声明 | ISA 执行时解释器必须实现这些系统值（J-S） |

---

## 4. 权威资料位置（可直接引用）

| 内容 | 位置 |
|---|---|
| **Valhall（v9）描述符逐字段权威** | Mesa `src/panfrost/genxml/v9.xml`（75 458 B）<br><https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/panfrost/genxml/v9.xml>（镜像：<https://github.com/chaotic-cx/mesa-mirror/blob/52b5500e/src/panfrost/genxml/v9.xml>） |
| 生成出的 JM 解码器（结构+字段名一览） | `src/panfrost/genxml/decode_jm.c`（21 617 B）<br><https://github.com/chaotic-cx/mesa-mirror/blob/52b5500e/src/panfrost/genxml/decode_jm.c> |
| **作业链语义（scoreboard/依赖规则/类型语义）** | `src/panfrost/lib/pan_jc.h`<br><https://github.com/chaotic-cx/mesa-mirror/blob/52b5500e/src/panfrost/lib/pan_jc.h> |
| **JM 描述符结构文档（结构名/语义；位域偏旧）** | 老版 `src/panfrost/include/panfrost-job.h`<br><http://git.brkho.com/brkho/mesa/raw/commit/2c5ba2ee6ea68aa3062156d1a4abfc3b2556775d/src/panfrost/include/panfrost-job.h><br>（同源镜像：<https://chromium.googlesource.com/chromiumos/third_party/mesa/+/72981c92ce806d48d1d7955a119abc38968fd2c7/src/gallium/drivers/panfrost/include/panfrost-job.h>） |
| 共有枚举（blend operand/equation、format、func、色彩格式） | `src/panfrost/genxml/common.xml`<br><https://github.com/chaotic-cx/mesa-mirror/blob/52b5500e/src/panfrost/genxml/common.xml> |
| Valhall ISA 编码（J-S） | `src/panfrost/compiler/bifrost/valhall/ISA.xml`、[Valhall ISA 文档](https://people.collabora.com/~alyssa/Valhall-Documentation.pdf) |
| 宿主 panfrost 对 v9 的支持 | Mesa Panfrost 文档（支持矩阵）<https://docs.mesa3d.org/drivers/panfrost.html> |

> **离线审计副本**（结论所依赖的原文片段）：[evidence/issue-gold15-gpu-jm-format/raw/external-jm-structures.md](evidence/issue-gold15-gpu-jm-format/raw/external-jm-structures.md)。

---

## 5. 对既有文档的修订（本轮已同步）

1. **[gpu-hw-emu-t3.md](gpu-hw-emu-t3.md) §2.1**：Tiler/Compute 的"待取证"改为"**结构已定位**"：
   `bifrost_payload_tiler{prefix, tiler{bifrost_tiler_only{tiler_meta}}, postfix}` + `bifrost_tiler_meta` + `bifrost_tiler_heap_meta`；
   Fragment 布局获得**逐字段外部确认**；并新增 **Valhall 特有作业类型 `MALLOC_VERTEX`/`FULLSCREEN`**。
2. **[gpu-hw-emu-t3.md](gpu-hw-emu-t3.md) §2.3**：J-F 层从"参考 panfrost"细化为**字段级落点**（shader_meta / viewport / tiler heap / fb_extra）。
3. **[gpu-hw-emu-t3.md](gpu-hw-emu-t3.md) §2.5**：新增**依赖 scoreboard**语义（执行顺序 ≠ next 链顺序）。
4. **[gpu-hw-emu-routeC.md](gpu-hw-emu-routeC.md) §3**：24 位 PC / 2^24 shader 池获得**外部印证**。
