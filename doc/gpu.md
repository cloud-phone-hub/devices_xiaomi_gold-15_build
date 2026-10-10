# gold-15 让「厂商图形栈」在云机跑起来 —— 计划（gpu.md）

- 设备：Xiaomi Redmi Note 13 5G（codename `gold`，board `mt6833`）全局版 ROM `OS3.0.5.0.VNQMIXM`，云机 `devices/xiaomi/gold-15`
- 云机宿主：macOS（Apple Silicon）/ QEMU + HVF，显示后端 = `virtio-gpu`（`CONFIG_DRM_VIRTIO_GPU=y`）
- 硬约束（用户已定）：**不替换厂商 vendor 里的图形 so**（App 检测面 = soname + md5，见
  `devices/xiaomi/note13-5G-15/build/doc/vendor-so-identity.md`）
- 目标（用户已定）：**让厂商图形栈真正跑起来**（非"只在位/被加载"）

---

## 0. 结论先行（可行性）

> **实施计划见 §7**（用户已定档：基于当前 CDH 内核自研 GPU 模拟，接通 virtio-gpu）。
> 本节是 2026-10-09 上午的初判，其中"L3 不可行"的结论已被 §7.3 的选型修正为
> "**L1/L2 工程可行、L3 为研究级**"——因为正确的落点不是"反编译厂商 kbase"，
> 而是"**自编上游 kbase + 替换其硬件访问层为虚拟后端**"。

| 档位 | 含义 | 初判 | §7 修正后 |
|---|---|---|---|
| **L1** | 厂商 DDK 初始化成功、SF 不崩、`sys.boot_completed=1` | 待验 | **工程可行**（Phase B，虚拟后端 + platform stub） |
| **L2** | 真能出图（`screencap` 有正确画面） | 存疑 | **可行但分档**：B2 blit 桥（部分）→ B3 shader 翻译（完整） |
| **L3** | 跑 GL/VK 应用并正确渲染 | 不可行 | **研究级**（需 Mali Valhall ISA → NIR 翻译，Mesa panfrost 前端可参考） |

**初判"不可行"的理由**（仍成立的部分）：`libGLES_mali.so` 对 `/dev/mali0` 说的是 **Mali DDK 私有 ABI**
（atom/job chain、GPU 页表、job slot，驱动直接编程 GPU 寄存器）；virtio-gpu 的提交面是
**virgl(Gallium)/venus(Vulkan)**，两者无语义映射；宿主 Apple Silicon **无 Mali 可透传**。
→ 所以"接通 virtio-gpu"**不能靠转发 Mali 命令**，只能靠"**在内核里模拟一块 Mali**"
（ABI/内存/job 生命周期成立），渲染产出再由桥接层落到 virtio-gpu。这正是 §7 的架构。

---

## 1. 现状实测（2026-10-09）

### 1.1 内核侧（gold 自编译内核，`out/kernel/config`）

| 项 | 值 |
|---|---|
| `CONFIG_DRM_VIRTIO_GPU` / `_KMS` | `=y`（显示后端就是 virtio-gpu） |
| `CONFIG_DMABUF_HEAPS` / `_SYSTEM` | `=y` / `=y`（无 `_CMA`） |
| `CONFIG_ION` | 无（legacy ion 未启用） |
| `CONFIG_DRM_MEDIATEK` | **未启用**（内核源码 `kernel/source/drivers/gpu/drm/mediatek/` 在，但没编） |
| `CONFIG_MALI*` | **无**；且 `kernel/source` 内**没有任何 Mali 源码** |

### 1.2 厂商图形栈的用户态依赖与内核接口（自官方 `vendor_a.img` 提取实测）

`lib64/egl/libGLES_mali.so`（53 MB）
- DT_NEEDED：`libion.so`、`libged.so`、`libgpud.so`、`libgralloc_extra.so`、`libgralloc_metadata.so`、
  `libladder.so`、`libdmabufheap.so`、`android.hardware.graphics.*`…
- 设备路径：**`/dev/mali`**

`lib64/hw/hwcomposer.mt6833.so`（`ro.hardware.hwcomposer=mtk_common`）
- DT_NEEDED：`libdrm.so`、`libdpframework.so`（DDP）、`libged.so`、`libgralloc_extra.so`、
  `libpq*.so`、`libmml.so`、`libdmabufheap.so`、`composer_ext`、`allocator-V2`…
- 设备路径：**`/dev/dri/card0`**（走 DRM，故"有戏"但也要求 MTK 语义）

### 1.3 云机运行时属性（gold-15/14 实例，adb）

```
ro.hardware              = mt6833
ro.hardware.egl          = meow
ro.hardware.gralloc      = (空 → default)
ro.hardware.hwcomposer   = mtk_common
ro.hardware.vulkan       = mali
ro.hwui.use_vulkan       = true
```

### 1.4 厂商栈 → 内核接口 需求表（现状）

| 厂商组件 | 要的内核接口 | 云机现状 |
|---|---|---|
| `libGLES_mali` / `libgpud` | `/dev/mali0`（MTK `mali_kbase`） | **无模块、无设备** |
| `libged` | `/dev/ged`（MTK GED 调度） | **无** |
| `libGLES_mali` / `libgralloc_*` | `/dev/ion` 或 `dma_heap/mtk_*` | 仅 `dma_heap/system`，**无 ion、无 MTK heap 名** |
| `hwcomposer.mt6833` / `libdpframework` | `/dev/dri/card0`（**MTK 语义 KMS**）+ DDP 经 cmdq/smi | card0=virtio-gpu（非 MTK）；**无 cmdq/smi** |
| 显示/内存 | `/dev/m4u`（MTK IOMMU）、`cmdq`、`smi` | **无** |

> 表内"现状"来自库文件 DT_NEEDED + 设备路径串 + 内核配置。

### 1.5 Phase 0 结果（2026-10-09 实测）：**门 G0 通过，但有两道硬门槛**

官方 `roms/gold_global_images_OS3.0.5.0.VNQMIXM_15.0/vendor_dlkm_a.img` 内 `/lib/modules` 共 **189** 个
模块，其中图形/显示/内存相关（实测清单）：

```
mali_kbase_mt6833_r49.ko     mali_mgm_mt6833_r49.ko     mali_prot_alloc_mt6833_r49.ko
ged.ko                       mtk_gpu_hal.ko             mtk_gpu_qos.ko
mtk_gpufreq_mt6833.ko        mtk_gpufreq_wrapper_legacy.ko
met_gpu_api.ko               met_gpu_adv_api.ko
cmdq-bdg-mailbox.ko          cmdq-sec-drv.ko            mtk_disp_sec.ko
```

即**厂商 Mali/GED 内核驱动随 ROM 提供**（prebuilt），G0 ✔。但：

| 门槛 | 实测 | 后果 |
|---|---|---|
| **① vermagic/符号版本不符** | 厂商模块 `vermagic = 6.6.118-android15-8-g4d05d8779bfd-4k SMP preempt mod_unload **modversions** aarch64`；我们 CDH 内核为 `6.6.127-4k-cdh` | 版本 + 符号 CRC 双不符 → `insmod` 会被拒（`invalid module format` / `disagrees about version of symbol module_layout`）。**要么换成官方 stock 内核做基座，要么拿到 kbase 源码自编**（MTK 的 kbase 不公开） |
| **② 依赖厂商子系统 + 靠 DT probe** | `mali_kbase_mt6833_r49` 的 `depends: mtk_gpufreq_wrapper_legacy,ged,mtk_gpu_hal,mrdump,sspm,mtk_gpu_qos`；`ged` 的 `depends: …,mediatek-drm,sspm`；两者都有 `of:` alias（`arm,mali-valhall` / `mediatek,ged`） | 即便强装，也需要 **DT 平台设备**（QEMU virt 无）与 `mediatek-drm`（我们内核 `CONFIG_DRM_MEDIATEK` 未开）等 |

→ 因此 Phase 1 的**真正前置**是：**把内核基座换成官方 stock 内核（6.6.118）并在其上叠加 CDH 补丁**，
否则厂商 kbase 连"被加载"都做不到。

---

## 2. 计划（证据优先，带 go/no-go 门）

> **状态（2026-10-09）**：Phase 0 **已完成**（结果见 §1.5，G0 通过 + 两道硬门槛）。
> Phase 1~4 是当时的"评估型"路线，已被 §7 的**实施型**方案取代（用户定档：
> 基于当前内核自研 GPU 模拟、接通 virtio-gpu）。本节保留作历史与判据来源。

### Phase 0 · 需求取证（零改动，只读）
产出：把 §1.4 表补成"每项 ioctl/设备名 + 云机是否存在"的**完整需求表**。
1. 查厂商 Mali 内核驱动是否随 ROM 提供：官方 `vendor_a.img` / `vendor_dlkm_a.img` / `system_dlkm_a.img`
   内的 `/lib/modules`（找 `mali_kbase.ko`、`ged.ko`、`mtk_*.ko` 等）。
2. 云机 `strace -f` 跟 `hwcomposer.mt6833` 与一次 GL 初始化，抄下**实际打开的设备与 ioctl**。
3. 记录每项"存在/缺失"。
→ **门 G0**：能拿到厂商 `mali_kbase`（.ko 或源码）→ 进 Phase 1；拿不到 → GPU 直接判负，跳 Phase 4。

### Phase 1 · GPU：让 `/dev/mali0` 存在（决定成败）
1. **装入厂商 kbase**：MTK 的 kbase 是 out-of-tree，通常**只给 prebuilt `.ko`**。先 `insmod` 试 →
   大概率因缺平台设备/DT 而失败（即证据）。
2. **造"设备"让 kbase 能 probe**：给 kbase 建虚拟平台设备/DT 节点。
   → **门 G1**：probe 成功（`/dev/mali0` 出现）。
3. **门 G2（真正的坎）**：kbase 在首次 MMU 初始化 / job 提交时必须读写 GPU 寄存器；
   没有真实 Mali 会挂死或报错。
   - **G2 通过** → L2/L3 有戏，进 Phase 2。
   - **G2 不过** → L2/L3 判负；"厂商栈真正跑起来"只剩：① 宿主/真机提供 Mali（透传）；
     ② MTK 内部的 kbase "virtual backend"（非公开）。二者都不在"改本内核"范围。
4. 备选：核实 QEMU 是否有可用 GPU 透传（macOS/HVF 下基本无 VFIO；`virtio-gpu-gl` 走宿主 Apple GPU + virgl，**仍非 Mali**）。

### Phase 2 · 显示/内存 shim（仅 G2 通过才值得做）
按 Phase 0 抄到的 ioctl 面逐项补齐：
- `/dev/ion` → dma-buf heaps 兼容层（或补 MTK heap 名，如 `mtk_mm`/`mtk_system`）；
- `/dev/ged`、`/dev/gpud` 桩（DVFS/调度类，多数 ioctl 可返回默认/成功）；
- `/dev/m4u`（MTK IOMMU）桩 → 映射到 dma-buf / virtio-gpu GEM；
- DDP/KMS：二选一
  - **2a（先试）** 给 `virtio-gpu` 加一层 **MTK 兼容 KMS 属性/原子语义**，让 `hwcomposer.mt6833` 认为 card0 是 MTK；
  - **2b** 启用内核树内 `drivers/gpu/drm/mediatek`（GPL 源码在）并给它写 **virtio-gpu 后端**（工作量≈半块 DRM 驱动）；
  - 外加 `cmdq`/`smi` 桩（DDP 经它们提交）。

### Phase 3 · 验证与验收
- `sys.boot_completed=1`；`init.svc.surfaceflinger=running` 且不再重启；
- `dumpsys SurfaceFlinger` 有正常 display/GLES 行；`screencap` 能出图；
- 跑 GL/VK 测试确认 L2/L3；
- 按 AGENTS.md 约束 2 归档六段式证据到 `build/doc/evidence/issue-gold15-vendor-gpu/`。

### Phase 4 · 若 G0/G2 判负（最可能）的替代
- **R1/R2 检测面路线**（note13 `vendor-so-identity.md` §14）：**一个厂商 so 都不替换**——
  `/vendor` 保留官方原件（gold-15 天然满足），系统侧另叠 virtio 图形栈 + 让 zygote 按真机 soname
  清单 dlopen。满足"App 检测面一致 + 系统能起"，但**不等于"厂商栈在跑"**。
- 或 **改云机形态**：宿主/真机带 Mali 才谈得上真厂商栈。

---

## 3. 关键判据汇总

| 门 | 判据 | 不通过的含义 |
|---|---|---|
| G0 | 厂商 `mali_kbase` 可得（.ko/源码） | 无法提供 `/dev/mali0` 的任何实现 → L1 起就出局 |
| G1 | kbase probe 出 `/dev/mali0` | 无设备可绑 → L1 出局 |
| G2 | kbase 能完成 MMU 初始化 / job 提交 | **L2/L3 出局**（只剩透传/真机） |

## 4. 与「不替换厂商 so」的关系

