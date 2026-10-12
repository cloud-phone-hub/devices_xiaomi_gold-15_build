# gold-15：厂商 so → 内核驱动的数据面（绘制路径 ABI）

> 回答："vendor 层的 so 在绘制图像时，把什么数据传给内核驱动？"
> 适用前提（用户已定）：**用户态 vendor so 不改**、**内核驱动可改（含 QEMU）**、**渲染工作交宿主 GPU**。
> 本文把这个前提下**内核侧必须接住的数据面**逐项钉死（含 in-tree uapi 的 file:line 与项目实测）。
> 关联：[gpu-hw-emu-spec.md](gpu-hw-emu-spec.md)（设备模型规格）、[gpu-hw-emu-refs.md](gpu-hw-emu-refs.md)（链上结构外部权威）。

---

## 0. 一句话答案

**内核拿到的不是"画什么"的描述，而是"指针 + 元数据"**：

| 通道 | 内容 | 谁写 | 内核/设备模型看到的形式 |
|---|---|---|---|
| **A. ioctl（控制面）** | 提交意图：`jc`（job chain 的 **GPU VA**）、`core_req`、依赖、导入的 dma-buf 表、优先级/槽位 | 厂商 so 通过 `/dev/mali0` | 一次 `JOB_SUBMIT` 里的 **atom 数组**（每项 64/72 B）——**只有地址，没有像素命令** |
| **B. GPU 内存（数据面）** | 真正的绘制内容：**job 描述符链、FBD、RT、shader 二进制、顶点/索引缓冲、纹理/采样器、viewport/blend/depth 状态** | 厂商 so 自己填（DDK 内含 shader 编译器） | 内核只能**按 GPU VA 经 kbase 页表去读**——读出来才知道要画什么 |

→ 所以"内核可改 + 宿主 GPU 干活"的**全部工作就是**：接住 A（ioctl/状态机），读 B（页表遍历 + 结构解析），
把 B 的语义翻译成宿主 GPU 的调用。**T2 只需要 B 的一小部分（FBD/RT），T3 需要 B 的全部（含 Mali ISA shader）。**

---

## 1. 通道 A：`/dev/mali0` 的 ioctl 面（控制面）

### 1.1 绘制前：内存与上下文

| ioctl | 传入 | 返回 | 依据 |
|---|---|---|---|
| `KBASE_IOCTL_MEM_ALLOC` | `va_pages`、`commit_pages`、`extension`（每次 GPU fault 增长页数）、`flags` | `gpu_va`（**GPU 虚拟地址**）、`flags` | `include/uapi/gpu/arm/midgard/mali_kbase_ioctl.h`（`union kbase_ioctl_mem_alloc`，ioctl 5） |
| `KBASE_IOCTL_MEM_IMPORT` | `flags`、`phandle`（外部内存句柄）、`type`、`padding` | `gpu_va`、`va_pages` | 同文件（ioctl 22）——**dma-buf → GPU VA** 的入口 |
| `KBASE_IOCTL_MEM_ALIAS` / `MEM_COMMIT` / `MEM_QUERY` / `MEM_FREE` / `MEM_SYNC` / `MEM_FLAGS_CHANGE` / `MEM_EXEC_INIT` / `MEM_JIT_INIT` / `MEM_FIND_CPU_OFFSET` / `MEM_FIND_GPU_START_AND_OFFSET` | — | — | 同文件（ioctl 6/7/15/20/21/23/38/14/16/…） |
| `KBASE_IOCTL_SET_FLAGS`、`GET_GPUPROPS`、`GET_DDK_VERSION`、`GET_CONTEXT_ID` | 上下文/能力 | 属性缓冲、版本 | 同文件（ioctl 1/3/13/17） |
| `KBASE_IOCTL_STREAM_CREATE` / `FENCE_VALIDATE` / `SOFT_EVENT_UPDATE` | 同步对象 | — | 同文件 + `jm/mali_kbase_jm_ioctl.h:203+` |

