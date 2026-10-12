# gold-15 GPU 硬件仿真设备模型规格（T1/T2 可落地）

> 定位：本文件是 [gpu-hw-emu.md](gpu-hw-emu.md) 中**路线 A/B**（QEMU/vhost-user 里的 Mali-JM 设备模型）的
> **实现规格**——"照着它就能写出设备模型骨架"，不含任何代码改动。
> 依据来源（全部已固化进证据目录）：
> - 寄存器权威集合：[raw/kbase-regmap-v9_0.md](evidence/issue-gold15-gpu-hw-emu/raw/kbase-regmap-v9_0.md)（**691 条**，机器抽取）
> - 行为参考实现：`kbase/backend/gpu/mali_kbase_model_dummy.c`（ARM 上游 `CONFIG_MALI_NO_MALI` 软模型）
> - 作业格式：[issue-gold15-gpu-jm-format](evidence/issue-gold15-gpu-jm-format/solution.md)（round 2 定案）
> - 硬件契约（DT/中断/时钟）：[raw/official-dtb-gpu-nodes.txt](evidence/issue-gold15-gpu-hw-emu/raw/official-dtb-gpu-nodes.txt)
> - 现状判据（对照）：[issue-gold15-vendor-gpu](evidence/issue-gold15-vendor-gpu/solution.md)（B2 v1 填色、run81-83）

---

## 0. 规格速览

| 维度 | 规格 | 依据 |
|---|---|---|
| 设备类型 | QEMU `sysbus`（内建，调试快）或 **vhost-user 独立进程**（生产推荐，崩溃不拖垮 QEMU） | [gpu-hw-emu.md](gpu-hw-emu.md) §3.1 |
| MMIO 窗口 | **至少 0x10800**（建议 0x20000 对齐）@ `0x13000000` | 本文件 §1.2（**新发现**：stage-2 MMU 在 0x10000+，超出官方 DT 声明的 0x4000） |
| 中断 | **3 条**（`JOB`/`MMU`/`GPU`，by name，level-high，共享）；GIC SPI **366/365/364** | `kbase/mali_kbase_core_linux.c:439-475`、官方 DTB |
| 寄存器条目 | **691**（GPU_CONTROL 82 / JOB_CONTROL 313 / MMU_STAGE1 148 / MMU_STAGE2 148） | [raw/kbase-regmap-v9_0.md](evidence/issue-gold15-gpu-hw-emu/raw/kbase-regmap-v9_0.md) |
| 需实现"行为"的寄存器 | **约 60 个**（其余为常量/存储回读） | 本文件 §2.2 |
| GPU 身份 | `GPU_ID=0x09030010`（= product_id `0x09000003`、r0p1）；`THREAD_FEATURES[31:30]=SOFTWARE` | `wire-kbase.sh:114-145`、`model_dummy.c:1852-1855` |
| job slots / AS | `JS_PRESENT=0x7`（3 槽）、`AS_PRESENT=0xFF`（8 个 AS） | `model_dummy.c:1779-1783` |
| 数据面 | GPU VA→guest PA 页表遍历 + job chain 遍历 + Fragment→FBD→RT 解析 | 本文件 §4；round 2 定案 |
| 出画档位 | **T1**（假完成，保活）/ **T2**（RT fill/copy/clear 由宿主 GPU 执行） | 本文件 §5 |

---

## 1. 外部接口规格

### 1.1 设备形态

| 方案 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| **QEMU 内建 sysbus** | MMIO/IRQ 直接接 GIC，调试最快；可用 `MemoryRegion` 精确建模 | 渲染逻辑进 QEMU 进程（崩溃影响面大） | **原型阶段** |
| **vhost-user 独立进程** | 与现有 `vhost-user-gpu`/`vhost-user-media-pci` 同构；崩溃隔离；渲染可复用 `virgl_render_server` 的 socket 模式 | 需要自定义协议 + 生命周期管理 | **生产推荐** |

两种形态**共用本文件的寄存器/状态机/数据面规格**，差异只在"MMIO 陷阱与 IRQ 的落地方式"。

### 1.2 MMIO 窗口（**重要发现**）

| 事实 | 数据 |
|---|---|
| 官方 DTB 声明 | `reg = <0x0 0x13000000 0x0 0x4000>`（**16 KiB**） |
| kbase v9.0 实际会寻址的最高偏移 | **`0x107F8`**（`MMU_STAGE2__ST2MMU__AS15__FAULTEXTRA`） |
| 超出 0x4000 的条目 | **148 条**（全部是 `MMU_STAGE2__ST2MMU__*`，基址 `0x10000`） |

→ **设备模型必须暴露 ≥ 0x10800 的窗口**（建议 `0x20000`），并在 DT 里如实声明；
否则一旦 guest 访问 stage-2 MMU（受保护模式/安全通路），会落在未映射区间。
当前路线（NO_MALI）没有窗口约束，所以历史上没暴露这个问题；`plat/cdh_mali_plat.c` 用的是 `0x100000` 占位资源，也绕过了它。