- gold-15 的 `/vendor` **就是官方 gold 原件**（基座=官方 super 换 vendor），故 **md5 天然一致**，
  本计划**不删/不替任何厂商 so**；Phase 2 的 shim 是**新增内核驱动/设备**，不改用户态 so 字节。
- 若最终走 Phase 4 的 R1/R2，仍需保持厂商 so 原样。

## 5. 记录

- 本文件随实施推进更新；每个通过/不通过的门都要在 `build/doc/evidence/issue-gold15-vendor-gpu/` 留证。
- Phase 0 原始证据已归档：`build/doc/evidence/issue-gold15-vendor-gpu/log-pass.txt`。

---

## 6. 评估：「反编译 kbase 桥接到 virtio-gpu」可行性（2026-10-09）

**结论：不成立。** 这不是一个"反编译 + 转发"的活，而是"写一个 GPU 模拟器"。

### 6.1 实测：kbase 是寄存器级 Job-Manager 驱动

`mali_kbase_mt6833_r49.ko` 内引用的源码路径（strings 实测）：

```
.../mali-r49p1/drivers/gpu/arm/midgard/backend/gpu/mali_kbase_jm_hw.c
.../mali-r49p1/drivers/gpu/arm/midgard/backend/gpu/mali_kbase_jm_as.c
.../mali-r49p1/drivers/gpu/arm/midgard/backend/gpu/mali_kbase_jm_rb.c
.../mali-r49p1/drivers/gpu/arm/midgard/context/backend/mali_kbase_context_jm.c
.../mali-r49p1/drivers/gpu/arm/midgard/hw_access/backend/mali_kbase_hw_access_real_hw.c
.../mali-r49p1/drivers/gpu/arm/midgard/hw_access/regmap/mali_kbase_regmap_jm.c
```

- 全是 **`_jm_`（Job Manager）+ `midgard`**；`hw_access_real_hw` = **直接寄存器访问**（非 dummy/模拟后端）。
- 未见 CSF / `csffw` 固件痕迹（ROM `vendor_a.img` 亦未找到 `mali_csffw.bin`）→ 该 DDK 走 **JM 路径**。

### 6.2 为什么"桥"不存在

| 层 | 上游（提交者） | 下游（执行者） |
|---|---|---|
| **kbase** | Mali 用户态（atom 链 / Mali 命令流） | **Mali GPU 硬件**（JS/MMU 寄存器 + Job Manager + 着色核/瓦片器） |
| **virtio-gpu** | virgl(Gallium 流) / venus(Vulkan) | 宿主 virglrenderer → 真实 GPU |

两者**抽象层与语义都不同**；kbase 下游就是硬件，**没有任何"可转发"的东西**。要桥接必须：
1. 在 kbase 与 virtio 之间**实现 Mali Job Manager + 着色核 + 命令流解释器**（即 Mali-G57 软件模拟器）；
2. 再把 Mali 命令翻译到 Gallium/Vulkan。
第 1 步等价于"实现一块 GPU"，工程量与性能均不可行。

### 6.3 剩下的可能路径

| 路径 | 结论 |
|---|---|
| A. 真 Mali GPU 在环（透传 / 真机 / 厂商 GPU 虚拟化） | 宿主 Apple Silicon **无 Mali 可透传**；MTK kbase virtual backend 不公开 |
| B. 写 Mali 软件模拟器 | 理论唯一纯软件解，不可行 |
| C. 反编译做"薄桩"只求初始化不崩 | 桩可让 `eglInitialize` 过，但 SF 一提交 job 即无解（垃圾/挂死）→ 不能出图 |

### 6.4 结论与建议

- **"厂商图形栈真正跑起来（L2/L3）"在本环境不可行**；L1 也依赖 kbase 能 probe（DL 门 G1），而它靠 DT 节点（`arm,mali-valhall`）、依赖 `mediatek-drm` 等 MTK 子系统，QEMU virt 均无。
- 务实交付（内核零改动、不替换任何厂商 so）：
  1. **系统可用**：新增 virtio 图形栈（mesa/minigbm/hwcomposer.drm/vulkan.virtio）+ 属性切换；
  2. **App 检测面一致**：保持官方 `/vendor` 原件 + note13 `vendor-so-identity.md` §14 的 **R1/R2**（zygote 侧加载真机 soname）。
- 若仍要"钉死"厂商栈：唯一可证的实验 = 换**官方 stock 内核（6.6.118，vermagic 匹配）**做基座后 `insmod` 那 4 个模块，观察 G1/G2；预计 probe 失败。代价是 gold-15 的内核基座要改（当前为 lineage）。**未采纳（收益≈0，风险高）。**

---

# 7. 实施方案：基于当前内核自研「虚拟 Mali GPU」，接通 virtio-gpu

> 定档（用户 2026-10-09）：**不换内核基座**（保持 CDH `6.6.127-4k-cdh`）、**不替换任何厂商 so**，
> 在内核里**实现 GPU 模拟**，让厂商图形栈（MEOW + `libGLES_mali` + MTK HWC）跑起来，并把渲染接通 virtio-gpu。

## 7.1 架构总览

```
┌─ 用户态（厂商原件，零改动：路径/字节/md5 全不动）────────────────────────┐
│  SF / App → libEGL → MEOW(ro.hardware.egl=meow) → libGLES_mali.so        │
│                                    │ ioctl(KBASE_IOCTL_*)  open("/dev/mali0")│
└────────────────────────────────────┼───────────────────────────────────────┘
┌─ Layer K  内核虚拟 Mali（本方案主体，新增 cdh_mali_vgpu.ko）───────────────┐
│  ① ARM kbase r49p1 **源码**（用当前内核编译 → vermagic 自动匹配）           │
│  ② hw_access 层替换：real_hw → **virtual backend（自研）**                  │
│       · 虚拟 regmap：上报 Mali-G57 MC2 身份（GPU_ID/PRODUCT_ID/L2/tiler/    │
│         shader core 掩码，取值见 §7.5 A2）                                  │
│       · job 提交：atom/job chain 落到我们可直读的内存 → 按档位处理（§7.8）   │
│       · 完成路径：软件触发 job done + fence signal（不走硬件中断）           │
│  ③ platform stub（无 DT）：platform_device_register_full + 虚拟 MMIO +      │
│       clk/regulator/power-domain 桩 + 软件 IRQ                              │
│  ④ MMU/AS：kbase 自带 MMU，物理页落到普通内核页 / dma-buf（不碰真 GPU 页表） │
└────────────────────────────────────┬───────────────────────────────────────┘
┌─ Layer S  周边设备桩（DDK 依赖，缺则起不来）───────────────────────────────┐
│  /dev/ged（GED 调度）· /dev/gpud（libgpud）· /dev/ion 或 dma_heap MTK 名    │
│  /dev/m4u（MTK IOMMU）· gralloc/composer 选型（**新增**实现 + 属性指向，     │
│  厂商原文件保留原位不动）                                                   │
└────────────────────────────────────┬───────────────────────────────────────┘
┌─ Layer B  渲染桥：把"像素产出"接到 virtio-gpu（分档，见 §7.8）──────────────┐
│  B1 立即完成（不渲染）→ B2 blit/copy 桥 → B3 job chain 解析 + shader 翻译   │
└────────────────────────────────────┬───────────────────────────────────────┘
                       virtio-gpu (CONFIG_DRM_VIRTIO_GPU=y)
                            → /dev/dri/card0 → SF/HWC 上屏
```

**要点**：厂商 DDK 只认 `/dev/mali0` 的 kbase ABI。我们**不改 DDK**，而是**在内核里把这块 GPU 造出来**；
"接通 virtio-gpu"发生在 Layer B——渲染结果以 **dma-buf** 形态交给 virtio-gpu 上屏。

## 7.2 选型对比（为什么是"自编 kbase + virtual backend"）

| 方案 | 做法 | 判定 |
|---|---|---|
| **① 自编上游 kbase + 替换 hw_access（选定）** | 取 ARM kbase r49p1 **源码**，用当前内核编译（vermagic 匹配），把 `hw_access_real_hw` 换成自研 virtual backend | ✅ **ioctl ABI 由真 kbase 提供**（版本协商/内存管理/fence/上下文全是真实现），只需替换最底层硬件访问；ARM 本就为 host 测试留了 `hw_access_dummy` 扩展点 |
| ② 反编译厂商 `.ko` 重建 ABI 桩 | 从 `mali_kbase_mt6833_r49.ko` 反推 ioctl 编号与结构体 | ❌ 实测**无 DWARF**（`.debug_info` 计数 0）、**无 `KBASE_IOCTL_*` 字符串**（0 条）→ 只能反汇编重建，代价极高且易错 |
| ③ 直接 `insmod` 厂商 prebuilt | 用官方 `.ko` | ❌ vermagic `6.6.118…modversions` ≠ `6.6.127-4k-cdh`；且 `depends: ged,mtk_gpu_hal,mrdump,sspm,…` + `of:` DT 绑定，QEMU virt 全无 |
| ④ 换 stock 内核基座 | 6.6.118 + CDH 补丁重打 | ❌ 用户已排除（要求基于当前内核）；且仍需解决 DT/依赖 |
| ⑤ 内核主线 panfrost | `drivers/gpu/drm/panfrost` | ❌ panfrost 也是**真硬件**寄存器驱动，无 Mali 硬件同样 probe 不出；但其 **ISA 前端（bi_compiler/valhall_dis）对 §7.8 B3 有参考价值** |

## 7.3 决定选型的三条实测（2026-10-09）

1. **厂商 `.ko` 不可用**：vermagic `6.6.118-android15-8-g4d05d8779bfd-4k SMP preempt mod_unload modversions aarch64`
   ≠ 我们 `6.6.127-4k-cdh`；有 `__versions` 段（强制符号 CRC）。
2. **反编译路径代价过高**：`mali_kbase_mt6833_r49.ko`（1495368 B）无 `.debug_info`/`.debug_str`，
   `strings | grep KBASE_IOCTL_` = **0 条** → ABI 只能靠反汇编。
3. **上游源码渠道可达**：`developer.arm.com` = HTTP 200、`gitlab.arm.com` = 302；
   `github.com` = **000（不可达）** → 取源码走 ARM 官方渠道，不依赖 GitHub。

## 7.4 里程碑与验收门

| 里程碑 | 验收门（可实测） | 对应档位 | 状态 |
|---|---|---|---|
| **M0** 源码与构建就位 | `mali_kbase.ko`(1382472 B) + `cdh_mali_plat.ko`(12800 B) 编出；`vermagic = 6.6.127-4k-cdh …modversions`；`depends` 均为空 | — | ✅ **达成** |
| **M1** `/dev/mali0` + DDK 初始化成功 | `Probed as mali0`；`/dev/mali0` = `crw-rw-rw- system graphics u:object_r:gpu_device:s0`；`eglInitialize`/`no suitable EGLConfig` 计数 **0**；无 Oops | **L1** | ✅ **达成**（2026-10-10，gold-15/18，补丁 3+4）：身份 = `0x3 arch 9.0.0 r0p1`（product_id `0x09000003`）；四项计数 0/0/0/0（补丁前 16/16/32/46）。UK 版本差仅为 WARN |
| **M2** SF 稳定 + 引导完成 | `init.svc.surfaceflinger=running` 且不再重启；`sys.boot_completed=1`；`screencap` 能出图 | **L1** | 🟡 **推进中**：Phase C 第一步（补丁 5，connector 改 DSI）已消除 HWC 构造期崩溃，`IComposer/default` 正常存活；剩余阻塞 = 49 个 MTK 私有 CRTC KMS 属性缺失（§7.18） |
| **M3** 画面正确 | `screencap` 内容与预期一致 | **L2** | 未开始（需 §7.8 B2/B3） |
| **M4** GL/VK 应用可跑 | glmark2 或自测 App 正常渲染 | **L3** | 未开始 |

> M1 是当前卡点的直接解：原状是 `eglInitialize` 失败 → `no suitable EGLConfig found, giving up`
> → SF SIGABRT（gold-15/14 实例里 `surfaceflinger`/`composer@3.3` 各崩 47 次）。现已消除。
> 六段式证据：`build/doc/evidence/issue-gold15-vendor-gpu/solution.md`。

## 7.5 Phase A · 前置取证与源码就位（先做，产出决定 B 的写法）

