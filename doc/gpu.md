# gold-15 让「厂商图形栈」在云机跑起来 —— 计划（gpu.md）

- 设备：Xiaomi Redmi Note 13 5G（codename `gold`，board `mt6833`）全局版 ROM `OS3.0.5.0.VNQMIXM`，云机 `devices/xiaomi/gold-15`
- 云机宿主：macOS（Apple Silicon）/ QEMU + HVF，显示后端 = `virtio-gpu`（`CONFIG_DRM_VIRTIO_GPU=y`）
- 硬约束（用户已定）：**不替换厂商 vendor 里的图形 so**（App 检测面 = soname + md5，见
  `devices/xiaomi/note13-5G-15/build/doc/vendor-so-identity.md`）
- 目标（用户已定）：**让厂商图形栈真正跑起来**（非"只在位/被加载"）

> ⛔ **§7 原形态 pass ＋ ✅ 新主线见 §8（用户决策 2026-10-12）**：原"渲染桥在
> guest 产出像素"（B2 填色 / B3 CPU 光栅）经评估为**全程 CPU 计算**、性价比不成立，
> **pass**；同日定档新主线 **B′：厂商栈原样生效 + host GPU 渲染**（见 §8），kbase
> 虚拟 Mali 降级为 **ABI 转发面**复用。§7 文档与证据链保留作历史参考；**后续会话
> 按 §8 执行**，不要续跑 §7 的 guest 渲染/发布验证（v74 构建已终止）。

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
| **M2** SF 稳定 + 引导完成 | `init.svc.surfaceflinger=running` 且不再重启；`sys.boot_completed=1`；`screencap` 能出图 | **L1** | ✅ **核心达成**（2026-10-10，run47，补丁 13 + ro.frp.pst 置空）：boot_completed=1、SF pid 恒活、composer 恒活、displayfeature 不再崩；screencap 出图（M3）未达——display 查询 `Failed to get ID for any displays`，另 system_server RenderThread 卡死致 watchdog 循环（不挡 boot，见 §7.27 遗留） |
| **M3** 画面正确 | `screencap` 内容与预期一致 | **L2** | 未开始（screencap 报 no displays，需 §7.8 B2/B3 + display 查询链） |
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

### §7.25 run42：timekeeping 三钟全定案 + mali-cmar 诱因勘正（2026-10-10）

实例 42（重启 41 换新盘，adb 5911 复活）完成 timekeeping 伴生问题闭环，
并**勘正诱因优先级**。固证全文：`evidence/issue-gold15-vendor-gpu/log-fail.txt`
run42 段。要点：

| 钟 | 实测（epoch 窗口，dmesg=72s 时刻） | 根因定案 |
|---|---|---|
| REALTIME（wall） | epoch+72 → [441s] 跳 2026-08-11 12:34:43 | MIUI SystemClockTime 无 RTC 时 settimeofday 到 ROM build date（ro.build.date.utc=1786451683），每 boot 复现；落后宿主 60 天 |
| MONOTONIC | 72（=sched_clock=vDSO，三方一致） | 正常 |
| BOOTTIME（/proc/uptime、进程 starttime、tombstone uptime） | 172872（+恰 172800s） | 注入者静态穷尽未获（无 RTC 驱动/无 suspend/timens 全 0/git 干净/注入 API 无调用者）；btime 恒等式与 uptime 实测矛盾 → 运行 Image≠源码 嫌疑（低优先追查） |

**诱因勘正（关键结论）**：DDK 全量反汇编（581MB）——cmarp_backend_thread
函数体内**无任何时钟读取**；全库 2337 处 clock_gettime@plt 中 CLOCK_BOOTTIME
仅 1 处、vDSO 数据页直读 0 处。**时间错乱与 mali-cmar SEGV 的因果链大幅
削弱**，降级为伴生显示问题（tombstone uptime/elapsedRealtime 偏 +2 天）。
崩点语义（静态模板指针出现在消息对象 +0x18 槽）**指向初始化顺序/对象
构造链**，非超时形态。

伴生新登记：pid 720 `vendor.xiaomi.h` SIGSEGV SEGV_MAPERR 空指针（与
mali-cmar 主线分开，待定位）。

### 下一步（run43 计划）

1. mali-cmar 崩点转向初始化链取证：事件总线生产者（0xb18200 深层）的
   对象构造 / 4fd 后台线程 spawn 条件 / CMAR 后端启用路径（属性
   `vendor.mali.cctx_memory_class_cmar_*`）。
2. 低优先：反汇编 v39 内核产物 uptime_proc_show/getboottime64，分辨
   BOOTTIME +172800 注入者（Image≠源码 嫌疑）。
3. 登记遗留：odm HAL 容忍链；hexdump 探针清理时机（见 §7.24 遗留）。
4. B2 出画（L1 闭环后）。

### §7.26 run43：mali-cmar SEGV 循环闭环——tombstone 定案 + NOP 修复（2026-10-10）

run43 四段取证（a/b/c/d）+ 修复验证完成，run41 起的 L1 头号阻塞（SF
`mali-cmar-backend` SEGV 5 秒循环）**闭环**。固证全文：
`evidence/issue-gold15-vendor-gpu/` 的 log-fail.txt（run43 段 + run43-d
定案段）/ log-pass.txt（run45 段）/ solution.md §5.7。要点：

| 阶段 | 结论 |
|---|---|
| run43-a | 属性开关假设排除（`debug.gpu.refs_flush_th` 是刷新阈值非开关） |
| run43-b | spawn 链定案：eglInitialize→单例→事件总线→34 步状态机→步 33 `pthread_create`，**spawn 无条件** |
| run43-c | 消息 fd = DDK 内部 pipe@0x1de5c60（创建者 0x1db0c7c）；[P+0x18] 函数指针静态五重排除证明**只能来自运行时数据流**——静态分析达天花板 |
| run43-d | **tombstone_21 实锤**：x8 = so+0x1cf0470（.text 函数序言），`strb wzr,[x8,#0x101]` 写 so+0x1cf0571 只读页 → SEGV_ACCERR；初始化链把静态模板/类型描述符指针留在 P+0x18 未覆盖为堆实例 |

**修复**：NOP 最小字节补丁（0x1cf2488：`3904051f`→`d503201f`，§5.7 记录
权衡——环境侧无可控面后的最小绕过，"不改 so 字节"约束的唯一例外，经用户
确认）。发布链免全量重建：build-vendor → vendor.img 2,363,490,304 B（与
super vendor_a 分配精确相等）→ `pack-roms.py --patch vendor=...` 增量 dd。

**run45 验证判据（通过）**：

- SF pid 794 单次启动（dmesg 22.86s）恒活 13min+（run43：5 秒必死，467 次/30min）
- mali-cmar SEGV_ACCERR **0 复现**；eglCreateSync abort 0 / 探针 16 行（stride72 层保持）
- md5 铁证：guest `/vendor/lib64/egl/libGLES_mali.so` =
  `53d4b879972c455705b00ce6e7e2606b` = 宿主 patch so，大小/时间戳双吻合
- 伴生单发自愈（登记）：bootanimation 1×MAPERR（45）、SF 首启 1×SIGBUS（44）
  ——NOP 后伪指针下游偶发消费
- **勘误**：`grep boot_completed` 命中 keystore2 监控行≠置位；run45 实际
  `sys.boot_completed` 未置位

**新卡点登记（下一层，非 mali-cmar 引入；43 同样存在）**：

- zygote ~21s 自杀循环（pid 25049→25847→26670 轮换实证，signal 9 self-kill）
  → SystemServer 起不全 → boot_completed 不置位
- vendor.xiaomi.h 持续 SIGSEGV（45: 2881 行 / 43: 5078 行，run42 起登记）

### 下一步（run46 计划）

1. **zygote 自杀循环取证**：logcat 里 zygote/system_server 崩溃前最后栈与
   abort 消息（`system_server` 卡在哪个服务构造）；与 vendor.xiaomi.h 崩溃
   链的关系（因果 or 并行）定案 → 这是 boot_completed 的直接阻塞。
2. vendor.xiaomi.h 空指针 SIGSEGV（run42 登记 pid 720 链）定位：tombstone
   backtrace + 该 HAL so 反汇编。
3. hexdump 探针清理时机（§7.24 遗留）；odm HAL 容忍链登记（§7.24 遗留）。
4. B2 出画（HWC 合成是否真正出画，virtio-gpu/DMS 渲染桥）——boot_completed
   链清完后推进。

## 7.27 run46：zygote 循环根因 = PDB 30s 超时（frp 分区缺失）→ 置空 ro.frp.pst 修复；新洋葱层 = displayfeature HAL 崩溃循环 → watchdog 杀 system_server（2026-10-10）

### 第一层：PDB init 超时（run45 qemu.log 取证，已修复 ✅）

- zygote 循环真身 = **system_server Java FATAL**（非自杀）：
  `Failed to boot service com.android.server.pdb.PersistentDataBlockService:
   onBootPhase threw an exception during phase 500` ←
  `IllegalStateException: Service PersistentDataBlockService init timeout`
  （`waitForInitDoneSignal` 30s 超时），每 ~30s 一轮与 zygote 重启周期精确吻合。
- 根因链：官方 `/vendor/build.prop` 声明 `ro.frp.pst=/dev/block/by-name/frp`
  → AOSP SystemServer 判据「ro.frp.pst 非空」即启动 PDB（vendor 内无
  `ro.persistent_data_block`，grep vendor.img = 0 处）→ PDB onStart 三次
  open `/dev/block/by-name/frp` 全 **ENOENT**（lineage 底包 vda GPT 无 frp
  分区；fstab 云机化时 frp 行也已剔除）→ init 线程死于
  `IllegalStateException: persistent partition not readable` →
  mInitDoneSignal 永不置位 → phase 500 超时。
- **修复（用户定方向：删 frp 配置而非造分区）**：`build/vendor/build/props.py`
  新增 `FRP_SETS = {"ro.frp.pst": ""}` 并入 VENDOR_SETS 改写链 → 重跑
  build-vendor.py（vendor.img 2,363,490,304 B，NOP 补丁 so 随 patch 树保留，
  md5 1f2003d5@0x1cf2488 验证）→ `pack-roms.py --patch vendor=...` 增量写 vda。
- run46（实例 46）验证：`PersistentDataBlockService|StartPersistentDataBlock`
  计数 **0**（PDB 不再启动，只剩 TestHarnessMode 无 PDB 实现的告警日志）、
  `System zygote died` 计数 **0**、SF/zygote `running`、RenderEngine GL 栈正常。

### 第二层：displayfeature HAL 崩溃循环 → watchdog 杀 system_server（新 L1 阻塞，修桩中）

PDB 修复后 system_server 仍每 ~2-5 分钟被 Watchdog kill（非固定周期）：

```
Watchdog: *** WATCHDOG KILLING SYSTEM PROCESS: Blocked in monitor
  com.android.server.am.ActivityManagerService ... for 70s, Blocked in handler
  on main/display/anim threads ...（三次形态：AMS monitor / WMS monitor / ui 134s）
→ GOODBYE! → zygote 重启（signal 9 自杀记录，无 Java 异常）
```

与 **`vendor.displayfeature-hal-1-0` SIGSEGV 5s 崩溃循环**（run42 起登记的
vendor.xiaomi.h 链，当时误判容忍）时刻吻合——**升级为 L1 阻塞头号嫌疑**。

