# gold-15 路线 C（宿主 = 同族真 Mali）桥接可行性与选型 go/no-go

> 定位：[gpu-hw-emu.md](gpu-hw-emu.md) 中**路线 C**的专项论证。它是**"stock 厂商栈 + 真出画"唯一高把握路径**，
> 也是 T3 的唯一解法（见 [gpu-hw-emu-t3.md](gpu-hw-emu-t3.md)：J-S（ISA 执行）在路线 C 下消失）。
> **本文件的全部机制性结论均在本地源码中确证**（`devices/xiaomi/gold-15/kernel/source/`，lineage 6.6.127），
> 不依赖外部资料；板卡选型/oracle 部分由外部调研补全（见 §5/§6）。

---

## 0. 结论速览：**GO**（前提 = 宿主 GPU 必须是 Valhall v9.0 同族）

| 问题 | 结论 |
|---|---|
| 宿主能否接收客机的**硬件作业链**？ | ✅ **能**——`panfrost` 的 `SUBMIT.jc` 就是 **"job 描述符的 GPU 地址"**，内核写 `JS_HEAD_NEXT` + `JS_COMMAND_NEXT=START`，与客机 kbase **寄存器级同构**（§1） |
| 能否保持客机的 GPU VA（零改写直通）？ | ❌ **不能**——宿主 GPU VA 由内核分配（`GET_BO_OFFSET`），用户态不可指定 → **必须重写链上指针**（§2.2） |
| 客机内存能否共享（零拷贝）？ | ⚠️ **分两类**：**数据面可导入共享（dma-buf）**；但 **导入的 BO 一律映射为 `IOMMU_NOEXEC`** → **着色器必须拷贝进宿主自建可执行 BO**（§2.3、§3） |
| 有什么硬约束？ | ① 导入 BO = NOEXEC；② **可执行 BO 不能跨 16 MB 边界（PC 是 24 位）**；③ GPU VA 不可保；④ `requirements` 只接受 `0`/`FS`；⑤ heap BO 2 MB 对齐且不可 mmap（§3） |
| 与既有工作的复用 | ✅ **T2 的"页表遍历 + Fragment→FBD→RT 解析"正是路线 C 指针重写所需的前半段**（§4） |
| 判定 | **GO**（工程），**但成功率取决于宿主 GPU 与客机 ISA 家族/型号的匹配度**（§5 选型） |

---

## 1. 为什么可行：宿主 panfrost 与客机 kbase 的**寄存器级同构**（本地确证）

| 环节 | 客机（kbase） | 宿主（panfrost） | 证据（本地源码） |
|---|---|---|---|
| 提交面 | `ioctl KBASE_IOCTL_JOB_SUBMIT`（atom 里带 `jc`） | `ioctl DRM_IOCTL_PANFROST_SUBMIT`（`jc` = "**Address to GPU mapping of job descriptor**"） | `kernel/source/include/uapi/drm/panfrost_drm.h:49-71` |
| 内核动作 | `JS_HEAD_NEXT = jc` → `JS_COMMAND_NEXT = START` | **完全一样** | 客机：`kbase/backend/gpu/mali_kbase_jm_hw.c:195-319`；宿主：`kernel/source/drivers/gpu/drm/panfrost/panfrost_job.c:192-236` |
| 作业类型 | `core_req` 位（FS=1<<0、CS=1<<1、T=1<<2…） | `requirements` 只接受 **`0` 或 `PANFROST_JD_REQ_FS(1<<0)`** | `panfrost_drv.c:245-248`、`panfrost_drm.h:42` |
| 槽位分配 | kbase 按 core_req 选 slot | panfrost：**JS0=fragment（FS）、JS1=vertex/tiler（其它）**、JS2=compute（当前 `#if 0` 未开放） | `panfrost_job.c:106-125` |
| 亲和性 | `JS_AFFINITY_NEXT` | `panfrost_job_write_affinity()` | `panfrost_job.c:129+` |

> **含义**：宿主 GPU 消费的**就是同一套硬件描述符格式**（因为格式由 GPU 定义，不由驱动定义）。
> 所以路线 C 的桥接**不是翻译、而是"搬运 + 指针重写"**——这是它与 T3-S（ISA→SPIR-V）的本质区别。

### 1.1 "同类型"要同到什么程度——四层匹配表

客机传来的数据面里，**描述符是硬件格式、shader 是硬件 ISA**，所以宿主必须"同类型"；但四层要求的松紧差别很大：