| # | 任务 | 产出 / 判据 |
|---|---|---|
| **A1** | 取 ARM kbase **r49p1** 源码（`mali_kbase` midgard，GPLv2）。渠道：`developer.arm.com`（Mali GPU Driver source）/ `gitlab.arm.com`；**GitHub 不可达**。若需人工接受 license 下载 → 请用户提供 tgz | `kernel/modules/mali_vgpu/kbase/` 就位；含 `hw_access/`、`backend/gpu/`、`mali_kbase_ioctl.h` |
| **A2** | 从厂商 `.ko` 提取 **GPU 身份参数**（用于虚拟 regmap 上报 Mali-G57 MC2）：`GPU_ID`/`PRODUCT_ID`/`RAW_GPU_ID`/`L2_CONFIG`/`COHERENCY_NUM`/shader·tiler core 掩码。对照真机 `dumpsys SurfaceFlinger` 的 GLES 行（note13 `gpu.json` 已记录：`ARM, Mali-G57 MC2, OpenGL ES 3.2 v1.r49p1-03bet0`） | 虚拟 regmap 常量表 |
| **A3** | 云机 `strace -f` 厂商 DDK 初始化序列（MEOW + `libGLES_mali` + MTK HWC），抄下**实际 open 的设备与 ioctl 顺序**（`/dev/mali0`、`/dev/ged`、`/dev/ion`\|`/dev/dma_heap/*`、`/dev/m4u`、`/dev/dri/card0`） | Layer S 的**必需清单**（避免多做） |
| **A4** | 确认 DDK 对 `KBASE_IOCTL_GET_GPUPROPS` 的最低要求（缺哪些 prop 会 abort），以及 MEOW 层是否有**额外私有协议**（共享内存/自定义 ioctl） | B3 的 regmap/props 初值；MEOW 风险结论 |

> A3/A4 需要云机在线（当前 gold-15/14 因 SF 崩溃循环 adb 会掉线）→ 先做一次**只读取证引导**。

## 7.6 Phase B · Layer K：内核虚拟 Mali（主体工作）

> ⚠ **本节已按 Phase A 实测结论重写**（见 §7.13）：ARM kbase **自带 `CONFIG_MALI_NO_MALI`
> dummy model**，寄存器模拟与 job 完成语义均为官方现成 → 原设想的"自研 hw_access virtual
> backend / job 完成路径"**不需要写**，Phase B 收敛为"**配置 + 让它 probe 起来**"。

**选型（实测确定）**：编 kbase 源码，配置
`CONFIG_MALI_MIDGARD=m`、`CONFIG_MALI_EXPERT=y`、`CONFIG_MALI_NO_MALI=y`、
`CONFIG_MALI_NO_MALI_DEFAULT_GPU="tNAx"`（= Mali-G57）、`CONFIG_MALI_CSF_SUPPORT=n`（走 **JM**，
与厂商 r49p1 的 `mali_kbase_jm_*.c` 一致）、**不设** `CONFIG_MALI_REAL_HW`。

落点：`devices/xiaomi/gold-15/kernel/modules/mali_vgpu/`（照 `device_info` 的
`Kbuild`+`Makefile`+`.dddk-config` 模式，经 `dddk build --target android15-6.6` 构建）。

| # | 任务 | 要点（含实测依据） |
|---|---|---|
| **B1** | 建模块骨架，把 kbase 源码纳入构建 | `kbase/` 已就位（8.8M，r49p0-00eac0）；`Kbuild` 引入 `drivers/gpu/arm/midgard`，`MALI_USE_CSF=0` |
| **B2** | **造 platform_device 让 probe 命中** | kbase 是 `platform_driver`，`driver.name = KBASE_DRV_NAME`；platform bus 匹配顺序第 4 条是**名字匹配** → **无需 DT 节点**，注册 `name="mali"` 的 `platform_device` 即可。须带 **1 个 `IORESOURCE_MEM`**（`registers_map()` 里 `platform_get_resource(MEM,0)` 为空即 `-ENOENT`） |
| **B3** | 确认 NO_MALI 下无真硬件依赖 | 已实测：`kbase_common_reg_map()` 在 `CONFIG_MALI_NO_MALI` 下**直接 `return 0`**（不 `request_mem_region`/不 `ioremap`）；`kbase_gpu_device_create()` 仅 `midgard_model_create()` + `spin_lock_init`；`kbase_install_interrupts()` 用 **workqueue 模拟 3 个中断**（`nr_irqs=3`，无需真 IRQ） |
| **B4** | job 完成语义 | **官方 dummy model 已实现**：写 `JS_COMMAND_NEXT=1`(START) → `job_active=1` → `midgard_model_update()` → `signal_int()` → `update_register_statuses()` → `gpu_device_raise_irq(MODEL_LINUX_JOB_IRQ)` → kbase 走完成路径。即 §7.8 的 **B1 档免费** |
| **B5** | 容错项核对 | `regulator_get_optional` / `of_clk_get(of_node=NULL)` 均**错误被忽略**（只 break，`nr_regulators/nr_clocks=0`）；各处 `of_property_read_*` 在 `of_node==NULL` 时返回错误并被容错 |
| **B6** | 构建与加载接线 | `kernel/build/build-kernel.sh` 模块清单；`vendor_dlkm` 打包；加载时机；`ueventd.py` 追加 `/dev/mali0` 权限；`sepolicy.py` 追加 `mali_device` 类型 + 授权（照 keymint/pioneer 段的 marker 幂等追加法） |

**M0/M1 门在此达成**（`.ko` vermagic 匹配 + `/dev/mali0` 出现 + `eglInitialize` 成功）。

## 7.7 Phase C · Layer S：周边设备桩（按 A3 清单做，不多做）

- `/dev/ged`：GED 调度桩（DVFS/帧统计类 ioctl 返回默认值）；
- `/dev/gpud`：`libgpud` 依赖的桩；
- `/dev/ion` 或 **dma_heap MTK 名**：现状只有 `dma_heap/system` → 按 A3 实测补 heap 名或 ion 兼容层；
- `/dev/m4u`：MTK IOMMU 桩 → 映射到 dma-buf / virtio-gpu GEM；
- **gralloc / composer**：厂商 `gralloc.default.so` / `hwcomposer.mt6833.so` 若因 MTK 私有 KMS 属性失败，
  ~~则**新增** `gralloc.minigbm.so` / `hwcomposer.drm.so` 并用属性选型指过去~~
  **【已作废，2026-10-09 用户约束收紧】**：厂商图形栈**既不能替换也不能绕过**，
  故不得改 `ro.hardware.gralloc` / `ro.hardware.hwcomposer` 把厂商实现指到别处。
  唯一允许的解法是**补齐厂商 HWC/gralloc 所依赖的 MTK 内核接口**（cmdq / smi / m4u / ged /
  gpud / ion 或 MTK 命名的 dma_heap），让它们原样工作。前置取证 = A3（strace）。

## 7.8 Phase D · Layer B：渲染桥接通 virtio-gpu（分档推进）

| 档 | 做法 | 能得到 | 代价 |
|---|---|---|---|
| **B1 立即完成** | job 提交即标记完成，不产生像素 | **L1**：DDK 活着、SF 不崩、`boot_completed=1`；画面未定义 | 小（Phase B 内完成） |
| **B2 blit/copy 桥** | 解析 atom 的 render target dma-buf，做 buffer 搬运/清屏级填充，经 virtio-gpu 上屏 | 部分 **L2**：纯色/清屏/简单搬运可见 | 中 |
| **B3 job chain 解析 + shader 翻译** | 从 job chain（GPU VA 可直读）取 vertex/fragment job → 用 **Mesa panfrost 的 Mali ISA 前端**（`bi_compiler`/`valhall_dis`）转 NIR → 交 llvmpipe/virgl 渲染 → dma-buf → virtio-gpu | 完整 **L2/L3** | **研究级**（Mali Valhall ISA 复杂；建议 B1/B2 交付后再评估） |

> B3 是"真 GPU 模拟"的完整形态，也是唯一能让厂商 GL 应用正确出图的纯软件路径。
> 组件都有开源参考（panfrost/panvk 的 ISA 前端 + virglrenderer），但工程量大，**按档交付**。

## 7.9 Phase E · 验证与证据

- 每达成一个门（M0~M4）按 AGENTS.md 约束 2 写六段式
  `build/doc/evidence/issue-gold15-vendor-gpu/solution.md` + `log-fail.txt`/`log-pass.txt`，并更新索引；
- 双机对比（检测面不回退）：用 device-checker 的 `proc.vendor_so` / `proc.vendor_so_md5`
  复跑 note13 `vendor-so-identity.md` §6 流程，确认厂商 so 的**名字与 md5 未被本方案改动**；
- 引导验证：`sys.boot_completed=1`、`init.svc.surfaceflinger=running`、`screencap` 出图。

## 7.10 风险与回退

| 风险 | 影响 | 缓解 / 回退 |
|---|---|---|
| **R1 拿不到 r49p1 源码**（ARM 需接受 license；GitHub 不可达） | 方案 ① 无法启动 | 请用户从 `developer.arm.com` 下载 tgz 提供；否则退路只剩"反汇编重建 ABI"（§7.2 ②，代价极高）或降级到检测面路线（note13 §14 R1/R2） |
| **R2 闭源 DDK 强校验** GPUPROPS/job 语义 | M1 反复失败 | 按崩溃点逐条对齐 regmap/props；A2/A4 先把身份参数取准 |
| **R3 MEOW 层额外私有协议**（`ro.hardware.egl=meow` 在 Mali 之上还有一层厂商包装） | 即使 `/dev/mali0` OK 仍可能失败 | A3 strace 先确认 MEOW 的设备/ioctl/共享内存需求，必要时为 MEOW 也做桩 |
| **R4 B3 是研究级工程** | L3 可能长期不达成 | 按档交付：先 B1（L1）→ B2（部分 L2），B3 单独立项 |
| **R5 软件渲染性能** | 帧率低 | 预期内；云机以"检测面 + 可用性"为先 |
| **R6 内核模块加载时机/SELinux** | `/dev/mali0` 出现但 DDK 打不开 | 照 keymint 已有做法：`ueventd.py` 追加节点权限 + `sepolicy.py` 追加 `mali_device` 类型与域授权 |

## 7.11 明确不做

- 不换内核基座（保持 `6.6.127-4k-cdh`）；
- 不替换/不修改任何厂商 so 的字节与路径（只**新增**文件 + 属性选型）；
- 不做 Mali 硬件透传（宿主 Apple Silicon 无 Mali）；
- 不在本阶段做 B3 的完整 ISA 翻译（单独立项评估）。

## 7.12 落点文件清单（新增 / 修改）

**新增**
- `kernel/modules/mali_vgpu/`：`Kbuild`、`Makefile`、`.dddk-config`、`core/*.c`（platform stub）、
  `backend/virtual/*.c`（hw_access virtual backend）、`kbase/`（上游 r49p1 源码）
- `kernel/modules/gpu_stub/`（如需）：`/dev/ged`、`/dev/gpud`、`/dev/m4u`、ion/dma_heap 兼容桩

**修改**
- `kernel/build/build-kernel.sh`：模块清单纳入 `cdh_mali_vgpu`（+ 桩模块）
- `kernel/build/lineage-kernel.config`：如需 `CONFIG_DRM_MEDIATEK`/dma-buf heaps 等开关
- `build/vendor/build/ueventd.py`：追加 `/dev/mali0`（及 `/dev/ged` 等）节点权限
- `build/vendor/build/sepolicy.py`：追加 `mali_device` 类型 + `hal_graphics_composer`/`surfaceflinger`/
  `gpu_device` 相关域授权（照现有 keymint/pioneer 段的 marker 幂等追加法）
- `build/vendor/build/props.py`：如需 gralloc/hwcomposer 选型属性（`VENDOR_SETS` 追加）
- `build/baselines/<build-id>.json`：新增文件的 `selinux-labels`
- `build/doc/README.md`：索引更新

## 7.13 Phase A 实测结论（2026-10-09，已完成 A1/A2/A5）

### A1 · 源码已取得
- 渠道：**`https://gitlab.arm.com/arm-reference-solutions/mali-kernel-driver`**（`visibility: public`）
- 分支：**`r49p0-00eac0`**（另有 `main` / `r41p0-01eac0` / `r54p0-00eac0` / `r55p0-02eac0`）
- 已 shallow clone 到 `kernel/modules/mali_vgpu/kbase/`（**8.8 MB**，`CLONE_EXIT=0`）
- 网络实测：`github.com` = **000（不可达）**；`developer.arm.com` = 200；`gitlab.arm.com` = 302 → 走 ARM 官方渠道
- 版本差：厂商用 **r49p1**（`mali_avalon/mali-r49p1`），公开分支是 **r49p0** → 同 r49 系列，
  kbase↔DDK 之间有 `KBASE_IOCTL_VERSION_CHECK`/`GET_DDK_VERSION` 协商；**版本兼容性列为 M1 的首要验证点**

### A2 · GPU 身份（用于 dummy model 选型）
- 真机 source-of-truth（note13 `gpu.json`，device-checker + `dumpsys SurfaceFlinger` 双向核对）：
  `vendor=ARM`、`renderer=Mali-G57 MC2`、`version=OpenGL ES 3.2 v1.r49p1-03bet0.569c83bf…`、
  `vulkan_device_name=Mali-G57 MC2`
- kbase dummy model 身份表 `all_control_reg_values[]` 含 17 款，其中 **`tNAx` = Mali-G57**：
  `.gpu_id = GPU_ID2_MAKE(9, 0, 8, 1, 0, 0, 0)`、`as_present=0xFF`、`tiler_features=0x809`、
  `mmu_features=0x2830`、`thread_max_threads=0x180`
