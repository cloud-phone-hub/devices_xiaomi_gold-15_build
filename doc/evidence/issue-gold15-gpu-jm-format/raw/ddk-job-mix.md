# DDK 实际提交的作业类型分布（本地取证，2026-10-12 round 4）

> 目的：T3（真渲染）的范围界定。**不是所有 9 种硬件作业类型都需要解**——先看厂商 DDK 在真实运行中到底提交了什么。
> 样本来源：`../issue-gold15-vendor-gpu/log-pass.txt`（run81-83 探针，限量打印）与 `log-fail.txt`。

## 1. 样本原始值

| 来源 | 原始行 | `core_req` |
|---|---|---|
| log-pass.txt（16b 摘要行） | `B2-atom-raw i=1 real jc=0x5effd82480 real req=0x4e` | `0x4e` |
| log-pass.txt（同文件注释，探针统计） | `[core_req 分布: 0x1 FS / 0x4e / 0x209 JIT_ALLOC / 0x20a JIT_FREE]` | `0x1`、`0x4e`、`0x209`、`0x20a` |
| log-fail.txt | `req=0x201` | `0x201` |

## 2. 位定义（uapi 权威）

来源：`kbase/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h`

| 宏 | 位 | 值 |
|---|---|---|
| `BASE_JD_REQ_DEP` | — | `0x0` |
| `BASE_JD_REQ_FS`（fragment shader） | 0 | `0x1` |
| `BASE_JD_REQ_CS`（compute shader） | 1 | `0x2` |
| `BASE_JD_REQ_T`（tiler） | 2 | `0x4` |
| `BASE_JD_REQ_CF`（cache flush） | 3 | `0x8` |
| `BASE_JD_REQ_V`（vertex） | 4 | `0x10` |
| `BASE_JD_REQ_EVENT_COALESCE` | 5 | `0x20` |
| `BASE_JD_REQ_COHERENT_GROUP` | 6 | `0x40` |
| `BASE_JD_REQ_SOFT_JOB` | 9 | `0x200` |
| `BASE_JD_REQ_ONLY_COMPUTE` | 10 | `0x400` |
| `BASE_JD_REQ_FS_AFBC` | 13 | `0x2000` |
| `BASE_JD_REQ_START_RENDERPASS` | 18 | `0x40000` |
| `BASE_JD_REQ_END_RENDERPASS` | 19 | `0x80000` |
| `BASE_JD_REQ_SOFT_DUMP_CPU_GPU_TIME` | — | `SOFT_JOB \| 0x1` = `0x201` |
| `BASE_JD_REQ_SOFT_JIT_ALLOC` | — | `SOFT_JOB \| 0x9` = `0x209` |
| `BASE_JD_REQ_SOFT_JIT_FREE` | — | `SOFT_JOB \| 0xa` = `0x20a` |

## 3. 解码结果与判定

| 样本 | 解码 | 类别 | 设备模型需要做什么 |
|---|---|---|---|
| `0x1` | `FS` | **硬件作业（Fragment）** | 解析 Fragment Job → FBD → RT（**布局已定案**，见 `jm-descriptor-facts.md` §3） |
| `0x4e` | `CS \| T \| CF \| COHERENT_GROUP` | **硬件作业（Tiler/Compute）** | 需解 **Tiler Job**（含 tiler heap / polygon list / 顶点阶段）——**T3 的主要未知之一** |
| `0x201` | `SOFT_DUMP_CPU_GPU_TIME` | 软作业 | **无需**（kbase 在 guest 内实现：`kbase/mali_kbase_softjobs.c:1476-1593` 的 soft job 分发表） |
| `0x209` / `0x20a` | `SOFT_JIT_ALLOC` / `SOFT_JIT_FREE` | 软作业（JIT 内存，Tiler heap 增长用） | **无需**（同上；`mali_kbase_softjobs.c:985-1086` 处理 JIT_FREE） |
| （未出现） | `V(0x10)`、`FS_AFBC(0x2000)`、`START/END_RENDERPASS` | — | 样本内未见（**不代表不会出现**：增量渲染/压缩 RT 场景才会用到） |

**结论（对 T3 的范围界定）**：
1. 硬件侧只需要覆盖 **Fragment（已解）+ Tiler** 两类，软作业全部由 guest 内核处理；
2. `V`（vertex）未单独出现——与 Mali 架构一致：**顶点着色在 Tiler Job 里**（因此 T3 的"顶点阶段"落在 Tiler 解析内）；
3. `FS_AFBC`、`START/END_RENDERPASS` 未在样本中出现，但 **RT 实测是 AFBC**（run74 "含 AFBC solid 头"），
   说明压缩由 gralloc/DDK 侧直接布置，不一定通过 `FS_AFBC` 位体现；
4. **样本局限**：探针限量（12 条 atom 摘要 + 4 条 raw）。要"全集"需扩展探针打印每种 job 的 Type（**属代码改动，按用户指令暂缓**）。

## 4. 复现

```bash
cd devices/xiaomi/gold-15/build/doc/evidence/issue-gold15-vendor-gpu
grep -o "core_req=0x[0-9a-f]*\|req=0x[0-9a-f]*" log-pass.txt log-fail.txt | sort -u
cd ../../../../kernel/modules/mali_vgpu/kbase
grep -E "^#define BASE_JD_REQ_(FS|CS|T|CF|V|SOFT_JOB|FS_AFBC|START_RENDERPASS|END_RENDERPASS) " \
  include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h
```

## 5. 与 T3 相关的三条本地事实（决定工作归属）

| 事实 | 证据 | 对设备模型的意义 |
|---|---|---|
| **JM 路径下 kbase 不管理 tiler heap** | 全树只有 CSF 侧实现（`kbase/csf/mali_kbase_csf_tiler_heap*.c`，由 CSF 固件协议驱动）；JM 侧 `grep -i "tiler_heap"` 只命中 uapi 注释与 `BASE_MEM_TILER_ALIGN_TOP` 宏 | **tiler 的堆/polygon list 语义完全由 DDK（用户态）+ 硬件承担** → 设备模型必须自己实现 tiler 行为（唯一公开参考是 panfrost 的 tiler 实现） |
| JIT 分配与 tiler heap 强相关 | `BASE_JIT_ALLOC_MEM_TILER_ALIGN_TOP`（`mali_base_jm_kernel.h:76`）、`BASE_MEM_TILER_ALIGN_TOP`（`:44`）；实测软作业 `0x209`（SOFT_JIT_ALLOC）出现在图形流程中 | DDK 通过 JIT 软作业扩 tiler heap；设备模型的 T3 需要把 tiler 写入的 heap 区域当作**硬件的输出缓冲**来对待 |
| 硬件 tiler 可越界写 heap（需处理） | uapi 注释：`mali_base_jm_kernel.h:369`（"GPU to write beyond the end of the physical pages backing the tiler heap"） | 极端情况下设备模型要复现"heap 溢出"语义（或至少不崩） |