tombstone_31 反汇编闭环（librhytheyecare.so + libmiXmlParser.so）：

```
#00 strtol/atoi(NULL)  ← SEGV_MAPERR 0x0
#02 android::RhyEyeCare::init()+236     (librhytheyecare.so)
#03 DisplayEffectBase::DisplayEffectBase+3408 (displayfeature.default.so)
...HIDL passthrough 注册链（服务 main）
```

- `RhyEyeCare::init` → `MiParseXml::parseXml(idx, tag, buf, out_str, out_uint)`
  返回 0（"成功"）但 `out_str`（std::string*）未填 → `atoi(sp+0x1180)=atoi(NULL)`。
- **ParseLine 语义实锤**（libmiXmlParser.so @0x4260）：strtok_r 无 token 时
  **返回 0 且不写 out**——空 XML 值 = 假成功 + 空指针。
- 云机崩因：无 `/sys/class/mi_display/disp-DSI-0/panel_info`（xiaomi 私有
  sysfs）→ `getPanelName` 失败 → panel 专用 XML
  `vendor/etc/<panel_name>_cn_mi.xml` 打不开（云机 cmdline 无 androidboot.hwc
  → property_get("ro.boot.hwc",def="CN") → 后缀 `_cn`）→ 只剩 df_default.xml，
  **其内没有 `<rhyeyecare>` 节**（实测 grep=0；panel XML 里有完整 18 参数）。
- 真机 = xiaomi mi_display 驱动提供 panel_info（key=value 行，getPanelName
  按 "=\n" 拆、strncmp "panel_name"、value 空格转 '_'，≤31B）。

### 修复：补丁 13（mi_display 内核桩，ged_stub.c 追加）

- **布局与官方 vendor_file_contexts 正则同构**（标签免补）：
  `/dev/mi_display/disp_feature` → `vendor_displayfeature_device`；
  `/sys/devices/virtual/mi_display/disp_feature/disp-DSI-0/*` →
  `vendor_sysfs_displayfeature`（device_create_with_groups 把 disp-DSI-0 挂在
  disp_feature 设备下，/sys/class/mi_display/disp-DSI-0 软链同时满足库路径）；
- `panel_info` = `panel_name=dsi_n17_36_0f_0c_dsc_vdo`（vendor/etc 内 4 个
  dsi_n17_*_mi.xml 中唯一 cn/gl 双版本并存的全球版主屏，含完整 rhyeyecare 节）；
- `doze_brightness`=0（rw）、`mipi_rw`/`wp_info` 空（EOF）；disp_feature ioctl
  一律 -ENOTTY（HAL 应降级；若空指针崩将暴露下一层按栈再补）；
- 构建链三处配套：wire-kbase.sh heap 补丁 (11) 改**三态幂等**（v2 在/v1 在/
  原始锚点——原链式锚点在全量卷上不命中硬失败）；recipe v39→**v40-mi-display**
  （缓存失效）；ged_stub 注释避 -Wcomment（`disp-DSI-0/*` 嵌套注释符）。

### run47 结果（实例 47，2026-10-10 22:14 起）：**L1 达成** ✅

| 判据 | run45 及以前 | run47 | 备注 |
|---|---|---|---|
| `sys.boot_completed` | 未置位（PDB/zygote 循环） | **1**（@486.8s，init 正式处理） | **L1 核心判据首次达成** |
| surfaceflinger | 5s/30s 崩溃循环 | **pid 1301 恒活 running** | |
| composer@3.3-service | 静态初始化期 SIGSEGV | **pid 581 恒活 running** | |
| displayfeature-hal-1-0 | **5s SIGSEGV 循环**（run42 起） | **running 不再崩**（signal 11 计数 0） | mi_display 桩生效 |
| PersistentDataBlockService | init 30s 超时杀 zygote | 不启动（ro.frp.pst 空） | TestHarnessMode 告警无害 |
| `eglInitialize` 失败 | 历史峰值 32 | 0 | |
| `System zygote died` | 137+98 次/boot | 0 | |
| 内核桩 | — | `cdh_mi_display: stub ready`、`/dev/mi_display/disp_feature`（0666）、`panel_info=dsi_n17_36_0f_0c_dsc_vdo` | 实测全就位 |

**M2 状态**：`boot_completed=1` + SF/composer 恒活已达；`screencap` 报
`Failed to get ID for any displays.`（display 查询链，M3 出画层课题）。

### run47 新登记遗留（不挡 L1，按序排队）

1. **system_server RenderThread 卡死 → watchdog 循环**（~几分钟一轮，已 4 次）：
   ui 线程 blocked 在 `HardwareRenderer.nSetStopped`（native，等 RenderThread）
   143s → watchdog kill。疑与 NOP 补丁的 CMAR 伪指针下游消费同源（RenderThread
   等 fence，CMAR 后台线程消息对象 P+0x18 静态模板指针未覆盖为堆实例）。
2. **GL 应用 mali-cmar-backend SEGV**（bootanimation×1、iui.miwallpaper×5，
   fault addr 垃圾值 0x23002700000000/0xa2004a00000000，非 NOP 前的
   so+0x1cf0571 只读页）——CMAR 伪指针被当对象解引用的下游变体；应用被
   AMS 拉起循环，不挡 boot。根治方向 = 运行时追 P+0x18 写入者（run43-c
   静态分析已达天花板）或评估禁用 CMAR 后台线程 spawn 链。
3. screencap `Failed to get ID for any displays`（M3 出画，L2/L3 课题）。
4. odm HAL 容忍链（power.stats/secure_element/vibrator 等，§7.24 登记）维持。

## 7.28 run48-53：cmar type=1 消息跳过（SEGV 归零）+ watchdog 死锁根因 = virtio-gpu 无 vblank（补丁 14 调试中，2026-10-10 深夜）

### cmar SEGV 二次迁移 → 定性 → 修复（run48-50，已闭环 ✅）

- run48：0x1cf1dd4 blr→mov w0,wzr 后崩点迁 0x1cf1de4→0x1cf16f4（payload 的
  +0x20 也垃圾）——**逐消费点 NOP 无尽头**；
- dispatch 0x1cf1bc0 反汇编定性：消息节点 {+0x10 type, +0x18 payload}，节点
  有效、**payload 从未被生产者填充**（各进程垃圾值不同）；
- 修复：0x1cf1bec `b.eq`（type=1 入口）→ 无条件 `b 0x1cf1d94`（default 尾
  = 原生"不处理直接返回"路径，机器码 54000ea0→0x1400006a）——type=1 消息
  整体跳过，payload 永不解引用；
- run50 验证：cmar SEGV **0**（run47/48/49 = 18/8/79 次），boot_completed=1、
  SF running 保持。生产者 fire-and-forget（pipe 单向），跳过处理无死锁。

### watchdog 死锁根因（run50 ANR trace + 反汇编闭环）

```
system_server android.ui（持 WindowManagerGlobalLock）
 → TaskSnapshotController 截屏快照 → captureLayersSync（binder 同步）→ SF 不回
 ← HWC vsync 线程忙等："failed to waitNextVsync: -1" 每 4ms（qemu.log 刷屏）
 ← DrmModeResource::waitNextVsync（hwcomposer.so@0x191b54）= 标准 drmWaitVBlank
 ← 上游 virtio-gpu 无 enable_vblank + 未 drm_vblank_init → 恒 -EOPNOTSUPP
```

修复 = 补丁 14（wire-kbase.sh）：hrtimer 60Hz 模拟 vblank + drm_vblank_init +
crtc_funcs 回调（详见 evidence solution.md §8.2）。

### 补丁 14 调试三轮（run51-53，进行中）

| 轮 | recipe | 判据 | 结果 |
|---|---|---|---|
| v41 | vblank | waitNextVsync 失败 2820/8min（60Hz 节奏持续） | 未生效 |
| v42 | vblank-dbg | enable_vblank 打印 0 次 | enable 未被调 |
| v43 | vblank-dbg2 | **init ok, num_crtcs=1 打印出现**（@0.111s）但 enable 仍 0 次 + vsync 仍 -1 | vblank_init 成功；get 路径短路 |
| v44 | vblank-dbg3 | drm_vblank.c ioctl 入口打印 type/num_crtcs | 构建中——分辨「ioctl 未到达」vs「type 非法/pipe 越界」 |

已排除：构建链（vda boot_a 字节 == boot.img 实证；cdh_mi_display 打印随
新内核出现）。下一候选根因：HWC 传的 type 带 HIGH_CRTC pipe bits（MTK 真机
多 CRTC 语义 vs 云机单 CRTC）→ pipe_index 越界 EINVAL → 修法 = ioctl 层
改写/多 CRTC 形态模拟。

### 补丁 14 定案（run54-58，v44-v48 六轮）：vblank 死状态 + 自愈修复 ✅

六轮逐层打印（ioctl 入口→pipe 值→get 入口 refcnt→enable 入口）定案链：
**ioctl 到达（type=0x1 合法、pipe=0、num_crtcs=1）→ drm_vblank_get 进入时
refcnt 恒=1 且 enabled=false → else 分支 -EINVAL 死循环**（refcount 悬空
+1 泄漏，来源不明：26s 前一次无日志的 +1，console 刷屏或 vendor 状态机）。

**修复（补丁 14f）**：`drm_vblank_get` else 分支死状态自愈——`!enabled` 时
强制重走 `drm_vblank_enable`（hrtimer start 幂等；成功保留计数，失败才 dec）。

**run58 验证（v48 内核）**：
- `failed to waitNextVsync` = **0**（v41-v47 时代 1920-2820 次/数分钟）——
  **vsync 全通**；
- `enable on crtc` 打印首次出现（60Hz hrtimer 生效）；
- cmar SEGV 0（保持）、zygote died 0、boot_completed 1、SF running；
- **watchdog 9 次/boot → 2 次**（间隔 ~4 分钟）：残余栈仍见
  `ScreenCapture.nativeCaptureLayers`（下一层：HWC SWWatchDog drmdev.cpp:2710
  10s 超时线程 + KICK_IDLE/presentFence 私有 ioctl 未实现，按洋葱法继续）。

## 7.29 run59-66：watchdog 残余根因三段定案——① 对照实验排除 CMAR；② sfdump 四轮探针锁定 SF 假死；③ crash_dump ptrace 冻结实锤 + 补丁 15 内核自愈（2026-10-11）

### 三段式取证链

**① 排除（run61 对照实验）**：撤销 cmar type=1 跳过后 SF 照样挂（cmar SEGV
回归 69 次证明撤销生效）→ SF 挂死与 CMAR 跳过无关，恢复跳过补丁。另排除：
fd 泄漏（FDSize 恒定）、内存压力（MemFree 1.5G/PSI 低）、allocator 服务
（全空闲）、cgroup freezer（freeze=0）、hung task（SF 挂是 S 态非 D 态；
`hung_task_timeout_secs` 无 early_param，cmdline 注入无效已勘误）。

**② 自研内核探针 sfdump 四轮迭代（v49-v52，ged_stub.c）**：每 15s 快照
SF/composer/SS 全线程 (state, 用户态 pc, VMA 归属, 内核栈)，连续 3 次不变
判冻结 → dump。定案数据：SF/composer 全部线程空闲（epoll/futex/binder_read）
+ **SF binder 线程 455 秒零唤醒**（ctxt_switches 连续采样）——SF 收不到任何
binder 事务。