- 且源码内已有 **`base_hw_features_tNAx[]`**、**`base_hw_issues_model_tNAx[]`**（专为 model 构建准备）
  → **"NO_MALI + tNAx" 是 ARM 官方支持的组合**，不是我们硬凑
- 注：MC2（2 个 shader core）由 `DUMMY_IMPLEMENTATION_SHADER_PRESENT` 决定，需按 MC2 校准（A2 遗留项）

### A5 · NO_MALI 路径的硬依赖（决定 Phase B 工作量）
| 项 | 实测 | 结论 |
|---|---|---|
| `Kconfig: MALI_NO_MALI` 官方描述 | "…**All calls to the simulated hardware will complete immediately as if the hardware completed the task.**" | 正是 §7.8 **B1 档**语义，官方现成 |
| `MALI_NO_MALI_DEFAULT_GPU` | `string`，default `"tMIx"`，可设 `"tNAx"` | 身份可配 |
| `hw_access/Kbuild` | `CONFIG_MALI_REAL_HW=y` → `real_hw.o`；**否则** → `model_linux.o` | 后端选择是**编译期开关** |
| `backend/gpu/Kbuild` | `mali_kbase-$(CONFIG_MALI_NO_MALI) += model_linux.o model_dummy.o` | dummy model 随配置编入 |
| `kbase_common_reg_map()` | `#if IS_ENABLED(CONFIG_MALI_NO_MALI)` → **`return 0`**（不 `request_mem_region`、不 `ioremap`） | **无需真 MMIO** |
| `kbase_gpu_device_create()` | 仅 `midgard_model_create()` + `spin_lock_init(&reg_op_lock)` | **无需硬件资源** |
| `kbase_install_interrupts()` | 建 `irq_workq` + `irq_slab`，`nr_irqs=3`；`gpu_device_raise_irq()` 走 `queue_work` | **无需真 IRQ** |
| job 完成链 | 写 `JS_COMMAND_NEXT=1` → `job_active=1` → `midgard_model_update()` → `signal_int()` → `update_register_statuses()` → `raise_irq(JOB_IRQ)` | **job 立即完成**（B1 档免费） |
| `registers_map()` | `platform_get_resource(pdev, IORESOURCE_MEM, 0)` 为空 → `-ENOENT` | **必须给 1 个 MEM resource**（值任意，NO_MALI 不映射） |
| probe 匹配 | `kbase_platform_driver.driver.name = KBASE_DRV_NAME`，`of_match_table = kbase_dt_ids`（含 `arm,mali-valhall`） | platform bus 匹配第 4 条为**名字匹配** → **无需 DT 节点** |
| regulator / clk | `regulator_get_optional()`、`of_clk_get(of_node)` 失败均**被忽略**（break，计数 0） | 无 DT 不致命 |
| `kbase_driver_init()` | `#ifndef CONFIG_OF` 时才调 `kbase_platform_register()`；我们 `CONFIG_OF=y` → **需自己注册 platform_device** | B2 的落点 |

### A5 结论（对计划的影响）
Phase B 从「**自研 virtual hw_access backend + job 完成路径 + MMU 适配**」收敛为
「**编译配置 + 注册一个 `name="mali"` 的 platform_device（带 1 个 MEM resource）**」。
即 **M0/M1 的工作量大幅下降**；真正的工程量后移到 §7.7（周边桩）与 §7.8（B2/B3 渲染桥）。

### A 阶段遗留
- **A3**（云机 strace 厂商 DDK 初始化序列）与 **A4**（GPUPROPS 最低要求 / MEOW 私有协议）
  **未做**：需云机在线，而当前 gold-15 实例处于 SF 崩溃循环（adb 会掉线）。
  二者是 §7.7 Layer S 清单的依据 → 安排在 M1 达成后（届时 SF 不再崩，adb 稳定）再取。

## 7.14 Phase B 实施实况（2026-10-09 起）

### 已落地文件
| 文件 | 作用 |
|---|---|
| `kernel/modules/mali_vgpu/kbase/`（clone） | ARM kbase r49p0-00eac0 源码（8.8 MB） |
| `kernel/modules/mali_vgpu/plat/cdh_mali_plat.c`（新） | 注册 `name="mali"` 的 platform_device + 1 个占位 `IORESOURCE_MEM`（`0x40000000000`+1 MiB，NO_MALI 下不映射），让 kbase 的 probe 经**名字匹配**命中 |
| `kernel/modules/mali_vgpu/{Kbuild,Makefile,.dddk-config}`（新） | 照 `device_info` 同构，产出 `cdh_mali_plat.ko` |
| `kernel/build/build-kernel.sh`（改 4 处） | ① `build_recipe` → `…-v29-mali-vgpu`（使 prebuilt 缓存失效）② `module_dirs` += `kernel/modules/mali_vgpu` ③ dry-run `publishedFiles` += `mali_kbase.ko`/`cdh_mali_plat.ko` ④ 容器脚本内**动态 in-tree 接线** + MALI 配置 + 产物收集 |

### 容器内 in-tree 接线（不污染宿主 git）
内核树由 `git archive HEAD` 导入且脚本**拒绝未提交的工作树修改**，故接线全部在容器里做：

```sh
cp -a kernel/modules/mali_vgpu/kbase/drivers/gpu/arm  drivers/gpu/arm
cp -a kernel/modules/mali_vgpu/kbase/include/linux/.   include/linux/
cp -a kernel/modules/mali_vgpu/kbase/include/uapi/.    include/uapi/
echo "source \"drivers/gpu/arm/midgard/Kconfig\""     >> drivers/Kconfig
echo "obj-\$(CONFIG_MALI_MIDGARD) += arm/midgard/"    >> drivers/gpu/Makefile
```

> 本树是 **GKI 布局**：`drivers/gpu/Kconfig` **不存在**，`drivers/Kconfig` 仅
> `source "drivers/gpu/trace/Kconfig"` → 接线点只能是上面这两处。

配置（`scripts/config` 后 `olddefconfig`）：
`MALI_MIDGARD=m`、`MALI_EXPERT=y`、`MALI_REAL_HW` 关、`MALI_NO_MALI=y`、
`MALI_NO_MALI_DEFAULT_GPU="tNAx"`、`MALI_CSF_SUPPORT` 关（→ `MALI_USE_CSF=0` 走 JM）、
`MALI_PLATFORM_NAME="devicetree"`、`MALI_ARBITRATION`/`MALI_KUTF`/`MALI_DEVFREQ` 关。
`MALI_MIDGARD` 会 `select GPU_TRACEPOINTS/DMA_SHARED_BUFFER/PM_DEVFREQ/DEVFREQ_THERMAL/FW_LOADER`
（这些在内核里已 `=y`，`CONFIG_TRACEPOINTS=y`）。

### B6 接线大幅简化（实测：三项都**不用改**）
| 项 | 实测结论 |
|---|---|
| 设备节点 | kbase 用 `misc_register(&kbdev->mdev)`，`devname = "%s%d"` → **`/dev/mali0`**，与厂商 DDK 打开的路径一致 |
| **ueventd** | 官方 gold `/etc/ueventd.rc` **已有** `/dev/mali*  0666  system  graphics`（另有 `/dev/ion 0666 system graphics`、`/dev/dri/card0 0660 system system`）→ **无需改 `ueventd.py`** |
| **sepolicy** | 官方 `vendor_file_contexts` **已有** `/dev/mali.*  u:object_r:gpu_device:s0`；`vendor_sepolicy.cil` 中 `gpu_device` 相关授权 **48 条** → **无需新增 `mali_device` 类型**，也不用补授权 |
| vendor_dlkm 打包 | `build/vendor_dlkm/build/modules.py` 的 `discover_modules()` **不硬编码清单**，以 `out/dist/kernel/modules/` 内实际 `.ko` 为准并自动生成 `modules.load` → 新 `.ko` **自动入包**；且 `cdh_mali_plat` 与 `mali_kbase` 无符号依赖，加载先后均可（platform bus 两侧注册时都会尝试匹配） |

> 这是 gold-15「**基座 = 官方 vendor**」的红利：MTK 的 Mali 权限/标签/授权本来就齐备，
> 我们只需把**设备**造出来。note13（lineage 基座）则没有这些。

### 当前状态
- 内核构建中（`out/tmp/kernel-mali-1.log`），M0 门待验：`mali_kbase.ko` + `cdh_mali_plat.ko`
  能否产出、`modinfo vermagic` 是否为 `6.6.127-4k-cdh`。

## 7.15 M0 达成；M1 部分达成（节点就位，DDK 兼容性检查未过）（2026-10-09，实例 gold-15/16）

### 达成现场
```
cdh_mali_plat: 已注册 'mali'（占位寄存器窗口 40000000000+100000，NO_MALI 下不映射）
mali mali.0: Kernel DDK version r49p0-00eac0
mali mali.0: Using Dummy Model
mali mali.0: Register LUT 00090000 initialized for GPU arch 0x00090008
mali mali.0: GPU identified as 0x1 arch 9.0.8 r0p0 status 0     ← arch 9.0.8 = tNAx = Mali-G57
mali mali.0: No OPPs found in device tree! Scaling timeouts using 100000 kHz
mali mali.0: No priority control manager is configured
mali mali.0: No memory group manager is configured
mali mali.0: No clock(s) available for rate tracing
mali mali.0: Probed as mali0
```
`adb shell ls -lZ /dev/mali0`：
```
crw-rw-rw- 1 system graphics u:object_r:gpu_device:s0  10, 263  /dev/mali0
```
- 权限来自官方 `/etc/ueventd.rc` 的 `/dev/mali*  0666  system  graphics`（**未改 ueventd**）
- 标签来自官方 `vendor_file_contexts` 的 `/dev/mali.*  → gpu_device`（**未改 sepolicy**）
- 无 Oops（`power_control_init` 补丁生效）

> ⚠ **一处误判已纠正**：先前本节曾写「`eglInitialize`/`no suitable EGLConfig` 计数 = 0，
> 厂商 EGL 初始化通过」。实际由脚本统计为 **48 / 77 次**（见
> `evidence/issue-gold15-vendor-gpu/log-pass.txt` 判据 5）。误判来自
> `grep -inE "eglInitialize|…|surfaceflinger" | head -12` 的输出被 `head` 截断。
> **M1 未达成**，真实根因见 §7.16。

### 构建期修的两个问题
| # | 现象 | 修法 |
|---|---|---|
| 1 | `mali_kbase_model_dummy.c:1329 error: unannotated fall-through [-Werror,-Wimplicit-fallthrough]` | `wire-kbase.sh` 幂等补丁：`case GPU_COMMAND_PRFCNT_SAMPLE` 补 `break`（`default` 为空操作，语义等价） |
| 2 | 运行期 Oops：`pc of_clk_get+0x28`、`x0=0`、访问 `device_node.full_name`(偏移 `0x10`) | `wire-kbase.sh` 幂等补丁：`power_control_init()` 在 `!kbdev->dev->of_node` 时提前 `return 0`（与该函数 `#ifndef CONFIG_OF` 分支注释的原意一致） |

> 两处都在**构建期**打补丁（`wire-kbase.sh`），kbase 源码保持 pristine，便于将来升级 r49p1/r54p0。
> 另核对：`platform/devicetree/mali_kbase_runtime_pm.c` 不用 `of_node`；`device/mali_kbase_device.c:261`
> 已有 `if (np != NULL)` 保护；其余 `of_*` 辅助函数对 NULL np 安全 → 只需上述 1 处运行期保护。

### 新阻塞点（Phase C 的第一目标）
`composer@3.3-service` **SIGSEGV**（`fault addr 0x1c`，null pointer dereference），栈：
```
#00 hwcomposer.mt6833.so  HWCDisplay::HWCDisplay(unsigned long const&, CreateDisplayInfo const&,
                                                 android::sp<IOverlayDevice> const&)+2028
#01 hwcomposer.mt6833.so  HWCMediator::initHWCDisplay()+724
#02 hwcomposer.mt6833.so  HWCMediator::HWCMediator()+1000
#03 hwcomposer.mt6833.so  getHWCMediator()+288
#04 hwcomposer.mt6833.so  _GLOBAL__sub_I_hwc2.cpp+8        ← 静态初始化期即崩
#10 libhardware.so        hw_get_module_by_class+576
#11 composer@3.3-service  HwcLoader::init(...)+100
```
→ MTK HWC 需要从 MTK 私有 DDP/`libdpframework` 取得 `IOverlayDevice`，在 virtio-gpu 的
`/dev/dri/card0` 上取不到 → NULL 解引用。**与 Mali/GPU 无关**，属 §7.7 Layer S 的 composer 选型问题。