**背景判读**：stage-2 MMU 只在 protected mode / arbiter 场景被 kbase 使用；MTK 是否启用决定它是否真的会被访问。
设备模型按"窗口给足 + 寄存器实现"处理即可，成本极低（地址→值映射）。

### 1.3 中断规格

| 项 | 规格 | 依据 |
|---|---|---|
| 条数 | **3**（`JOB`、`MMU`、`GPU`），byname 获取，可回退小写 | `kbase/mali_kbase_core_linux.c:443-470` |
| DT 实际条目 | 5 条（`GPU MMU JOB EVENT PWR`）→ `364/365/366/367/368`；**EVENT/PWR 只给 MTK fork 用** | [raw/official-dtb-gpu-nodes.txt](evidence/issue-gold15-gpu-hw-emu/raw/official-dtb-gpu-nodes.txt) |
| GIC 连法 | SPI 号 = DT 第二 cell（364/365/366），**level-high（`0 364 4`）** | 同上 |
| 触发 | **共享中断**（`IRQF_SHARED`）+ 电平语义：`*_IRQ_STATUS = RAWSTAT & MASK`，handler 早退条件 = `gpu_powered == false` | `kbase/backend/gpu/mali_kbase_irq_linux.c:41-146,443-470` |
| 掩码语义 | `*_IRQ_MASK` 的 1 位 = 屏蔽；`*_IRQ_CLEAR` 写 1 清对应 RAWSTAT 位；**必须先清 RAWSTAT 再靠 MASK 抑制**（kbase 依赖此序） | `kbase/backend/gpu/mali_kbase_pm_driver.c:2727-2761`、`backend/gpu/mali_kbase_jm_hw.c:452-454` |

三块中断寄存器的偏移（v9.0 LUT 实证）：

| 块 | RAWSTAT | CLEAR | MASK | STATUS | 备注 |
|---|---|---|---|---|---|
| `GPU_CONTROL` | `0x0020` | `0x0024` | `0x0028` | `0x002C` | GPU 事件位见 §2.2 |
| `JOB_CONTROL` | `0x1000` | `0x1004` | `0x1008` | `0x100C` | `JOB_IRQ_JS_STATE @0x1010` |
| `MMU_STAGE1` | `0x2000` | `0x2004` | `0x2008` | `0x200C` | — |

### 1.4 客机内存访问（DMA）

| 需求 | 规格 |
|---|---|
| 读 GPU 页表 / job chain / FBD / RT | 设备模型用 **guest 物理地址** 访问；GPU VA 需先经 **kbase 的 GPU MMU 页表**（§4.1）翻译 |
| QEMU 侧机制 | `AddressSpace`（`address_space_read/write`）或 `MemoryRegion` 映射；vhost-user 形态则用共享内存区 + `mmap` |
| 注意 | 本仓库 QEMU `virt` 机器默认无 SMMU：**物理访问可直接进行**；若将来启用 SMMU，需 `MemoryListener`/IOMMU notifier |
| 缓存一致性 | 宿主可见内存的读写需显式处理（[gpu-hw-emu.md](gpu-hw-emu.md) §5.4 的 `dc cvac` 类坑） |

### 1.5 DT 节点规格（QEMU 侧生成）

```dts
mali@13000000 {
        compatible = "mediatek,mali", "arm,mali-valhall";  /* kbase 匹配 arm,mali-valhall */
        reg = <0x0 0x13000000 0x0 0x20000>;                /* 见 §1.2：给足窗口 */
        interrupts = <0 366 4>, <0 365 4>, <0 364 4>;      /* JOB, MMU, GPU（顺序不必与 DT 原样，
                                                              但 interrupt-names 必须匹配 byname 查询） */
        interrupt-names = "JOB", "MMU", "GPU";
        /* 可选：ged-supply / operating-points-v2 / cooling-cells——只有 V1（stock MTK kbase）才需要 */
};
```

- **上游 kbase 只查 `JOB`/`MMU`/`GPU` 三条**，其余（`EVENT`/`PWR`、`ged-supply`、OPP）**可省**。
- `power_control_init()` 会做 `of_clk_get()`/`regulator_get_optional()`：
  - 若 DT 节点**存在但没有 `clocks`/`*-supply`** → 返回 0 个 clk/regulator（正常路径，无补丁）；
  - 若**完全无 of_node**（platform_device 形态）→ 需要 §2.8 的补丁 2（`of_node==NULL` 早退）。

---

## 2. 寄存器规格

### 2.1 全量清单

**691 条**，见 [raw/kbase-regmap-v9_0.md](evidence/issue-gold15-gpu-hw-emu/raw/kbase-regmap-v9_0.md)
（含 ENUM / 偏移 / 位宽 / 权限，机器抽取自 `kbase_regmap_v9_0_init` 继承链，已剔除 v9.0 作废项）。
复现：`tools/extract_regmap.py <mali_kbase_regmap_jm.c> v9_0`。

分块（含每块地址范围）：