| 层 | 必须匹配什么 | 不匹配的后果 | 松紧 |
|---|---|---|---|
| **① 提交模型** | **JM vs CSF** 必须一致（G57=v9=**JM**；G310/G510/G710=v10+=**CSF**） | 完全是两套提交协议（CSF 要 MCU 固件 + 共享接口区），**无法搬运** | **硬要求** |
| **② ISA（shader 编码）** | **架构代际**必须一致（Bifrost v6/v7 ≠ Valhall v9 ≠ v10+）；同代内**小版本**（v9.0 / v9.2）编码可能不同 | 代际不同 → 二进制根本跑不了；同代不同小版本 → 需**同族重编码**（工程活，非研究） | **硬要求 + 小版本越近越好** |
| **③ 描述符/内存格式** | job 链、FBD、RT、tiler heap、页表格式（同族内基本一致） | 同代不同产品多数兼容；跨代不兼容 | 同族内宽松 |
| **④ 修订与 quirks** | GPU 修订（如 r0p1）、问题表（issues）、L2/tiler/线程等**能力属性** | 编出的 shader/驱动假设不同 → 隐性错误（不是立刻崩） | **越接近越好；同型号最稳** |

**配套的两个约束**（不匹配也修不掉，必须由宿主满足）：
- 宿主必须有**能收"硬件 job 链"的内核驱动**：panfrost 的 `SUBMIT.jc` 正是 job 描述符的 GPU 地址（§1），所以**宿主必须是 Mali**；
- 宿主必须接受**用户态自建/导入的内存**（§2.3 的 NOEXEC / 16 MB 规则）。

**一个容易被忽略的设计要点**：客机 DDK 会**读取设备模型上报的 GPU 能力**（L2 大小、线程数、tiler 特性…），
而**真正执行的是宿主硬件**。若上报值与宿主实际能力不一致，就会出现"DDK 按 A 编译、宿主按 B 执行"的隐性错配。
→ **选与客机同型号的宿主（G57）能同时满足"DDK 身份校验"和"能力一致"两个约束**，这是 §5 选型的第一原则。

---

## 2. 桥接架构

```
客机                                  QEMU 设备模型（Mali MMIO+JM）            宿主
────                                  ─────────────────────────────            ────
DDK → kbase(REAL_HW) → JS_HEAD_NEXT=jc ──→ 记录 atom(jc, requirements, BOs)
                                          │
                                          ├─ ① 读客机 job chain（经 kbase 页表遍历：客机 VA → 客机 PA）
                                          ├─ ② 指针重写：客机 VA →（宿主 BO 映射表）→ 宿主 GPU VA
                                          └─ ③ PANFROST_SUBMIT{jc=宿主VA, bo_handles, requirements}
                                                                    │
                        JOB_IRQ_RAWSTAT ←── 完成后回执 ←── out_sync fence（宿主完成）
```

### 2.1 宿主侧最小 ioctl 序列（本地确证）

| 步 | ioctl | 作用 | 关键返回值 |
|---|---|---|---|
| 1 | `DRM_IOCTL_PANFROST_CREATE_BO{size, flags}` | 建宿主 BO（`PANFROST_BO_NOEXEC`=1、`PANFROST_BO_HEAP`=2；**heap 必须同时带 NOEXEC**） | `handle` **与 `offset`（= GPU VA；本调用内部已建映射）** |
| 2 | `DRM_IOCTL_PANFROST_GET_BO_OFFSET{handle}` | **为（导入的）BO 建 GPU 映射并返回 GPU VA**（自建 BO 已在步 1 得到） | `offset` = `mapping->mmnode.start << PAGE_SHIFT` |
| 3 | `DRM_IOCTL_PANFROST_MMAP_BO{handle}` | 仅创建 **CPU mmap** 偏移（不是 GPU 映射）；heap BO 被拒 | `offset`（mmap 用） |
| 4 | `DRM_IOCTL_PANFROST_SUBMIT{jc, bo_handles, bo_handle_count, requirements, in/out_sync}` | 提交作业链 | `out_sync` 填完成 fence |
| 5 | `DRM_IOCTL_PANFROST_WAIT_BO` / poll `out_sync` | 等完成 | — |

证据：`panfrost_drm.h:25-31,96-130`、`panfrost_drv.c:333-380`（mmap_bo/get_bo_offset）、`panfrost_drm.h:49-71`（submit）。

### 2.2 GPU VA 翻译管线（**路线 C 的核心**）