同期另有：
- `android.hardware.boot-service.mtk` SIGABRT：`Abort message: 'Check failed: impl_.Init() '`
- `avc denied { read } surfaceflinger → large_page_conf (sysfs)`：我们的设备路径是
  `platform/mali.0`，真机是 `1c500000.mali` 之类 → 官方 mali sysfs 的 `file_contexts` 规则匹配不上，
  落成通用 `sysfs`（需按 §7.12 补标签 + 授权）

### 下一步（Phase C，已按用户约束改写）
> **约束收紧（用户 2026-10-09）**：厂商图形栈**既不能替换、也不能绕过**——不得用
> `ro.hardware.hwcomposer` / `ro.hardware.egl` / `ro.hardware.gralloc` 等属性选型把厂商实现
> 指到别处（那等于让厂商 so 不工作）。**先前拟议的「新增 `hwcomposer.drm.so` +
> `ro.hardware.hwcomposer=drm`」方案作废。**

因此 MTK HWC 的 SIGSEGV 只能靠**模拟它依赖的 MTK 内核接口**来解（§7.7 Layer S）：
DDP/`libdpframework` 所需的 `cmdq`、`smi`、`/dev/m4u`、`/dev/ged`、`/dev/gpud`、
ion 或 MTK 命名的 dma_heap。前置是 **A3**（strace 抄出 HWC 实际打开的设备与 ioctl），
而 A3 需要 adb 稳定 → 先过 §7.16 的 DDK 兼容性检查。

### 已知工程问题（不影响正确性）
`CDH_KERNEL_INCREMENTAL=1` 已跳过源码导入与配置阶段，但 `make` 仍重编 `scripts/`
（`HOSTCC scripts/basic/fixdep`、`scripts/kconfig/*`）并触发全量（约 20 分钟/轮）；
疑与 `SYNC include/config/auto.conf` 刷新依赖有关，待查。

## 7.16 M1 的真实阻塞：厂商 DDK 兼容性检查（GPU 版本 r0p0 vs r0p1）

实测（实例 gold-15/16，SF 进程 pid 805）：

```
D libMEOW : load [plugin][system] driver: libMEOW_qt.so / libMEOW_gift.so   ← MEOW 层加载正常
D MMUD    : mtk_memory_debug_init surfaceflinger …
W mali    : BASE: uku_open <unknown>
W mali    : ==>[WARN] file /dev/mali0 is different version (user 11.46, kernel 11.45)
E mali    : ERROR: The DDK (built for 0x9000003 r0p1 status range [0..15]) is not compatible
            with this Mali GPU device, /dev/mali0 detected as 0x9000801 r0p0 s…
E mali    : Failed creating base context during DDK compatibility check.
W libEGL  : eglInitialize(0x…) failed (EGL_NOT_INITIALIZED)
F RenderEngine: no suitable EGLConfig found, giving up
F libc    : Fatal signal 6 (SIGABRT) … (surfaceflinger)
```

| # | 不匹配项 | 厂商 DDK | 我们（r49p0 dummy model） | 严重性 |
|---|---|---|---|---|
| 1 | UK 版本 | `user 11.46`（r49p1-03bet0） | `kernel 11.45`（r49p0-00eac0） | WARN（暂未见致命） |
| 2 | **GPU 版本** | **`r0p1`**（`0x9000003`，status range [0..15]） | **`r0p0`**（`0x9000801`） | **致命** |

**GPU_ID2 位布局**（`include/uapi/gpu/arm/midgard/gpu/mali_kbase_gpu_id.h`）：
`VERSION_STATUS` bits[3:0]、`VERSION_MINOR` bits[11:4]、`VERSION_MAJOR` bits[15:12]、
`PRODUCT_MAJOR` bits[19:16]、`ARCH_REV` bits[23:20]、`ARCH_MINOR` bits[27:24]、`ARCH_MAJOR` bits[31:28]；
`GPU_ID2_MAKE(arch_major, arch_minor, arch_rev, product_major, version_major, version_minor, version_status)`。
dummy model 的 tNAx 条目原为 `GPU_ID2_MAKE(9, 0, 8, 1, 0, 0, 0)` → `version_minor=0` = **r0p0**。

**补丁 3（只改我们自己的内核模块）**：`version_minor` 0 → 1，即 `GPU_ID2_MAKE(9, 0, 8, 1, 0, 1, 0)`
→ 上报 **r0p1**。product 部分（`arch_major=9`、`product_major=1`）不变，故 `mali_kbase_hw.c`
按 `GPU_ID_PRODUCT_TNAX` 查 `base_hw_features_tNAx` / `base_hw_issues_model_tNAx` 仍命中，
不会落入 `Unknown Product ID` → `-EINVAL`。已写入 `wire-kbase.sh`，重编验证中
（`out/tmp/chain13-mali-r0p1.log`）。

**若补丁 3 后 DDK 仍拒绝**（即 UK 版本差也致命），备选：
① 试 ARM 公开的 `r54p0-00eac0` / `r55p0-02eac0` 分支（UK 版本更高，但需确认仍支持 JM 路径）；
② 只改 `BASE_UK_VERSION_MINOR` 45→46（**有风险**：须先比对 `mali_kbase_ioctl.h` 的结构体布局
是否随 UK 版本变化，否则与厂商 DDK 的 ioctl 结构不一致会更难查）。

### §7.16 验证结果（2026-10-10，实例 gold-15/18）：M1 达成 ✅

按 §7.16 补丁终态（补丁 3 = tNAx 身份改 `GPU_ID2_MAKE(9,0,0,3,0,1,0)` → product_id `0x09000003`/r0p1；
补丁 4 = `mali_kbase_hw.c` features/issues 两处 switch 补 `case GPU_ID_MODEL_MAKE(9,3):` 复用 tNAx 表）
重编引导后，**计数判据全部归零**：

```
GPU identified as 0x3 arch 9.0.0 r0p1 status 0
Probed as mali0
is not compatible with this Mali GPU  计数 0   （补丁前 16）
Failed creating base context          计数 0   （补丁前 16）
eglInitialize（失败）                  计数 0   （补丁前 32）
no suitable EGLConfig                 计数 0   （补丁前 46）
```

UK 版本差（user 11.46 / kernel 11.45）经两轮实测**仅为 WARN，不阻塞** → §7.16 的两个备选路径
暂不需要。原始证据：`evidence/issue-gold15-vendor-gpu/log-pass.txt`（M1 达成现场）、
`log-fail.txt`（剩余阻塞现场）。

**M1 达成后的剩余图形阻塞（M2 的卡点）= MTK HWC（唯一）**：
`composer@3.3-service` 在静态初始化期 SIGSEGV——`hwcomposer: [DRMDEV] connectAllDisplay(), unknow type 15`
（MTK HWC 枚举 virtio-gpu card0 的 connector，遇 type 15=VIRTUAL 不认识）→ `IOverlayDevice` 取不到 →
`HWCDisplay::HWCDisplay()` NULL 解引用（`fault addr 0x1c`，28 次）。
这给 A3 提供了直接证据：**厂商 HWC 的显示依赖走标准 DRM 接口枚举 card0**，与 cmdq/m4u 无关的
部分先行成立。解法仍按约束 = 在内核侧模拟 MTK 显示接口（§7.7 Layer S），不碰厂商 so。

## 7.17 Phase C 第一步：MTK HWC 的 connector 枚举面取证（2026-10-10）

### 取证方法与结果

对官方 `vendor_a.img` 内 `lib64/hw/hwcomposer.mt6833.so`（2024520 B）静态反汇编：
日志串 `"[%s] %s(), unknow type %u"` 的 VA = `0x62251`（`.rodata`），由 `adrp 0x62000 + add #0x251`
唯一定位于 **`DrmModeResource::connectAllDisplay()`**（`0x18e418` 附近），其对
`drmModeConnector->connector_type` 的判定只有三条路：

```
cmp w23, #0x12   ; 18 = DRM_MODE_CONNECTOR_WRITEBACK → 次显示分支（w15=2）
b.eq ...
cmp w23, #0x10   ; 16 = DRM_MODE_CONNECTOR_DSI → 主显示（internal）分支
b.ne 18e4ac      ; 其余 → DrmModeConnector::isConnectorTypeExternal()
                 ;   VIRTUAL(15) 判非外接 → "connectAllDisplay(), unknow type 15"
                 ;   → 不创建任何 display → HWCDisplay 构造时 IOverlayDevice NULL → SIGSEGV
```

另外确认：HWC 通过标准 libdrm 符号 `drmModeGetConnector/drmModeGetCrtc/drmModeGetEncoder`
（`DrmDevice`/`DrmModeResource` C++ 封装）枚举 KMS——**厂商 HWC 的显示依赖走标准 DRM 接口**，
无 cmdq/m4u 介入此路径（它们在后续合成/提交阶段才可能出现）。

### 解法：补丁 5（Phase C 第一补丁，极薄）

guest 内核 virtio-gpu 在 `virtgpu_display.c` 里硬编码
`drm_connector_init(..., DRM_MODE_CONNECTOR_VIRTUAL)`（全目录唯一一处）。
把它改为 **`DRM_MODE_CONNECTOR_DSI`** → MTK HWC 会将 virtio card0 按**主显示（internal DSI）**路径处理。

- 落点：`wire-kbase.sh` 补丁 (5)（容器内幂等打补丁，宿主内核树不动；virtio-gpu 为本内核 built-in）；
- 约束符合性：只改我们自己的内核一行，**不碰厂商 so、不改属性选型**；
- 状态：重编验证中（`out/tmp/chain15-dsi.log`）。

### 预期与后续

- 若 connectAllDisplay 认下 DSI connector 后，HWC 会继续要求主 CRTC
  （反汇编里可见 `DrmModeCrtc::isMainCrtc()` / `DrmObject::getId()` 等调用）与显示模式、
  overlay port 参数等 KMS 形态——**这些是 Phase C 第二步的取证/模拟对象**；
- `boot-service.mtk` 的 `'Check failed: impl_.Init()'`（14 次）与此路径无关，另行排查；
- 每轮迭代仍以计数判据验证：`unknow type`、`composer@3.3` SIGSEGV、`surfaceflinger` 重启次数。

## 7.18 Phase C 第一步验证通过 + 第二步（MTK KMS 属性集）（2026-10-10）

### 第一步验证结果（实例 gold-15/21，计数判据）

| 判据 | 19（旧内核） | 21（补丁 5） |
|---|---|---|
| `unknow type` | 24 | **0** |
| `HWCDisplay::HWCDisplay` NULL deref | 24 | **0** |
| `composer@3.3` Fatal | 24 | **0** |
| `IComposer/default` 注册存活 | ✗（崩溃循环） | ✅ |
| connector sysfs | Virtual | **`card0-DSI-1 connected 1080x2400`** |
| `eglInitialize` 失败（回归） | 0 | 0 |

流程教训两条（已入 solution.md）：
1. built-in 内核（Image）改动必须重跑 `build-boot.py`（19 只刷了 super，跑的还是旧内核）；
2. `daemon run N | head` 会让 daemon 因 SIGPIPE 被杀（实例 20 未起、qemu.log 不存在）——
   实例启动一律用文件重定向。

### 第二步取证：49 个 MTK 私有 CRTC KMS 属性（composer 启动日志，pid 596）

```
E [DRMDEV] failed to get drm master info, err: -1
I [DRMDEV] getCaps(), crtc[id=33|pipe=0x0]
W 0xcccccccc[33] property[OVERLAP_LAYER_NUM] does not do initialize
… 共 49 个：OVERLAP_LAYER_NUM/LAYERING_IDX/PRESENT_FENCE/DOZE_ACTIVE/OUTPUT_*/
INTF_BUFF_IDX/DISP_MODE_IDX/COLOR_TRANSFORM/USER_SCEN/HBM_ENABLE/HDR_ENABLE/
MSYNC2_0_*/OVL_DSI_SEQ/OUTPUT_SCENARIO/CAPS_BLOB_ID/AOSP_CCORR_LINEAR/
PARTIAL_UPDATE_ENABLE/BL_SYNC_GAMMA_GAIN/DYNAMIC_WCG_OFF/WCG_BY_COLOR_MODE/
DIM_COLOR/STYLUS_MODE/PANEL_NITS/OVL_CSC_SET_*/DIRTY_ROI_*/BUFFER_ALLOC_ID/
NEXT_BUFF_IDX/COMPRESS/CSC_BL/DATASPACE/IS_MML/MML_SUBMIT/PLANE_PROP_*/VPITCH
```

**后果链**：caps 查不到 → `pipe=0x0` → `connectAllDisplay` 的闸门（反汇编 `[x19+284]` 字节）
为 0 → display 0 "not connected" → SF 查询全 BadDisplay → MIUI 版 SF
`configureLocked → Scheduler::dispatchHotplugError` NULL deref → SF 无限 restarting。

### 第二步方案：补丁 6（virtgpu_mtk_props，进行中）