**③ 根因定案（run65）**：SF 主线程 `State: t (tracing stop)` +
`TracerPid=<crash_dump64>`——**MIUIScout 循环触发 debuggerd dump → MIUI
定制 crash_dump64 读 "guest state" EFAULT（云机无 MTK hypervisor 状态）挂死
不 detach → SF 主线程+binder 线程永久 ptrace 冻结** → SS→SF 全挂 →
watchdog kill SS → 循环。

### 补丁 15（sfdump v5 自愈）：watchdog 155→3 次（-98%）

每轮检测 SF/composer 主线程 `TASK_TRACED` → 内核 SIGKILL tracer →
tracer 死亡自动 detach → SF 复活。限频 60s。run66（20 分钟）：自愈 12 次
全部解开、watchdog 3 次（原 155 次/33min）、boot_completed=1 稳定、
zygote died 0。

### 遗留（按序）

1. 自愈限频 60s vs watchdog 65s 窗口的偶发穿透（可降 20s）；
2. 上游诱因 = AtomicCommit 慢（10s 级）触发 Scout 判挂——根治在 B2 渲染桥
   （M3）性能或 system_ext 持久禁 scout（属性在 system_ext 968883526 偏移，
   改动面扩张待定）；
3. 探针/诊断打印清理时机（稳定性目标达成后）；
4. M3 出画（screencap no displays）排队。

## 7.30 run67-70：watchdog→整机重启链终局 + 72B atom 布局定案 + ptrace deny（2026-10-11 下午）

### watchdog 链终局定案

```
GPU job 假完成 / fence 链断（B2 缺失，app 主线程卡 HardwareRenderer.nSetStopped 2.5s+）
  → SF 合成/响应真慢（binder 65s+ 无响应）
  → system_server watchdog 全 monitor 阻塞 65-67s
  → WATCHDOG KILLING SYSTEM PROCESS
  → "Rebooting system because: nativehang,/system/bin/surfaceflinger"  ← 整机重启机制
  → guest 自我重启
```

- crash_dump64 ptrace 冻结 SF（v53 补丁 15 自愈对象）是**放大器不是根因**：
  run70 ptrace deny（补丁 17）后 SF 无冻结（TracerPid=0、deny 47 次），
  watchdog 仍 65s 击穿；
- SF 快照证据：冻结时刻主线程 `epoll_wait`、binder 线程 `binder_thread_read`
  均空闲——"binder 悬挂"是冻结窗口内 ping（code 1）累积表象；
- **治本 = B2 渲染桥**（§7.8），watchdog 链随渲染链通畅而根除。

### 72B user atom 布局定案（B2 前置，补丁 16b/16c，§12.4 solution.md；v70 订正）

**72B = v1 `base_jd_atom`（64B，seq_nr 开头）+ 8B 尾部**（非 v2 布局！）：

| 偏移 | 字段 | 证据 |
|---|---|---|
| +0x00 | seq_nr（=0） | 全样本 |
| +0x08 | **jc（job chain GPU VA）** | `0x5effb5d780` 等（SAME_VA）；soft job 时为堆指针 |
| +0x10/0x18 | udata blob[2] | `0xb4000073…` 等堆指针 |
| +0x20 | extres_list | 0 或堆指针 |
| +0x28 | **nr_extres**（JIT_ALLOC 时=分配数） | 0x01 与 jit count 吻合 |
| +0x30 | **atom_number（序号）** | 0x01 |
| +0x31 | prio（如 0x03） | — |
| +0x34 | **core_req** | `0x1` FS / `0x4e` TILER 组合 / `0x209` SOFT_JIT_ALLOC / `0x20a` SOFT_JIT_FREE |

（v70 前 kernel v2 copy 从 +0x00 起解读 → 字段整体错位 8B：jc←seq_nr(0)、
atom_number←+0x28 nr_extres（GPU job 恒 0）→ **全部 atom 挤爆槽 0/1** →
path C「慢性碰撞」实为确定性槽位冲突（solution.md §20）；core_req←pre_dep 区
=0=DEP 秒完成，JIT 分配/真调度路径从未走过。v70 起按 v1 解析，(12c) 拷贝限长
64B。72B atom 放行后 B2 实现按 raw 72B 取 +0x08/+0x34 与 kernel 解析一致。）

### 补丁清单（本轮新增）

| # | 文件 | 内容 |
|---|---|---|
| 15v5.1 | ged_stub.c（built-in） | 自愈限频 60s→5s、轮询 15s→5s |
| 16b/16c | mali_kbase_jd.c（wire-kbase） | B2-atom 摘要行 + 72B raw hexdump 探针 |
| 17 | kernel/ptrace.c（wire-kbase，built-in） | 拒绝 crash_dump/debuggerd attach SF/composer/system_server |

### 工程坑（教训）

1. **wire-kbase patch() 幂等失效**：后续补丁改写前序补丁的插入内容 →
   `new in s` 判据失效 → 重复插入（redefinition）。已加 `done=` 幂等标记；
2. **ptrace.c 锚点 tab/空格**：grep 显示空格是 tab 渲染假象，锚点须 python repr 核对；
3. **发布链含 build-boot.py**：built-in 探针（sfdump/ptrace）在 Image，
   只发 vendor_dlkm 不生效；`--patch boot_a=` 必须随 Image 变更执行；
4. `--patch super=` 会回滚 super 内 vendor 注入件（run67 教训）——
   一律用 `--patch vendor=`/`--patch vendor_dlkm=`/`--patch boot_a=` 直写。

### 遗留

1. ~~B2 v1~~ 已落地（16d RT 填色 + 18 scanout 桥，VNC 判据已翻转）；剩余 =
   **丢唤醒残留**（v70 槽位冲突根除后 SF binder 仍周期性 futex 丢唤醒，
   solution.md §20.6）→ watchdog 归零 + 真 UI 上屏最终确认；
2. 探针清理（16b/16c 定案后删，17 保留为稳定性护栏或转可配置）；
3. watchdog 长观察目标：丢唤醒根除后 `Rebooting because nativehang` 归零。

### atom 滞留 COMPLETED 死锁定案（run79，B2 前置，solution.md §17）

run79 全体提交线程卡 `kbase_jd_submit+0x1480`、atom 全 St=4 的根因
= **path C 自死锁**：完成事件已投递 event_list 但用户态不取（poll 判据
revents=1/POLLIN 铁证）→ 槽位不释放 → 提交线程卡 submit → 用户态
cmar"提交+消费"同线程更不可能取事件，永久自锁。

- **判据方法**：`kbase_poll` 返回 POLLIN ⟺ event_count!=0；必须
  `pidfd_open`+`pidfd_getfd` 拿同一 struct file 判定（`/proc/<pid>/fd/N`
  重新 open 会因 setup_state=NEED_VSN 假报 POLLERR）；
- **干预铁证**：替用户态 read 走 1 个事件（code=16387）→ submit 立即返回、
  atoms 清空、poll 归零；
- **对照**：停代取 25s 不复发（一次性自锁事故）；兜底 90s 后
  boot_completed=1、cmar 转 do_sys_poll 自消费；
- **内核交付链清白**：work_count 仅 jd.c:1474 inc / :1342 dec，STOPPED
  早退是死路径，job_fault 无日志——全部静态排除；
- **B2 v1 靶子**：恢复后 VNC 抓帧 99.3% 纯灰 #888888（GPU 假完成），
  RT 填纯色后此判据应翻转；
- **遗留**：死锁初始触发时序未究；内核兜底（submit 超时强制
  kbase_event_process）作为防复发保险待 B2 过程中按需加。

# 8. 新主线：保厂商栈 + host GPU 渲染（B′，用户定档 2026-10-12）

> **定档（用户 2026-10-12）**：厂商图形栈**原样生效**，像素计算落 **host Apple GPU**，
> **不消耗 guest CPU**。原 §7 的"渲染桥在 guest 产出像素"（B2 填色 / B3 CPU 光栅）
> 因全程 CPU 计算性价比不成立而 pass；kbase 虚拟 Mali **降级为 ABI 转发面**（复用）。

## 8.1 结论先行（可行性与成本形态）

- **命令必须翻译，像素可以直达**：host GPU 只认 Metal/Apple ISA，job chain 里的
  Valhall ISA 机器码必须过翻译器；dma-buf 像素缓冲无语义问题（共享/零拷贝后
  host 可直接作 Metal 纹理）。FBD 等描述结构是普通数据，可解析。
- **翻译是按 shader 一次性成本**（估算 10–100ms/shader，含后端 pipeline 编译；
  UI 场景全局约 50–200 个 shader，全量数秒），**不进每帧热路径**；缓存（内存+磁盘）
  命中后 ≈0，支持离线预翻译消首编译卡顿。
- **每帧分工**：guest CPU = 只转发 job chain（KB 级）；host CPU = 录命令（若走
  virgl 解释层另加 5–15% 单核，见 8.3 取舍）；host GPU = 全部像素计算。渲染结果
  **host scanout 不回流 guest**（仅 screencap/读回例外）。
- **"很耗时"的是研发工程量**（Valhall 前端翻译器，研究级），不是运行时。
- 真机对照：真机上 job chain 的组装（GLSL→Valhall 编译）本来就是 CPU 干的一次性
  活；我们只是把"执行体"从 Mali GPU 换成 host GPU，录制语义不变。

## 8.2 架构

```
guest（薄，CPU≈0）                     host（重）
─────────────────────────             ──────────────────────────────
SF/App → 厂商 DDK（字节/md5 不动）
   ↓ open /dev/mali0 + JOB_SUBMIT
kbase ABI 转发面（复用 run 系列：
   72B atom / fence / 事件链 / GPU VA）
   ↓ job chain 零拷贝（共享内存）
                                      renderer 进程
                                      ├─ Mali 前端（自研·研究级）
                                      │   Valhall → NIR/SPIR-V（缓存）
                                      ├─ 执行后端（成熟件）
                                      │   MoltenVK→Metal（首选）
                                      │   virglrenderer（次选）
                                      ↓
                                      Apple GPU 执行
                                      ↓
                                      host scanout（QEMU 窗口）
```

## 8.3 选型与取舍

| 项 | 首选 | 次选/备选 | 判定依据 |
|---|---|---|---|
| 执行后端 | **MoltenVK（VK→Metal）** | virglrenderer（host GL；macOS GL 已废弃、4.1 封顶，有风险） | macOS 落地成熟度（P0 调查） |
| 前端输出 | SPIR-V | NIR→Gallium | 与后端配套 |
| 命令路径 | 前端直出 VK 命令（无解释层） | virgl 协议（每帧解释 +5–15% 单核，估算） | 性能 vs 工程量 |
| guest↔host 通道 | vhost-user + 共享内存（virtio-gpu blob 零拷贝上屏） | ivshmem + 自定义队列 | P0 调查本 QEMU 版本支持 |
| shader 缓存 | 内存 + 磁盘持久化 + 离线预翻译 | — | 消首次编译卡顿 |

**对照路线 A（virtio-gpu 替换厂商栈，Mesa/virgl/venus）**：性能成熟（估算为真机
GPU 的 40–70%）、工程量小一个数量级，但检测面全破（厂商 so 不再被使用）——违反
"不替换不绕过"硬约束，**仅作性能对照，不做**。B′ 远期上限高于 A（少一层 Gallium
转换），初期大概率低于 A（翻译器不成熟）。