```
客机 GPU VA ──(① kbase 页表遍历，见 gpu-hw-emu-spec.md §4.1)──→ 客机物理地址 PA
            ──(② 我们的"客机 PA ↔ 宿主 BO+偏移"映射表)────────→ 宿主 BO + 偏移
            ──(③ 宿主 BO 的 GPU VA（GET_BO_OFFSET）)──────────→ 宿主 GPU VA
```
- ①②③ 的组合是**一次纯函数式翻译**，可以对链上任意指针做；
- **必须翻译的指针（已解部分）**：Fragment Job 的 `Next`、`Payload`、`FBD`（注意**低 6 位打包位必须原样保留**）、FBD 的 RT `Base`；
  其余（Tiler/Compute Job 与其嵌套结构）属 [gpu-hw-emu-t3.md](gpu-hw-emu-t3.md) §2.1 的待取证项；
- **重写后的链放在宿主自建 BO 里**（不改客机内存）；宿主的 `jc` 指向它。
- ✅ **依赖 scoreboard 无需我们实现**：`dep1/dep2` 是链头里的 u16 **索引**，原样搬运即可，
  **由宿主 GPU 自己解析**（这正是"搬运而非翻译"的又一好处）；我们要保证的只是
  **`bo_handles` 覆盖链上引用的所有 BO** 与**提交顺序**。

### 2.3 内存分类处置（决定"共享 vs 拷贝"）

| 客机内容 | 宿主处置 | 依据 |
|---|---|---|
| **着色器二进制**（vertex/fragment/compute） | **拷贝**进宿主自建**可执行** BO（不设 `NOEXEC`） | 导入 BO 一律 `IOMMU_NOEXEC`（`panfrost_mmu.c:333`）→ 若放在导入内存里，取指会 fault |
| 可执行 BO 的尺寸/对齐 | 宿主自建；**不得跨 16 MB 边界** | `panfrost_gem.c:129-140`（PC 是 24 位，可执行缓冲按自身大小对齐以避开跨界） |
| **Tiler heap** | **宿主侧 scratch**（`PANFROST_BO_HEAP`，2 MB 向上取整、页不 pin、不可 mmap）；**尺寸可"按需增长"**：宿主 panfrost 内置 heap fault 分配器 | `panfrost_gem.c:243-253`、`panfrost_drv.c:351-355`、**`panfrost_mmu.c:439-500`（`panfrost_mmu_map_fault_addr`：fault 时按 2 MB 对齐补页、页标记 unevictable）、`:713`（AS fault 入口）**；与客机 kbase 的 `GROW_ON_FAULT` 语义**天然对齐** |
| **RT / 顶点·索引·常量缓冲 / 多边形列表数据** | **dma-buf 导入共享（零拷贝）** | 宿主支持 `.gem_prime_import_sg_table`（`panfrost_drv.c:548`）+ BO 为 shmem 背衬（`panfrost_gem.c:52`） |
| 客机内存 → 宿主 dma-buf | `udmabuf`（内核树内已有 `drivers/dma-buf/udmabuf.c`、`CONFIG_UDMABUF`）+ memfd | 本地源码确证可用 |

### 2.4 中断与完成语义

```
宿主 out_sync fence 完成 ──→ 设备模型置 JOB_IRQ_RAWSTAT 的完成位（1<<slot）
                        ──→ 清除 JS_COMMAND_NEXT（保证 kbase 的 js_free_wait 通过）
                        ──→ STATUS=0；失败则 STATUS=0x40+ 且置 (1<<(16+slot))
```
**顺序契约**：必须"宿主真正完成"之后再回执（否则客机会读到未写完的 RT）。

---

## 3. 硬约束清单（全部本地确证，逐条 file:line）