- **文件已写好**：`kernel/modules/mali_vgpu/mtk-props/virtgpu_mtk_props.c`——
  49 个属性名逐字对齐实测清单，以 `drm_property_create_range`（`DRM_MODE_PROP_ATOMIC`）
  挂到 virtio CRTC，合理初值（`OUTPUT_*`/`DST_*`=1080x2400 与 EDID mode 一致、
  `OVERLAP_LAYER_NUM`=4、fence/sync 类=0、`OUTPUT_ENABLE`=1）。
- **接线点已确认**（经 `wire-kbase.sh` 容器内打补丁，宿主内核树保持干净）：
  `virtio/Makefile` 的 `virtio-gpu-y` 追加 `virtgpu_mtk_props.o`；
  `virtgpu_display.c` 的 `drm_crtc_helper_add(crtc, …)` 之后调用
  `virtio_gpu_mtk_attach_crtc_props(dev, crtc)`（extern 声明与 missing-prototypes 处理见实现）。
- **迭代策略**：先让 `getCaps` 过（display 建立起）再看 HWC 下一步实际报什么；原子 set 语义
  （`atomic_set_property`）按报错逐层补。`CAPS_BLOB_ID` 若要求真 blob 内容（MTK 私有 caps 结构），
  是本步的最大不确定项，届时按 HWC 读取路径反推最小结构。
- 上游 `mediatek-drm` 精简版无这些属性名（厂商 stock 内核私有）；`github.com`（MICode 内核源）不可达
  → 属性名以**实测日志**为准（已归档 `log-fail.txt`）。

### 第二步验证结果（实例 gold-15/28，落盘流取证）

- **(6f) 编译修复**：(6d) 在 `virtio_gpu_crtc_funcs` 成员里引用 (6e) 的 extern 声明出现
  use-before-declare → 构建失败。新增 (6f) 把 extern 前置到结构体定义之前（实例 24 轮实证）。
- **crtc 49 属性判据全过**：`getCaps(), crtc[id=33|pipe=0x0]` 正常执行、
  49 条 `does not do initialize` 与 `failed to init crtc property: errno[-19]` **全部消失**、
  `List of all crtc (total:1)` 打印 `DrmModeCrtc_0x0: id=33 planes=1 WxH=0x0 mode:(0|1080x2400)`——
  MTK 读到了 virtio-gpu 的 mode。
- `create_disp_infos size 1` → HWCDisplay 0 建成（`type=1 internal=1 main=1 crtc=33,-1 connector=83`）——
  **connectAllDisplay 成功**（DSI 主显示路径、getPlaneNum=1>0、mMainCrtc 赋值均过）。
- **取证方法论（重要）**：logcat main buffer 开机后被 SF tombstone + BOOTPROF 刷掉，composer init 段
  （~25.1-25.4s）"消失"是假象。唯一可靠手段：adb 一可用立即在 guest 后台
  `nohup logcat -v time > /data/local/tmp/boot.log &` 落盘流（28 轮全量拿到）。
  另：连接前 `adb kill-server` 可避免 offline 卡死。
- **剩余失败（非致命，暂不挡路）**：connector 83 缺 CSC_BL/PANEL_NITS 2 属性（-19）、
  plane 31 缺 19 属性（-19）、`DRM_IOCTL_MTK_GET_PANELS_INFO` 无 handler（-1）、
  DIM fb `add fb ret=-22`（4096x4096 vendor 格式）——均失败但不中断 init。

## 7.19 Phase C 第三步：display session 闸门（28 轮新阻塞 + 反汇编取证，2026-10-10）

### 28 轮新阻塞链（28-boot-full.txt 3547-3612）

HWCDisplay 0 建成后，SF 首次注册 hotplug callback（25.885）触发 onPlugIn 流程：

```
[XMFP]  open /sys/devices/platform/14013000.dsi/14013000.dsi.0/panel_event failed
[DRMDEV] (0) DISP_IOCTL_CREATE_SESSION (DL), sid:0xffffffff err:-1   ← 内核无 handler
[OVL]   (0) Failed to create display session
[JOB]   (0) Failed to create OverlayEngine (dpy=0) !!
[DPY]   genEdid: panel info is null, use fake edid
[DPY]   Display Information: ... connected   : 0                     ← 闸门
[HWC]   displayGetDisplayConnectionType: the display(0) is not connected（HAL API 全拒）
SF      Failed to hotplug display 0(HAL ID 0) → configureLocked NULL deref → SF 崩溃循环
```

### 反汇编取证（analyze_hwc11.py/xref + dump，out/tmp/analyze_hwc11.out）

1. **`DrmDevice::createOverlaySession` @0x17dbf0**：向 **card0 fd** 发 ioctl
   **`DISP_IOCTL_CREATE_SESSION = 0xc0106443`**（type=0x64'd'，nr=0x43，size=16B；
   入参 `[+0]=type 表值 [+4]=dpy [+8]=session_mode 表值 [+12]=sid(out)`）。
   失败 → `setSessionId(-1)`、打日志、返回 -22；成功 → `setSessionId(sid)`+`activateDisplay`。
2. **`OverlayEngine::OverlayEngine` @0xed3f0**：构造内虚调用 `[vtable+296]`
   （→createOverlaySession）失败打 "Failed to create display session"；成功置
   **`[this+380]=1`**，再虚调用 `[vtable+232]` 取 OverlayInput 槽数存 `[this+384]`
   （槽数应来自 OVERLAY_LAYER_NUM=4，属性已挂）。
3. **`HWCDispatcher::onPlugIn` @0xd2d60**：new OverlayEngine 后检查
   **`[OverlayEngine+380]==1`**，否则释放并 `DisplayData+432=NULL`、打
   "Failed to create OverlayEngine" → display 永不上线。
4. **HAL API 判据**：`HWCMediator::displayXXX` 系列读
   `DisplayManager::getDisplayData(dpy)` 的 **`[DisplayData+33]`（connected 字节）**，
   为 0 打 "the display(0) is not connected" 并返回 BadDisplay(2)。

### 关键事实：MTK 私有 ioctl 全部走标准 DRM driver-defined ioctl 通道

nr∈{0x43 CREATE_SESSION, 0x48, 0x4c master info, 0x9a GET_PANELS_INFO} 均落在
`DRM_COMMAND_BASE(0x40)~DRM_COMMAND_END(0xA0)`；标准 `drm_ioctl()` 按
`driver->ioctls[nr-0x40]` 分派，virtio-gpu 表未填 → **ENOTTY**（err:-1）。
四个 ioctl 中只有 CREATE_SESSION 失败即致命（无 fallback），其余三个失败仅打日志。

### 方案：补丁 7（virtio-gpu 挂 MTK 私有 ioctl nr=0x43，极薄）

给 `virtgpu_ioctl.c` 的 `virtgpu_ioctls[]` 表补 nr=0x43 条目：分配一个 u32 session id
（per-CRTC 单调递增、避开 -1）写回用户结构 +12 并返回 0。不碰厂商 so、不改属性选型，
内核新增桩 ~60 行（复用 virtgpu_mtk_props.c 文件追加）。
验证判据（落盘流）：`CREATE_SESSION ... err:-1` 消失、`Failed to create display
session`/`Failed to create OverlayEngine` 消失、`[DPY] connected : 1`、
`is not connected` 消失、SF 无 `Failed to hotplug display 0`、boot 走完无崩溃循环。
预期后续（session 建立后 present 链的 SET_BUFFER/TRIGGER 等 ioctl）为下一层，按报错逐个补。

## 7.20 Phase C 第四步：补丁 7/8/9/10 四轮剥洋葱——session→dumb bpp→fb modifiers（2026-10-10）

### 补丁 7 验证通过（run29，recipe v32）

`cdh_mtk: CREATE_SESSION type=1 dpy=0 mode=1 -> sid=1..N` 内核打印出现（DRM_INFO），
`Failed to create display session`/`Failed to create OverlayEngine`/`is not connected`
全部消失；**RenderEngine GL 栈活了**：`renderer: Mali-G57 MC4, version: OpenGL ES 3.2
v1.r49p1-03bet0...`（虚拟 Mali NO_MALI tNAx + 厂商 DDK 用户态跑通）。失败点推进到
allocateBuffer。

### 补丁 8：dumb create 放宽 bpp（run30，recipe v33）

反汇编（out/tmp/analyze_hwc12.out）：`DrmModeResource::allocateBuffer`@0x1910d0 调
`DRM_IOCTL_MODE_CREATE_DUMB(0xc02064b2)`+`MAP_DUMB(0xc01064b3)`，MTK prepareFb 会话哑
buffer 要 **8bpp**（1080x2400）；上游 `virtio_gpu_mode_dumb_create` 只认 bpp==32 →
EINVAL → `failed to set display_0: prepare buffer` → MTK abort。补丁放宽为 8 的倍数
（8..64），pitch 按 bpp 折算；宿主 2D 资源仍按 XRGB8888 建、宽度 pitch/4 折算保
stride 一致。run30 判据通过：`failed to create dumb buffer` 消失。

### 补丁 9 的教训：一个错的根因假设 + run31 的"内核身份"疑云（recipe v34）

run30 深层链新现 `failed to add fb ret=-22, w:1080 h:2400 f:38424752('RGB8')`，当时
归因于 `drm_gem_fb_create_with_funcs` 的 `drm_any_plane_has_format`（格式表拒）→
补丁 9 给 virtio-gpu primary plane 格式表加 RGB332/C8。**run31 验证仍 -22**，且
`uname -r` 显示 `6.6.89-android15-8-gbe8d201b0d27-ab13762941-4k` 与 cdh_mtk 打印并存，
看似矛盾。裁决（字节级判据链）：
- `/proc/version` 编译主机是 `root@0acd6ee907f1`（**Docker 容器**）+ Ubuntu clang 21.1.8
  → uts 串系 gold-15 构建链**刻意伪装**为 MIUI stock（vendor 模块 vermagic 匹配），
  uname 从一开始就区分不了任何 v3x，"矛盾"不成立；
- boot.img 内 kernel@4096（v4 header，header_size=1584）sha256 == chain22 Image；
- vda GPT boot_a/boot_b 分区字节 == out/dist/rom/boot.img；
- Image 内 `XR24AR24...AB24RGB8C8  ` 10 格式表序列命中（offset 19963896）→ **v34
  确在跑**。结论：补丁 9 生效但打错了地方——-22 与格式无关。

### 真根因与补丁 10：fb_modifiers_not_supported（run32，recipe v35）

反汇编（out/tmp/analyze_hwc13.out）：MTK `DrmModeResource::addFb`@0x190850 调
**`drmModeAddFB2WithModifiers`**（modifier 数组参数恒非 NULL，bo+0x60；会话哑 buffer
值为 0/LINEAR）→ libdrm 置 `DRM_MODE_FB_MODIFIERS` → 内核
`drm_internal_framebuffer_create`（drm_framebuffer.c:311）见 virtio 声明的
`fb_modifiers_not_supported=true`（virtgpu_display.c:387）→ **无条件 -EINVAL**。
订正：virtio 的 fb_create=virtio_gpu_user_framebuffer_create（virtgpu_display.c:316）
**根本不走** drm_gem_fb_create_with_funcs、无任何格式检查；补丁 9 仅对 GETPLANE
呈报有益，保留无害。run31 之前未暴露此层，是因 run29 前 MTK 卡在 CREATE_SESSION
闸门即 abort，add fb 链是 run30 打通 dumb 后才首次被走到。
补丁 10 = 删除 `fb_modifiers_not_supported = true` 一行（virtio 2D scanout 忽略
fb->modifier，放行无害）。

### run32 判据（全绿）

- `failed to add fb ret=-22 ... hnd:1`（RGB332 会话哑 buffer）**消失**；
- `[DPY] connected : 1`（run29-31 恒 0）；
- composer@3.3-service（hwcomposer pid 594）**存活不再 abort**——dmesg
  `cdh_mtk: CREATE_SESSION` 仅 1 次（此前每崩一次重开一次 session）；
- 剩余：dim 层 `C8 4096x4096 hnd:0xFF79E6ED`(PRIME 导入) 仍一条 -22（独立子问题，
  present 链 queryPanelsInfo 阶段，容忍不崩）；`enableHWVSync/waitNextVsync -1`、
  `Failed to get presentFence`（私有 vsync/fence ioctl 未实现，仅打日志）。

### 下一层（run32 新卡点）

SF RenderEngine 线程 SIGABRT 循环（~5s）：`Abort message: 'output buffer not gpu
writeable'`，背架 `RenderEngine::validateOutputBufferUsage` ←
`SkiaRenderEngine::drawLayersInternal` ← `Cache::primeShaderCache`（tombstone_10）。
primeShaderCache 分配的输出 GraphicBuffer usage 缺 `USAGE_HW_RENDER` 位 → 指向
gralloc（MEOW/mtk）分配链的 usage 处理，真机不崩云机崩。子问题队列：
- SF primeCache 'output buffer not gpu writeable'（本轮下一目标）
- dim 层 C8 PRIME 导入 -22
- vendor.xiaomi.h* SEGV 0x0（独立链，待查是否 SF 连锁）