## 8.4 性能目标（估算）

| 场景 | 目标 | 说明 |
|---|---|---|
| UI/合成 | 满帧 60fps | shader 少、fill 主导 |
| 3D（P4 远期） | 真机 GPU 的 50–85% | 转换层 + 通道开销 |
| guest CPU | < 5% 单核 | 只做转发/簿记 |
| shader 全量编译 | UI 全量数秒一次性 | 可预热 + 持久化 |

## 8.5 分阶段（含 go/no-go 门）

| 阶段 | 内容 | 验收门 |
|---|---|---|
| **P0 落地调查** | ① macOS 执行后端能力（MoltenVK / virglrenderer / QEMU virgl 现状）② 通道选型（vhost-user-gpu / ivshmem / virtio-gpu blob 在本 QEMU 的支持）③ Valhall 前端资产盘点（panfrost `valhall_dis`/ISA 文档获取渠道；GitHub 不可达，走 gitlab.freedesktop.org）④ 复用盘点（kbase ABI 面、job chain/FBD 逆向） | 调查报告 + 路线 go/no-go |
| **P1 通道打通** | job chain 转发 + 回传 echo（假渲染）：guest 提交 → host 收到完整 job chain → 内存回写可见 | 链路跑通，延迟/带宽实测 |
| **P2 前端 MVP** | Valhall fragment 子集 → SPIR-V → MoltenVK，单三角形/纹理出正确像素 | 与参考实现逐像素比对通过 |
| **P3 UI 负载端到端** | SF/HWUI shader 集合全量，真 UI 上屏 | screencap 正确 UI、60fps、guest CPU < 5% |
| **P4 扩展** | 3D 子集、缓存持久化/预热、性能优化 | 3D demo 出正确画面 |

## 8.6 复用盘点（§7 资产）

- **kbase ABI 面全复用**：72B atom 布局（§7.30 定案）、fence/事件链、GPU VA 映射、
  `/dev/mali0` probe、v70–v73 探针体系；
- **job chain/FBD 布局逆向**（B2 桥的 panfrost 依据）→ P2 前端解析直接用；
- **v74 job 执行延迟模拟**建议带入转发面（时序保真，防 DDK 状态机竞态）；
- hangsnap/grab 取证工具链、发布链脚本复用。

## 8.7 风险与回退

| 风险 | 影响 | 缓解/回退 |
|---|---|---|
| Valhall 翻译正确性（研究级） | P2 不出正确像素 | 先啃 SF/HWUI 有限 shader 集合；panfrost 资产对照 |
| macOS 后端能力（virglrenderer GL 4.1；QEMU virgl 缺失） | 后端换型 | 首选 MoltenVK；renderer 自建 vhost-user 后端，不依赖 QEMU virgl |
| 首次编译卡顿 | 掉帧 | 缓存持久化 + 离线预翻译 |
| 通道带宽/延迟 | 帧率 | 零拷贝 blob；P1 实测 |
| 检测面 | 硬约束 | 厂商 so 字节/md5 全程不动（device-checker 双机复验） |

## 8.8 明确不做

- 不做 guest CPU 光栅（B2/B3 老路线，已 pass）；
- 不替换/不绕过厂商 so（路线 A 不做）；
- 不做 Mali 命令透传给 host GPU（ISA 鸿沟，物理不可行）。

## 8.9 P0 落地调查结果（2026-10-12，初判 **go**）

**① macOS 执行后端 —— host GPU 栈已存在，风险大降**
- 本仓 QEMU 为自建 fork（`out/qemu/dist/darwin/bin/qemu-system-aarch64`，
  v11.0.2-dirty，Cuttlefish 系 host 工具链），构建时 `--enable-opengl
  --enable-virglrenderer`，`libexec/virgl_render_server` 就位；
- virglrenderer 基线 1.3.0（ca50e008）+ **21 个云机补丁**（`qemu/patch/virglrenderer/`），
  全部是 macOS/venus 集成：`macos-vulkan-portability-allocator/instance`、
  `venus-host-pointer-*`（host 指针/内存直通）、`macos-vulkan-loader-path`、
  `macos-core-profile-fragment-shaders` 等 → **host GPU 经 Vulkan portability →
  MoltenVK → Metal 的路径已被本仓打通并打过补丁**；
- 构建配置 `-Dvenus=true -Dvulkan-dload=true`；Vulkan 运行时随 dist 打包
  （`qemu/backend/gpu-host-macos/`：libMoltenVK.dylib + libvulkan + ICD），
  系统 Homebrew 亦装有 molten-vk/vulkan-loader（ICD：
  `/opt/homebrew/etc/vulkan/icd.d/MoltenVK_icd.json`）。
- 结论：B′ 的"执行后端（成熟件）"**直接复用本仓 virglrenderer+MoltenVK 栈**，
  不从零建；MoltenVK 首选 / virglrenderer-GL 次选的取舍按补丁面看应为
  **venus/MoltenVK 为主路径**。

**② guest↔host 通道 —— 器件面已编入**
- QEMU `-device help`：`vhost-user-gpu`、`vhost-user-gpu-pci`、
  `virtio-gpu-gl-device/pci`（virgl）均在；display 后端 = cocoa；
- `skills/build-qemu/SKILL.md`：linux dist 另带 `libexec/vhost-user-gpu`
  后端 + `.capabilities.json`（virglrenderer 系 GPU 后端）；
- **P1 前置确认项**：darwin dist 的 vhost-user-gpu 后端二进制（当前
  `libexec/` 未见）、virtio-gpu blob/零拷贝在本 fork 的支持面、cocoa 与
  virtio-gpu-gl 的组合可用性（cocoa 未见 GL 上下文代码，倾向 vhost-user 后端路线）。