| # | 约束 | 影响 | 依据 |
|---|---|---|---|
| 1 | **导入的 dma-buf BO 一律 `noexec`** | 着色器不能放在导入内存里（取指 fault）→ 必须拷进自建可执行 BO | `panfrost_gem.c:266-272`（import 时 `bo->noexec = true`）、`panfrost_mmu.c:333` |
| 2 | **可执行 BO 不能跨 16 MB 边界**（24 位 PC） | 着色器 BO 的对齐/尺寸需按其大小对齐；大 shader 集合要拆 | `panfrost_gem.c:129-140`；**外部印证**：老版 panfrost 文档明写 PC 只有 24 位、blob 用 2²⁴ B shader 池（[refs](gpu-hw-emu-refs.md) §0/§3） |
| 3 | **GPU VA 由内核分配，不能保 VA** | 不能"直通"，必须做链指针重写（VA 来自 per-fd GPU 地址空间的 `mmnode`，用户态无法指定） | `panfrost_drv.c:104-107`（create 时取 `mmnode.start`）、`:362-380`（`GET_BO_OFFSET` 同源）、`panfrost_gem.c:110-146`（`panfrost_gem_open` 建 `mmnode`） |
| 4 | `requirements` 只接受 `0` 或 `FS(1<<0)` | 客机 `core_req=0x1` → `FS`；`0x4e`（CS\|T\|CF\|COHERENT_GROUP）→ **只能传 0**（其余位会被拒） | `panfrost_drv.c:245-248` |
| 5 | 槽位映射固定 | FS→JS0；其它→JS1；compute 槽（JS2）当前 `#if 0` **未开放** | `panfrost_job.c:106-125` |
| 6 | heap BO：2 MB 向上取整、页不 pin、**不可 mmap** | tiler heap 只能当 scratch 用，不能 CPU 访问 | `panfrost_gem.c:243-253`、`panfrost_drv.c:351-355` |
| 7 | 导入 BO 的 CPU 侧一致性 | 共享内存的 cache 维护需显式处理（见 [gpu-hw-emu.md](gpu-hw-emu.md) §5.4） | 经验性（Haven 记录） |
| 9 | **非 heap BO 的 GPU fault 是致命错误**（宿主只对 heap 做按需补页） | 数据面（RT/缓冲）必须**提交前映射完备**，否则 fault 即 `-EINVAL`/告警 | `panfrost_mmu.c:456-461`（`!bo->is_heap` → `dev_WARN` + `-EINVAL`） |
| 8 | **MT8192 的 G57 变体 GPU ID 非标准**（Arm 驱动不识别，ChromiumOS 有下游 hack） | 选宿主时避开该变体（或确认内核已有 workaround） | `panfrost_gpu.c:211-222`（注释 + `GPU_MODEL(g57, 0x9003, GPU_REV(g57,0,0))`） |

---

## 4. 与 T1/T2 工作的复用（重要：路线 C 不是从零）

| 已有工作 | 在路线 C 中的作用 |
|---|---|
| **kbase 页表遍历**（[gpu-hw-emu-spec.md](gpu-hw-emu-spec.md) §4.1：4 级、ATE/PTE 编码） | 翻译管线第 ① 步 |
| **Fragment Job → FBD → RT 解析**（[issue-gold15-gpu-jm-format](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md) §3） | 指针重写的第一批目标（也是 T2 的定位手段） |
| **作业类型枚举**（`JS<n>_FEATURES` 位号） | 判别链上 job 类型，决定重写哪些字段 |
| **实测作业类型分布**（[raw/ddk-job-mix.md](evidence/issue-gold15-gpu-jm-format/raw/ddk-job-mix.md)） | 先把 **Fragment + Tiler** 两条路径做通即可覆盖实测负载 |
| MMIO+IRQ+JS 槽状态机规格 | 客机侧提交/完成语义（与宿主 fence 对接） |

---

## 5. 宿主选型（本地已确证的支持面 + 外部调研补全）

**本地确证**（lineage 6.6.127 的 panfrost）：

| 项 | 值 | 依据 |
|---|---|---|
| Generic compatible | **`arm,mali-valhall-jm`** | `panfrost_drv.c:711` |
| MediaTek compatible | **`mediatek,mt8186-mali`**（MT8186 = **Mali-G57 MC2**，与 gold-15 同配置）、`mediatek,mt8183-mali`（G72，非同族）、`mediatek,mt8192-mali` | `panfrost_drv.c:662-714` |
| 已知 G57 模型 | **`g57 (0x9001)`**、`g57 (0x9003，MT8192 变体)` | `panfrost_gpu.c:209-222` |
| MT8186 平台数据 | 2 个 PM domain（`core0`/`core1`）+ 供电名 | `panfrost_drv.c:677-683` |

**选型原则**：宿主 GPU 必须是 **Valhall v9.0 同族**（shader 二进制才可直接复用）；**同型号（G57）最佳**；
CSF 架构（如 G610/v10）**不行**（ISA 与提交模型都不同）。

> 候选板卡清单、可购买性、内核/发行版成熟度、oracle 方案：**外部调研进行中**（本轮已派专门任务），结论到齐后补入本节的候选表。

---

## 6. oracle（正确性对照）方案