| 块 | 条目 | 范围 | 内容 |
|---|---|---|---|
| `GPU_CONTROL` | 82 | `0x0000-0x0F0C` | 身份/特性/中断/命令/电源域/配置/时间戳/纹理特性/JS features |
| `JOB_CONTROL` | 313 | `0x1000-0x1FFC` | JOB 中断 + 16 个 job slot（`0x1800 + n*0x80`，每槽 19 个寄存器） |
| `MMU_STAGE1` | 148 | `0x2000-0x27F8` | MMU 中断 + 16 个 AS（`0x2400 + n*0x40`，每 AS 9 个寄存器） |
| `MMU_STAGE2` | 148 | **`0x10000-0x107F8`** | ST2MMU（受保护模式；**超出官方 DT 窗口**，见 §1.2） |

> **v9.0 已作废（不会访问，无需实现）**：`CORE_FEATURES(0x008)`、`THREAD_TLS_ALLOC(0x310)`、`JOB_IRQ_THROTTLE(0x1014)`
> ——LUT 里 `flags=0, regs=NULL`（`kbase_regmap_jm.c:2212-2216`）。照抄旧 DDK 寄存器表会引入不必要工作。

### 2.2 必须实现"行为"的寄存器（约 60 个）

**A. 身份与特性（常量，只读）**