## 7.21 Phase C 第五步：gralloc heap 别名 + dma_coerce（补丁 11 v1→v2）→ GED 桥 stub（补丁 12）（2026-10-10）

### primeCache 断言根因三链闭环（反汇编 + guest 日志 + AOSP libui 语义）

SF tombstone `Cache::primeShaderCache+3592`（0x5d3df8，vtable+96 = drawLayers
虚调用）→ 反汇编实锤（out/tmp/sf-dump2.txt）：
- primeShaderCache @0x5d2ff0 两次构造 `GraphicBuffer(128,128,RGBA_8888,1,usage
  0x300=HW_RENDER|HW_TEXTURE)`（0x5d37d4/0x5d38a0 处 `mov w5,#0x300`）——**usage
  请求值正常**；
- drawLayersInternal @0x5c9ed0 → `bl validateOutputBufferUsage`（0x5c9f48）；
- validateOutputBufferUsage @0x5bbd00：`getUsage()&0x200==0 → __android_log_assert`
  → LOG_FATAL。
guest 日志补第三链：`libdmabufheap allocation failed for mtk_mm heap`、
`Failed to allocate (128 x 128) layerCount 1 format 1 usage 300: -12`。
闭环：**mali_gralloc 按 heap 名打开 `/dev/dma_heap/mtk_mm(-uncached)`，云机只有
system/system-uncached → 分配 -12 → libui GraphicBuffer 仅在 allocate 成功后才写
ANativeWindowBuffer::usage 字段（失败保持 0）→ 断言 0&0x200 → SIGABRT 循环**。

### 补丁 11 v1（MTK heap 别名）：run33 Kernel panic 循环（recipe v36，log-fail）

system_heap.c 注册 6 个 MTK heap 名别名（cached 组=system_heap_ops：
mtk_mm/mtk_2d_fr_region/mtk_prot_region；uncached 组=system_uncached_heap_ops：
mtk_mm-uncached/mtk_svp_region-aligned/mtk_wfd_region-aligned），名单来自
allocator-V2-mediatek.so strings 实测。SELinux 佐证：官方 vendor file_contexts
已有 mtk heap 标签规则（run32 dmesg：mtk_prot_region → dmabuf_system_secure_heap
_device）。chain24 构建成功，但 run33 hwcomposer 分配 mtk_mm-uncached buffer 时
内核 Oops：`dcache_inval_poc` 收到非法地址 0xffffffffc0000000 → level 1
translation fault → **Kernel panic 循环（qemu.log 31 次 Linux banner）**。
判据教训：`grep -c 'Linux version'` 会把 fsck.f2fs 打印的宿主（OrbStack）版本串
误算成重启——判 panic 必须 grep `Kernel panic`/`Internal error`。

### 补丁 11 v2 订正：heap device 补 dma_coerce（recipe v37，run34 部分通过）

上游 system_heap.c:521 **仅给 sys_uncached_heap 做
`dma_coerce_mask_and_coherent(DMA_BIT_MASK(64))`**——uncached 分配的 map/unmap
链（`dma_direct_unmap_sg→arch_sync_dma_for_cpu→dcache_inval_poc`）依赖 heap
device 的 dma_mask，v1 漏做。v2 给每个注册的 MTK heap device 补 coerce（cached
组对齐处理，无害）；wire-kbase.sh 改为链式补丁（old=v1 文本、new=v2 文本，增量卷
上 v1 已在）。
run34 判据：
- `/dev/dma_heap/` 8 个 heap 全就位（6 MTK 别名 + system/system-uncached）；
- **无 panic 无 Oops**（Kernel panic/Oops 计数 0）；
- adb 上线（fsck.f2fs 修复 run33 遗留脏分区后）。
但分配错误 -12 → **-129**：allocator 服务 `Passing -1 file descriptor as
non-@nullable AIDL ParcelFileDescriptor`——heap 层通了，暴露新洋葱层。

### GED 桥取证（-129 根因，三 so 反汇编闭环）

- run32 对照：`ged_bridge is not init` 计数 0（heap 层先死，走不到 GED）→ GED 是
  新暴露层 ✓。
- **libged.so**（out/tmp/libged-full.txt / libged-bridge.txt）：
  `ged_bridge_open` = `open("/proc/ged",0)` + `fcntl(fd,2,1)`（FD_CLOEXEC）；
  `ged_bridge_call(fd,cmd,in,in_len,out,out_len)` = 栈上 40 字节包
  `{u32 cmd; u32 size=0x28; void* in; u32 in_len; void* out; u32 out_len}` →
  `ioctl(fd, cmd, 包)`；ioctl 返回非 0 → 记日志返回 1。5 个 cmd 实测
  （mov w1,#0x67XX + movk w1,#0xc028 组合，均 `_IOWR('g',nr,0x28)`）：
  **ALLOC=0xc0286764、GET=0xc0286765、SET=0xc0286766、QUERY=0xc0286769、
  DMABUF_SET_NAME=0xc028676b**。
  出参消费语义（决定 stub 写法）：ged_ge_get 客户端把 out[0] 预填 1（失败），
  内核必须写 0 + values；ged_ge_query 客户端预填 rodata 4f40 常量（status 默认
  1），状态取 out+8；ged_ge_alloc out 预填 -1。
- **gralloc_extra.so**（out/tmp/gralloc-extra-full.txt）UND 实锢 GED 调用者：
  `ged_ge_get/query/set/dmabuf_set_name/ged_ge_alloc`。`ge_alloc@31a0` = 3 条
  指令 `mov w0,#0x16; adr x1,8010; b ged_ge_alloc`——**count=22、values=rodata
  固定 22 项常量表**（4,272,64,24,60,224,8,16,64,16,2572,4,60,4,96,1024,120,
  64,4,4,8,428，GED 元数据预算）。`ge_release` 把 `[handle+12]`（=data[0]）当
  fd 传给 ge_perform_free_sec_hwc——交叉验证 **ge_alloc 返回值 = buffer 主 fd**。
- **alloc-mtk.so**（out/tmp/alloc-mtk-full.txt）：`mali_gralloc_buffer_allocate`
  @2e8c8 在 allocator_allocate（dmabufheap，已打通）成功后调 ge_alloc，返回值
  `str w0,[x8,#12]`（native_handle data[0]）→ **返回 -1 即
  "Passing -1 file descriptor" → binder 拒绝 → 客户端 -129**。闭环。

### 补丁 12：内核 /proc/ged stub（recipe v38，chain26 构建中）

新文件 [kernel/modules/mali_vgpu/ged-stub/ged_stub.c]，wire-kbase.sh 第八补丁
接线（拷至 drivers/dma-buf/ + Makefile 幂等追加 obj-y）。要点：
- `proc_create("ged", 0666, NULL)` + proc_ops(unlocked_ioctl/compat_ioctl)；
- 40 字节包 `struct ged_bridge_pkt` 与 ged_bridge_call 栈布局逐字段一致；
- **ALLOC：用 system heap 分配真实 dma-buf（4KB）安装 fd 返回**
  （`dma_heap_find("system")` + `dma_heap_buffer_alloc` + `dma_buf_fd`，built-in
  直链内部符号）——该 fd 经 binder 传递并被 mali kbase 导入，必须真 dma-buf；
- GET/SET/QUERY/SETNAME：status=0 + values 全 0（「特性关闭」默认，客户端预填
  1/常量，内核不写=失败，必须显式写 0）；
- 防御：GET count 上限 4096、各 cmd 校验 in_len/out_len、默认 -ENOTTY。

### run35 验证清单（待跑）

判据：
1. dmesg `cdh_ged:` 日志出现（stub 就位 + ALLOC 被命中）；
2. allocator `Passing -1` 计数 0、`Failed to allocate ... usage 300` 计数 0；
3. SF `output buffer not gpu writeable` 断言消失、`init.svc.surfaceflinger=running`；
4. boot_completed=1 且稳定运行 10+ 分钟；
5. SELinux avc：`grep -c "denied.*proc.*ged\|denied.*ged"`（预期 0；若
   enforcing 拦 /proc/ged 需另补 sepolicy——已知潜在下一层）。

### §7.22 run35 结果：-129 消除、GL 上下文建成，新卡点 = job_submit stride=72

run35（recipe v38）判据实测（out/core/daemon/runtime/gold-15/35/qemu.log +
adb logcat/dmesg）：

1. ✅ `cdh_ged: ready` @0.126s；ALLOC 命中 **276 次**返回真 fd（allocator
   binder:600_3 与 surfaceflinger 都打开了桥）；
2. ✅ `Passing -1` 仅 2 次（启动早期残留，ALLOC 打通后不再出现）；
3. ✅ `output buffer not gpu writeable` crash 计数 0（消失）；
4. ✅ 无 panic；SELinux 未拦 /proc/ged（avc 计 0）；
5. ❌ SF 仍 restarting：**RenderEngine SIGABRT 循环**（每 ~5s 一轮，tombstone_29）。

**重大里程碑**：RenderEngine GL 上下文建成——`renderer: Mali-G57 MC4`、
extensions 完整、mali DDK 与 kbase 通信（`user 11.46 vs kernel 11.45` 仅
WARN）、dmesg mali job_submit 在跑（`Stride 72 ...` 每 5s）。

#### eglCreateSync abort 根因链（libGLES_mali.so 反汇编闭环）

tombstone 完整背架（#00→#14，out/tmp/egl-sync-a7d370.txt /
abort-chain-01-02.txt / abort-chain-1de5050.txt / atom-fill-1cf1150.txt）：

```
#14 eglCreateSyncImpl+604 (0xadd64c)
#13 0xa7d4ac   bl a3b190（sync 创建主体，尚未返回）
#12 0xa3b2e0（a3b190 内）→ #11 aa68f0 → #10 aa5be4 → #09 ab0434
#08 0xaaf7ac → #07 0x1cd60fc → #06 0x1cedb28（1ceda30 内）
#05 0x1cf1ac0 → #04 0x1cf103c → #03 0x1cf11c4（1cf1150 内，主提交调用点）
#02 0x1de21a8（1de2160 内）→ #01 0x1f530b0 纯 abort thunk（2 条指令）→ abort
```

- **0x1de5050 = KBASE_IOCTL_JOB_SUBMIT 包装**：`w1=0x40108002` =
  `_IOWR(0x80,2,16B)`，参数 `struct kbase_ioctl_job_submit
  {u64 addr; u32 nr_atoms; u32 stride}`；
- **0x1de2160 硬编码 stride=72**（`mov w9,#0x48`），其上层 0x1cf1150 的 atom
  数组步进 `add x22,x22,#0x48` —— **user 11.46 atom 结构 = 72 字节**；
- 0x1de2160 对 ioctl 非 0 返回**直接走 abort thunk**（1de2160 的 ioctl 包装
  1de5050 仅 ENOMEM→2/EBUSY→70/其他→1），无 Abort message（对应 tombstone
  无消息）；
- 内核 [mali_kbase_jd.c:1038-1045] stride 白名单
  `{40,48,56}`（offsetof/sizeof × v2/atom）拒收 72 → `-EINVAL` → 上述 abort；
  dmesg `Stride 72 passed to job_submit isn't supported` 每 5s 一条与 SF 重启
  周期吻合；
- 内核结构实测：`base_jd_atom_v2`=48B（renderpass_id@40）、
  `base_jd_atom`=56B；72 = user 11.46 尾部扩展 24B；
- 1cf1150 语义：extres 链表阶段 0 回调（1cf1430 虚表 7 项）→ 主提交 → 阶段 1
  回调；提交前 atoms 从 host extres 指针序列化为 GPU 地址。

#### 补丁 (12)（Phase C·第九补丁，recipe v39-jd-stride72）

wire-kbase.sh 追加两个 patch（锚点缩进已 repr 核对）：
1. `jd_atom_is_v2` 加 `|| stride == 72`（v2 路径只 copy 前
   sizeof(v2)=48B，user 尾部 24B 不读，`user_addr += stride` 步进天然按 72，
   [mali_kbase_jd.c:1123]）；
2. stride 白名单加 `&& stride != 72`；并加探针：前 4 次 stride=72 提交
   hexdump 首 atom 全 72 字节（`cdh-mali:` 前缀，run36 dmesg 比对前 48 字节
   与 kernel v2 兼容性：jc@0/core_req@36/renderpass_id@40/padding 全 0），
   布局确认后探针可删。

### run36 验证清单（待跑）

判据：
1. dmesg `cdh-mali: stride=72 submit` hexdump 出现（探针命中）且
   `Stride 72 ... isn't supported` 不再出现；
2. hexdump 解读：前 48 字节与 kernel v2 兼容（padding@41-47 全 0，否则
   [mali_kbase_jd.c:1085-1105] 的 padding 检查会 -EINVAL）；
3. RenderEngine SIGABRT 消失（crash buffer 无
   `eglCreateSyncImpl+604` 背架）、`init.svc.surfaceflinger=running`；