| 方案 | 做法 | 优点 | 风险 |
|---|---|---|---|
| **O1 同款真机**（MT6833 手机，Android 原厂栈） | 用真机跑同一 App 场景，抓帧做 golden | 端到端最真实 | 抓帧/对齐成本；无法直接看 GPU 中间态 |
| **O2 同族板卡 + Linux + panfrost（即路线 C 宿主本身）** | 在宿主上直接跑 Mesa panfrost 的例子与寄存器回读 | 可直接对比 job 级行为 | 宿主就是被验证对象，不是独立 oracle |
| **O3 同族板 + Android 原厂栈** | 板子跑 Android（若厂商提供），用其原厂 DDK 出图 | 与客机栈同源 | 板卡多无 Android BSP |
| O4 无 oracle（纯 in-vitro） | — | — | **不可取**（[Collabora 的经验](https://www.collabora.com/news-and-blog/blog/2022/01/27/writing-an-open-source-gpu-driver-without-the-hardware/)：正确性无法收敛） |

**建议**：**O1 + O3 组合**——O3（若可得）用于 job 级对照，O1 用于端到端像素对照。

---

## 7. 风险与回退

| # | 风险 | 影响 | 缓解/回退 |
|---|---|---|---|
| 1 | 宿主 GPU 与客机**不同型号**（同族但 rev 不同） | shader 二进制可能因 quirks/ISA 小版本失效 | 选同型号（G57）；必要时对 shader 做**同族重编码**（比 ISA→SPIR-V 简单一个数量级） |
| 2 | 链上指针**未被完全识别** | 翻译遗漏 → GPU fault | 增量：先 FS 路径（已解），再 Tiler（T3-J-D 待取证）；用 fault 码（`AS_FAULTSTATUS`）定位 |
| 3 | 导入内存的一致性/可见性 | 偶发花屏/错帧 | 显式 cache 维护 + 完成 fence 严格顺序 |
| 4 | 宿主 panfrost 的 `requirements`/槽位限制 | 客机的 compute/coherent-group 语义无法直接表达 | 先只支持 FS+Tiler；compute 走宿主 JS1 或另立项 |
| 5 | tiler heap 语义差异（客机 DDK 是否回读堆） | 若 DDK 回读，host-only scratch 会不一致 | 待确认（低风险）；必要时把 heap 改成"导入 + 拷贝回" |
| 6 | 宿主不是 Apple Silicon | 生产形态变化（双轨：Mac 开发 / ARM 板生产） | 已在 [gpu-hw-emu.md](gpu-hw-emu.md) §5.1 记录 |

---

## 8. go/no-go 判定

| 判据 | 结论 |
|---|---|
| 宿主能否执行客机作业链（无需 ISA 翻译） | ✅ 能（§1 寄存器级同构） |
| 桥接是否有明确的工程路径 | ✅ 有（§2 VA 翻译管线 + 内存分类处置） |
| 硬约束是否都可满足 | ✅ 可（§3；代价是"着色器要拷贝 + 链要重写"） |
| 是否依赖未解的研究级问题 | ❌ **不依赖**（ISA 翻译层被同族硬件取代） |
| **总判定** | **GO**（工程），**前置条件 = 选定 Valhall v9.0 同族宿主 + 解决 oracle** |

---

## 附录 · 本地证据索引（全部可复现）

| 事实 | 文件:行 |
|---|---|
| panfrost `SUBMIT.jc` = job 描述符 GPU 地址 | `kernel/source/include/uapi/drm/panfrost_drm.h:49-71` |
| requirements 只接受 0/FS | `.../panfrost_drv.c:245-248`、`panfrost_drm.h:42` |
| 宿主写 `JS_HEAD_NEXT`/`JS_COMMAND_NEXT=START` | `.../panfrost_job.c:192-236` |
| 槽位：FS→JS0、其它→JS1 | `.../panfrost_job.c:106-125` |
| GPU VA：`CREATE_BO` 即返回、`GET_BO_OFFSET` 为导入 BO 补建 | `.../panfrost_drv.c:101-113`、`:362-380` |
| heap BO 必须 `NOEXEC` | `.../panfrost_drv.c:92-95` |
| 导入 BO → `noexec` → `IOMMU_NOEXEC` | `.../panfrost_gem.c:266-272`、`.../panfrost_mmu.c:333` |
| 可执行 BO 不跨 16 MB（24 位 PC） | `.../panfrost_gem.c:129-140` |
| heap BO：2 MB 对齐、不可 mmap、需 NOEXEC | `.../panfrost_gem.c:243-253`、`.../panfrost_drv.c:92-95,351-355` |
| **heap 按需补页（tiler heap 扩容）** | `.../panfrost_mmu.c:439-500`、`:713` |
| 非 heap fault 致命 | `.../panfrost_mmu.c:456-461` |
| G57 模型与 MT8192 变体 | `.../panfrost_gpu.c:209-222` |
| `arm,mali-valhall-jm` / `mediatek,mt8186-mali` | `.../panfrost_drv.c:711,714` |
| dma-buf 导入支持 | `.../panfrost_drv.c:548` |
| udmabuf 可用 | `kernel/source/drivers/dma-buf/udmabuf.c`、`drivers/dma-buf/Kconfig:33` |