| 寄存器 | 偏移 | 值 | 说明 |
|---|---|---|---|
| `GPU_ID` | `0x000` | **`0x09030010`** | = `GPU_ID2_MAKE(9,0,0,3,0,1,0)`；product_id `0x09000003`、r0p1（厂商 DDK 要求，见 §2.8 补丁 3/4） |
| `L2_FEATURES` | `0x004` | `0x06100206` | 参考实现值（`model_dummy.c:1827`） |
| `TILER_FEATURES` | `0x00C` | `0x809` | tNAx 档（G57） |
| `MEM_FEATURES` | `0x010` | `0x01 \| ((L2 片数-1)<<8)`，L2_PRESENT=1 → `0x01` | 位 0 = core group coherent |
| `MMU_FEATURES` | `0x014` | `0x2830` | VA/PA 位数来源：`[7:0]=va_bits`、`[15:8]=pa_bits` |
| `AS_PRESENT` | `0x018` | `0xFF` | 8 个 AS（`num_address_spaces = hweight32`） |
| `JS_PRESENT` | `0x01C` | `0x7` | 3 个 job slot |
| `JS0_FEATURES` | **`0x0C0`** | `0x20E` | bits 1,2,3,9 = NULL/SET_VALUE/CACHE_FLUSH/**FRAGMENT** |
| `JS1_FEATURES` | **`0x0C4`** | `0x1FE` | bits 1-8（含 VERTEX/TILER/FUSED） |
| `JS2_FEATURES` | **`0x0C8`** | `0x07E` | bits 1-6 |
| `JS3..15_FEATURES` | `0x0CC..0x0FC` | `0` | 不存在（槽只有 3 个） |
| `SHADER_PRESENT` | `0x100`(64) | `0xF` | 与 `TILER_PRESENT(0x110)`、`L2_PRESENT(0x120)` 同族 |
| `TILER_PRESENT` / `L2_PRESENT` / `STACK_PRESENT` | `0x110` / `0x120` / `0xE00` | `0x1` / `0x1` / `0xF` | 64 位 |
| `THREAD_MAX_THREADS`/`WORKGROUP_SIZE`/`BARRIER_SIZE` | `0x0A0/0x0A4/0x0A8` | `0x180` | — |
| `THREAD_FEATURES` | `0x0AC` | **`0xC0046000`** | `0x6000 \| (4<<16) \| (0<<24) \| (3<<30)`；**bits[31:30]=3(SOFTWARE)** 用于绕开 `/lib/firmware/valhall-1691526.wa`（见 §7） |
| `TEXTURE_FEATURES_0..3` | `0x0B0..0x0BC` | `0xFFFFF` / `0xFFFF` / `0x9F81FFFF` / `0` | — |

> ⚠️ **易错点**：`JS<n>_FEATURES`（`GPU_CONTROL`，`0x0C0+n*4`）与 job slot 寄存器（`JOB_CONTROL`，`0x1800+n*0x80`）名字相似但**块与偏移完全不同**；
> 且 `0x100` 是 `SHADER_PRESENT` 而非 `JS0_FEATURES`（本文件的偏移均已用 [raw 表](evidence/issue-gold15-gpu-hw-emu/raw/kbase-regmap-v9_0.md) 反向校验）。

**B. 复位/中断/命令**

| 寄存器 | 偏移 | 语义 |
|---|---|---|
| `GPU_COMMAND` | `0x030` | 写 `SOFT_RESET(0x01)` → 延迟后置 `GPU_IRQ_RAWSTAT.RESET_COMPLETED(1<<8)`；其余命令（`HARD_RESET 0x02`、`PRFCNT_*`、`CLEAN_CACHES 0x07`、`CLEAN_INV_CACHES 0x08`、`SET_PROTECTED_MODE 0x09`）按需回对应完成位 |
| `GPU_STATUS` | `0x034` | 位：`PRFCNT_ACTIVE(1<<2)`、`CYCLE_COUNT_ACTIVE(1<<6)`、`PROTECTED_MODE_ACTIVE(1<<7)`、`GPU_DBG_ENABLED(1<<8)`；其余为 0 |
| `GPU_IRQ_RAWSTAT/CLEAR/MASK/STATUS` | `0x020-0x02C` | 位语义（`hw_access/regmap/mali_kbase_regmap_jm_macros.h:276-297`）：`GPU_FAULT(1<<0)`、`MULTIPLE_GPU_FAULTS(1<<7)`、**`RESET_COMPLETED(1<<8)`**、`POWER_CHANGED_SINGLE(1<<9)`、`POWER_CHANGED_ALL(1<<10)`、`PRFCNT_SAMPLE_COMPLETED(1<<16)`、`CLEAN_CACHES_COMPLETED(1<<17)`、`FLUSH_PA_RANGE_COMPLETED(1<<20)` |
| `GPU_FAULTSTATUS`/`GPU_FAULTADDRESS` | `0x03C` / `0x040`(64) | 恒 0 |
| `LATEST_FLUSH` | `0x038` | 单调递增的 flush id（kbase 只用它做 cache flush 序号） |
| `PWR_KEY` / `PWR_OVERRIDE0/1` | `0x050/0x054/0x058` | **kbase 主干不写**；仅超时诊断读取 → 返回 0 即可 |
| `CYCLE_COUNT` / `TIMESTAMP` | `0x090`(64) / `0x098`(64) | 可返回单调计数；**v9.0 LUT 中没有 `TIMESTAMP_OFFSET(0x088)`**（不要实现） |
| `JM_CONFIG`/`SHADER_CONFIG`/`TILER_CONFIG`/`L2_MMU_CONFIG` | `0x0F00/0x0F04/0x0F08/0x0F0C` | **读写回环**（kbase 读默认值→算 quirks→回写；必须存得住） |
| `L2_CONFIG` | `0x048` | v9.2+ 才进 LUT；v9.0 **不会访问** → 仍需可读（返回 0）以防万一 |

**C. 电源域（shader / tiler / L2 / stack）**

| 寄存器组 | 偏移 | 语义 |
|---|---|---|
| `*_PWRON` | `SHADER 0x180/0x184`、`TILER 0x190`、`L2 0x1A0`、`STACK 0xE20` | 写 1 → 目标 core 位图开始上电 |
| `*_READY` | `SHADER 0x140`、`TILER 0x150`、`L2 0x160`、`STACK 0xE10` | 上电完成后 = PRESENT 位图 |
| `*_PWRTRANS` | `SHADER 0x200`、`TILER 0x210`、`L2 0x220`、`STACK 0xE40` | 过渡中位图；**必须在有限延迟内清零**（kbase 会轮询） |
| `*_PWROFF` | `SHADER 0x1C0`、`TILER 0x1D0`、`L2 0x1E0`、`STACK 0xE30` | 写 1 → 关断对应位 |
| `*_PWRACTIVE` | `SHADER 0x240`、`TILER 0x250`、`L2 0x260` | 可直接回 READY 值 |
| `COHERENCY_FEATURES`(R)/`COHERENCY_ENABLE`(RW) | `0x300/0x304` | G57 走 tNAx 表，不涉及强制一致性回写 → 常量/存储即可 |

> 权限要点（取自 [raw 表](evidence/issue-gold15-gpu-hw-emu/raw/kbase-regmap-v9_0.md)）：`*_PRESENT`/`*_READY`/`*_PWRTRANS`/`*_PWRACTIVE` 均为 **RO**；
> 只有 `*_PWRON`/`*_PWROFF` 是 **WO(write)**。`SHADER_PWRFEATURES(0x188)` **不在 v9.0 LUT 中**（无需实现）。

**D. Job slot（`JOB_CONTROL__JS<n>__*`，`0x1800 + n*0x80`）**

| 槽内偏移 | 寄存器 | 语义 |
|---|---|---|
| `0x00`(64) | `HEAD` | 当前执行的 job chain GPU VA |
| `0x08`(64) | `TAIL` | 链尾 |
| `0x10`(64) | `AFFINITY` | 亲和性位图 |
| `0x18` | `CONFIG` | `START_MMU(1<<10)`、`JOB_CHAIN_FLAG(1<<11)`、flush 位、`THREAD_PRI<<16` |
| `0x1C` | `XAFFINITY` | `XAFFINITY_ENABLE(1<<0)`、`TILER_ENABLE(1<<8)`、`CACHE_ENABLE(1<<16)` |
| `0x20` | **`COMMAND`** | `NOP 0`、`START 1`、`SOFT_STOP 2`、`HARD_STOP 3`、`SOFT_STOP_0 4`、`HARD_STOP_0 5`、`SOFT_STOP_1 6`、`HARD_STOP_1 7`（`..._jm_macros.h:148-157`；`MASK=0x07`） |
| `0x24` | `STATUS` | 无异常 `0x00`；`INTERRUPTED 0x02`、`STOPPED 0x03`、`TERMINATED 0x04`；故障基 `0x40+`（`..._jm_macros.h:185-220`） |
| `0x30` | `FLUSH_ID` | = `LATEST_FLUSH` 的快照 |
| `0x34/0x38/0x3C` | `EVENT_0/1`、`EVENT_MASK` | 存储回读 |
| `0x40`(64) | `HEAD_NEXT` | **kbase 写它提交 job chain** |
| `0x48`(64) | `TAIL_NEXT` | — |
| `0x50`(64) | `AFFINITY_NEXT` | — |
| `0x58` | `CONFIG_NEXT` | as_nr + flush/prio/jobchain 位 |
| `0x5C` | `XAFFINITY_NEXT` | — |
| `0x60` | **`COMMAND_NEXT`** | **写 `START` 触发执行**；完成后**必须归零**（`kbasep_jm_wait_js_free` 轮询它） |
| `0x70` | `FLUSH_ID_NEXT` | — |
| `0x7C` | `EVENT_MASK_NEXT` | — |

> 权限（表实证）：`HEAD`/`TAIL`/`AFFINITY`/`CONFIG`/`XAFFINITY`/`STATUS`/`FLUSH_ID` = **R**；
> `COMMAND`、`EVENT_0/1`、`EVENT_MASK` 与全部 `*_NEXT` = **RW**（`COMMAND` 表内为 RW，kbase 按 WO 用）。

**E. MMU（`MMU_STAGE1__ST1MMU__AS<n>__*`，`0x2400 + n*0x40`；IRQ 为 `MMU_STAGE1__ST1MMU__IRQ_*` @ `0x2000-0x200C`）**

| AS 内偏移 | 寄存器 | 语义 |
|---|---|---|
| `0x00`(64) | `TRANSTAB` | 页表基址（低 4 位保留；`AS_TRANSTAB_BASE_SHIFT 4`） |
| `0x08`(64) | `MEMATTR` | 内存属性（`MEMORY_TYPE[7:6]`、`ALLOC_*`、coherency 位） |
| `0x10`(64) | `LOCKADDR` | fault 恢复用锁定地址 |
| `0x18` | **`COMMAND`** | `NOP 0`、`UPDATE 1`、`LOCK 2`、`UNLOCK 3`、`FLUSH_PT 4`、`FLUSH_MEM 5`（`hw_access/mali_kbase_hw_access_regmap.h:173-178`；字段 `COMMAND[7:0]`） |
| `0x1C` | `FAULTSTATUS` | 异常类型/访问类型/来源 id |
| `0x20`(64) | `FAULTADDRESS` | fault 地址 |
| `0x28` | **`STATUS`** | bit0 = `AS_ACTIVE_EXT`（`hw_access/mali_kbase_hw_access_regmap.h:74-76`）；kbase 在写 `COMMAND` 前轮询它清零 |
| `0x30`(64) | `TRANSCFG` | `MODE[3:0]`（`AARCH64_4K 0x6` / `AARCH64_64K 0x8` / `IDENTITY 0x2` / `UNMAPPED 0x1`）+ PTW 属性 |
| `0x38`(64) | `FAULTEXTRA` | 次级 fault 信息 |

- **写序契约**：`TRANSCFG → TRANSTAB → MEMATTR → COMMAND=UPDATE`（`kbase/mmu/mali_kbase_mmu_hw_direct.c:346-353`）。
- `MMU_IRQ` 位语义：页故障 = `1<<n`（n<16），总线错误 = `1<<(n+16)`（`hw_access/mali_kbase_hw_access_regmap.h:62-72`）。
- 权限（表实证）：`TRANSTAB`/`MEMATTR`/`LOCKADDR`/`TRANSCFG` = **RW**；`COMMAND` = **W**；`STATUS`/`FAULTSTATUS`/`FAULTADDRESS`/`FAULTEXTRA` = **R**。
- **stage-2（`MMU_STAGE2__ST2MMU__*`，`0x10000-0x107F8`）**：受保护模式相关，T1/T2 可先不实现行为，仅保证窗口可寻址（§1.2）。

### 2.3 访问规则

| 规则 | 说明 |
|---|---|
| 读未实现/未映射偏移 | 返回 0（不要 fault）；若 `CONFIG_MALI_DEBUG`，kbase 会对"读 WO / 写 RO"发 WARN → 设备模型应记录这类访问用于调参 |
| 写 RO 寄存器 | 忽略（不改变值），但可记录 |
| 64 位寄存器 | 按 32 位对（`_LO`/`_HI`）访问，注意 `readl`/`writel` 的半字序；设备模型按字节偏移统一处理即可 |
| 位图寄存器 | `PRESENT`/`READY`/`PWRTRANS`/`AFFINITY` 均为位图；多 slot/core 共享一个寄存器 |

---

## 3. 状态机规格（G0/G1 判据对应的时序）

### 3.1 probe → `/dev/mali0`（G0）

| 步 | guest 侧动作 | 设备模型必须的响应 |
|---|---|---|
| 1 | `registers_map()`：请求 MEM 资源 + ioremap | 提供 ≥0x10800 的 MMIO 区（§1.2） |
| 2 | `get_irqs()`：`JOB`/`MMU`/`GPU` byname | 3 条 IRQ 资源可用 |
| 3 | `kbase_pm_register_access_enable()` → 首次读 `GPU_ID` | 返回 `0x09030010` |
| 4 | 读特性/present 寄存器 | 按 §2.2A 返回常量 |
| 5 | `kbase_hw_set_issues_mask()` | 依赖 `product_model=(9,3)` 与 `impl_tech`（见 §7 风险 1/2） |
| 6 | `kbase_install_interrupts()` | — |
| 7 | **`kbase_pm_init_hw()`：写 `GPU_COMMAND=SOFT_RESET`，只 unmask `RESET_COMPLETED`，等中断（500 ms 超时）** | **必须**在延迟后置 `GPU_IRQ_RAWSTAT.RESET_COMPLETED` 并按 MASK 拉 IRQ |
| 8 | 读/回写 `JM_CONFIG`/`SHADER_CONFIG`/`TILER_CONFIG`/`L2_MMU_CONFIG` | 读写回环 |
| 9 | `kbase_pm_enable_interrupts()`：`*_IRQ_CLEAR/MASK` 写全 1 后按需放开 | 支持 CLEAR/MASK 语义 |
| 10 | 上电 shader/tiler/L2 | `*_PWRON → *_READY=PRESENT, *_PWRTRANS=0` |
| 11 | `misc_register` → `/dev/mali0` | —（纯 guest 侧） |

### 3.2 复位状态机

```
写 GPU_COMMAND.SOFT_RESET(0x01)  →（内部延迟，建议 1–10 ms 内）
   → GPU_IRQ_RAWSTAT |= RESET_COMPLETED(1<<8)
   → 若 MASK 中该位放开则拉 IRQ（否则只置 RAWSTAT）
   → guest 写 GPU_IRQ_CLEAR 清位 → 解除
写 HARD_RESET(0x02) → 行为同上（仅 RESET_COMPLETED）
```
**失败模式**：不产生 `RESET_COMPLETED` → `kbase_pm_do_reset()` 500 ms 超时 → 尝试 HARD_RESET → 再失败返回 `-EINVAL`（probe 失败）。

### 3.3 电源域状态机（每类 core 独立）

```
PWROFF/关断  → PWRTRANS 置位 →（延迟）→ READY 清位、PWRTRANS 清 0
PWRON/上电   → PWRTRANS 置位 →（延迟）→ READY = PRESENT、PWRTRANS 清 0、PWRACTIVE = PRESENT
```
- `L2` 的关键判据：`L2_READY == L2_PRESENT` **且** `L2_PWRTRANS == 0`。
- `STACK_*` 建议恒为 `ready=present, trans=0`（G57 无 corestack 时 kbase 也不会依赖）。
- 延迟建议：1 ms 级（过慢会拖累 probe；过快也可，但 `PWRTRANS` 至少要在下一次读取前为 0）。

### 3.4 Job slot 状态机（关键，决定 atom 能否回收）

```
kbase: 轮询 COMMAND_NEXT == 0（js_free_wait）
写 HEAD_NEXT = jc（GPU VA）、AFFINITY_NEXT、CONFIG_NEXT、[FLUSH_ID_NEXT]
写 COMMAND_NEXT = START(1)
设备模型:
   COMMAND = COMMAND_NEXT          ← 搬到 current
   HEAD    = HEAD_NEXT
   COMMAND_NEXT = 0                ← 必须归零（否则 kbase 提交线程永久等待）
   执行作业（T1: 立即完成 / T2: 解析 chain → 交宿主 → 完成后回调）
   STATUS = 0x00（成功）
   JOB_IRQ_RAWSTAT |= (1<<slot)                 ← 完成
                    或 (1<<(16+slot))           ← 失败（STATUS 置 0x40+ 故障码）
   按 MASK 拉 JOB IRQ
guest: kbase_job_done() 读 RAWSTAT → 读 STATUS → 写 JOB_IRQ_CLEAR → 读 JOB_IRQ_JS_STATE
```
**失败模式**：忘记清 `COMMAND_NEXT` → 提交线程卡死（现状 run79 的"atom 滞留"即此类自锁的镜像问题）。

### 3.5 MMU 命令状态机

```
写 AS<n>_COMMAND:
  UPDATE/LOCK/UNLOCK/FLUSH_PT/FLUSH_MEM
设备模型:
   AS_STATUS.AS_ACTIVE_EXT = 1（执行中）
   →（按命令更新内部 TLB/页表缓存状态）
   → AS_STATUS.AS_ACTIVE_EXT = 0
kbase: 写 COMMAND 前轮询 AS_ACTIVE_EXT 清零；写后按需轮询完成
fault: AS_FAULTSTATUS/FAULTADDRESS/FAULTEXTRA 赋值 + MMU_IRQ_RAWSTAT 置位
```

---

## 4. 数据面规格

### 4.1 GPU VA → guest PA 页表遍历

| 项 | 规格 | 依据 |
|---|---|---|
| 级数 | 4 级（0=顶层 .. 3=底层） | `kbase/mali_kbase_defs.h:98-102` |
| 每表项 | 8 字节，`PAGE_SIZE/8` 项 | `kbase/mali_kbase_defs.h:328` |
| 有效标志（低 2 位） | L3 有效 ATE = `3`；**L0-L2 有效 ATE = `1`**；无效 = `0`；**PTE（指向下级表）= `3`**（仅 level<3） | `kbase/mmu/mali_kbase_mmu_mode_aarch64.c:29-40,89-105` |
| 物理地址 | `entry & ~0xFFF` | 同文件 |
| 权限/属性位 | `RW = bits6:7`、`ENTRY_ACCESS_BIT = bit10`、`NX = bit54`、`bit55` 起为未用（`VALID_ENTRY_MASK`） | 同文件 `:42-45,129-150` |
| 顶层基址 | `MMU_STAGE1__MMU_AS<n>__TRANSTAB` 的值 | §2.2E |
| 算法（伪码） | `pa = walk(TRANSTAB, va, level=0)`；逐级：取 `idx = (va >> (12 + 9*(3-level))) & 0x1FF` → 读 entry → 若 level<3 且 `type==3` 下钻；若 `type==1` 说明是叶级块（按实现处理）→ level==3 时 `type==3` 得到页物理地址 + `va & 0xFFF` | 同上 |

> 注意：kbase 的"ATE/PTE"命名与真实 AArch64 描述符不同（`ENTRY_ACCESS_BIT(bit10)`、无 AF/UXN/AP 标准位），
> **必须按 kbase 自己的编码实现**，不要照搬 CPU 的 AArch64 表格式。

### 4.2 job chain 遍历 + renderpass 语义

- chain 为单链表：`Next` 字段（§4.3）到 0 结束。
- `END_RENDERPASS` atom 携带 **4 条** fragment chain（norm/forced read/write 组合），
  kbase 依 tiler 是否 OOM 选择：`kbase/backend/gpu/mali_kbase_jm_hw.c:116-168`（`KBASE_JD_RP_*` 状态机），
  结构定义 `kbase/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h:475-523`。
- T2 只需要"能识别 FS（fragment）job 并取到 RT"；T3 才需要完整渲染链。

### 4.3 Fragment Job → FBD → RT（已定案，可直接实现）

| 结构 | 偏移/位域 | 字段 |
|---|---|---|
| Fragment Job（64 B） | `u7 @ (0x10:1)` | **Type = 9**（Fragment） |
| | `0x18`(64) | `Next`（下一条 job 的 GPU VA） |
| | `0x20` | Payload |
| | `0x28`(64) | **FBD 指针**；低 6 位打包：bit0=type、bit1=ZS CRC 扩展存在、bits2-5=**RT 数−1** |
| FBD `+0x00`（64 B） | `0x20`(u16) / `0x22`(u16) | Width−1 / Height−1 |
| FBD `+0x40`（64 B） | — | Padding |
| FBD `+0x80` | — | 若有 ZS CRC 扩展：先占 64 B |
| FBD RT 数组 | 每项 64 B，`+0x20`(64)=**Base（RT GPU VA）**、`+0x28`(u32)=**Row Stride**、`+0x2C`(u32)=**Surface Stride** | |
| RT word1（`+0x04`） | bit0 = Write Enable；bits3-7 = Format；bits8-11 = **Block Format**（0=No Write、1=Tiled、2=Linear、**12=AFBC**、13=AFBC Tiled） | |

来源：`wire-kbase.sh:920-933`（规格注释）+ 实测判据（run81-83 `filled=163200`、AFBC solid 头）；
完整事实表见 [raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md)。

### 4.4 T1 / T2 语义

| 档 | job 完成语义 | 数据面动作 | 出画 |
|---|---|---|---|
| **T1** | `COMMAND_NEXT=START` → 立即（或 1 ms 后）置 `STATUS=0` + `JOB_IRQ_RAWSTAT` bit | 无 | 无（保活；等价现状 L1） |
| **T2** | 同上，但**先解析 chain → 取 RT** → 交宿主执行 fill/copy/clear → 完成后置中断 | RT 写入由宿主 GPU 完成 | 部分（清屏/纯色/搬运可见；等价现状 B2 v1） |

**T2 的"完成回执"是正确性关键**：宿主渲染异步 → 必须等宿主回执后再置 `JOB_IRQ_RAWSTAT`，
否则 guest 会读到未写完的 RT（对应 [gpu-hw-emu.md](gpu-hw-emu.md) §5.4 的 fence 类坑）。

---

## 5. 设备模型 ↔ 宿主渲染进程接口（T2）

建议复用 `virgl_render_server` 的 **socket + 独立进程**模式（[gpu-hw-emu.md](gpu-hw-emu.md) §5.2），
消息集最小集：

| 消息 | 参数 | 宿主动作 |
|---|---|---|
| `fill_rt` | `base`(guest PA)、`row_stride`、`surface_stride`、`w`、`h`、`format`、`block_format`、`color` | `vkCmdClearColorImage` / `vkCmdFillBuffer`（AFBC 时按 tile 布局写） |
| `copy_rt` | 源/目的 `base`、stride、矩形 | `vkCmdCopyImage`/`vkCmdBlitImage` |
| `present` | `base`、`w`、`h`、`format` | 交给显示通路（VNC/推流或回写 scanout） |
| `done`（回执） | `job_id`、状态 | 设备模型据此置 `JOB_IRQ_RAWSTAT` |

**内存取得方式**（二选一）：
1. 设备模型把 RT 所在 guest 内存以 fd/共享内存形式交给 renderer（QEMU 侧 `memory_region_get_fd`/`memfd`）；
2. renderer 通过设备模型暴露的 `read/write` RPC 访问 guest 内存（实现简单、性能一般）。

> ⚠️ AFBC（Block Format 12/13）：T2 若只做"填纯色"，AFBC solid 头已可被 guest 正确解读
> （现状 run74 实测"filled 与理论值吻合（含 AFBC solid 头）"）；若做任意搬运/图像，则需要完整 AFBC 解码/编码。

---

## 6. 验收判据（每档的 go/no-go）

| 门 | 判据 | 现状对照 |
|---|---|---|
| **G0** probe | `dmesg` 无 mali fault/超时；`/dev/mali0` 存在；`gpuinfo` 可读且 `product_id=0x09000003` | 现状已达成（NO_MALI 路径） |
| **G1** DDK | 不改的 `libGLES_mali.so` 完成 `Device::Initialize`；`eglInitialize` 失败计数 0；一次真实 atom 从提交到事件完成 | 现状已达成（M1，`0x3 arch 9.0.0 r0p1`） |
| **G2** 内存 | MMU fault 计数 0；页表遍历正确（可用 job chain 的 `jc` 反查自证）；`CLEAN_INV_CACHES → CLEAN_CACHES_COMPLETED` 有回 | 现状部分（NO_MALI 不真遍历页表） |
| **G3** 出画 | VNC 帧 md5 变化；RT 颜色命中预期值；`screencap` 非单调灰 | 现状：`fill` 成功但**帧仍 99.3% 纯灰**（上屏链未通） |
| **回归** | 厂商 so 的名字/md5 不变（沿用 [note13 vendor-so-identity](../../../note13-5G-15/build/doc/vendor-so-identity.md) 流程） | 本方案不动任何厂商文件 |

---

## 7. 已知风险与待确认项

| # | 项 | 影响 | 处置 |
|---|---|---|---|
| 1 | **官方 DT 窗口 0x4000 < 实际最大偏移 0x107F8** | 若 guest 访问 stage-2 MMU 会落在未映射区 | 设备模型窗口给 ≥0x10800 并在 DT 如实声明（§1.2）；stage-2 行为可后补 |
| 2 | **`product_model=(9,3)` 不在上游 issues 表** | 上游 kbase 走 `unknown` 分支或报错 | 需 §2.8 补丁 4（1 处 case）；设备模型侧只需如实返回 `GPU_ID` |
| 3 | **`/lib/firmware/valhall-1691526.wa`** | JM+真硬件路径在 `TTRX_3485` 命中时 `open()` 会 `-ENODEV` | 设备模型让 `THREAD_FEATURES[31:30]=SOFTWARE(3)`（§2.2A）或版本报 r0p1 即可绕开 |
| 4 | 轮询超时 | 500 ms（复位）、MMU inactive、JS free 等长轮询在 QEMU 下若被调度拖慢会误判 | 状态迁移延迟取 1 ms 级；IRQ 及时拉高 |
| 5 | AFBC | T2 的任意搬运需要 AFBC 编解码 | 先只做 solid/clear；搬运另立项 |
| 6 | cache 一致性 | 宿主可见内存脏数据 | 显式 cache 维护（`dc cvac` 类经验） |
| 7 | 多 AS/TLB | 8 个 AS 并行、fault 恢复（LOCK/UNLOCK） | 逐 AS 维护页表缓存与 TLB 状态；fault 路径先做"报错不崩" |
| 8 | 性能 | MMIO 陷阱次数极多（每 atom 数十次访问） | sysbus vs vhost-user 的 per-access 开销要实测；必要时批处理/共享内存旁路 |
| 9 | `MMU_STAGE2` 语义 | 受保护模式（安全视频等） | 先保证可寻址，功能后补；必要时明确不支持并记录 |

---

## 附录 · 生成物

| 文件 | 内容 |
|---|---|
| [raw/kbase-regmap-v9_0.md](evidence/issue-gold15-gpu-hw-emu/raw/kbase-regmap-v9_0.md) | 691 条权威寄存器表（机器抽取） |
| [issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md) | 作业格式事实（类型枚举、Fragment/FBD/RT、页表编码） |
| [issue-gold15-gpu-hw-emu/tools/extract_regmap.py](evidence/issue-gold15-gpu-hw-emu/tools/extract_regmap.py) | 寄存器表抽取器（只读，可复现） |