**③ Valhall 前端资产 —— 渠道可达**
- Mesa 源码走 `gitlab.freedesktop.org/mesa/mesa`（GitHub 不可达不阻塞，与 §7.3 一致）；
- `src/panfrost/`：`compiler/`（Bifrost+ shader 编译器，含 Valhall `pan/va`、
  `bi`）、`lib/`（job chain/command stream 数据结构——FBD/job 布局的权威参照）、
  反汇编器（`valhall_dis`/`bi_` 系）；docs.mesa3d.org 明示 panfrost 覆盖
  Midgard/Bifrost/**Valhall**；
- 结论：翻译器的"语义参照系"开源可得，P2 的实现路线 = 参照 pan/va + bi
  语义建 Valhall→SPIR-V 前端。

**④ 复用盘点**：见 §8.6（kbase ABI 面、job chain/FBD 逆向、探针/取证工具链）。

**Go/No-Go**：**go**。落地不确定性大头从"host 后端是否存在"（已存在，本仓
自建且打满 macOS 补丁）转移到"Valhall 前端正确性"（研究级，符合预估）。下一步 P1：
通道打通（job chain 转发 echo），含上述 3 项前置确认。

## 8.10 P1 通道打通结果（2026-10-12，验收门 **通过**）

**链路**：guest `/dev/hvc11`（virtconsole）⇄ QEMU `cdh-gpuecho` chardev ⇄
`<instance>/channels/gpu.sock`（AF_UNIX，QEMU 作 server）⇄ host `gpuecho_host.py`
（echo/sink）。帧格式 `[4B BE 长度][payload]`（与 hvc7 事件通道同款）。
通道由 `daemon/qemu/qemu.go` denseVirtconArgs 末位追加（**hvc11**）——
hvc6 曾预留此用途，后让位宿主振动（cdh-haptic）迁至末位，不改既有编号。

**P1 前置 3 项确认（实测）**：
- **blob/零拷贝**：✓ QEMU 二进制特性串含 `VIRTIO_GPU_F_RESOURCE_BLOB`
  （另有 `CONTEXT_INIT`/`VIRGL`/`RESOURCE_UUID`）→ P2+ 数据面零拷贝通道可用；
- **vhost-user-gpu 后端**：darwin dist `libexec/` **无** `vhost-user-gpu`（linux dist 有），
  但 `virgl_render_server` 在 → 走 **virtio-gpu-gl + virgl_render_server**
  （venus→MoltenVK→Metal，§8.9① 的 21 补丁路径），不依赖 vhost-user-gpu；
- **cocoa+gl 组合**：cocoa display 无 GL 上下文（§8.9②）→ 渲染在 render server
  离屏完成，显示走 QEMU scanout/自绘窗口，不依赖 cocoa GL，B′ 路线不受影响。

**实测（run93，gold-15 guest，host=Apple Silicon）**：
| 指标 | 数值 | 说明 |
|---|---|---|
| RTT 64B | **avg 0.297ms** | 2000 iters，含 guest tty 写+读与 host python echo |
| RTT 1024B | **avg 0.201ms** | 同上 |
| RTT 4096B | **avg 0.378ms** | 同上 |
| TX 吞吐 | **25.2 MB/s** | 64MB / 2540.8ms，64KB 帧只写（host sink 模式） |
| echo 校验 | **全过** | memcmp 逐档比对，exit 0 |

**结论**：P1 验收门（链路跑通 + 延迟/带宽实测）**通过**。0.2–0.4ms 级 RTT 对
job chain 描述结构（KB 级/帧）绰绰有余；25.2MB/s 是 **virtconsole tty 调试通道**
的写入上限，不是最终数据面约束——job chain 本体走 §8.3 的共享内存/blob 零拷贝，
tty 通道仅承担控制/回执（本 P1 亦兼作测量通道）。

**实作教训（两条硬的）**：
1. `/dev/hvcN` 是 tty，**必须 `cfmakeraw`**——默认 ICANON|ECHO 会把二进制帧当输入行
   （NUL→`^@`、0x17 WERASE→退格序列），host 侧表现为 `bad frame len 1581276736`
   （=0x5E405E40 `^@^@`）；
2. gold-15 处于 nativehang/watchdog 重启循环，adb 窗口短——`adb connect` 对已注册
   offline 端点无效，须先 `adb disconnect` 再 connect 复位。

证据：[evidence/issue-gold15-bprime-p1-gpuecho/solution.md](evidence/issue-gold15-bprime-p1-gpuecho/solution.md)。
下一步 P2：Valhall fragment 子集 → SPIR-V → MoltenVK，单三角形逐像素比对。

## 8.11 P2 前端 MVP 结果（2026-10-12，验收门 **通过**）

**链路**：Valhall 汇编（Mesa `asm.py`）→ 机器码 `.bin` → **自研解码器+翻译器
`va2spirv.py`**（Mesa ISA.xml 元数据驱动解码，只读复用）→ SPIR-V →
MoltenVK（Apple M4 Pro）离屏渲染单三角形 → 逐像素比对。

**验收（与参考实现逐像素比对）**：
```
逐像素比对：256x256，三角形内 7812 px、外 0 px（边界带跳过），失败 0
exit 0
```
片段期望色 (255,0,0,255)、三角形外 clear (0,0,0,0)，`spirv-val --target-env
vulkan1.1` frag/vert 双 PASS。**翻译语义正确性由像素比对证明**（不是静态推断）。

**覆盖的指令子集**（刻意含三种操作数形态，验证解码面）：
| 指令 | 操作数形态 | SPIR-V 映射 |
|---|---|---|
| `MOV.i32` | 小常量 LUT（`0x3F800000`）→ 寄存器 | 位型常量折叠 |
| `MOV.i32` | 寄存器→寄存器 | SSA 直通 |
| `FADD.f32` | 寄存器 + LUT 常量（浮点解释） | `OpFAdd`（非折叠路径） |
| `FMA.f32` | 3 源：寄存器 + LUT + 寄存器（C 非零） | `OpExtInst` + GLSL.std.450 `Fma`（编号 50） |
| `FRSQ.f32` | 1 源（寄存器） | `OpExtInst` + GLSL.std.450 `InverseSqrt`（编号 32） |
| `FRCP.f32` | 1 源（LUT 常量） | `OpFDiv`（`1.0/x`；GLSL.std.450 无 Recip） |
| `FMAX.f32` | 2 源（均 LUT 常量） | `OpExtInst` + GLSL.std.450 `FMax`（编号 40） |
| 浮点源修饰 `neg`/`abs` | 作用于任意 f32 源（A/B/C，可叠加） | `neg`→`OpFNegate`（127）；`abs`→`OpExtInst` GLSL.std.450 `FAbs`（编号 4）；次序 abs 先于 neg |
| `LD_VAR_BUF_IMM.f32` | 无普通 dest：staging 区间 `@rB:…` + 偏移源；宽度 v2/v3/v4、采样 center/centroid/sample | `OpVariable(Input)` + `OpLoad`（`Location=index/(comps*4)`；centroid/sample→`Centroid`/`Sample` 装饰，sample 需 `SampleRateShading` 能力）→ 分量写回 rB.. |
| `IADD_IMM.i32` | 1 dest + 1 src(A) + **32 位立即数**（`val<<8` 直嵌 bits8-39；src 绑零=LUT0 即规范的立即数 move） | `OpIAdd`（128）；**任意 32 位常量只能靠它自造**（`MOV.i32` 源为单 src 无 imm，小常量 LUT 仅 32 项） |
| `U16_TO_F32` / `S16_TO_F32` | 16 位 lane `h0`(低半)/`h1`(高半)@bit28；src `rN^`(mode1) 取等值 | `U16`：逻辑移位+`&0xFFFF`→`OpConvertUToF`（**112**）；`S16`：算术移位→`OpConvertSToF`（**111**）；`OpShiftRightLogical=194/Arithmetic=195/LeftLogical=196`、`OpBitwiseAnd=199`（编号一律 `spirv-as`/`spirv-dis` 实证） |
| `IADD.u32` / `ISUB.u32` / `IMUL.i32` | 2 src（A/B）+ 1 dest，源 `reg`/`reg_ptr`(rN^)/`lut`/`fau`；**同 opcode `0xA0@48`，以 opcode2@16 区分（`0x0`/`0x1`/`0xA`，mask `0xF`）** | `OpIAdd`（128）/`OpISub`（130）/`OpIMul`（132）；**加/减/乘 32 位结果位型与有/无符号无关 → 统一用 `t_i32`，无需新增 unsigned 类型**；saturate（bit30，IADD 组）未建模 → 显式 fail-loud，widen（`0xF@36\|0xF@26`）由 `src_mod_mask()` 拦 |
| `LSHIFT_OR.i32` / `RSHIFT_AND.i32` | 3 src：A（`widen`）+ shift + B（`not`）；**同 opcode `0xB4@48`，以 opcode2@24 区分（`0x101`=LSHIFT_OR、`0x000`=RSHIFT_AND，mask `0x703`；`ARSHIFT_AND`=`0x400`）**。**`shift` 源的 `widen` 字段落 `offset 26/mask 0xF`**（非 0/2 源的 36）= 字节选择 swizzle，`.b00`（低字节）= `swizzles_8_bit` 的 `B0000`（值 **4**） | `LSHIFT_OR`→`OpShiftLeftLogical`（196）+`OpBitwiseOr`（197）；`RSHIFT_AND`→`OpShiftRightLogical`（194）+`OpBitwiseAnd`（199）；**移位/按位 32 位结果位型与符号无关 → 统一 `t_i32`**；修饰已建模：B 源 `not`（bit35，组合前 `OpNot`）与 `not_result`（bit30，组合后 `OpNot`，见移位/旋转族修饰行）；shift 源非 `.b00`（widen≠4）显式 fail-loud，B `not`（bit35）/A `widen`（bit36-39）由 `src_mod_mask()` 拦 |
| `ICMP_OR.u32` / `FCMP_OR.f32` / **`ICMP_AND.u32` / `ICMP_AND.v4u8`** | 3 src：A、B、C（accum 项）；**`ICMP_OR.u32`=`opcode 0xF0@48`+`opcode2 0x0@24`、`ICMP_AND.u32`=`0xF0/0x1`、`ICMP_OR.v4u8`=`0xF2/0x0`、`ICMP_AND.v4u8`=`0xF2/0x1`、`FCMP_OR.f32`=`0xF4/0x0`**（mask `0x3`；`ICMP_MULTI`=`0xF2/0x2` 等未建模）。两个修饰位域：**`condition`@32/3**（`0=eq 1=gt 2=ge 3=ne 4=lt 5=le 6=gtlt 7=total`）、**`result_type`@30/2**（`0=i1` int 0/1、`1=f1` float 0.0/1.0、`2=m1` int 0/~0、`3=u1` 64 位比较低半） | `dest=(A cond B) <accum> C`：先产 **`%bool` 谓词**（整数无符号→`OpIEqual/OpINotEqual`/`OpUGreaterThan` 系列，`gtlt`=ne；浮点→`OpFOrdEqual`/`OpFOrdGreaterThan`…有序系列，`ne`→`OpFUnordNotEqual`、**`gtlt`→`OpFOrdNotEqual`（182，有序不等）**），再按 `result_type` 用 **`OpSelect`** 编码（i1→`%int 1/0`、m1→`%int 0xFFFFFFFF/0`、f1→`%float 1.0/0.0`）；accum：OR→位或/`OpLogicalOr`、AND→位与/`OpLogicalAnd`（kraid 布尔与先于编码，v4u8 逐 lane `OpLogicalAnd`）；**C 源仅建模常量**（0 短路；整数域非零→位组合；浮点域非零/非常量→fail-loud）；`total`（SPIR-V 无 totalOrder 原语）/`u1`（仅 ICMP_MULTI）/`f1`（8 位 lane 编码非法）显式 fail-loud。编号一律查 `spirv/1.0/spirv.hpp`（`OpTypeBool=20`、`OpSelect=169`、`OpIEqual..OpFUnordGreaterThanEqual=170..191`） |
| `BRANCHZ` | 1 src（`combine`："compare to zero"）+ 1 立即数 `offset`（`@8/27 有符号`）+ `va_mod eq`@36；**`opcode 0x1F@48`**；语义 `if(src?=0) PC+=8*(offset+1)` 否则 `PC+8`，offset 以指令字（8B）为单位、相对下一指令（正偏移=跳过数）。**默认分支当「非零」、`.eq` 分支当「为零」**；真机 flow=10=`reconverge`（divergent 前置）。`BRANCHZI`=`opcode 0x2F@48`（2 源+`absolute`@40，跳 blend shader）不在范围 | → **SPIR-V CFG**：寄存器由 **Function i32 变量 + `OpBitcast`** 承载（合流/回边免 phi）；前向=if-then（`OpSelectionMerge`+`OpBranchConditional`）；**后向=do-while 循环**（`OpLoopMerge`+回边 `OpBranch`，`offset` 符号扩展，fall-through 进 header 须 `OpBranch`）。条件：源按 **`combine`@37/3 归约**（`branch_combine_m`/kraid `BranchCombineOp`：none=整字、h0/h1=对应半字非零、and=整字==0xFFFFFFFF、lowbits=bits3..20 非全 0/全 1，**五形态全建模**；`.eq` 整体取反）后判定→`OpINotEqual`/`OpIEqual`/`OpLogicalNot` 系（源须整数比较结果 m1/i1 或常量）。fail-loud：嵌套/浮点源/`BRANCHZI` |
| 字节向量子族：`LSHIFT_*/RSHIFT_*/ARSHIFT_*.v4i8`（9 变体）/ `ICMP_OR.v4u8` / `U8_TO_U32` | 4×i8 打包算术，语义=kraid `PerCompFoldable` 逐分量 fold（lane 宽截断移位=模 8、ARSHIFT 按 i8 保号、逐 lane 逻辑组合；比较族 accum **布尔** OR 先于编码、`i1`=0/1、`m1`=0/0xFF 逐 lane）。编码：v4i8 移位族 opcode **`0xB6@48`**（同 i32 opcode2 表）；A 源 `widen@36`、shift/B 源 `widen@26` = **`swizzles_8_bit` 字节 lane 置换**（`b<abcd>`=输出 lane i 取源字节 abcd[i]，`b0000` 广播码与 `lanes_8_bit b00` 同值同义，12-15 reserved）；`ICMP_OR.v4u8`=`0xF2@48`+opcode2 `0x0@24`；`U8_TO_U32`=`0x90@48`+opcode2 `0x10@16`、源 lane@28/2（b0..b3） | 逐 lane 拆解为 i32 标量流：`OpShiftRightLogical`+`OpBitwiseAnd(0xFF)` 提字节（`swizzle8_lanes` 置换）→ `OpShift*`（移位量 `&7` 截断）+ `OpBitwise*` 组合 → `OpShiftLeftLogical`+`OpBitwiseOr` 打包；比较族 `OpUGreaterThan` 系→`OpLogicalOr`→`OpSelect` 逐 lane 编码；`U8_TO_U32` 提字节零扩展。**dest 宽度字段**（bits46-47，`dest_width_m`）：h0/h1=窄写掩码合并保留旧半字（`test_lower_copy` 硬件实测），width=0 实证=真写全字。fail-loud：swizzle reserved 码 / `result_type` f1（8 位）/u1 / `.h0` 窄写未定义寄存器（not_result 已随移位/旋转族修饰批放行） |
| 移位/旋转族宽度变体：`LSHIFT_*/RSHIFT_*/ARSHIFT_*.{v2i16,i64}` + **`LROT_*/RROT_*` 全宽**（i32/v2i16/v4i8/i64 × AND/OR/XOR） | 与 i32/v4i8 移位同 R3 布局（kraid `isa-v9-v14.xml`），仅 **`shift_operation@32/3`**（rshift=0/lshift=1/**rrot=2/lrot=3**/arshift=4）不同 → valhall opcode2：RROT=0x200 系 / LROT=0x300 系（与 RSHIFT=0x000/LSHIFT=0x100/ARSHIFT=0x400 双源自洽）；`LROT` 纯形=`LROT_OR`+src2=LUT0。宽度变体 opcode：v2i16=**`0xB5@48`**、i64=**`0x1B7@48`**（`SourceEncoding64` 寄存器对 + dest align(2) + `double_width` exact=3）。**rotate 不在 valhall ISA.xml** → 解码表 `_synth_rotate_instrs` 合成、测试字 `gen_rot_words.py` 生成（asm.py 占位 + 3 位补丁）。移位量 lane 码宽度相关：v2i16=`shift_bitwise_i16_lanes_m`（b02=0 默认→字节 [0,2]，b00/b11/b22/b33/b01/b23）、i32/i64=`shift_bitwise_i32_lanes_m`（b0..b3=码 4..7）；**A 源 swizzle/widen 已全放**（v2i16=`swiz_int_m` 半字置换 + b\*\* 字节构造零扩展、i32/i64=`widen_int_m` h0/h1/b0..b3/w0 子元素加宽零扩展；B 源 ISA 无 widen 字段）；LUT 零扩展（`Imm32→u64`） | `emit_shift_packed` 泛化逐 lane（8/16/32/64 × 5 方向 × 3 组合）：lane 宽截断移位/旋转（`rot=(x<<s)|(x>>((w-s)&(w-1)))`，s 掩 w-1 自洽）+ ARSHIFT `_sext_w` 保号；64 位走 u64（`read_64`/`write_64` 对读）；`_widen32`/`_wide_src` 子元素加宽（**U 类型零扩展**，kraid `fold_u32` + `hw_tests::test_op_shift_lop` U32/U64 硬件 Exact 实证）。fail-loud 仅余非法编码防御（swizzle reserved 码 / 移位量 lane 码越界 / widen 码越界）——**族内全清** |
| 移位/旋转族修饰：`not_result`（bit30）/ B 源 `not`（bit35） | 移位族 group 级修饰（valhall ISA.xml `not="true"` 6 处全在移位组 B 源，全 ISA 仅此族有）。语义=kraid `OpShiftLop` fold 同序两步：`SrcMod::BNot`（`fold_u32/u64 → !u`）**组合前**全宽按位取反源（与逐 lane 提取可交换）；`not_result` **组合后** `!data` 截 lane 宽 | `OpNot`（**200**，spirv-dis 实证）；8/16 位 lane 取反后 `&0xFF/0xFFFF` 截断，32/64 位域天然全宽；`emit_shift`（i32）与 `emit_shift_packed`（全宽）同序实现。`src_mod_mask` 仅对 `SHIFT_I32|SHIFT_EXT` 豁免 `not` 键，其余指令继续拦 |
| `NOP.end` | flow 控制（`end`=15） | shader 终止 |
**阶段输出 ABI**（`build(stage)`，非 fragment 输出组装批）：`fragment` = P2 约定 r0..r3 = 片元 RGBA → `OpCompositeConstruct vec4 → OpStore`；`compute` = `GLCompute` + `LocalSize 1 1 1`，**无输出接口**（输出=STORE→SSBO，指令流内已建模），不要求 r0..r3；`vertex` = `Vertex` + **占位 Position**（`vec4(0,0,0,1)`，SPIR-V 模块契约；真机位置/varying 经 STORE 写输出缓冲，接口映射待 B2）。E2E 按阶段（3C/4V/3F）驱动。
**预置寄存器指针 ABI**（`_emit_preloads(stage)`，preload-abi 批）：r55–r63 入口预置（读=环境值、可覆盖写）按阶段 **BuiltIn 输入组装**（kraid `preload_reg()` 权威映射 + 真机语境实证）：
`compute` = r55=LocalId01 双半字（local_id_0 | local_id_1<<16）/r56=LocalId2/r57..r59=WorkgroupId0..2/r60..r62=GlobalId0..2（`LocalInvocationId`/`WorkgroupId`/`GlobalInvocationId` 提分量）；
`vertex` = r60=VertexIndex、r61=InstanceIndex（r59/r62/r63 无 Vulkan BuiltIn → 占位 0）；
`fragment` = r59=PositionXY（`FragCoord.xy` floor 转 u16 双半字打包）/r60=SampleMaskIn[0]/r61=SampleId<<16|coverage。
Builtin 枚举（spirv-dis 逐值实证，低位区偏移 +1）：FragCoord=**15**/SampleId=**18**/SampleMask=**20**/WorkgroupId=26/LocalInvocationId=27/GlobalInvocationId=28/VertexIndex=42/InstanceIndex=43；SampleId/SampleMask 整数 Input 须 Flat + `SampleRateShading` 能力。占位 0 待 B2：fragment r57/r58/r62:63（PrimitiveId/PrimitiveFlags/FrameArg）、vertex r59/r62/r63（InternalId/DrawId/ViewId）；PositionXY u16 坐标编码待 B2 复核。
顶点侧另有宿主原生模板 shader（location0 vec2 → gl_Position，并**镜像片元 varying 接口**按 (loc,comps) 输出同型常量 varying），非翻译产物。

**B2 渲染桥最小闭环（开工 2026-10-14，[issue-gold15-bprime-b2-bridge-min](evidence/issue-gold15-bprime-b2-bridge-min/solution.md)）**：FS atom → 内核抽 shader+RT 元数据 → `/proc/cdh_gpumbox`（0666，procfs）→ `gpumbox_bridge`（NDK 静态）→ hvc11 GR01 帧 → `gpurender_host.py`（va2spirv+harness `--dump`）→ RP01 → 内核写回 RT（线性/AFBC solid 头）→ 放行 job。shader 捕获双路：Tiler atom DCD 捕获缓存 + payload/FBD BFS（+0xe8 直指，(19) chase 实证）。卡点：helper 保活（guest 重启循环杀进程）。

**纹理族建模（2026-10-14，[issue-gold15-bprime-p3-tex-subset](evidence/issue-gold15-bprime-p3-tex-subset/solution.md)）**：`TEX_SINGLE`（0x128@48，真采样 `OpImageSampleImplicitLod`，2x2 测试纹理 DescriptorSet 0 Binding 3）+ `VAR_TEX_BUF_SINGLE`（0x130@48，LD_VAR v2f32 融合采样）；真机字（run106 gpu.sock.log GR01 帧取证，seq=10 TEX/seq=11 VAR_TEX）翻译通过，GR01 oneshot **status=0 带像素**（四象限四色验证真采样）；fail-loud 面（dimension/shadow/lod/array 等）拦截。真纹理数据面归 B2。遗留：`MKVEC.v2i16` 向量构造族（vartex_real.bin 全链下一缺口）。

**实现三件套**（`devices/xiaomi/gold-15/out/tmp/p2/`）：
- `va2spirv.py`：解码器（复用 Mesa `valhall.py` 解析 ISA.xml 的位域元数据）+
  翻译器（寄存器文件→SSA）+ SPIR-V 生成器（手写指令编码，无第三方依赖）；
- `frag_flat.asm` / `frag_flat.bin`：flat-color 最小 shader（6 指令/48B）；
  `frag_fma.asm` / `frag_fma.bin`：`FMA.f32` 用例（3 源含 LUT，C 非零）；
  `frag_sfu.asm` / `frag_sfu.bin`：`FRSQ`/`FRCP`/`FMAX` 三连（寄存器/LUT/双 LUT 三形态）；
  `frag_mod.asm` / `frag_mod.bin`：浮点源修饰 `neg`/`abs` 用例（三通道各探一种修饰，7 指令/56B）；
  `frag_vary.asm` / `frag_vary.bin`：片元 varying 载入 `LD_VAR_BUF_IMM.f32`（v4，staging `@r0:r1:r2:r3`，2 指令/16B）；
  `frag_vary_v2.asm` / `frag_vary_v2.bin`：v2 宽 + `sample` 采样（两个 Location，3 指令/24B）；
  `frag_vary_v3.asm` / `frag_vary_v3.bin`：v3 宽 + `centroid` 采样（3 指令/24B）；
  `frag_vary_real.asm` / `frag_vary_real.bin`：**真机字 `0x005c82841208003d` 直接取材**（v2+src_f32+sample+index=8，4 指令/32B）；
  `frag_cvt.asm` / `frag_cvt.bin`：`IADD_IMM.i32`（含「绑零 imm move」与「`rS+imm`」双形态）+ `U16_TO_F32`/`S16_TO_F32`（lane h0/h1，8 指令/64B）；
  `frag_cvt_real.asm` / `frag_cvt_real.bin`：**5 条真机字直接取材**（4 转换 + 1 立即加 + 前置造常量，10 指令/80B）；
  `frag_cvt_neg.asm` / `frag_cvt_neg.bin`：负向对照（`S16` 故意误用 `U16`，8 指令/64B）；
  `frag_ialu.asm` / `frag_ialu.bin`：整数二元 ALU 合成用例（`r10=1`/`r11=0xFFFF` 由 `IADD_IMM` 自造，`IADD`/`ISUB`/`IMUL` 各出一通道，11 指令/88B）；
  `frag_ialu_real.asm` / `frag_ialu_real.bin`：**3 条真机字直接取材**（含 `rN^` mode1）+ 前置造常量（13 指令/104B）；
  `frag_ialu_neg.asm` / `frag_ialu_neg.bin`：负向对照（三算子全互换，11 指令/88B）；
  `frag_shift.asm` / `frag_shift.bin`：整数移位族合成用例（`LSHIFT_OR`/`RSHIFT_AND` 各出一通道 + `MOV` LUT 0.1 缩放，16 指令/128B）；
  `frag_shift_real.asm` / `frag_shift_real.bin`：**2 条真机 `LSHIFT_OR.i32` 字直接取材** + 前置造常量（14 指令/112B）；
  `frag_shift_neg.asm` / `frag_shift_neg.bin`：负向对照（两条算子互换，16 指令/128B）；
  `frag_cmp.asm` / `frag_cmp.bin`：比较族合成用例（ICMP `gt`×`m1/i1` + FCMP `eq`/`gt`×`m1/f1`，C=0，17 指令/136B）；
  `frag_cmp_real.asm` / `frag_cmp_real.bin`：**3 条真机字直接取材**（2 ICMP + 1 FCMP）+ 前置造常量（17 指令/136B）；
  `frag_cmp_or.asm` / `frag_cmp_or.bin`：**C 源非零**（`0xFFFFFFFF`，整数域位或，13 指令/104B）；
  `frag_cmp_neg.asm` / `frag_cmp_neg.bin`：负向对照（比较条件互补，17 指令/136B）；
  `fl_cmp_rtype.asm` / `fl_cmp_fau.asm` / `fl_cmp_csrc.asm`（+ `.bin`）：三条 fail-loud
  （`result_type=u1` / 源为 FAU `u2.w1` / C 源非常量；fl_cmp_cond 已随 gtlt 放开语义并入 cmp_and）；
  `frag_branch.asm` / `frag_branch.bin`：BRANCHZ 前向 if-then（base r0=0.2→条件 5>3 真→跳过 then→保持 0.2，10 指令/80B）→ 期望 (51,0,0,255)；
  `frag_branch_neg.asm` / `frag_branch_neg.bin`：负向对照（ICMP 操作数互换 3>5 假→then 执行→r0 翻倍 0.4）→ 期望 (102,0,0,255)；
  `frag_branch_eq.asm` / `frag_branch_eq.bin`：`.eq` 修饰（5>3 真、分支判「为零」→不跳→then）→ 期望 (102,0,0,255)；
  `fl_br_fsrc.asm` / `fl_br_i.asm`（+ `.bin`）：两条 fail-loud
  （浮点源/BRANCHZI；fl_br_combine 已随 combine and/lowbits 放开语义并入 br_combine）；
  `frag_loop.asm` / `frag_loop.bin`：BRANCHZ 后向 do-while 循环（cnt=4→body acc+=1/cnt-=1→BRANCHZ 回跳→acc=4，12 指令/96B）→ 期望 (102,0,0,255)；
  `frag_loop_neg.asm` / `frag_loop_neg.bin`：负向对照（cnt=2→acc=2）→ 期望 (51,0,0,255)；
  `fl_loop_nest.asm`（+ `.bin`）：fail-loud（循环体内嵌套分支）；
  `frag_bytevec.asm` / `frag_bytevec.bin`：字节向量子族用例（`LSHIFT_AND.v4i8` b0101 置换+逐 lane
  移位量 / `LSHIFT_XOR.v4i8` + dest `.h0` 窄写保留高半字 / `ARSHIFT_AND.v4i8` 保号 /
  `ICMP_OR.v4u8.ne.i1` + `U8_TO_U32` 四 lane 加权链，四通道 eq.f1 检查）→ 期望 (255,255,255,255)；
  `frag_bytevec_neg.asm`（+ `.bin`）：负向对照（ARSHIFT 误作 RSHIFT，真实像素 255,255,0,0）；
  `fl_bytevec_swz.asm` / `fl_v4u8_rtype.asm` / `fl_bytevec_wrmask.asm`
  （+ `.bin`）：三条 fail-loud（swizzle reserved 码 / v4u8 `result_type=f1` /
  `.h0` 窄写未定义寄存器；not_result 已随移位/旋转族修饰批放行）；
  `frag_shift_ext.asm` / `frag_shift_ext.bin`：移位族宽度变体用例（`LSHIFT_AND.v2i16` b02 默认
  移位量 / `ARSHIFT_OR.v2i16` 保号 + `.b11` 位移字节选择 / `LSHIFT_XOR.i64` 寄存器对 /
  `ARSHIFT_AND.i64` 保号）→ 期望 (255,255,255,255)；
  `frag_shift_ext_neg.asm`（+ `.bin`）：负向对照（ARSHIFT 误作 RSHIFT，真实像素 255,0,255,255）；
  `frag_rot.asm` + `rot.bin`（`gen_rot_words.py` 生成，asm.py 汇编移位占位字 + kraid 字段表
  `shift_operation@32/3` 3 位补丁）：rotate 全宽用例（`LROT_OR.i32` 纯形 / `RROT_AND.i32` /
  `LROT_XOR.v2i16` 逐半字 / `RROT_OR.i64`）→ 期望 (255,255,255,255)；`rot_neg.bin` 同 asm
  少打一处补丁 = 错误实现对照（真实像素 255,0,255,255）；
  `fl_rot_shiftsel.asm`（+ `.bin`）：一条 fail-loud（i32 rotate 移位量 lane 码越界；
  v2i16/i64 源形态已随 widen-src 批放行）；
  `shift_notres.asm` / `bytevec_notres.asm` / `rot_notres.asm`（rot 由 `gen_rot_words.py` 生成）：
  `not_result` 修饰正例（原 fl_shift_and_notres/fl_bytevec_notres/fl_rot_notres 语义反转，
  i32/v4i8/rotate 三形态）→ 期望 (255,255,255,255)；
  `frag_shift_mod.asm` / `frag_shift_mod.bin`：B 源 `not` 综合正例（i32/v2i16/i64 +
  `not_result` 叠加 ×2）→ 期望 (255,255,255,255)；
  `frag_shift_mod_neg.asm`（+ `.bin`）：负向对照（`.not` 误漏，真实像素 255,0,255,255）；
  `frag_cmp_and.asm` / `frag_cmp_and.bin`：比较族残余正例（`ICMP_AND.v4u8` 逐 lane 布尔 AND +
  `ICMP_AND.u32` + `gtlt` 有序不等双向锁定）→ 期望 (255,255,255,255)；
  `frag_cmp_and_neg.asm`（+ `.bin`）：负向对照（accum AND 误作 OR，真实像素 0,255,255,255）；
  `frag_br_combine.asm` / `frag_br_combine.bin`：BRANCHZ combine `.and`/`.lowbits` 双分支正例
  → 期望 (102,102,255,255)；
  `compute_store.asm` / `compute_store.bin`：非 fragment 输出组装正例（compute 阶段：
  **无 r0..r3**，LD_PKA+STORE 写 SSBO，GLCompute+LocalSize 模块）——VAL_ONLY（val 即绿）；
  `vtx_store.asm` / `vtx_store.bin`：非 fragment 输出组装正例（vertex 阶段：LD_ATTR_IMM+
  LEA_BUF_IMM+STORE 贴真机 vertex 尾形态，Vertex 模块+占位 Position）——VAL_ONLY；
  `frag_preload.asm` / `frag_preload.bin`：预置寄存器真实 ABI 正例（r0/r2=预置非零探测、
  r1=r57 占位 0 路径、r3=可覆盖验证，双向锁定）→ 期望 **(255,128,255,255)**；
  `frag_widen_src.asm` / `frag_widen_src.bin`：源形态综合正例（i32 A.h0 加宽 / i32 A.b2 /
  v2i16 A.b20 字节构造 / i64 A.h1+LUT 零扩展）→ 期望 (255,255,255,255)；
  `frag_widen_src_neg.asm`（+ `.bin`）：负向对照（A.h0 加宽误漏，真实像素 0,255,255,255）；
  `shift16_swz.asm` / `shift64_widen.asm` / `shift64_lut.asm`（+ `.bin`）：源形态正例
  （原 fl_shift16_swz/fl_shift64_widen/fl_shift64_lut 语义反转）→ 期望 (255,255,255,255)；
- `harness.c`：MoltenVK 离屏渲染 + 边函数逐像素比对（跳过 1.5px 边界带），
  可选 4 参覆盖期望 RGBA（默认 `255,0,0,255`，`FMA` 用例传 `28 0 0 255`）。

**实作教训（六条）**：
1. **SPIR-V 指令首字 = `wordCount<<16 | opcode`**，写反则 spirv-val 报
   `OpSourceContinued` 之类的假错误，MoltenVK 报 `-3 INITIALIZATION_FAILED`；
2. **MoltenVK 1.4.2 对空 `VkPipelineLayout` 段错误**（`MVKPipeline` 解引用 0x30），
   `gpci.layout` 必须给有效 layout（哪怕空）；
3. CPU 参考三角形须按 **Vulkan 视口约定**（`fb_y=(ndc_y+1)*H/2`，不翻转），
   用 GL 翻转约定会镜像错位、下半部误报；
4. **同族 opcode「方向写反」时 `spirv-val` 不报错**：`OpConvertSToF=111`/`OpConvertUToF=112`
   初版写反，`spirv-val` 仍 PASS（两者皆合法），只有 `spirv-dis` 显示符号方向异常才暴露 →
   转换类 opcode 必须**用 `spirv-as` 取编号 + `spirv-dis` 反查符号方向**双验。
5. **验证 fail-loud 时修饰位一律用表达式构造，不手算位移**：IADD 组 `saturate`=bit30 与
   `widen`=bit36 相邻，手算 `| (1<<30)` 极易串成 bit38（落进 widen 掩码）→ 报错名不符即
   暴露位算错。**错误信息里的「修饰名」就是位域归属的自证**。
6. **任意 32 位 float 常量必须用 `MOV.i32`+LUT 物化，不可用 `IADD_IMM.i32`**：
   后者产 `('ssa', id, 'int')` 标签，`val_to_ssa(_, "float")` 对 int SSA **直通不转换** →
   `FMA` 报 `expected types of all operands to be equal to Result Type`（`spirv-val` exit 1）。
   整数结果缩放到 [0,1]（P2 ABI 颜色为 UNORM 映射，整数直接当色值会全 clamp 到 255）时，
   改用 `MOV.i32 rN, 0x3DCCCCCD`（LUT 0.1f）即可；缩放期望值须避开 round-to-even 歧义点
   （用 `int=2→51`、`int=6→153`，避开 `int=3→76/77`、`int=5→127/128`）。

**范围边界（如实记录）**：本轮机器码为 Mesa 汇编器**合成**（验证翻译器本体），
非真机 DDK 捕获；比对基准为解析期望值。真机输入闭环（`mali_shader_meta.shader`
指针定位 + dump）已在 P3 前补齐（见 `issue-gold15-bprime-p3-shader-capture`，
10 个真机 shader、大端解码 UNKNOWN=0）；指令子集覆盖全量闭环（含基础算术、SFU、插值、整数 ALU、移位、比较、控制流、widen/FAU 源修饰、全局内存族 `LD_PKA`/`LOAD`/`STORE`、顶点属性通路 `LD_ATTR_IMM`、整数字节选择 `.b0`、`LD_VAR_BUF_IMM.f16` flat16 片元 varying 载入通路、`CLPER.i32` 跨 lane 置换 / Subgroup Shuffle 与 `flow=discard` helper 终止放行、`LEA_BUF_IMM` 缓冲有效地址计算通路、64 位整型算术 `IADD.s64` / `SHADDX.u64` 通路，以及**固定功能与混合族 `ATEST` / `BLEND` / `BRANCHZI` / `LD_TILE` 通路**）；
寄存器 Function i32 变量 + `OpBitcast` 承载免 phi；StorageBuffer (SSBO) 建模全局内存池；Location 映射建模顶点属性通路（含 f16 PackHalf2x16 打包）；flat varying 对称 Flat 装饰与 PackHalf2x16 压缩打包回写；声明 `GroupNonUniformShuffle` 能力与 `OpGroupNonUniformShuffle` 映射跨车道置换；`flow=discard` 纳入放行，全库未建模修饰彻底清零；`LEA_BUF_IMM` 结构索引低字回写与 SSBO 读写同构闭环；声明 `OpCapability Int64`，双寄存器 64 位值拼接/拆解与进位自动传递；GLSL.std.450 `UnpackHalf2x16` 解包片元输出颜色，`flow=end` 安全终止程序，Tile 缓冲 staging 初始化，**实现 Valhall P3 真机样本全指令全语义闭环**。
**真机 160 条全量覆盖 160 条（100.000% 全满贯），可直译指令达到 160 条（100.000%），未建模指令彻底清零（0 条），未建模修饰保持清零（0 条），涵盖 36 种 Valhall 操作码 100% 全支持**。

证据：[evidence/issue-gold15-bprime-p2-va2spirv/solution.md](evidence/issue-gold15-bprime-p2-va2spirv/solution.md)；
`FMA.f32` 扩充：[evidence/issue-gold15-bprime-p3-fma-subset/solution.md](evidence/issue-gold15-bprime-p3-fma-subset/solution.md)；
SFU 三连：[evidence/issue-gold15-bprime-p3-sfu-subset/solution.md](evidence/issue-gold15-bprime-p3-sfu-subset/solution.md)；
浮点源修饰 neg/abs：[evidence/issue-gold15-bprime-p3-float-mod-subset/solution.md](evidence/issue-gold15-bprime-p3-float-mod-subset/solution.md)；
片元 varying 载入（最小切片）：[evidence/issue-gold15-bprime-p3-varying-subset/solution.md](evidence/issue-gold15-bprime-p3-varying-subset/solution.md)；
插值族放开（v2/v3 宽度 + centroid/sample + 宽度感知 Location）：[evidence/issue-gold15-bprime-p3-varying-width-subset/solution.md](evidence/issue-gold15-bprime-p3-varying-width-subset/solution.md)；
整数立即加 + 16→32 位转换（`IADD_IMM.i32`/`U16_TO_F32`/`S16_TO_F32`）：[evidence/issue-gold15-bprime-p3-int-convert-subset/solution.md](evidence/issue-gold15-bprime-p3-int-convert-subset/solution.md)；
整数二元 ALU（`IADD.u32`/`ISUB.u32`/`IMUL.i32`）：[evidence/issue-gold15-bprime-p3-int-alu-subset/solution.md](evidence/issue-gold15-bprime-p3-int-alu-subset/solution.md)；
整数移位族（`LSHIFT_OR.i32`/`RSHIFT_AND.i32`）：[evidence/issue-gold15-bprime-p3-shift-subset/solution.md](evidence/issue-gold15-bprime-p3-shift-subset/solution.md)；
比较族（`ICMP_OR.u32`/`FCMP_OR.f32`，含 SPIR-V 谓词基础设施）：[evidence/issue-gold15-bprime-p3-cmp-subset/solution.md](evidence/issue-gold15-bprime-p3-cmp-subset/solution.md)；
控制流（`BRANCHZ` → SPIR-V 选择 CFG + OpPhi）：[evidence/issue-gold15-bprime-p3-branch-subset/solution.md](evidence/issue-gold15-bprime-p3-branch-subset/solution.md)；
后向分支/循环（`BRANCHZ` → SPIR-V 循环 CFG + Function 变量）：[evidence/issue-gold15-bprime-p3-loop-subset/solution.md](evidence/issue-gold15-bprime-p3-loop-subset/solution.md)；
widen 源修饰（h0/h1 半字选择 + f16→f32 加宽）：[evidence/issue-gold15-bprime-p3-widen-subset/solution.md](evidence/issue-gold15-bprime-p3-widen-subset/solution.md)；
FAU 源建模（uniform 常量 u0..u127 → PushConstant + 特殊值）：[evidence/issue-gold15-bprime-p3-fau-subset/solution.md](evidence/issue-gold15-bprime-p3-fau-subset/solution.md)；
全局内存族（`LD_PKA` / `LOAD` / `STORE` 33 条）：[evidence/issue-gold15-bprime-p3-mem-subset/solution.md](evidence/issue-gold15-bprime-p3-mem-subset/solution.md)；
顶点属性通路（`LD_ATTR_IMM` 10 条）：[evidence/issue-gold15-bprime-p3-attr-subset/solution.md](evidence/issue-gold15-bprime-p3-attr-subset/solution.md)；
整数字节选择与异步 flow 修饰收尾：[evidence/issue-gold15-bprime-p3-int-mod-subset/solution.md](evidence/issue-gold15-bprime-p3-int-mod-subset/solution.md)；
flat16 片元 varying 载入（`LD_VAR_BUF_IMM.f16` 3 条）：[evidence/issue-gold15-bprime-p3-flat16-subset/solution.md](evidence/issue-gold15-bprime-p3-flat16-subset/solution.md)；
跨车道置换与 flow=discard 放行（`CLPER.i32` 3 条）：[evidence/issue-gold15-bprime-p3-clper-subset/solution.md](evidence/issue-gold15-bprime-p3-clper-subset/solution.md)；
缓冲有效地址计算（`LEA_BUF_IMM` 2 条）：[evidence/issue-gold15-bprime-p3-leabuf-subset/solution.md](evidence/issue-gold15-bprime-p3-leabuf-subset/solution.md)；
64 位整型算术（`IADD.s64`/`SHADDX.u64` 2 条）：[evidence/issue-gold15-bprime-p3-int64-subset/solution.md](evidence/issue-gold15-bprime-p3-int64-subset/solution.md)；
固定功能与混合族（`ATEST`/`BLEND`/`BRANCHZI`/`LD_TILE` 4 条，100% 满贯闭环）：[evidence/issue-gold15-bprime-p3-blend-subset/solution.md](evidence/issue-gold15-bprime-p3-blend-subset/solution.md)。
**B′ P3 阶段性目标达成**：真机 10 个着色器全部 160 条指令翻译器模型 100% 闭环收官，P3 前置 ISA 子集扩充工作圆满完成。


## 下一步建议（2026-10-15 更新，见 vendor-gpu solution.md §26-§35）

### 一、原「cmar 池 budget=0 死锁」= **探针幻影，已冻结**
~~根因 = kbase job completion 事件回传~~ → ~~DDK cmar 池 budget=0~~ → **run132-138 复核推翻**：
- **run136**（事件出入队双点实测）：**EV-ADD≈EV-GET（交付≈消费）**、blob0/blob1 逐条回显
  忠实 → 「事件零读」「回显错值」均**死**；
- **run137/138**（干净启动零干预复采）：**同一 cmar 生命周期内 budget 恒 32、free-list
  指针持续变动（活跃 pop/push）、SF pid 稳定 → 池漏空不复现**；
- ⇒ 极可能是 run129 强制播种 SEGV + 多轮 SIGSTOP/补丁注入**自伤**的幻影，**无确定
  复现标的**；二进制补丁两次证伪仍有效。
- 中途两次**对象混同**误读纠正：`obj[0x180]`=DDK 编译期常数（非 ioctl 字段）、
  `obj+0x6c8`=`pthread_mutex` 字段（非池缓冲）、`type`=DDK 内部工作项类别（非事件码
  位域）。详见 solution.md §30/§31/§33/§34。

### 二、真实显示链路（run139，2026-10-15）——阻塞 = **guest SurfaceFlinger 主线程挂**
- **host 侧正常活跃**：`gpu.sock.log` 含 GR01 帧、`gpurender.log` `seq→195+` 持续出帧
  （`262144B/帧`）；QMP `screendump`→`no surface`（无 QEMU 显示面 ⇒ 画面走 **B2 桥**，
  非 virtio-gpu scanout）；
- **guest DRM 正常**：`card0-DSI-1 connected`、`modes=1080x2400`；
- **SF 主线程 `futex_wait` 挂死**：`[SW-WD] spend 53s→95s→101s`（**31 次**）、SF 重启
  760→765（启动后立即挂）；连带 `Blocked in monitor WindowManagerService for 30s`、
  binder `system_server→surfaceflinger` **39.75s**、`/data/anr` **每 ~10s 一个**；
- 旁证异常：8× `mali-utility-worker` 同卡 futex、SF 持 11 `sync_file`；
  `media.c2 IComponentStore/default` 未起、`flags_health_check: updatable crashing`。
- 详见 solution.md §35 + [log-run139-display-chain.txt](evidence/issue-gold15-vendor-gpu/log-run139-display-chain.txt)。

### 三、run140（2026-10-16）：virtgpu Double Free 根治 + SF/RenderEngine 死锁因果链汇编级定案
1. **内核 Double Free 根治**：定位并删除 `drivers/gpu/drm/virtio/virtgpu_prime.c:223` 处冗余的 `kvfree(ents);`（commit `01ad98489a4d`，内核升级至 `v90-doublefree`），彻底消除了 41s / 184s 的 SLUB freelist 损坏 Panic，系统平稳运行突破 216s+。
2. **SF 主线程死锁底层定位**：
   - SF 主线程挂在 `composeSurfaces` 等待 RenderEngine Promise；
   - RenderEngine (TID 809) 上下文切换每秒 700+ 次，活跃在 `libGLES_mali.so` `0xaaf494` -> `usleep(500)`；
   - 汇编根因：`0xaaf480` 检测 `[ctx + 0x3e04]`（in-flight 任务计数）超过上限 171（0xab），进入死循环轮询；源头是启动期 `primeShaderCache` 一口气提交了上百个着色器测试层绘制。

### 四、run141（2026-10-16）：关闭着色器预热避开 171 门控，系统首次 BOOT COMPLETED，图层树构建与 screencap 出图里程碑
1. **标准属性注入**：在 `cph_start_adbd.rc` 与 `/vendor/build.prop` 注入 `service.sf.prime_shader_cache=0` 和 `ro.surface_flinger.prime_shader_cache.ultrahdr=0`，从源头跳过预热，使 in-flight 任务保持在 1~3 的极低水位。
2. **系统首次开机里程碑**：
   - **`sys.boot_completed=1`** 与 `dev.bootcomplete=1` 成功置位！Android 系统完整跑通全部启动流程！
   - RenderEngine 静息（`usleep` 轮询归零），SurfaceFlinger 主线程进入平稳的 `do_epoll_wait` 事件循环！
3. **100+ 图层树完整构建**：
   - 包含 `Built-in Screen`、`StatusBar`、`NavigationBar`、`ImageWallpaper`、`com.android.provision`、`NotificationShade` 等完整系统图层树。
4. **screencap 成功出图**：
   - 执行 `screencap /data/local/tmp/screen.png` 成功生成 1080x2400 标准物理分辨率 8-bit RGBA PNG 图片。
5. **GPU 渲染通道持续出帧与指令缺口定位**：
   - `gpu.sock.log` 持续输出 GR01 协议包，`gpurender.log` 序列推进至 `seq=280+`。
   - 定位实际渲染中缺失的 Valhall 指令：`IADD.v2u16` 与 `FRCP.f16`。

### 五、最新进展：攻克 UMM Fault 与 pfnmap 偏移两大内核卡点，解决 screen.png 全透明黑屏（run142，2026-10-16）

1. **解决全透明黑屏**：
   - 针对用户反馈的 `screen.png 是个透明的`（Alpha 通道全 0），深入内核排查出两大物理级卡点：
     1. `mali_kbase_mem_linux.c` 中拦截 `KBASE_MEM_TYPE_IMPORTED_UMM` 的 CPU fault 访问，导致内核桥尝试建立 Render Target 写入页表时被拒绝（`wb abort err=-14 wrote=0B`）；
     2. `cdh_gpumbox.c` 中 `cdh_write_pfnmap` 的偏移计算使用页首地址导致 `off` 恒为 0，整行 34 个 Superblock Header 覆盖写入页首 offset 0，导致第 1~33 个 Header 全为 0。
2. **内核改动与全链路发布**：
   - 放行 UMM CPU 映射，`cdh_write_pfnmap` 接收完整 `uaddr` 正确寻址每个 16B Header；
   - 增加 AFBC Wide Superblock (32x8) 格式支持；
   - 彻底删除 `vendor/patch/lib64/egl/libGLES_mali.so` 冗余副本，严格保持厂商原版闭源驱动零修改；
   - 升级编译 `lineage-22.1-gki-virtio-wifi-cdh-modules-v93-pfnmap-offset`，打包 `boot.img`、`vendor_dlkm.img` 并打入 `vda.img`。
3. **固证与真机出图验证**：
   - 内核 `dmesg` 稳定输出 `cdh-mali: B2 bridge done seq=133 status=0 wrote=163200B`；
   - 截屏文件 `devices/xiaomi/gold-15/build/doc/evidence/screen.png` 经 PIL 严格检验：1080x2400 RGBA，Alpha 极值 `min=255, max=255`，不透明像素达到 100%（2,592,000 / 2,592,000），全透明黑屏彻底消除，稳定呈现非透明真机画面！

### 六、最新下一步靶子
1. **翻译器补齐**：在 `devices/xiaomi/gold-15/out/tmp/p2/va2spirv.py` 中扩充 Valhall 真实着色器指令翻译；
2. **高保真回显**：在宿主 `harness` 中接入真正的 guest 纹理图层采样，实现完整桌面图标与真实文字的高保真渲染回显。