4. boot_completed=1 且稳定运行 10+ 分钟；
5. 若 abort 迁移到新的调用点 → 继续剥洋葱（记录新背架）。

## 7.23 stride=72 放行落地：发布链路弯路 → Bad padding 新层 → run40 abort 归零（「SF 存活」系误判，run41 勘正）（2026-10-10）

### run36/37/38：新模块连续三轮不生效——发布链路五步教训

补丁 (12) 入 wire-kbase.sh 后，run36/37/38 三轮实例上 `Stride 72` 拒绝依旧、
探针零命中，计数链（各实例 qemu.log）：

| run | Stride 72 拒绝 | Bad padding | eglCreateSyncImpl abort | 探针 cdh-mali |
|---|---|---|---|---|
| 36 | 31 | 0 | 31 | 0 |
| 37 | 60 | 0 | 60 | 0 |
| 38 | 33 | 0 | 33 | 0 |
| 39 | **0** | 68 | 68 | 16 |
| 40 | **0** | **0** | **0** | 16 |

层层排查结论——**内核模块改动要进 guest 必须走完整五步链**，漏任何一步
guest 都跑旧模块：

1. `build-kernel.sh`（产物在 `out/kernel/modules/`）→
2. **`cp out/kernel/modules/*.ko out/dist/kernel/modules/`**（build-vendor_dlkm.py
   从 **dist** 取产物，不会看 out/kernel！）→
3. `build-vendor_dlkm.py`（产出 `dist/rom/vendor_dlkm.img`）→
4. **super.img 更新**（`pack-roms.py` 只把现成 super.img 写进 vda；vendor_dlkm 是
   super 的子分区，必须 build-super 或 lpmemeta 单替换）→
5. `pack-roms.py` 刷 vda → 重启实例。

配套教训两条：
- **md5 误判**：`adb shell md5 ... 2>/dev/null; md5 -q ...` 两行输出相同会误以为
  guest=新模块——实际 guest 的 md5 命令失败被 `2>/dev/null` 吞掉，两行都是宿主的。
  应以 **文件大小 + 时间戳** 双重验证。
- build-super.py 全量路径当时被 vendor.img 超容挡住（10-9 的无关产物 2363490304 B >
  官方分配 1372213248 B）；vendor 分区内容 run32-35 已验证正常无需动，绕过方式 =
  以现有 super.img 为基底 `lpmemeta.update_partitions` 单替换 vendor_dlkm（脚本见
  `solution.md` §5.5）。

### run39：stride 放行生效，Bad padding 新层暴露

五步链补全后 run39 上探针命中、`Stride 72` 拒绝归零，但失败点**前移一层**：
`mali mali.0: Bad padding byte 0: 3`（每轮 68 次）→ `-EINVAL` → 同一 abort 链。

探针 dump 的首 atom hexdump（run39/40 一致）：

```
mali mali.0: cdh-mali: stride=72 submit nr_atoms=4, first atom:
cdh-mali: 00000000: 0000000000000000 b4000070454a9e90 b400006fb54393a0 b4000071a7c0a000
cdh-mali: 00000020: 0000000000000000 0000000000000001 0000020900000301 0000000000000000
```

> 解读注意：`print_hex_dump` 的 `groupsize=8` 分支（lib/hexdump.c）按 `%016llx`
> 小端 u64 打印，故第三组 `0000020900000301` 的内存字节序为
> `01 03 00 00 09 02 00 00`。

按 v2 布局（mali_base_jm_kernel.h L633-695）逐字节对照 72B atom：

| 偏移 | 值 | v2 字段 | 结论 |
|---|---|---|---|
| +0x00 | 0 | jc | soft job（jc=0，type 由低 4 字节定义，无渲染命令） |
| +0x18/0x20 | 指针/0 | extres_list/nr_extres | 兼容 |
| +0x28 | 01 | atom_number=1、prio/device_nr/jobslot=0 | 兼容 |
| +0x2C | 0 | **core_req=0**（11.46 把 core_req 挪到了 +0x34，见下） | 兼容（soft job） |
| +0x30 | 01 | renderpass_id=1 | 兼容 |
| +0x31 | **03** | v2 padding[0] —— **11.46 新字段** | `Bad padding byte 0: 3` 的来源 |
| +0x34 | 0x00000209 | v2 padding[3..6] 区 —— 11.46 疑似把 core_req 迁到此处（0x209 soft job 位） | 内核不消费 |

即 11.46 的 72B atom 不是「v2 尾部追加」而是 **padding 区复用**：前 56 字节
（sizeof(v2)）之外 kernel copy 路径不会读，但 copy 进来的 56 字节里 padding 区
带新字段 → [mali_kbase_jd.c:1095-1102] 的 padding 零检查 `-EINVAL`。

### 补丁 (12b)：72B atom 豁免 padding 校验

wire-kbase.sh (12b)：copy 后的 padding 检查对 `stride == 72` 分支豁免——该
字节落入 kernel `user_atom.padding`（提交后不被消费），忽略即可。同时订正
hexdump 解读注释（0x209 在 +0x34，非 +0x2C）。

### run40：串口侧判据全过；guest 侧判据误取自他机（同日 run41 勘正，L1 未达成）

实例 40（chain28 产物 + 五步链全跑）实测，对照 run36 清单：

| # | 判据 | 结果 | 勘正备注 |
|---|---|---|---|
| 1 | 探针命中 + Stride 72 拒绝消失 | ✅ qemu.log `cdh-mali:` 16 行/boot（4 提交×4 行，`<=4` 限次吻合）；`Stride 72`/`Bad padding` 计数 0 | 串口侧，真实 |
| 2 | 首 atom 与 v2 兼容 | ✅ hexdump 与 run39 相同布局；padding 区新字段经 12b 豁免 | 真实 |
| 3 | eglCreateSync SIGABRT 消失 | ✅ run39 abort 68 次 → run40 **0 次**（串口 qemu.log） | 真实；原行尾「SF running」部分作废 |
| 4 | boot_completed=1 + 稳定 10+ 分钟 | ❌ 作废 | ~~✅~~ 误取自 OnePlus 15r-16/56 实例（adb 5919）；本设备实况 = SF mali-cmar-backend SEGV 5s 循环（boot1 137 + boot2 98 次，见 §7.24） |
| 5 | abort 是否迁移新调用点 | ❌ **迁移了**：SF 崩点移至 libGLES_mali.so+0x1cf2488（mali-cmar-backend 线程，图形栈内） | 见 §7.24 |

**勘正后结论（run41）**：eglCreateSync abort 卡点闭环成立（串口计数全 0、
72B job atom 放行、GL 上下文建成）；但 **L1（SF 存活 + boot_completed=1）
未达成**——SF 全程处于 mali-cmar-backend SEGV 5 秒循环，新卡点见 §7.24。
（原「里程碑：全链存活」声明基于误取的他机判据，作废。）

### run40 新登记独立链（非图形栈，暂容忍观察）

- `odm/bin/hw/android.hardware.power.stats-impl.oplus`：main+764
  `Check failed: s == STATUS_OK` SIGABRT，**5 秒周期** servicemanager 拉起即崩
  （开机 08:00 起持续）。不挡 boot（boot_completed=1 已证明）。
- `odm/bin/hw/android.hardware.secure_element-service.qti`：linker 缺
  `libQSEEComAPI.so`；`vendor.oplus.hardware.vibrator-service`：缺
  `libsoc_helper.so`（同样 5s 周期拉起失败）。
- 已知：`vendor.xiaomi.hardware.displayfeature SEGV 0x0`、
  `android.hardware.boot-service.mtk Check failed: impl_.Init()`。

共同模式：odm HAL 依赖真机硬件/私有 so，云机拉不起。均不影响图形链与 boot，
归容忍清单。

### 遗留

1. **hexdump 探针待删**：72B 布局已确认（run39/40 双证），探针限前 4 次提交、
   log 量极小；等整个 GPU 栈稳定（HWC 出画验证完）后随下轮重编一并清理。
2. dmesg 环形缓冲区被 connysdrv（`wmt_loader`/`wmt_launcher`）刷屏冲掉早期
   日志——判据统计应以实例 qemu.log 为准（qemu.log 含完整 serial console）。
3. guest timekeeping 从 boot 起错乱（非仅墙钟未同步，run41 升级定性）：
   `/proc/uptime`=173991s 而 QEMU 实跑仅 ~28min，tombstone 报 SF 进程
   uptime 172801s（实际 <25s）；dmesg/logcat 秒数正常 → 系统 uptime 口径
   自 boot 起即坏。稳定性判据以进程 PID/属性为准；该问题影响 CMAR poll
   timeout、fence/watchdog 等时间敏感逻辑，待独立排查（见 §7.24 伴生问题）。
4. 下一层卡点：SF mali-cmar-backend SEGV 5 秒循环（L1 未达成，见 §7.24）；
   B2 出画验证排在它之后（见 §7.8）。

## 7.24 run41：run40 勘正 + 新卡点 mali-cmar-backend SEGV 5 秒循环（2026-10-10）

### run40「L1 达成」勘正（判据跨实例误配）

- guest 侧判据（boot_completed=1 / SF PID 1039 / zygote 847 / system_server
  980 / 稳定 40+ 分钟）误取自另一项目 15r-16/56 实例（OnePlus CPH2769、
  Android 16、无 /dev/mali0，adb 5919）；本设备 adb=5911（按本实例 QEMU
  cmdline `hostfwd tcp:127.0.0.1:5911-:5555` 现场确认）。OnePlus 的 SF PID
  恰亦为 1039，加剧了自洽假象。
- 串口侧判据不受影响（真实）：eglCreateSync abort 0、Stride 72/Bad padding
  0、探针 16 行/boot → 补丁 (12)+(12b) 效果为真，eglCreateSync 层闭环成立。
- 规则固化（solution.md §5.6）：跨实例取 guest 判据前先
  `getprop ro.product.model / ro.build.fingerprint` 核身；端口↔实例映射从
  本实例 QEMU cmdline 现场读取；计数类判据优先走串口 qemu.log。

### 新卡点：SF 全部崩溃 = mali-cmar-backend 线程 SEGV_ACCERR 一种

- 计数（`log-run40-41-malicmar-capture.txt` 全量统计，boot 以 Linux version
  banner 分界）：boot1=137 / boot2=98，SF 每次被 init 拉起后 ~26ms 崩——
  时序 = HWC `SetPowerMode(2)` 显示上电 + 首帧 GrallocExtra query 之后。
  抽验 6 条 fault addr 页内偏移恒 0x571 → ASLR 下确定性写只读段。
- 崩点（tombstone `#00 pc 0x1cf2488 libGLES_mali.so`，BuildId
  839bafe942188cd7；objdump 全量反汇编对照）：cmarp_backend_thread
  （0x1cf2170）poll 4 fd → read 24 字节事件条目流 → 遍历条目（仅
  flags&0x1800==0x1800 或 ==0x1000）→ 条目+0x10 指针对象 +0x18 指针 =
  so+0x1F0470（只读数据段）→ `strb wzr,[x8,#0x101]` SEGV_ACCERR
  （fault = so+0x1F0571）。即 DDK 11.46 的 CMAR（内存管理）后台线程把
  只读静态模板当可写实例对象。
- CMAR 线程身份佐证：prctl 自命名 `mali-cmar-backend`；字符串
  `cmarp_backend_thread`、`Failed to close render list fd for CMAR signal
  flag (error %d)`、perfetto 数据源
  `cctx_memory_class_cmar_{hmem,prof_dump,signal,flush_chain,metadata_list}`、
  属性 `vendor.mali.cctx_memory_class_cmar_*`。
- fd1 真身排除：DDK 反汇编无 TLSTREAM(nr18=0x40408012)/PRFCNT(nr57) ioctl
  调用序列 → backend 线程 poll 的 fd1 是 DDK 内部「render list fd」事件
  管道（生产者=DDK 自己的 GL 线程）→ 崩因指向 DDK 内部时序/初始化问题，
  非内核 fd 格式错配；内核 kbase 侧 timeline fd 路径（mali_kbase_timeline_io.c
  的 timeline_is_permitted，默认 0）与此无关。
- 伴生问题：guest timekeeping 从 boot 起错乱（见 §7.23 遗留 3），CMAR
  poll timeout 等时间敏感逻辑可能被殃及，待独立排查。
- 证据：`evidence/issue-gold15-vendor-gpu/log-fail.txt` run41 段（六段式
  取证全文）+ `log-run40-41-malicmar-capture.txt`（147MB 双 boot 串口快照）。

### 下一步（run42 计划）

1. 重启实例恢复 adb（5911）→ `/proc/<sf>/fd` + readlink 定 fd1 指向
   （pipe/eventfd/memfd）→ 为修复方案定层：内核侧可做的面（若涉及时钟/
   DT 供给）vs DDK 内部问题需绕行的面（硬约束：不改厂商 so 字节）。
2. guest timekeeping 错乱独立取证（clocksource、DT chosen、QEMU virt 时钟）。