**要点**：内存接口给出的只是 **GPU VA**；页面/页表由 kbase 管理（我们的设备模型要**照着 kbase 页表遍历**，见 [gpu-hw-emu-spec.md](gpu-hw-emu-spec.md) §4.1）。

### 1.2 绘制时：**JOB_SUBMIT**（唯一的主提交入口）

```c
/* include/uapi/gpu/arm/midgard/jm/mali_kbase_jm_ioctl.h:187-199 */
struct kbase_ioctl_job_submit {
        __u64 addr;        /* 用户态数组地址：struct base_jd_atom_v2 或 v1 base_jd_atom */
        __u32 nr_atoms;    /* 数组中 atom 个数 */
        __u32 stride;      /* sizeof(...) —— 厂商 UK 11.46 实测 = 72 */
};
#define KBASE_IOCTL_JOB_SUBMIT _IOW(KBASE_IOCTL_TYPE, 2, struct kbase_ioctl_job_submit)
```

### 1.3 atom 布局：**厂商发的是 v1 形状（`base_jd_atom`，带 `seq_nr`）**

文档字段与偏移（`jm/mali_base_jm_kernel.h:650-695`；括号内为**项目实测**确认值，见 [issue-gold15-vendor-gpu/solution.md](evidence/issue-gold15-vendor-gpu/solution.md) §12.4）：

| 偏移 | 字段 | 含义 |
|---|---|---|
| `0x00` | `seq_nr` | 逻辑分组序号（**实测恒 0**） |
| `0x08` | **`jc`** | **job chain 的 GPU VA**（★实测 `0x5effb5d780` 等 = SAME_VA）；若带 `END_RENDERPASS`，这里是**CPU 地址**（指向 `base_jd_fragment` 四链） |
| `0x10` | `udata`（16 B） | 用户数据（实测堆指针） |
| `0x20` | `extres_list` | 外部资源（导入的 dma-buf）数组地址 |
| `0x28` | `nr_extres` | 外部资源数（★实测；JIT 分配时=分配数） |
| `0x2A` | `jit_id[2]` | JIT 分配 ID（UK 11.20+） |
| `0x2C` | `pre_dep[2]` | 前依赖：`{atom_id, dependency_type}`（**依赖图**，与链内 scoreboard 是两套） |
| `0x30` | `atom_number` | ★实测：atom 序号 |
| `0x31` | `prio` | ★实测：优先级 |
| `0x32` | `device_nr` | core group（`SPECIFIC_COHERENT_GROUP` 时） |
| `0x33` | `jobslot` | 指定槽（`JOB_SLOT` 时） |
| `0x34` | **`core_req`** | ★实测：`0x1`=FS、`0x4e`=CS\|T\|CF\|COHERENT_GROUP、`0x209/0x20a`=JIT_ALLOC/FREE |
| `0x38` | `renderpass_id` | 关联 START/END_RENDERPASS |
| `0x39` | `padding[7]` | 0 |

> 口径说明：项目早期把 `+0x18` 记作 "extres_list"，按 uapi 该位置实为 `udata[1]`（`udata` 占 0x10-0x1F）；
> `extres_list` 在 `0x20`。**字段以 uapi 为准**，实测已确认的偏移（`jc@0x08`、`nr_extres@0x28`、`atom_number@0x30`、`prio@0x31`、`core_req@0x34`）与 uapi 完全一致。
| `0x40` | **+8 B 尾部（厂商版本）** | 文档 v1 结构止于 0x40；厂商 `stride=72` ⇒ **v1 64 B + 8 B 尾部**（实测 +0x38..0x47 全 0） |

> **ABI 结论**：内核必须**同时接受 64 B 与 72 B stride**（我们现用补丁 12/12b/12c 正是为此），
> 且解析前 64 B 时要**按 v1 布局**（`jc@0x08`），否则字段整体错位（现状 run69-70 的教训）。

### 1.4 内核收到 atom 后做什么（设备模型要复现的寄存器动作）

```
写 JS_HEAD_NEXT  = jc（GPU VA）
写 JS_AFFINITY_NEXT / JS_CONFIG_NEXT（as_nr、flush 位、JOB_CHAIN_FLAG、THREAD_PRI）
写 JS_COMMAND_NEXT = JS_COMMAND_START(0x01)
→ 硬件（或我们的设备模型）沿页表读 job chain 执行
→ 完成后置 JOB_IRQ_RAWSTAT bit js（失败则 bit js+16）并置 JS_STATUS
```
（客机侧依据：`kbase/backend/gpu/mali_kbase_jm_hw.c:195-319`、`:365-570`；宿主同构：`panfrost_job.c:192-236`）

### 1.5 完成后回给 so 的数据

| 数据 | 形态 | 依据 |
|---|---|---|
| 完成事件 | **`read()`/`poll()` `/dev/mali0` 得到 `base_jd_event_v2{event_code, atom_number, udata}`** | `jm/mali_base_jm_kernel.h:963-968` |
| 软事件 | `SOFT_EVENT_UPDATE{event, new_status, flags}` 与软作业（fence/event/JIT）由 kbase 内部实现 | `jm/mali_kbase_jm_ioctl.h:203+`；`mali_kbase_softjobs.c:1476-1593` |
| 同步 | dma-buf fence / `STREAM_CREATE` + `FENCE_VALIDATE` | `mali_kbase_ioctl.h`（ioctl 24/25） |

---

## 2. 通道 B：GPU 内存里的"作业内容"（数据面，**真正决定画什么**）

### 2.1 结构树（全部经 GPU VA 引用，内核必须自己遍历）

```
atom.jc ──► job chain（GPU 内存）           ← 依赖 scoreboard（执行顺序≠next 链）
   ├─ JOB_HEADER: type@0x10:1 / barrier / index / dep1@0x14 / dep2@0x16 / next@0x18(u64)
   ├─ FRAGMENT payload: min_tile@0x20, max_tile@0x24, FBD@0x28
   ├─ TILER/VERTEX payload: prefix(draw_mode/计数/indices) + tiler{tiler_meta} + postfix
   │     └─ postfix: shader_meta / uniforms / attributes / attribute_meta / varyings /
   │                 viewport / texture_trampoline / sampler_descriptor / framebuffer
   └─ COMPUTE payload: 工作组维度 + mali_compute_fbd
FBD（fragment 输出描述）
   ├─ width/height、rt_count、clear 值、mfbd_flags
   ├─ tiler descriptor: polygon_list / heap_start / heap_end / weights
   ├─ [bifrost_fb_extra]: CRC(每 tile 8B) / depth-stencil（linear 或 AFBC）
   └─ RT 数组（每项 64 B）: Base(u64@0x20) / Row Stride(u32@0x28) / Surface Stride(u32@0x2c)
                            + format / block format（12=AFBC、13=AFBC Tiled）/ AFBC metadata
mali_shader_meta ──► **shader 二进制指针（Mali ISA）+ 采样器/纹理/属性计数
                      + depth/stencil/blend/alpha-coverage + preload_regs（系统值）**
纹理/采样器描述符、顶点/索引缓冲、uniforms ──► 各自由 postfix 指针界定
```

字段级权威与外部交叉验证见 [gpu-hw-emu-refs.md](gpu-hw-emu-refs.md)（§2 表 + §1 scoreboard）。

### 2.2 关键性质（决定了"能不能交给宿主 GPU"）

| 性质 | 含义 |
|---|---|
| **内容全是"数据"，不是"命令"** | shader 是**编译好的 Mali ISA 二进制**（DDK 在线编译产生）；顶点数据是裸缓冲；状态是描述符字段 |
| **指针是 GPU VA** | 必须用 kbase 页表翻译（4 级、ATE/PTE 编码，见 spec §4.1），设备模型才能读到 |
| **执行顺序由依赖决定** | 链上 `dep1/dep2` 构成 scoreboard；宿主 GPU 自己能解析 ⇒ 桥接时**原样搬运即可**（[routeC](gpu-hw-emu-routeC.md) §2.2） |
| **分块渲染** | 16×16 tile；tiler job 产出 polygon list + tiler heap；fragment job 消费 FBD |
| **完成必须由宿主回执** | 只有宿主真正写完 RT，才能置 `JOB_IRQ_RAWSTAT`（否则 guest 读到脏数据） |

---

## 3. 在"so 不改 / 内核可改 / 宿主 GPU 干活"下的三条实现形态

| 形态 | 内核侧做什么 | 需要解析通道 B 的哪些部分 | 出画 |
|---|---|---|---|
| **① 内核内虚拟后端**（现状 NO_MALI 的升级） | 用我们自己的模块提供 mali0 语义；省掉 MMIO 仿真 | **T2：chain → Fragment Job → FBD → RT** | 部分（清屏/搬运） |
| **② QEMU 设备模型**（真"硬件仿真"） | MMIO 窗口 + JM 槽状态机 + 3 中断 + 页表遍历 | 同 ①，且必须有页表遍历（设备模型看客机内存） | 同上 |
| **③ 桥接到宿主 GPU** | 把 ①② 解析出的作业交给宿主：`fill/copy/clear`（Vulkan）或**同族 Mali 转发**（路线 C） | T2 子集；T3 还要 shader/state/texture | T2 部分 / T3 完整（同族宿主） |

**必须实现的最小数据面（T1+T2）**：`SET_FLAGS` / `GET_GPUPROPS` / `MEM_ALLOC` / `MEM_IMPORT` / `JOB_SUBMIT`（64+72 B stride）
+ `read/poll` 事件 + 页表遍历 + `JOB_HEADER/FRAGMENT payload/FBD/RT` 解析 + `JOB_IRQ`/`RESET_COMPLETED` 中断时序。

**T3 的增量**：`TILER/VERTEX/COMPUTE` payload、`mali_shader_meta`（shader + blend/depth/stencil + preload）、
纹理/采样器、顶点属性、AFBC 编解码、CRC（事务消除）——**其中 shader 是 Mali ISA，公开无 ISA→宿主 翻译器**。

---

## 4. 证据索引（本地可复现）

| 事实 | 文件:行 |
|---|---|
| JOB_SUBMIT ioctl 结构（addr/nr_atoms/**stride**） | `kbase/include/uapi/gpu/arm/midgard/jm/mali_kbase_jm_ioctl.h:187-199` |
| `base_jd_atom`（v1，带 seq_nr）字段与顺序 | `kbase/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h:650-695` |
| `base_jd_atom_v2` 差异（UK 10.2/10.3/11.20 演进） | 同文件 `:591-648` |
| `base_jd_fragment`（END_RENDERPASS 四链，**CPU 地址**） | 同文件 `:475-523` |
| `base_jd_event_v2`（完成事件） | 同文件 `:963-968` |
| 内存 ioctl（`MEM_ALLOC` 的 va_pages/commit_pages/extension/flags → gpu_va；`MEM_IMPORT` 的 phandle/type → gpu_va） | `kbase/include/uapi/gpu/arm/midgard/mali_kbase_ioctl.h`（ioctl 5 / 22） |
| 软事件 / FENCE / STREAM | 同文件（18/24/25）+ `jm/mali_kbase_jm_ioctl.h:203+` |
| 内核侧寄存器动作（HEAD_NEXT/CONFIG_NEXT/COMMAND_NEXT） | `kbase/backend/gpu/mali_kbase_jm_hw.c:195-319`、`:365-570` |
| 72 B atom 实测与 v1 布局定案 | [evidence/issue-gold15-vendor-gpu/solution.md](evidence/issue-gold15-vendor-gpu/solution.md) §12.4（`:809-830`）、[raw/ddk-job-mix.md](evidence/issue-gold15-gpu-jm-format/raw/ddk-job-mix.md) |
| B2 v1 已按此读出 RT 并填色（`jc→Fragment Job→FBD→RT0`） | `kernel/modules/mali_vgpu/wire-kbase.sh:920-933`；[log-pass.txt](evidence/issue-gold15-vendor-gpu/log-pass.txt)（run81-83 `filled=163200`） |
| 链上结构与状态落点（外部权威 + 交叉验证） | [gpu-hw-emu-refs.md](gpu-hw-emu-refs.md) §0-§3 |
