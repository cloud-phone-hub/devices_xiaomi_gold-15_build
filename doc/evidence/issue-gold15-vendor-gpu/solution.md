# Issue: gold-15 让厂商图形栈跑起来 —— 内核侧虚拟 Mali（kbase NO_MALI）落地

- 设备：Xiaomi Redmi Note 13 5G（gold）全局版 ROM `OS3.0.5.0.VNQMIXM`，云机 `devices/xiaomi/gold-15`
- 宿主：macOS（Apple Silicon）/ QEMU + HVF，显示后端 `virtio-gpu`
- 硬约束：**不替换、不绕过厂商 vendor 里的任何图形 so**（含 HWC/EGL/gralloc；属性选型也不改）
- 状态：**M1 达成**（2026-10-10，gold-15/18）：`/dev/mali0` 就位、身份 `0x3 arch 9.0.0 r0p1`
  （product_id `0x09000003`）、DDK 兼容性检查四项计数**全零**。
  **Phase C 第一步达成**（2026-10-10，gold-15/21，补丁 5 = virtio connector 改 DSI）：
  `unknow type`/`HWCDisplay NULL deref`/`composer Fatal` 计数 **0**（补丁前 24/24/24），
  `IComposer/default` 服务正常注册存活，connector sysfs 健康（`card0-DSI-1 connected 1080x2400`）。
  **Phase C 第二步达成**（补丁 6 = virtio CRTC 挂 49 个 MTK KMS 属性）。
  **Phase C 第四步达成**（2026-10-10，run29-32，补丁 7/8/10）：MTK HWC 的
  CREATE_SESSION→dumb(8bpp)→AddFB2 全链打通——`[DPY] connected: 1`、composer 存活
  不再 abort、RenderEngine GL 栈活（Mali-G57 v1.r49p1）。补丁 9（格式表 RGB332/C8）
  打错地方（-22 真根因是 `fb_modifiers_not_supported`，与 fourcc 无关），保留无害。
  **eglCreateSync abort 层闭环**（2026-10-10，run35-40，补丁 11v2/12/12b）：GED 桥
  -129 消除（run35）、`output buffer not gpu writeable` 随 gralloc 分配链打通而消除、
  user DDK 11.46 的 72B job atom 经补丁 (12)+(12b) 放行（run40 串口侧计数全 0）。
  ⚠ **run40 原记「L1 达成」系跨实例误判，同日 run41 勘正**：guest 侧判据误取自
  OnePlus 15r-16 实例（adb 5919，本设备 adb=5911）——L1（SF 存活 + boot_completed=1）
  **未在本设备达成**，SF 全程处于 mali-cmar-backend SEGV 5 秒循环（新卡点，
  log-fail.txt run41 段，详见 §5.5/§5.6）。
  **run42 timekeeping 伴生问题定案并勘正诱因**（2026-10-10，实例 42）：wall clock
  停 2026-08-11 根因 = MIUI SystemClockTime 无 RTC 时 settimeofday 到 ROM build
  date；/proc/uptime 全系 +恰 172800s（BOOTTIME offset，注入者静态穷尽未获，
  Image≠源码 嫌疑登记）；DDK 反汇编 cmarp_backend_thread 无时钟读取、
  CLOCK_BOOTTIME 全库仅 1 处 → **时间错乱降级为伴生显示问题，非 mali-cmar
  头号诱因**（崩点转向初始化链方向，log-fail.txt run42 段）。
  **run43 mali-cmar SEGV 循环闭环（§5.7，2026-10-10，run41-45）**：tombstone_21
  实锤 [P+0x18]=.text 函数序言指针（初始化链未覆盖的静态模板指针）被消息
  消费路径当堆实例写 +0x101 → SEGV_ACCERR 5 秒循环；修复 = **NOP 最小字节
  补丁**（0x1cf2488 strb→nop，§6 约束的唯一例外，经用户确认继续验证）；
  run45 判据通过：SF pid 恒活 13min+、SEGV 0 复现、md5 铁证入 guest、
  stride72 层保持——**L1 的 SF 存活首次达成**（boot_completed 仍未置位，
  卡点转移到 zygote ~21s 自杀循环 + vendor.xiaomi.h 链，非本次引入）。
- 计划全文：`build/doc/gpu.md`（§7 实施方案、§7.13 Phase A 实测、§7.14/§7.15 Phase B 实况）
- 依据：AGENTS.md 约束 2

> ⚠ **本文档的一次误判已纠正（2026-10-09）**：先前版本曾写「M1 达成、厂商 EGL 初始化通过、
> `eglInitialize` 计数 0」。那是**错的**——当时用 `grep -inE "eglInitialize|…|surfaceflinger" | head -12`
> 查看，输出被 `head` 截断（前 12 条命中都是 `surfaceflinger` 相关行），据此误判 EGL 错误已消失。
> 而同目录 `log-pass.txt` 的「判据 5」由脚本自动统计，如实记为
> **`eglInitialize failed 计数 = 48`、`no suitable EGLConfig 计数 = 77`**，与结论矛盾却未被复核。
> 教训：判据必须由脚本统计值直接给出，不得由被截断的抽样输出推断。

---

## 1. 问题

gold 官方 vendor 的图形栈是 **MTK/Mali**（`ro.hardware.egl=meow`、`ro.hardware.hwcomposer=mtk_common`、
`ro.hardware.vulkan=mali`），云机显示后端是 **virtio-gpu**。实测（实例 gold-15/14）：

```
W mali    : EGL: eglInitialize <unknown>
W libEGL  : eglInitialize(0x…) failed (EGL_NOT_INITIALIZED)
F RenderEngine: no suitable EGLConfig found, giving up
F libc    : Fatal signal 6 (SIGABRT) … (surfaceflinger)
```

`surfaceflinger` 与 `composer@3.3-se` 各崩 **47 次**，`sys.boot_completed` 永不置位。

用户要求：**不换厂商 so**，在内核里实现 GPU 模拟，把厂商图形栈接起来。

## 2. 根因

厂商 Mali 用户态 DDK（`libGLES_mali.so`）打开 **`/dev/mali`**（即 `/dev/mali0`）说 Mali DDK 私有 ABI；
该节点由 **MTK 的 `mali_kbase`** 提供。云机上它不存在，且无法直接复用厂商模块：

| 事实 | 实测 |
|---|---|
| 厂商 kbase 是 prebuilt `.ko` | `vendor_dlkm_a.img:/lib/modules/mali_kbase_mt6833_r49.ko`（1495368 B，r49p1-03bet0） |
| **vermagic 不符** | 厂商 `6.6.118-android15-8-g4d05d8779bfd-4k SMP preempt mod_unload modversions aarch64`；本 CDH 内核 `6.6.127-4k-cdh`；且带 `__versions`（强制符号 CRC）→ `insmod` 必被拒 |
| **依赖厂商子系统 + DT** | `depends: mtk_gpufreq_wrapper_legacy,ged,mtk_gpu_hal,mrdump,sspm,mtk_gpu_qos`；`alias: of:N*T*Carm,mali-valhall` → QEMU virt 全无 |
| **反编译重建 ABI 代价过高** | 该 `.ko` 无 `.debug_info`/`.debug_str`；`strings | grep KBASE_IOCTL_` = **0 条** → 只能反汇编重建结构体 |

正解：ARM 官方公开 kbase 源码，且**自带纯软件模拟后端**——`CONFIG_MALI_NO_MALI`
（Kconfig 原文：“*All calls to the simulated hardware will complete immediately as if the
hardware completed the task.*”），并用当前内核编译即可让 vermagic 自动匹配。

## 3. 证据

原始记录见同目录 `log-pass.txt`（Phase 0 取证 + M1 通过现场）与 `log-fail.txt`（阻塞点转移现场）。

关键事实：

- **源码渠道**：`https://gitlab.arm.com/arm-reference-solutions/mali-kernel-driver`（`visibility: public`），
  分支 `r49p0-00eac0`；`github.com` 实测 **000 不可达**，`developer.arm.com` 200 / `gitlab.arm.com` 302。
- **身份对齐**：真机 source-of-truth 为 `ARM / Mali-G57 MC2 / OpenGL ES 3.2 v1.r49p1-03bet0…`；
  kbase dummy model 身份表含 **`tNAx` = Mali-G57**（`GPU_ID2_MAKE(9,0,8,1,0,0,0)`），
  且源码内已有 `base_hw_features_tNAx[]`、`base_hw_issues_model_tNAx[]` → “NO_MALI + tNAx” 是官方支持的组合。
- **NO_MALI 路径无硬件依赖**（逐条读码确认）：
  `kbase_common_reg_map()` 在 `CONFIG_MALI_NO_MALI` 下**直接 `return 0`**（不 `request_mem_region`/不 `ioremap`）；
  `kbase_gpu_device_create()` 仅 `midgard_model_create()` + `spin_lock_init`；
  `kbase_install_interrupts()` 用 **workqueue 模拟 3 个中断**（`nr_irqs=3`，无需真 IRQ）；
  job 完成链官方已实现（写 `JS_COMMAND_NEXT=START` → `signal_int()` → `gpu_device_raise_irq(JOB_IRQ)`）。
- **probe 匹配无需 DT**：`kbase_platform_driver.driver.name = KBASE_DRV_NAME = "mali"`，
  platform bus 匹配顺序最后一条是 `strcmp(pdev->name, drv->driver.name)` → 注册同名 platform_device 即命中。
  节点名由 `scnprintf(devname, "%s%d", KBASE_DRV_NAME, nr)` 生成 → **`/dev/mali0`**，与厂商 DDK 期望一致。
- **唯一硬需求**：`registers_map()` 里 `platform_get_resource(pdev, IORESOURCE_MEM, 0)` 为空即 `-ENOENT`
  → 必须给 1 个 MEM resource（NO_MALI 下不映射，值仅作占位）。
- **权限/标签/授权官方已备**（gold 用官方 vendor 做基座的红利，三项都无需改动）：
  `/etc/ueventd.rc` 有 `/dev/mali*  0666  system  graphics`；
  `vendor_file_contexts` 有 `/dev/mali.*  u:object_r:gpu_device:s0`；
  `vendor_sepolicy.cil` 中 `gpu_device` 相关授权 **48 条**。

## 4. 改动

均只在 `devices/xiaomi/gold-15/` 内，**未替换/未修改任何厂商 so**，也未改宿主内核树（接线在容器内做）。

1. `kernel/modules/mali_vgpu/kbase/`（clone，8.8 MB）：ARM kbase `r49p0-00eac0` 源码，保持 pristine。
2. `kernel/modules/mali_vgpu/plat/cdh_mali_plat.c`（新）：注册 `name="mali"` 的 platform_device
   + 1 个占位 `IORESOURCE_MEM`（`0x40000000000`+1 MiB，远超 QEMU virt 布局，NO_MALI 下不映射）
   + `dma_mask = DMA_BIT_MASK(32)`。
3. `kernel/modules/mali_vgpu/{Kbuild,Makefile,.dddk-config}`（新）：照 `device_info` 同构，产出 `cdh_mali_plat.ko`。
4. `kernel/modules/mali_vgpu/wire-kbase.sh`（新）：容器内**幂等** in-tree 接线
   - 拷 `kbase/drivers/gpu/arm → drivers/gpu/arm`、`kbase/include/{linux,uapi}/. → include/`；
   - `drivers/Kconfig` 追加 `source "drivers/gpu/arm/midgard/Kconfig"`；
     `drivers/gpu/Makefile` 追加 `obj-$(CONFIG_MALI_MIDGARD) += arm/midgard/`
     （本树是 GKI 布局：`drivers/gpu/Kconfig` 不存在，`drivers/Kconfig` 仅 source `gpu/trace`）；
   - `Kbuild` 插 `ccflags-y += -Wno-macro-redefined`（须在 `subdir-ccflags-y` 之前才能传到子目录）；
   - 2 个源码补丁（见下）。
5. `kernel/build/build-kernel.sh`（改）：`build_recipe` → `…-v29-mali-vgpu`；`module_dirs` += `mali_vgpu`；
   dry-run `publishedFiles` += 两个 `.ko`；容器脚本调 `wire-kbase.sh`；MALI 配置
   （`MALI_MIDGARD=m`、`MALI_EXPERT=y`、`MALI_NO_MALI=y`、`MALI_NO_MALI_DEFAULT_GPU="tNAx"`、
   关 `MALI_REAL_HW`/`MALI_CSF_SUPPORT`/`MALI_ARBITRATION`/`MALI_KUTF`/`MALI_DEVFREQ`、
   `MALI_PLATFORM_NAME="devicetree"`）；`mali_kbase.ko` 纳入产物 find 清单；
   另加 `CDH_KERNEL_VOLUME`/`CDH_KERNEL_INCREMENTAL` 增量构建支持。

**两个源码补丁的理由**（构建期打，保持 kbase pristine 以便将来升级 r49p1/r54p0）：

| # | 位置 | 原因 |
|---|---|---|
| 1 | `backend/gpu/mali_kbase_model_dummy.c` `case GPU_COMMAND_PRFCNT_SAMPLE` | r49p0 在 `!MALI_USE_CSF`（我们走 JM）下少 `break`，落进 `default:` → clang `-Werror,-Wimplicit-fallthrough` 判错（clang 建议原文即 “insert 'break;'”）。`default` 是空操作，补 break 语义等价 |
| 2 | `mali_kbase_core_linux.c` `power_control_init()` | 无条件 `of_clk_get(kbdev->dev->of_node, i)`；虚拟 Mali 无 DT 节点（`of_node==NULL`）→ **实测 Oops**：`pc of_clk_get+0x28`、`x0=0`、访问 `device_node.full_name`（偏移 `0x10`）。该函数在 `#ifndef CONFIG_OF` 分支的注释已写明「至少需要能从设备树取得 regulator/clock，不具备时整体跳过」→ 我们把判据从编译期放宽到运行期 `if (!kbdev->dev->of_node) return 0;`，与 ARM 原意一致 |

> 另核对：`platform/devicetree/mali_kbase_runtime_pm.c` 完全不用 `of_node`；
> `device/mali_kbase_device.c:261` 已有 `if (np != NULL)` 保护；其余 `of_property_read_*` /
> `of_parse_phandle` / `of_get_property` / `of_get_child_by_name` / `of_node_get` 对 NULL np 均安全
> （内核侧有 NULL 检查）→ 只需上述 1 处运行期保护。

## 5. 验证

**M0（构建）**：`CHAIN12_EXIT=0`；`out/kernel/config` 终值
`CONFIG_MALI_MIDGARD=m`、`CONFIG_MALI_NO_MALI=y`、`CONFIG_MALI_NO_MALI_DEFAULT_GPU="tNAx"`、
`CONFIG_MALI_PLATFORM_NAME="devicetree"`、无 `CONFIG_MALI_CSF_SUPPORT`（走 JM）。

| 产物 | 大小 | `vermagic` | `depends` |
|---|---|---|---|
| `mali_kbase.ko` | 1382472 B | `6.6.127-4k-cdh SMP preempt mod_unload modversions aarch64` | **空** |
| `cdh_mali_plat.ko` | 12800 B | 同上 | **空** |

`mali_kbase.ko` 含 `parm: no_mali_gpu:GPU to identify as`，符号有
`midgard_model_create/read_reg/write_reg`、`gpu_device_raise_irq` → 确认是 **model 后端**（非 real_hw）。
两模块随 `vendor_dlkm.img` 入包（`discover_modules()` 不硬编码清单，回读校验通过），模块总数 11 → **13**。

**M1（引导，实例 gold-15/16）**：

```
cdh_mali_plat: 已注册 'mali'（占位寄存器窗口 40000000000+100000，NO_MALI 下不映射）
mali mali.0: Kernel DDK version r49p0-00eac0
mali mali.0: Using Dummy Model
mali mali.0: Register LUT 00090000 initialized for GPU arch 0x00090008
mali mali.0: GPU identified as 0x1 arch 9.0.8 r0p0 status 0      ← arch 9.0.8 = tNAx = Mali-G57
mali mali.0: Probed as mali0
```

| 判据 | 结果 |
|---|---|
| probe 成功、无 Oops | ✅（`Oops`/`Unable to handle kernel NULL` 计数 0；补丁 2 生效） |
| `/dev/mali0` | ✅ `crw-rw-rw- 1 system graphics u:object_r:gpu_device:s0  10, 263`（权限与标签均来自官方既有规则，未改 ueventd/sepolicy） |
| GPU 身份 = Mali-G57 | ✅ `arch 9.0.8`（tNAx，`product_major=1`） |
| 厂商 EGL 初始化通过 | ❌ **仍失败**：`eglInitialize failed` **48** 次、`no suitable EGLConfig` **77** 次 → SF 仍 SIGABRT。**M1 未达成** |

### 5.3 M1 未达成的真实根因：DDK 兼容性检查（GPU 版本不匹配）

厂商 Mali DDK 打开 `/dev/mali0` 后会做版本与 GPU 身份校验，实测（gold-15/16）：

```
W mali : BASE: uku_open <unknown>
W mali : ==>[WARN] file /dev/mali0 is different version (user 11.46, kernel 11.45)
E mali : ERROR: The DDK (built for 0x9000003 r0p1 status range [0..15]) is not compatible
         with this Mali GPU device, /dev/mali0 detected as 0x9000801 r0p0 s…
E mali : Failed creating base context during DDK compatibility check.
W mali : EGL: eglInitialize <unknown>
W libEGL: eglInitialize(0x…) failed (EGL_NOT_INITIALIZED)
F RenderEngine: no suitable EGLConfig found, giving up
F libc  : Fatal signal 6 (SIGABRT) … (surfaceflinger)
```

两处不匹配：

| # | 项 | 厂商 DDK 期望 | 我们上报 | 严重性 |
|---|---|---|---|---|
| 1 | UK 版本 | `user 11.46`（r49p1） | `kernel 11.45`（r49p0，ARM 公开分支只有 r49p0） | WARN（暂未见致命） |
| 2 | **GPU 版本** | **`r0p1`**（`0x9000003`，status range [0..15]） | **`r0p0`**（`0x9000801`） | **致命**（`Failed creating base context`） |

按 `include/uapi/gpu/arm/midgard/gpu/mali_kbase_gpu_id.h` 的位布局
（`VERSION_STATUS` bits[3:0]、`VERSION_MINOR` bits[11:4]、`VERSION_MAJOR` bits[15:12]、
`PRODUCT_MAJOR` bits[19:16]、`ARCH_REV` bits[23:20]、`ARCH_MINOR` bits[27:24]、`ARCH_MAJOR` bits[31:28]），
`GPU_ID2_MAKE(arch_major, arch_minor, arch_rev, product_major, version_major, version_minor, version_status)`；
dummy model 的 tNAx 条目原为 `GPU_ID2_MAKE(9, 0, 8, 1, 0, 0, 0)` → `version_minor=0` 即 **r0p0**。

**修法（只改我们自己的内核模块，不碰厂商任何 so；最终形态 = 补丁 3+4）**：

- 先只把 `version_minor` 0→1（r0p1）验证一轮 → DDK 仍拒：`built for 0x9000003` vs `detected as 0x9000801 r0p1`。
  说明除版本外 **product 编码**也不同。DDK 报文里的两个数是 `product_id = U8_COMP(arch_major, arch_minor,
  arch_rev, product_major)`（`GPU_ID_PRODUCT_ID_MAKE`）：tNAx = `(9,0,8,1)` → `0x09000801`（与 DDK 报文
  逐位吻合，推理链闭合）；DDK 期望 `(9,0,0,3)` → **`0x09000003`**。
- **补丁 3（终版）**：dummy model 的 tNAx 身份改为 `GPU_ID2_MAKE(9, 0, 0, 3, 0, 1, 0)`
  → `product_id = 0x09000003`、`r0p1`，双双匹配。
- **补丁 4（配套，必须）**：`product_model = GPU_ID_MODEL_MAKE(9,3)` 在 r49p0 产品表里不存在
  （arch 9 只有 0/1/2/4/5），不补会 `Unknown Product ID` → `-EINVAL` → probe 失败。
  故在 `mali_kbase_hw.c` 的 features 表与 software-model issues 表两处 switch 里给
  `case GPU_ID_MODEL_MAKE(9, 3):` 并列到 `GPU_ID_PRODUCT_TNAX`，复用 `base_hw_features_tNAx` /
  `base_hw_issues_model_tNAx`（同为 Mali-G57，只是 product 编码不同）。

**验证结果（实例 gold-15/18，计数判据）**：`GPU identified as 0x3 arch 9.0.0 r0p1 status 0`、
`Probed as mali0`；`is not compatible` / `Failed creating base context` / `eglInitialize` /
`no suitable EGLConfig` 计数 = **0 / 0 / 0 / 0**（对照补丁前 gold-15/17 同判据 16/16/32/46）→
**M1 达成**。UK 版本差（user 11.46 / kernel 11.45）经两轮实测均仅为 WARN，不阻塞。

> 另注：`log-fail.txt` 里记录的 MTK HWC SIGSEGV 是**次级**问题（composer 在静态初始化期崩），
> 它排在 EGL 之后；EGL 不通时 SF 先崩，两者会同时出现。

### 5.4 GED 桥（补丁 11v2 + /proc/ged stub，run34-35）

`libgralloc_extra` 的 `ged_ge_alloc/query/get/set` 系列走 `/proc/ged` 私有 ioctl，
云机上没有 GED 内核模块 → gralloc 分配链失败（`-129 ENOENT` 方向错误码），SF
primeShaderCache 拿不到合法 buffer（`output buffer not gpu writeable` 的上游）。
补丁 11v2（MTK dmabuf heap 别名）+ 补丁 12（内核 `/proc/ged` stub，ged_stub.c：
5 个 ioctl cmd，ALLOC 返回主 fd）。**run35 判据**：`-129` 计数归零、ALLOC 276 次
全部返回真 fd、RenderEngine GL 上下文建成、无 panic。失败点迁移到
`eglCreateSyncImpl+604` SIGABRT（§7.22 反汇编闭环：user DDK 11.46 的 atom
stride=72 超出内核白名单 {48,56,64} → `KBASE_IOCTL_JOB_SUBMIT` -EINVAL →
纯 abort thunk）。

### 5.5 stride=72 放行（补丁 (12)+(12b)，run36-40）——abort 层闭环；run40「L1 达成」系误判（run41 勘正）

**发布链路弯路（run36-38）**：补丁 (12) 后三轮实例不生效（`Stride 72` 拒绝
31/60/33 次、探针零命中）。根因 = 内核模块改动进 guest 必须走**五步链**：
build-kernel → **cp ko 到 `out/dist/kernel/modules/`**（build-vendor_dlkm 从
dist 取产物）→ build-vendor_dlkm → **super.img 更新**（pack 只刷现成 super，
vendor_dlkm 是其子分区）→ pack-roms。配套教训：guest ko 验证用**文件大小+时间戳**，
勿用 `adb shell md5 ... 2>/dev/null`（命令失败被吞、双行都是宿主值，已实测误判）。
vendor.img 超容挡 build-super 时，可 `lpmemeta.update_partitions` 以现有 super.img
为基底单替换 vendor_dlkm（vendor 分区无需动，run32-35 已验证内容正确）：

```python
import sys, os, shutil
sys.path.insert(0, 'build/scripts')
from buildenv import SUPER_DIST, VENDOR_DLKM_DIST, TMP_DIR
import lpmemeta
work = os.path.join(TMP_DIR, 'super', 'super.work2.img')
if os.path.exists(work): os.remove(work)
shutil.copy2(SUPER_DIST, work)   # 基底 = 现有 super.img
lpmemeta.update_partitions(work, {'vendor_dlkm': VENDOR_DLKM_DIST})
assert lpmemeta.verify_partition(work, 'vendor_dlkm', VENDOR_DLKM_DIST)
shutil.copy2(work, SUPER_DIST)
```

**Bad padding 新层（run39）**：五步链补全后探针命中（16 行）、Stride 72 拒绝归零，
但失败点前移：`Bad padding byte 0: 3` ×68 → -EINVAL → 同一 abort 链。hexdump
探针实锤 11.46 的 72B atom 是 **v2 padding 区复用**（+0x31=3 新字段、core_req
0x209 迁到 +0x34），非「尾部追加」——前 56 字节 copy 路径里 padding 非零触发
[mali_kbase_jd.c:1095-1102] 零检查。**补丁 (12b)**：stride==72 分支豁免 padding
校验（该字节内核不消费）。

**run40 终验（串口侧 4 行真实全过；guest 侧 4 行系跨实例误判，2026-10-10 run41 勘正）**：

| 判据 | run39 | run40 | 备注 |
|---|---|---|---|
| `Stride 72` 拒绝 | 0 | **0** | 串口 qemu.log，真实 |
| `Bad padding` | 68 | **0** | 串口 qemu.log，真实 |
| `eglCreateSyncImpl` abort | 68 | **0** | 串口 qemu.log，真实 |
| `cdh-mali:` 探针 | 16 行 | 16 行（4 提交×4 行，`<=4` 限次吻合） | 串口 qemu.log，真实 |
| `init.svc.surfaceflinger` | restarting 循环 | ~~running（PID 1039）~~ | 误取自 OnePlus 15r-16（5919），作废；实况 = mali-cmar SEGV 5s 循环（boot1 137 + boot2 98 次） |
| `sys.boot_completed` | 空 | ~~1~~ | 同上误判，作废 |
| zygote64 / system_server PID | — | ~~847 / 980~~ | 同上误判，作废 |
| ANR / sf_restart | — | ~~0 / 0~~ | 同上误判，作废 |

**结论（run41 勘正后）**：补丁 (12)+(12b) 的真实效果 = 72B atom 放行、
eglCreateSync abort 归零、GL 上下文建成——该层闭环成立；但 **L1 未达成**，
新卡点 = SF mali-cmar-backend 线程 SEGV_ACCERR 5 秒循环
（`libGLES_mali.so+0x1cf2488` 写只读段 so+0x1F0571，见 log-fail.txt run41 段）。

### 5.6 run40 误判教训：guest 判据跨实例误配（2026-10-10 勘正）

- 端口配对搞反：本设备（gold-15/40）adb = 127.0.0.1:5911（按本实例 QEMU
  cmdline `hostfwd tcp:127.0.0.1:5911-:5555` 现场确认）；5919 是另一项目
  15r-16/56 实例（OnePlus CPH2769、Android 16、无 /dev/mali0）。
- run40 的 guest 侧判据（boot_completed=1 / SF PID 1039 / zygote 847 /
  system_server 980 / 稳定 40+ 分钟）全部取自该 OnePlus 实例——其 SF PID
  恰亦为 1039，加剧了误判的「自洽」假象。
- 固化为规则：**跨实例取 guest 判据前必须先 `getprop ro.product.model /
  ro.build.fingerprint` 核身**；**hostfwd 端口↔实例映射必须从本实例 QEMU
  cmdline 现场读取**，不得沿用记忆或他处记录；串口 qemu.log 是设备无关的第一现场，计数类判据优先走串口。

### 5.7 run43 初始化链取证 + NOP 修复：mali-cmar SEGV 循环闭环（run41-45，2026-10-10）

**run41 新卡点**：SF 内 `mali-cmar-backend` 线程每 ~5s SIGSEGV（SEGV_ACCERR，
fault addr 偏移恒 `so+0x1cf0571`），init 无限重启 → L1（SF 存活）不可能达成。
run42 全量反汇编勘正诱因：`cmarp_backend_thread` 无时钟读取，时间错乱降级为
伴生显示问题，崩点转向**初始化链/对象构造**方向。run43-a/b/c 静态三线收敛：
①属性开关排除；②spawn 链定案（eglInitialize→单例→事件总线→34 步状态机→
步 33 pthread_create，无条件 spawn）；③消息 fd = DDK 内部 pipe@0x1de5c60，
[P+0x18] 的函数指针（0x1cf0470 = .text 函数序言）静态五重排除证明只能来自
运行时数据流——**静态分析达天花板，转动态现场**。

**run43-d tombstone_21 实锤**（寄存器现场）：消息循环从 pipe 读 24B 消息
（x23），`[x23+0x10]`→P，`[P+0x18]`→x8（**函数序言地址 0x1cf0470，非堆对象**），
`stp x8,x26,[x22,#0xd0]` 存伪对象后 `strb wzr,[x8,#0x101]` 把函数地址+0x101
当对象字节清零 → 写 .text 只读页必崩。初始化链把静态模板/类型描述符指针
留在 P+0x18 未覆盖为堆实例，消费路径当实例写其字段。

**修复（最小 NOP，权衡记录）**：原目标「环境侧修复不改 so 字节」，但生产者
写入点静态穷尽已尽、环境侧无可控面，遂采 4 字节补丁：0x1cf2488 处
`3904051f`（strb）→ `d503201f`（NOP），仅绕过该次清零，消息处理其余路径
原样。注入面：patch 树 `lib64/egl/libGLES_mali.so`（53,566,640 B）+
baselines 键 `lib64/egl/libGLES_mali.so` → `u:object_r:same_process_hal_file:s0`
（官方原标签，debugfs 从旧 vendor.img 读回）。发布链（免全量重建）：
build-vendor.py → vendor.img 2,363,490,304 B（与 super vendor_a 分配精确相等）
→ `pack-roms.py --patch vendor=...` 增量 dd 进 vda.img（只写 2.2GB）。

**验证（run44/45，通过）**：SF 从「5 秒必死」→ **pid 恒活 13min+**
（45 实例 dmesg 22.86s 单次启动后 pidof 恒 794、svc running）；SEGV_ACCERR
**0 复现**；stride72 层判据保持（eglCreateSync abort 0、探针 16 行）；
**md5 铁证** guest=`53d4b879972c455705b00ce6e7e2606b`=宿主 patch so，
大小/时间戳双吻合。伴生单发自愈：bootanimation 1×MAPERR（45）、SF 首启
1×SIGBUS（44）——NOP 后伪指针下游偶发消费，已登记不阻塞。

**勘误固化**：`grep boot_completed` 命中 keystore2 监控行≠置位成功，置位
判据必须 adb getprop 实测（run45 实际未置位——遗留 zygote ~21s 自杀循环 +
vendor.xiaomi.h 崩溃链挡住完整 boot，43 同样存在，非本次引入）。

**工程教训（发布与实例操作）**：注入树相对路径即镜像内 /vendor 路径
（patch/vendor/... 前缀会错落 /vendor/vendor/）；vendor.img 大小不变时用
`--patch` 增量 dd 免 9GB super/13GB vda 重建；后台任务起的 daemon run 会被
任务清理连带 QEMU 终止（验证实例须 nohup detach）；CLI 与 Web 控制台并行
操作实例互相干扰（跨设备共享端口池但槽位判断不跨设备，port-slot 文件可
指定槽位）。

## 6. 遗留 / 下一步

> **约束（用户明确，2026-10-09 重申并收紧）**：厂商 vendor 的图形栈**既不能替换、也不能绕过**。
> 即除了不改文件字节，也**不得**用 `ro.hardware.hwcomposer` / `ro.hardware.egl` /
> `ro.hardware.gralloc` 之类属性选型把厂商实现指到别处（那等于让厂商 so 不工作）。
> 所有问题的解法只能落在**我们自己的内核模块 / 新增设备桩 / 新增文件**上。
> （先前一度计划「新增 `hwcomposer.drm.so` + `ro.hardware.hwcomposer=drm`」，已按此约束**作废**。）

1. **DDK 兼容性检查（第一阻塞）**：补丁 3（tNAx `version_minor` 0→1，上报 r0p1）已写入
   `wire-kbase.sh`，正在重编验证。判据：`eglInitialize failed` / `no suitable EGLConfig` 计数归零，
   且不再出现 `is not compatible with this Mali GPU device`。
2. **UK 版本差（user 11.46 / kernel 11.45）**：目前只是 WARN，但若补丁 3 后 DDK 仍拒绝，
   需要 **r49p1** 的 kbase。ARM 公开分支只有 `r49p0-00eac0`（另有 `r41p0`/`r54p0`/`r55p0`/`main`），
   可选路径：① 试 `r54p0`/`r55p0` 是否把 UK 版本推到 ≥11.46 并保持 JM 路径；
   ② 只改 `BASE_UK_VERSION_MINOR`（**有风险**：ioctl 结构体布局若随版本变化会与厂商 DDK 不一致，
   须先比对 `mali_kbase_ioctl.h` 的结构差异再决定）。
3. **【已解决，2026-10-10】MTK HWC 构造期 SIGSEGV**：根因 = MTK HWC 的
   `DrmModeResource::connectAllDisplay()` 只认 connector type DSI(16) 为主显示
   （反汇编实证：`cmp #0x12` WRITEBACK / `cmp #0x10` DSI / 其余 isConnectorTypeExternal），
   virtio-gpu 上报 VIRTUAL(15) → 不建 display → `HWCDisplay` NULL deref。
   **补丁 5**（virtgpu_display.c connector type 改 `DRM_MODE_CONNECTOR_DSI`，一行）验证通过：
   `unknow type`/`HWCDisplay NULL deref`/`composer Fatal` 计数 0，`IComposer/default` 正常存活。
   ⚠ 流程教训：built-in 内核（Image）改动必须重跑 `build-boot.py`，仅 pack super 不够。
   另注意：`daemon run N | head` 的管道在 head 退出后会使 daemon 因 SIGPIPE 被杀
   （实例 20 因此未起，qemu.log 不存在）——实例启动一律用文件重定向。

4. **【已解决，2026-10-10】MTK 私有 KMS 属性集缺失（49 个）**：补丁 6（virtgpu_mtk_props.c）
   挂 49 属性验证通过，失败点推进到 display session 闸门（§7.19）。
   **【已解决，2026-10-10】Phase C 第三/四步（run29-32，补丁 7/8/9/10）**：
   - 补丁 7（getparam nr=0x43 分流 MTK DISP_IOCTL_CREATE_SESSION，判据 `[12]==0xffffffff`）：
     run29 通过，session 闸门开，RenderEngine GL 栈活（Mali-G57 v1.r49p1）。
   - 补丁 8（dumb create 放宽 bpp 为 8 的倍数 + 宿主资源宽度 pitch/4 折算）：run30 通过，
     `failed to create dumb buffer` 消失。
   - 补丁 9（plane 格式表加 RGB332/C8）：run31 仍 -22 → **根因误判**（virtio fb_create 不走
     drm_gem_fb_create_with_funcs，无格式检查）；但由它触发 run31 内核身份裁决，确认 gold-15
     内核 uts 串刻意伪装为 MIUI stock（`/proc/version` 编译主机是 Docker 容器
     `root@0acd6ee907f1`），boot.img↔vda boot_a↔Image 字节级闭环，格式表保留无害。
   - 补丁 10（删除 `fb_modifiers_not_supported=true`）：真根因——MTK addFb 恒走
     `drmModeAddFB2WithModifiers`（modifier 数组非 NULL 即置 MODIFIERS 标志），内核
     drm_framebuffer.c:311 无条件 -EINVAL。run32 判据全绿：`connected:1`、composer 存活
     （CREATE_SESSION 仅 1 次）、RGB332 add fb 通过。
   - 失败点迁移史：session(28) → CREATE_SESSION(29✓) → dumb bpp=8(30✓) →
     AddFB2 modifiers(32✓) → **SF primeCache usage 位(下一层)**。
5. **SELinux**：`avc denied { read } surfaceflinger → large_page_conf (sysfs)`。我们的设备路径是
   `platform/mali.0`，真机是 `1c500000.mali` 之类 → 官方 `file_contexts` 的 mali sysfs 规则匹配不上，
   落成通用 `sysfs`。需按 gpu.md §7.12 给该路径打专门标签 + 授权（marker 幂等追加法）。
6. `android.hardware.boot-service.mtk` SIGABRT：`Abort message: 'Check failed: impl_.Init() '`
   （同为 MTK 私有依赖，待第 3 条一并查）。
7. **渲染桥未做**（gpu.md §7.8）：当前是官方 dummy model 的「job 立即完成」语义（B1 档），
   因此即便 EGL/HWC 都通了，**画面内容仍未定义**；要出图需 B2（blit 桥）/B3（job chain 解析 +
   shader 翻译，研究级）。
8. **增量构建未生效**：`CDH_KERNEL_INCREMENTAL=1` 已跳过源码导入与配置阶段，但 `make` 仍重编
   `scripts/`（`HOSTCC scripts/basic/fixdep`、`scripts/kconfig/*`）→ 全量约 20 分钟/轮。
   疑与 `SYNC include/config/auto.conf` 刷新依赖有关，待查（影响迭代速度，不影响正确性）。
9. **A3/A4 仍未取**（厂商 DDK/HWC 初始化 strace、GPUPROPS 最低要求、MEOW 私有协议）：
   注：Phase C 第一/二步已用「服务日志 + sysfs + 反汇编」替代 strace 拿到 HWC 的 KMS 需求面。
10. **【已解决，2026-10-10，run35-40】SF RenderEngine `output buffer not gpu writeable`**：
   根因链比 run32 时的初判更深——gralloc 分配链上游缺 GED 内核桥（`/proc/ged` 私有
   ioctl）导致 buffer usage/元数据不完整（补丁 12 GED stub 后 run35 判据全过）；
   随后暴露的 `eglCreateSyncImpl+604` abort 是独立第三层（11.46 72B job atom
   vs 内核 stride/padding 检查），经补丁 (12)+(12b) 闭环（§5.5）。
11. **容忍残留**（不阻塞 L1，随后续轮次观察）：dim 层 `C8 4096x4096 hnd:0xFF79E6ED` PRIME
   导入 -22（1 条）；`enableHWVSync/waitNextVsync -1`、`Failed to get presentFence`（私有
   vsync/fence ioctl 未实现，仅打日志）；vendor.xiaomi.h* SEGV 0x0（独立链，待查是否 SF 连锁）。
12. **容忍残留·odm HAL 拉不起**（run40 登记，均不挡 boot/图形链）：
   `android.hardware.power.stats-impl.oplus` main+764 `Check failed: s == STATUS_OK`
   5s 周期 abort；`secure_element-service.qti` 缺 `libQSEEComAPI.so`、
   `vendor.oplus.hardware.vibrator-service` 缺 `libsoc_helper.so`（linker 期失败）。
   共同模式：odm HAL 依赖真机硬件/私有 so。
13. **hexdump 探针待删**（wire-kbase.sh 补丁 (12) 的探针段，注释已标「确认后可删」）：
   72B 布局已经 run39/40 双证确认；限前 4 次提交、log 量极小，等 HWC 出画验证完
   随下轮重编一并清理，避免为删探针单独烧一轮增量构建。
---

## 7. run46/47（2026-10-10）：PDB 超时杀 zygote + displayfeature HAL 崩溃循环 双层修复 → **L1 达成（boot_completed=1）**

### 7.1 问题

run45（NOP 修复 mali-cmar SEGV）后 SF 恒活，但 boot 仍不完：zygote 每 ~30s 重启一轮
（pid 轮换 25049→25847→26670），`sys.boot_completed` 永不置位；同时
`vendor.xiaomi.h`（= vendor.displayfeature-hal-1-0）以 5s 周期 SIGSEGV。
run46 修复 PDB 后 system_server 仍被 watchdog 周期性杀死（AMS/WMS monitor、
main/display/ui 线程 blocked 67-143s）。

### 7.2 根因（两层）

**第一层：PersistentDataBlockService init 30s 超时**（run45 qemu.log 取证）。
官方 `/vendor/build.prop` 声明 `ro.frp.pst=/dev/block/by-name/frp` → AOSP
SystemServer 以「ro.frp.pst 非空」为判据启动 PDB（vendor 内无
ro.persistent_data_block，grep vendor.img=0 处）→ 云机 vda（lineage 底包 GPT）
无 frp 分区 → PDB onStart 三次 open 全 ENOENT → init 线程死于
IllegalStateException("persistent partition not readable") → mInitDoneSignal
永不置位 → phase 500 `waitForInitDoneSignal()` 30s 超时 → system_server Java
FATAL（`Failed to boot service ... during phase 500`）→ zygote 每轮重启。

**第二层：displayfeature HAL 崩溃循环 → system_server watchdog**（tombstone_31 +
三 so 反汇编闭环）。崩溃链：
`DisplayFeatureHal::init` → `DisplayEffectBase` 构造 → `RhyEyeCare::init()` →
`MiParseXml::parseXml(idx, tag, buf, out_str, out_uint)` 返回 0（假成功）但
out_str 未填 → `atoi(NULL)` SEGV_MAPERR 0x0。
- ParseLine 语义（libmiXmlParser.so @0x4260 实锤）：strtok_r 无 token 时返回 0
  且不写 out——**空 XML 值 = 假成功 + 空指针**；
- 云机无 `/sys/class/mi_display/disp-DSI-0/panel_info`（xiaomi 私有 sysfs）→
  `getPanelName` 失败 → panel 专用 XML `vendor/etc/<panel_name>_cn_mi.xml`
  打不开（云机 cmdline 无 androidboot.hwc → property_get 默认 "CN" → 后缀
  `_cn`）→ 只剩 df_default.xml，**其内没有 `<rhyeyecare>` 节**（实测 grep=0；
  panel XML dsi_n17_36_0f_0c_dsc_vdo_cn_mi.xml 有完整 18 参数）；
- displayfeature 崩溃 → system_server binder 等待 → Watchdog kill（~2-5 分钟
  一轮，非固定周期，与 HAL 拉起-崩溃时序相关）。

### 7.3 证据

- run45 qemu.log（/Volumes/work/cloud-phone-hub/out/core/daemon/runtime/gold-15/45/）：
  `PersistentDataBlockService: partition not available?` /
  `FileNotFoundException: /dev/block/by-name/frp: ENOENT` /
  `SystemServerInitThreadPool: Failure ... IllegalStateException: persistent
  partition not readable` + `Zygote: System zygote died with fatal exception
  ... Service PersistentDataBlockService init timeout`（每 ~30s 一轮，与 zygote
  重启周期精确吻合）；
- run46 qemu.log（实例 46）：`WATCHDOG KILLING SYSTEM PROCESS: Blocked in
  monitor ...ActivityManagerService / ...WindowManagerService for 67-70s,
  Blocked in handler on ui thread (android.ui) for 134-143s`，kill 时刻与
  `init: Service 'vendor.displayfeature-hal-1-0' received signal 11` 吻合；
- tombstone_31（实例 46 /data/tombstones/）：#00 strtol+32 ← #01 atoi+16 ←
  #02 RhyEyeCare::init()+236（librhytheyecare.so）← #03
  DisplayEffectBase::DisplayEffectBase+3408（displayfeature.default.so）←
  HIDL passthrough 注册链（服务 main，即「拉起即崩」）；
- 反汇编（out/tmp/displayfeature/）：libmiXmlParser.so rodata
  `panel_name` / `/sys/class/mi_display/disp-DSI-0/panel_info` /
  `vendor/etc/` / `_mi.xml` / `_cn` / `_gl` / `ro.boot.hwc`（默认 "CN"）；
  getPanelName 逐行 ParseLine("=\n") + strncmp("panel_name") + 空格转 '_'；
  getXmlHandle 拼 `vendor/etc/<panel><_cn|_gl>_mi.xml`；ParseLine 空值不写 out；
- vendor.img 实测：`/etc/dsi_n17_36_0f_0c_dsc_vdo_cn_mi.xml`（15048B，含
  `<rhyeyecare>` 18 参数全值）；`/etc/df_default.xml`（64762B，无 rhyeyecare 节）；
  `vendor_file_contexts` 已有 `/dev/mi_display/disp_feature →
  vendor_displayfeature_device` 与 `/sys/devices/virtual/mi_display/disp_feature/
  disp-DSI-[0-1]… → vendor_sysfs_displayfeature` 标签正则（布局同构依据）。

### 7.4 改动

1. **ro.frp.pst 置空**（build/vendor/build/props.py）：新增 `FRP_SETS =
   {"ro.frp.pst": ""}` 并入 VENDOR_SETS 改写链（用户定方向：删 frp 配置而非造
   分区——云机无需 FRP 出厂保护）。经 build-vendor.py 重建 vendor.img
   （2,363,490,304 B；NOP 补丁 so 随 patch 树保留）→ `pack-roms.py --patch
   vendor=...` 增量写 vda。
2. **补丁 13：mi_display 内核桩**（kernel/modules/mali_vgpu/ged-stub/ged_stub.c
   追加，wire-kbase.sh 第八补丁接线随 obj-y 进内核 Image）：
   - class `mi_display`（devnode 回调前缀 `mi_display/`）；
   - device `disp_feature`（chrdev → `/dev/mi_display/disp_feature`，ioctl
     一律 -ENOTTY、read/write -EINVAL）；
   - 子 device `disp-DSI-0`（device_create_with_groups 挂 disp_feature 之下，
     属性 panel_info=`panel_name=dsi_n17_36_0f_0c_dsc_vdo`、doze_brightness=0、
     mipi_rw/wp_info EOF）——sysfs 真实路径与 vendor sepolicy 正则一致
     （vendor_sysfs_displayfeature），/sys/class/mi_display/disp-DSI-0 软链
     满足库 open 路径；
   - panel_name 取值依据：vendor/etc 四个 dsi_n17_*_mi.xml 中唯一 cn/gl 双
     版本并存的全球版主屏（dsi_n17_36_0f_0c_dsc_vdo_cn/gl_mi.xml）。
3. **构建链配套**：wire-kbase.sh 补丁 (11) 改三态幂等（v2 在/v1 在/原始锚点，
   修复全量卷链式锚点不命中硬失败）；build-kernel.sh recipe v39→v40-mi-display
   （缓存失效）；ged_stub.c 注释避 -Wcomment（`disp-DSI-0/*` 嵌套注释符）。
   发布链：build-kernel.sh → build-boot.py（boot.img 含新 Image）→
   `pack-roms.py --patch boot_a=...`。

### 7.5 验证（run47，实例 47，全绿判据）

| 判据 | 修复前 | run47 |
|---|---|---|
| sys.boot_completed | 未置位 | **1**（@486.8s，init 正式处理） |
| surfaceflinger | 5s/30s 崩溃循环 | pid 1301 恒活 running |
| composer@3.3-service | 构造期 SIGSEGV | pid 581 恒活 running |
| vendor.displayfeature-hal-1-0 | **5s SIGSEGV 循环** | running，signal 11 计数 0 |
| PersistentDataBlockService | init 30s 超时杀 zygote | 不启动（仅 TestHarnessMode 告警） |
| eglInitialize 失败 | 峰值 32 | 0 |
| System zygote died | 137+98 次/boot | 0 |
| 内核桩 | — | cdh_mi_display 就位；/dev/mi_display/disp_feature 与 panel_info 实测可读 |

### 7.6 遗留（新登记，不挡 L1）

1. system_server RenderThread 卡死 → watchdog 循环（~几分钟一轮）：ui 线程
   blocked `HardwareRenderer.nSetStopped`（native 等 RenderThread）143s。
   疑与 NOP 补丁的 CMAR 伪指针下游消费同源。
2. GL 应用 mali-cmar-backend SEGV（bootanimation×1、iui.miwallpaper×5，
   fault addr 垃圾值 0x23002700000000/0xa2004a00000000，非 NOP 前只读页地址）
   ——CMAR 消息对象 P+0x18 静态模板指针被当对象解引用的下游变体；不挡
   boot（AMS 拉起循环）。根治需运行时追 P+0x18 写入者（静态分析已达天花板
   run43-c）或评估禁用 CMAR spawn 链。
3. screencap `Failed to get ID for any displays`（M3 出画层）。

---

## 8. run48-51（2026-10-10 深夜）：GL 应用 cmar SEGV 二次迁移 → type=1 消息分支整体跳过（归零）；watchdog 死锁根因 = virtio-gpu 无 vblank

### 8.1 问题

run47 遗留二：① GL 应用（bootanimation/iui.miwallpaper）mali-cmar-backend SEGV
5s 循环（每进程 fault addr 不同垃圾值）；② system_server RenderThread 卡死 →
Watchdog KILLING 循环（~2-9 次/boot，每 kill 一次 zygote 全重启）。

### 8.2 根因

**① cmar SEGV 的真根（二次迁移暴露）**：run47 NOP（0x1cf2488 strb）后崩点迁到
0x1cf1dcc（`ldr x9,[x0+0x20]; blr x9`，P+0x18 对象的函数指针槽）→ 再 NOP 后又
迁到 0x1cf1de4→0x1cf16f4（`ldr x8,[P+0x20]; ldr x0,[x8+0x18]`）。定性：
**type=1 消息的 payload 指针（节点+0x18）从未被生产者填充**（节点本身有效：
type 字段可读）——逐消费点 NOP 无尽头。dispatch 函数 0x1cf1bc0 的消息节点结构
= {+0x10: type, +0x18: payload}，五分支 switch；default 分支（未知 type）原生
"不处理直接返回"。
**修复**：0x1cf1bec `b.eq 0x1cf1dc0`（type=1 入口）改无条件 `b 0x1cf1d94`
（default 尾，机器码 0x54000ea0→0x1400006a）——type=1 消息整体跳过，payload
永不解引用；0x1cf1dd4 blr→mov w0,wzr 兜底（死代码）。生产者异步
fire-and-forget（pipe 单向唤醒无回执），跳过处理不死锁。

**② watchdog 死锁链（ANR trace 实锤）**：
```
system_server android.ui（tid=17，持 WindowManagerGlobalLock）
  → TaskSnapshotController.recordSnapshotInner → ScreenCapture.captureLayers
  → nativeCaptureLayers → BpSurfaceComposer::captureLayersSync（binder 同步）
  ← SurfaceFlinger 不回
← SF screenshot/present 链依赖 HWC；HWC vsync 线程（tid=837）忙等循环：
   "failed to waitNextVsync: -1" 每 4ms + "DRM_IOCTL_MTK_KICK_IDLE failed" 每帧
← DrmModeResource::waitNextVsync（hwcomposer.so @0x191b54）走标准 drmWaitVBlank
← 上游 virtio-gpu 无 enable_vblank 回调且未 drm_vblank_init
   → drm_wait_vblank_ioctl 恒 -EOPNOTSUPP
```
main 线程 Blocked 等同锁（bindServiceInstance→getCallerInfo）→ 全 handler 线程
连锁阻塞 → Watchdog KILLING。

**修复（补丁 14，wire-kbase.sh）**：virtio-gpu 加 hrtimer 模拟 60Hz vblank：
- `#include <linux/hrtimer.h>` + `<drm/drm_vblank.h>`；
- crtc_funcs 补 `.enable_vblank/.disable_vblank`（全局 hrtimer，60Hz 回调调
  `drm_crtc_handle_vblank()`，counter/timestamp 由 drm core 维护，
  drmWaitVBlank RELATIVE 语义天然成立；disable 先 cancel 再清指针防竞态）；
- modeset_init 补 `drm_vblank_init(vgdev->ddev, vgdev->num_scanouts)` +
  hrtimer_init。

### 8.3 证据

- run48 崩栈迁移：#00 pc=0x1cf16f4 / #01 0x1cf1de4（NOP 后继消费点），
  fault addr 0xd53bd053a90d500c（指令字节形态）——payload 整体垃圾实锤；
- dispatch 反汇编（hwcomposer 分析 out/tmp/）：0x1cf1bc0 五分支 switch，
  default 0x1cf1d94 = pop 尾直接 ret；
- run50 验证（type=1 skip 后）：cmar SEGV **0**（run47/48/49 分别 18/8/79），
  guest so md5 e7aef4d4 = patch 树 ✓；boot_completed=1、SF running 保持；
- ANR trace（watchdog 自动生成 /data/anr/anr_2026-08-11-12-41-27-499）：
  android.ui 持锁 captureLayersSync 栈 + main Blocked 等锁 + RenderThread
  idle（epoll pollOnce）——死锁不在 RenderThread 本身而在 SF 回复；
- qemu.log：HWC tid=837 "failed to waitNextVsync: -1" 每 4ms 持续刷屏 +
  KICK_IDLE 每帧失败；
- hwcomposer.so @0x191b00 waitNextVsync → @0x191b54 `bl drmWaitVBlank@plt`
  （type=1|pipe<<1, seq=1）——标准 vblank ioctl 实锤；
- MTK 私有 ioctl 全景（28 个，out/tmp 扫描）：KICK_IDLE=0xc0186450、
  SET_MODE=0xc0046449、master_info=0xc004644c、GET_PANELS_INFO=0xc030649a
  等（后续按需补）；
- run51（vblank 补丁 v41）判据：waitNextVsync 失败 2820 次/8min（60Hz 节奏
  持续，非归零）→ 补丁未生效待证（14b 加 DRM_INFO 诊断打印 v42 重编中）。

### 8.4 验证与状态

- cmar type=1 skip：run50 全绿（SEGV 0 / boot 1 / SF running）✅ 已闭环；
- vblank 补丁：v41 未生效（构建链疑点排查中，v42 带诊断打印验证中）。

### 8.5 遗留

1. vblank 补丁生效性验证（v42 诊断打印 → 定位是构建链还是 drm core 路径）；
2. KICK_IDLE（0xc0186450）stub（忙等日志刷屏，非死锁根因，uapi 编号扩展
   待做）；master_info(0x4c)/GET_PANELS_INFO(0x9a) 同队列；
3. guest timekeeping 错乱（§7.25 遗留，HWC monitorActiveThread deadline 巨值
   与其相关）。

---

## 9. run51-58（2026-10-11 凌晨）：vblank 六轮调试定案 + 死状态自愈（补丁 14f）——watchdog 死锁主根因闭环

### 9.1 vblank 调试链（v41→v48，每轮 ~20 分钟构建）

| 轮 | 打印 | 观测 | 排除/定案 |
|---|---|---|---|
| v41 | 无 | waitNextVsync 60Hz 节奏失败（2820/8min） | 补丁在内核但路径未通 |
| v42 | enable_vblank 入口 | 0 次 | enable 从未被调 |
| v43 | drm_vblank_init 后 | init ok, num_crtcs=1 @0.11s；enable 仍 0 | init 成功 |
| v44 | ioctl 入口 | 到达；type=0x1（RELATIVE）、num_crtcs=1 | ioctl/SIGNAL/type-mask/pipe 解析全过 |
| v45 | pipe 值 | pipe=0 pipe_index=0 | lease/pipe 越界假设排除 |
| v47 | get 入口+refcnt | **refcnt 恒=1、has_vblank=1** → else 分支 `-EINVAL` | **死状态定案：refcount 悬空 +1 且 enabled=false**（26s 前有一次无日志的 +1 泄漏，来源不明——console 被刷屏或 vendor 状态机黑盒） |
| v48 | 自愈补丁 | **enable on crtc 首次出现 + waitNextVsync 归零** | 闭环 |

### 9.2 修复（补丁 14f，wire-kbase.sh）

`drm_vblank_get` 的 else 分支（refcount>0 且 !enabled 时原版直接 -EINVAL）改为
**强制重走 drm_vblank_enable**（hrtimer start 幂等，成功则保留计数；失败才 dec）。
不再深挖 refcount 泄漏来源（vendor 内核状态机黑盒），自愈逻辑使 vblank 永远可用。

### 9.3 run58 验证（全绿 + 残余登记）

- **vsync 全通**：`failed to waitNextVsync` = **0**（历史 1920-2820 次/数分钟）；
- `enable on crtc` = 1（60Hz timer 生效）；
- cmar SEGV = 0（保持）、zygote died = 0、boot_completed = 1、SF running；
- **watchdog 9 次/boot（run47/50）→ 2 次**（间隔 ~4 分钟）：残余栈仍见
  `ScreenCapture.nativeCaptureLayers`——captureLayers 的 SF/HWC 依赖还有下一层
  （候选：KICK_IDLE(0xc0186450) stub、presentFence 私有 ioctl、HWC SWWatchDog
  10s 超时的 drmdev.cpp:2710 线程），按洋葱法继续。

### 9.4 遗留

1. captureLayers 残余挂点（watchdog 2 次/boot，间隔 4 分钟）——下一层取证：
   HWC SWWatchDog（drmdev.cpp:2710）+ KICK_IDLE/presentFence ioctl stub；
2. vblank 诊断打印清理时机（14b/c/d/e 打印保留至 watchdog 归零后一并清）；
3. KICK_IDLE（每帧失败日志刷屏）——uapi 编号扩展或 ioctl 表 stub。

---

## 10. run59-64（2026-10-11 上午）：watchdog 残余 = SF 假死（binder 黑洞取证三轮探针）

### 10.1 排除项（对照实验 + 系统指标）

- **CMAR type=1 跳过与 SF 挂死无关**（run61 对照实验：撤销跳过后 SF 照样挂、
  cmar SEGV 回归 69 次证明撤销生效）→ 恢复跳过补丁（e7aef4d4）；
- fd 泄漏排除：SF FDSize 恒 512、HWC 128（间隔 30s 双采样）；
- 内存压力排除：MemFree 1.5G / MemAvailable 7G / PSI some avg10=0.24；
- allocator 服务（pid 582）嫌疑排除：4 binder 线程全空闲（等事务）；
- AtomicCommit 内核 D 态挂死（SW_WDT 报 2-10s）：仅 boot 早期 4 次，
  非 watchdog 主因；
- 勘误：`hung_task_timeout_secs` 无 early_param（sysctl only）——cmdline 注入
  无效（"Unknown kernel command line parameters" 实证），hung task 线索作废
  （SF 挂是 S 态非 D 态，hung task 本来就抓不到）。

### 10.2 自研内核探针 cdh_sfdump（ged_stub.c，三轮迭代）

- v1（v49）：每 15s 快照 SF/composer 全线程 (state, 用户态 pc)，连续 3 次
  完全不变判"冻结"→ dump；
- v2（v50）：pc/lr 解析所属 VMA（内核态 find_vma + d_path → "libc.so+0x..."）；
- v3（v51）：frozen 后重新快照并抓**每线程内核栈**（stack_trace_save_tsk）；
- v4（v52，构建中）：目标加 system_server（client 端挂点）。

### 10.3 定案数据（run63/64 dump）

SF（33 线程）与 composer（16 线程）**全部线程处于空闲等待**：
- SF 主线程：Looper::pollOnce（epoll_wait）；
- SF RenderEngine + mali 工作线程 ×9 + TimerDispatch：futex_wait（用户态条件变量）；
- SF/composer binder 线程：binder_thread_read（等新事务）；
- 无任何线程卡内核 ioctl/fence/页回收。

而 MIUIScout 报 system_server→SF 的 sync 事务 elapsed 36s+ 持续增长。

### 10.4 当前定性（待 v52 验证）

**binder 驱动黑洞**：client（system_server android.ui，nativeCaptureLayers）
等 BR_REPLY，server（SF）的 todo 无事务可取（线程全闲）。候选机制：
① 回复写入 client thread todo 后唤醒丢失；② 事务滞留某已退出的动态 binder
线程的 thread todo（BC_EXIT_LOOPER 竞态）；③ SF node 的 async todo 堵塞
导致 sync 事务调度异常。v52 dump system_server 的 android.ui 内核栈
（binder_thread_read 等 reply = 黑洞实锤；否则新方向）。

### 10.5 遗留

1. v52（sfdump v4）验证黑洞定性 → 之后对策：binder 驱动补丁或规避
   （如 SF 侧重启检测）；
2. SW_WDT/KICK_IDLE 刷屏治理（非死锁根因）；
3. grub.cfg 的 hung_task_timeout_secs 注入清理（无效参数，无害但应删）。

---

## 11. run65/66（2026-10-11 中午）：watchdog 残余根因定案 = crash_dump ptrace 冻结 SF + 内核自愈修复（补丁 15）

### 11.1 根因链（多手段交叉实锤）

1. **SF 455 秒 binder 零唤醒**（guest 侧 ctxt_switches 连续采样：SF 全部
   binder 线程自愿切换增量恒 0，同期 SS binder 线程增量 6-197——SF 完全
   收不到事务）；
2. **SF 主线程 State: t (tracing stop) + TracerPid=<crash_dump64>**（直接
   实锤：SF 主线程 + binder:796_1/2 被 ptrace 冻结；tracer 换代循环
   9654→10490→11375→...）；
3. **MIUIScout 循环触发**：SF 首个真实渲染期 AtomicCommit 慢（10s 级，
   SW_WDT 实证）→ Scout 判定 binder 挂 → `debuggerd -b` dump SF →
   **MIUI 定制 crash_dump64（apex com.android.runtime）读 "guest state"
   EFAULT（`failed to get the guest state header: Bad address`）挂死不
   detach** → SF 永久 tracing stop → SS→SF 事务全挂 → Watchdog kill SS
   → SS 重启 → Scout 再触发 → 循环（每 ~60s 一轮）；
4. crash_dump64 依赖链取证：dlopen /system_ext/lib64/libsavior.so /
   libmidebuggerdimpl_system.so（MIUI fault savior + guest state proxy），
   guest state 为 MIUI 虚拟机/云机场景定制（云机无 MTK hypervisor 状态，
   读取 EFAULT）。

### 11.2 排除项（补充 §10）

- binder 驱动黑洞假说排除：SS android.ui/main 在 frozen dump 时也空闲
  （epoll/futex）——挂死窗口与 dump 窗口错位；真正死锁点在 SF 被 ptrace 冻结
  （SF 不响应任何 binder，与"事务丢失"表象一致）；
- cgroup freezer 排除：SF cgroup.freeze=0、frozen 0（events）；state=8193
  = TASK_INTERRUPTIBLE|TASK_FREEZABLE（freezable 睡眠正常态）。

### 11.3 修复（补丁 15 = sfdump v5 自愈，ged_stub.c）

每 15s 轮内检测 SF/composer 主线程 `__state & TASK_TRACED` → 内核
`send_sig_info(SIGKILL)` 杀 tracer（crash_dump64）→ tracer 死亡内核自动
detach → SF 复活。限频 60s 一次。纯内核侧，零分区/so 改动。

配套（非持久）：`setprop persist.sys.miui_scout_enable false` 等四项——
persist 属性存 /data，新实例（独立 data.qcow2 种子）不继承，仅当轮有效；
根治需改 system_ext 分区（scout 属性定义处，968883526 偏移实测）——
**扩张改动面，本轮不做**，登记遗留。

### 11.4 run66 验证（v53 内核，~20 分钟）

| 判据 | run58（自愈前） | run66（自愈后） |
|---|---|---|
| watchdog kill | 155 次/33min | **3 次/20min（-98%）** |
| 自愈触发 | — | 12 次（挂-杀-复活循环持续，全部解开） |
| boot_completed | 反复重置 | 1（SS 稳定） |
| SF/zygote | 死锁循环 | running 持续 |
| zygote died | 随 watchdog | 0 |

### 11.5 遗留

1. 自愈限频 60s > watchdog 65s 窗口 → 偶发 SS 仍被杀（3 次）：可降限频
   至 20s 或在 tracer 退出时同步唤醒 SF；
2. **上游诱因**：AtomicCommit 慢（10s 级）触发 Scout 判挂——根治 =
   B2 渲染桥（M3 层）性能达标，或 system_ext 持久禁 scout（改动面待定）；
3. sfdump 探针（v1-v5）与诊断打印在稳定性目标达成后清理；
4. guest timekeeping 错乱（§7.25 遗留）维持登记。

---

## 12. run67-70（2026-10-11 下午）：watchdog→整机重启链终局定案 + 72B atom 布局定案

### 12.1 run68 观察（v54）：nativehang→整机重启链完整实锤

- SF **非真卡**：sfdump 快照显示 SF 主线程 `epoll_wait`（空闲）、
  binder:770_3 `binder_thread_read`（空闲）——与 Scout 的"binder 悬挂
  74-84s"表象矛盾，实为 crash_dump64 冻结窗口内累积的 ping（code 1）悬挂；
- 自愈 v5 打地鼠：SIGKILL crash_dump64 ×7（13658→…→24712），**限频 60s +
  15s 轮询 → 冻结窗口 45-75s**，击穿 watchdog 67s 阈值；
- 链条完整复现（12:44-12:56）：FW_SCOUT_HANG（Level 升至 5）→
  **WATCHDOG KILLING SYSTEM PROCESS**（12:46:19、12:55:37 两次）→
  nativehang 启动（12:56:15）→ **guest 自我重启**（qemu.log 回到 init 早期）
  → adb offline。run67 的"nativehang→整机重启"链完整闭环；
- 伴生线索：app 主线程卡 `HardwareRenderer.nSetStopped` 2.5s+
  （APP_SCOUT_WARNING，SystemUI/provision 复现）——GPU job 假完成/
  fence 链问题的用户态表征，Scout dump 风暴的上游诱因。

### 12.2 v55 修复（sfdump v5.1 自愈加速）+ 构建坑

- ged_stub.c：自愈限频 60s→**5s**、轮询 15s→**5s**（冻结窗口压到 ~10s）；
- B2 摘要探针（补丁 16b）补 `cdh_all_probe` 声明；
- **构建坑（wire-kbase.sh 幂等失效）**：补丁 14b/14c 改写了补丁 14 的插入
  内容（enable_vblank 体加打印、drm_vblank_init 后加打印），使 14 的幂等判据
  `new in s` 失效 → 增量卷上 virtgpu_display.c 被重复插入 →
  `redefinition of 'cdh_vblank_timer_fn'` 构建失败。修复：`patch()` 增加
  `done=` 幂等标记参数（以不被改写的稳定标记判定已打状态），补丁 14 的
  两处易被改写 patch 调用标记 `cdh_vblank_timer_fn`/`hrtimer_init(&cdh_vblank_timer`。
  kbase 源码每次 `cp -a` 重拷不受此坑影响；
- **ptrace.c 锚点坑**：grep 显示的"4 空格缩进"实为 tab 渲染假象，
  python repr 实测 `\t\t\t unsigned long addr,`——锚点必须按 tab 写。

### 12.3 run69 验证（v55）+ run70 验证（v56）

- run69（v55，自愈加速）：boot_completed=1、watchdog kill 由 run68 的
  每 9 分钟一次降为 **boot 后仅早期 1 次、随后 10+ 分钟零击穿**；
  sfdump kill 间隔 60s→10-12s；
- v56 补丁 17（ptrace 拒绝）：`ptrace_attach` 拒绝 crash_dump64/32/debuggerd
  attach SF/composer/system_server → run70 实测 `deny crash_dump64 attach`
  47 次、SF `TracerPid=0`（冻结彻底消失）；
- **run70 终局结论**：deny 生效后 watchdog 仍 65s 击穿（12:41:27），
  Watchdog 直接打出 `Rebooting system because: nativehang,/system/bin/surfaceflinger`
  ——**整机重启机制定案**：SF 渲染链真卡（B2 缺失，nSetStopped/fence 等待）
  → SS→SF binder 65s+ 无响应 → watchdog 全 monitor 阻塞 → 判 nativehang
  → reboot。dump 风暴只是放大器，**治本 = B2 渲染桥**（§13）。

### 12.4 72B user atom 布局定案（B2 前置，补丁 16b/16c）

kernel v2 copy 只读前 56B 且字段与 UK 11.46 user atom 错位（run69 摘要行：
jc 恒 0、core_req 现 0x2010202 串读形态、extres 非空而 nr_extres=0）。
run70 补丁 16c 原始 72B hexdump 三批样本一致，定案：

| 偏移 | 字段 | 证据 |
|---|---|---|
| +0x00 | 废弃（v2 jc 位恒 0） | 全样本 8B 零 |
| +0x08 | **真 jc（job chain GPU VA）** | `0x5effb5d780`/`0x5effd82480`/`0x5effb1c780`，与 run66 探针同形态 |
| +0x10 | udata/堆指针 | `0xb4000073…` |
| +0x18 | extres_list（堆指针） | `0xb4000074…` |
| +0x30 | 序号/jit 相关 | `0x01`、`0x301` |
| +0x34 | **真 core_req** | `0x1`=BASE_JD_REQ_FS、`0x2`=TILER、`0x209`=SOFT_JIT_ALLOC |
| +0x38..0x47 | 0 | |

→ B2 落地点：从 atom+0x08 取 jc、+0x34 取 core_req；FS atom 的 render target
在 jc 指向的 job chain（Mali HW job descriptor）内，需 dump chain 定位 RT
字段（B2 v1 = 对 RT 填纯色，SAME_VA 语义下 ioctl 上下文直接写）。

### 12.5 发布链定稿（含 build-boot）

```
build-kernel.sh
cp out/kernel/modules/*.ko out/dist/kernel/modules/
python3 build/scripts/build-vendor_dlkm.py          # 模块 → vendor_dlkm.img
python3 build/scripts/build-boot.py                  # ★ Image（built-in 探针）→ boot.img
python3 build/scripts/pack-roms.py --patch boot_a=out/dist/rom/boot.img \
    --patch vendor_dlkm=out/dist/rom/vendor_dlkm.img  # 直写逻辑分区，不碰 vendor
```

⚠ 教训（run67）：`--patch super=` 会连带覆盖 super 内 vendor 逻辑分区，
回滚 vendor 注入件（补丁 so + frp props）→ mali-cmar SEGV 回归。已改用
`--patch vendor=`/`--patch vendor_dlkm=` 直写；boot 镜像（含 built-in
sfdump/ptrace 补丁）必须 `--patch boot_a=` 一并更新。

### 12.6 遗留

1. **B2 v1（治本）**：按 12.4 布局改 chain dump 用 +0x08 jc → 解析 job
   descriptor 定位 RT → FS atom 提交时对 RT 填纯色（§7.8 B2 语义）；
2. watchdog 65s 阈值与 SF 渲染慢的关系：B2 前 watchdog 链无法根除；
3. `--patch` 后 vda 校验和未重算（GPT/liblp），跨工具搬运 vda 需重打；
4. §11.5 的 2/3/4 遗留维持登记。

## 13. run71-75（2026-10-11 晚）：B2 v1 双桥落地 → 黑屏真根因终局定案 = HWC 未获 DRM master → v62 真授权

### 13.1 B2 v1 双桥（v60/v61，wire-kbase.sh 补丁 18/18e/18d/19）

- (18) **scanout 填色桥**（virtgpu_display.c）：`cdh_scanout_fill_fn`
  delayed_work（500ms 自重排），取 primary fb → vmap 填色（cpp==4 填
  `0xFF00FF00` 绿、cpp==2 填 `0x07E0`、否则 memset 0x88）→
  set_scanout + transfer_2d(pitch/4 × height) + flush + notify；限 4 条
  DRM_INFO，真实 present（18d 时间戳）2s 内停填让位。
- (18e) 修正补丁：`drm_gem_object_get()` 在 6.6 返回 **void**，改
  `gobj = fb->obj[0]; drm_gem_object_get(gobj);`（done 标记绕开增量卷
  跳过陷阱）。
- (18d) plane.c `cdh_fill_last_plane_update` 上屏活动时间戳打点。
- (19) **MTK 私有 ioctl stub**（virtgpu_drv.c）：`cdh_virtio_gpu_fops`
  wrapper 按完整 cmd 精确匹配，处理 KICK_IDLE(0xc0186450) /
  SET_MODE(0xc0046449) / master_info(0xc004644c)。

### 13.2 run74（v60）观察：填色成功但不上屏

填色 filled 与理论值吻合（含 AFBC solid 头），但 0 次 frame commit：
transfer/flush 从未执行；KICK_IDLE failed 583 次、monitorActiveThread
timeout 12730 次、enableHWVSync 失败、handlePowerModeChange 阻塞 10.7s。
关键线索：`[DRMDEV] failed to get drm master info, err: -1`（4 次后放弃）
→ 之后 `drm_mode_atomic_ioctl` 全部被拒。

### 13.3 run75（v61）观察：stub 假成功 → HWC 死循环

stub（19）对 master_info 返回 0 但**未真授权**：

- 正面：monitorActiveThread timeout 12730→0、KICK_IDLE failed 583→0、
  enableHWVSync 失败→0、handlePowerModeChange 10.7s 阻塞→0（config
  switch 解锁）——stub 挂点本身有效；
- 反面：HWC 校验 master 仍失败 →
  `[DRMDEV] failed to get drm master, retry: N` **无限重试**
  （qemu.log 218493 条、retry 计数 21865101、约 36k 次 ioctl/s、持续
  600s+）→ composer 主线程死循环 → system_server watchdog
  `Blocked in handler on main thread (main) for 65s`（3 次 WATCHDOG
  KILLING）→ 依旧 0 次 present、黑屏。

### 13.4 根因终局定案

**HWC（vendor.hwcomposer）从未真正成为 DRM master。**

- 机制（drm_auth.c `drm_master_check_perm`）：SET_MASTER 要求
  `file_priv->was_master && pid==tgid` 或 `capable(CAP_SYS_ADMIN)`；
  HWC 无 CAP_SYS_ADMIN → 标准 SET_MASTER 被拒；
- MTK 真机内核用私有 ioctl `master_info`(`_IOWR('d',0x4c,4)`) 绕过该
  检查授权 master。run74 该 ioctl 无 handler → err:-1 → 无 master →
  原子提交（DRM_MASTER 权限）全被拒 → 0 present → **VNC 全黑的真正
  根因**（此前"present 链断点"是表象）；
- run75 stub 假成功（返回 0 未授权）放大成主线程死循环 + watchdog。

### 13.5 v62 修复（补丁 20a-20g / 21a-21b）

- (21a) `cdh_mtk_grant_master()`：`get_user` 取输入 →
  `file_priv->was_master = true` 放行 `drm_master_check_perm` →
  调 `drm_setmaster_ioctl(dev, NULL, file_priv)` **真授权**（非静态，
  drm_internal.h 声明；virtio-gpu 为 built-in 可直接调）→ 成功后
  `put_user(1)` 回写 payload（is_master=1）→ `pr_info_ratelimited`
  记录 `in=%u ret=%d`（payload 语义待下轮定案）。
- (21b) `case 0xc004644c` 分流到真授权 helper。
- (20a) `cdh_fill_make_fb` 自建兜底 fb：`virtio_gpu_object_create`
  （HOST_XRGB8888，w=crtc->mode.hdisplay?:1080，h=:2400）+
  `virtio_gpu_framebuffer_init`（前向声明），无 primary fb 时用
  `drm_framebuffer_get(fb)` 平衡 put。
- (20b-20f) bail 限速日志（no vgdev / no crtc / vmap fail）；
  `!bo->dumb` 收窄为仅跳过 `bo->host3d_blob`。
- (20g) transfer 前显式 `virtio_gpu_cmd_set_scanout(...)` 绑定 scanout。

### 13.6 构建坑（复盘登记）

- `cmd | tail` 吞 exit code（构建失败却 exit 0）→ 一律重定向日志文件后
  单独查退出码；
- 增量卷 done 标记陷阱：已落盘补丁被 `patch()` 跳过，修正须用「修正型
  补丁 + 新 done 标记」（(18e)/(21) 模式）；
- 改 `build_recipe` 是强制重编开关（recipe 不变且 SHA256 匹配直接复用
  prebuilt）。

### 13.7 遗留

1. run76（v62）验证判据：master_info ret、`failed to get drm master`
   retry 归零、cdh_fill 行、VNC 上屏、watchdog 计数（结果见 §14）；
2. master_info payload 语义定案（get_user 日志 in=%u 供判读）；
3. 宽块 RT（blk=12、w2=0x2807）filled=0 例外；
4. 探针清理：16b/16c/rt-struct 定案后删，17（ptrace 拦截）保留。

## 14. run76（2026-10-11 深夜，v62）：master 真授权成功 → 新卡点 flip_done 超时 + KICK_IDLE cmd 常量错误

### 14.1 v62 判据（master 授权闭环 ✅）

| 判据 | run75（v61） | run76（v62） | 结论 |
|---|---|---|---|
| `cdh_mtk: master_info in=0 ret=0` | 无（stub 假成功） | 2 次，ret=0 | **真授权成功** |
| `failed to get drm master` retry | 21865101 | **0** | 重试死循环消失 |
| 原子提交 | 全部被拒（0 present） | **进入 commit_tail**（栈实锤） | DRM master 生效 |
| watchdog WATCHDOG KILLING | 3 | 3 | 仍卡（新卡点，见 14.2） |

关键栈（run76 qemu.log L18383-18421）：`drm_mode_atomic_ioctl →
drm_atomic_commit → drm_atomic_helper_commit → commit_tail`——原子提交
已真正执行（run74/75 从未走到这里），master 问题闭环。master_info
payload：`in=0`（输入值 0），我们回写 1，HWC 无异议继续。

### 14.2 新卡点一：flip_done 超时（commit 卡 10s）

`*ERROR* flip_done timed out` + `[CRTC:35] commit wait timed out` +
`WARN_ON(new_crtc_state->event)` at `drm_atomic_helper_commit_hw_done`
（L2540「backend must have consumed any event by now」）→
handlePowerModeChange 阻塞 10.5s（SW_WDT 10170ms）。

根因：virtio-gpu 原生 atomic_flush **不 arm vblank 事件**；旧路径靠
`drm_atomic_helper_fake_vblank`（仅 `no_vblank=true` 时生效）代发。
自 vblank 补丁（14 系列）给了 crtc vblank 能力后 `no_vblank=false`，
fake_vblank 不再代发 → 事件无人消费 → flip_done 永不完成。此前 HWC
从未拿到 master、没有真 commit，故未暴露。

修复（v63 补丁 22）：`virtio_gpu_crtc_atomic_flush` 末尾
`drm_crtc_arm_vblank_event(crtc, crtc_state->event)`（event_lock 内，
消费后置 NULL）；cdh_vblank hrtimer 60Hz 调 `drm_crtc_handle_vblank`
→ `drm_handle_vblank_events` 送出事件 → flip_done 完成。

### 14.3 新卡点二：KICK_IDLE cmd 常量错误（探针补丁 24）

run76：`DRM_IOCTL_MTK_KICK_IDLE failed ... error: -1` 2665 次，但 stub
`case 0xc0186450` 命中 **0 次**（探针 pr_info_ratelimited 无输出）。
run75 对照：stub 2630 次命中全是 `cmd=0xc004644c`（master_info 重试），
KICK_IDLE 失败 0 次——不是 stub 生效，而是 HWC 卡在 master 重试、从未
走到 KICK_IDLE 阶段（13.3 的「KICK_IDLE 归零」系误读，勘正）。

→ 0xc0186450 常量有误（master_info/SET_MODE 常量均已实锤命中，唯它
从未命中）。v64 补丁 24：default 臂对 `_IOC_NR(cmd) < 0x90` 的未知 cmd
限 8 条打印真实 cmd（type/nr/size），run77 定值后精确 stub。

### 14.4 灰屏判据（272×2400 #888888）

VNC 抓帧 272×2400、99.3% `#888888`（稳定复现）：#888888 = 填色桥 else
分支 `memset(row, 0x88)` 产物 → 当前 primary fb 的 cpp∉{2,4}；272 宽 =
填色桥 set_scanout `w=pitch/4` 折算（pitch=1088）。前 4 条填色日志均
`1080x2400 pitch=4320 cpp=4`（自建兜底 fb，绿色），4 条限额后无日志。
v63 补丁 23：填色日志改为按 fb 格式变化记录（fmt=0x…，上限 20 条），
run77 辨认灰屏 fb 归属。

### 14.5 证据

`run76-v62.log`（master_info/fill/flip_done/AtomicCommit 关键行摘录）。

### 14.6 遗留

1. run77（v64）：flip 修复验证（flip_done 超时归零、watchdog 归零）、
   KICK_IDLE 真 cmd 定值、灰屏 fb 格式定值、真 UI 是否上屏；
2. 13.7 的 2/3/4 维持登记。

## 15. run77（2026-10-12 凌晨，v64）：flip 修复全绿 → KICK_IDLE 真 cmd 反查定案 0xc004649b（v65）

### 15.1 v64 判据（补丁 22 生效 ✅ / 23 生效 ✅）

| 判据 | run76（v62） | run77（v64） | 结论 |
|---|---|---|---|
| flip_done timed out | 2 | **0** | 补丁 22 闭环 |
| commit wait timed out | 2 | **0** | 同上 |
| WATCHDOG KILLING | 3 | **0** | watchdog 归零 |
| handlePowerModeChange 耗时 | 10.5s（SW_WDT） | **0.43s** | commit 链解锁 |
| KICK_IDLE failed | 2665 | 2424 | 仍未解决（见 15.2） |
| enableHWVSync 成功 | 0/10 | **0/10** | vsync 通道仍断 |
| onVsync | 0 | 0 | SF 无 vsync 不出帧 |

### 15.2 KICK_IDLE 真 cmd 定案：0xc004649b（so 反查 + 探针交叉）

- run77 stub 命中分布：仅 1 条 `cmd=0xc0046449`（SET_MODE）——
  `case 0xc0186450` 与共享 ratelimit 调用点均无 KICK_IDLE 痕迹，
  实锤该 cmd 从未以 0xc0186450 到达 wrapper；
- 探针（24）cap=8 在25.9-26.3s 即耗尽（早于 KICK_IDLE 阶段），且
  nr<0x90 过滤器会漏掉 nr=0x9b；
- **二进制反查**（capstone + pyelftools 反汇编
  `out/tmp/hwcomposer.so`）：失败日志串
  `[%s] DRM_IOCTL_MTK_KICK_IDLE failed...` 定位到
  `DrmDevice::kickIdle(uint)`，0x189af4 处
  `mov w1,#0x649b; movk w1,#0xc004,lsl#16` →
  **cmd = 0xc004649b = _IOWR('d', 0x9b, 4)**（nr=0x9b）；
  0xc0186450 虽存在于 so（别处），但非 kickIdle 的 ioctl。
- HWC 仅检查 ioctl 返回值（`mov w19,w0` 后打 error:%d），stub
  `return 0` 即可，无需写 payload。

### 15.3 灰屏 fb 定值：RGB8（补丁 23 生效）

填色日志（fmt 变化记录）：`cdh_fill: 1080x2400 pitch=1080 cpp=1
fmt=0x38424752 handle=3`——fmt 0x38424752 = fourcc 'RGB8'（RGB332，
cpp=1）。即 HWC 绑定的 primary fb 是 1080x2400 RGB332；填色桥 else
分支 memset 0x88 → 灰屏；scanout 折算宽 pitch/4=270（VNC 桌面
272×2400）。真 UI 未上屏因 SF 无 vsync 不出帧（onVsync 0），
非 fill 遮挡（fill 有 2s 让位逻辑）。

### 15.4 v65 修复（补丁 25，修正型 + done 标记）

(19) switch 补 `case 0xc004649b`（真 cmd）挂到 stub 成功臂；
0xc0186450 旧常量保留在臂内。预期：KICK_IDLE failed→0、
enableHWVSync→通、onVsync 有帧、SF 出真 UI。

### 15.5 证据

`run77-v64.log`（master_info/fill fmt/passthru 探针摘录）；反查脚本
（venv /tmp/capenv + capstone）结论见 15.2。

### 15.6 遗留

1. run78（v65）：KICK_IDLE failed 归零、enableHWVSync/onVsync 通、
   真 UI 上屏判定（若 RGB8 折算宽问题显形再修 scanout 映射）；
2. 14.6-2 / 13.7 的 2/3/4 维持登记。

## 16. run78（2026-10-12 凌晨，v65）：KICK_IDLE 闭环 ✅ → 新卡点 enableHWVSync 真 cmd = 0xc0046456（v66）

### 16.1 v65 判据（补丁 25 生效 ✅）

| 判据 | run77（v64） | run78（v65） | 结论 |
|---|---|---|---|
| KICK_IDLE failed | 2424 | **0** | 补丁 25 闭环 |
| stub cmd=0xc004649b 命中 | 0 | **150** | 真 cmd 实锤命中 |
| flip_done/commit timed out | 0 | 0 | 维持归零 |
| enableHWVSync 成功 | 0/10 | **0/11** | 仍失败（见 16.2） |
| onVsync | 0 | 0 | SF 仍无 vsync |
| VNC 主色 | #888888 99.3% | #888888 99.3% | 真 UI 未上屏（RGB8 兜底填色） |
| WATCHDOG KILLING | 0 | 1 | 65s 阻塞 1 次（vsync 断的次生） |

### 16.2 enableHWVSync 真 cmd 定案：0xc0046456（so 反查）

- run78 实锤 KICK_IDLE 通（failed=0）后 enableHWVSync 仍
  11/11 失败 error:-1 → 说明它与 KICK_IDLE 是**两个不同 ioctl**，
  此前「KICK_IDLE 断链 → enableHWVSync 失败」的因果推断只对了一半
  （KICK_IDLE 是独立断点，enableHWVSync 有自己的断点）；
- 反查 `out/tmp/hwcomposer.so`：失败日志串
  `[%s] (%lu:%u) failed to enableHWVSync: %d`（str 0x71aa6）引用点
  0x187c74；失败调用在 0x187c04/0x187c4c 两分支
  `mov w1,#0x6456; movk w1,#0xc004,lsl#16` →
  **cmd = 0xc0046456 = _IOWR('d', 0x56, 4)**；
- HWC 仅检查返回值符号（`mov w20,w0; tbnz w20,#31` 分流日志/成功），
  stub `return 0` 即可；
- 探针（24）nr=0x56<0x90 本可抓到，但 cap=8 早已耗尽 → 再次实锤
  「有限 cap 探针不适合长生命周期 cmd」，后续一律 so 反查优先。

### 16.3 v66 修复（补丁 26，修正型 + done 标记）

(19) stub switch 的 SET_MODE 臂后补 `case 0xc0046456`（挂 stub
成功臂，不入 master_info 真授权分支）。构建教训：锚点必须用
「已分流」后的 master_info 注释原文（旧注释 `DRM_IOCTL_MTK
master_info` 已被真授权补丁改写，直接锚旧文本失配）。

### 16.4 证据

`run78-v65.log`（判据计数 / enableHWVSync 失败样本 / stub cmd 分布 /
cdh_fill fmt / WATCHDOG 摘录）。

### 16.5 遗留

1. run79（v66）：enableHWVSync 成功、onVsync 有帧、真 UI 上屏判定；
2. run78 WATCHDOG 1 次（65s 阻塞）待 vsync 通后复查是否归零；
3. RGB8 折算宽 / 14.6-2 / 13.7 维持登记。

## 17. run79（2026-10-11 深夜，v66）：atom 滞留 COMPLETED 死锁终局定案 = path C 自死锁（用户态不取事件）

### 17.1 问题

run79（v66）开机后全体 GPU 提交线程卡 `kbase_jd_submit+0x1480`
（779/802 RenderEngine、841/1258 mali-cmar-backe，wchan=kbase_jd_submit、
sys=29 ioctl），mali0 各 ctx atom 全部滞留 St=4 COMPLETED 不再前进，
boot 无法完成。此前 run76-78 已逐个打通 master 真授权/flip_done/KICK_IDLE，
此卡点是 B2 v1 落地前最后一个阻塞环。

### 17.2 根因：path C 自死锁（事件已投递、用户态不取）

1. **静态排除（内核侧交付链清白）**：
   - `work_count` 全库仅两处变更：`kbase_jd_done` inc（mali_kbase_jd.c:1474）、
     done_worker 排空 dec（:1342-1355）；done_worker STOPPED 早退
     （:1243-1259 不 dec）是死路径（STOPPED 主流程走
     `kbase_jm_return_atom_to_js`→unpull，不进 done_worker）；
   - `kbase_jd_done` 唯一调用点 mali_kbase_js.c:3412（done_code=0）；
   - job_fault 路径排除：run79 qemu.log 无 "Job dumping"/"Job fault happen"/
     GPU fault 任何内核日志；
   - St=4 COMPLETED 语义 = "已完成但未交还用户态"，只被 `kbase_event_process`
     （dequeue 时）置 UNUSED——本身不区分 path B（completed_jobs 未排空）/C。
2. **poll 判据一锤定音**：`kbase_poll`（mali_kbase_core_linux.c:2125-2148）
   返回 EPOLLIN|EPOLLRDNORM 当且仅当 `kbase_event_pending`（event_count!=0）。
   实测 **revents=1（POLLIN）** → 事件已投递 event_list 但无人取 → **path C**。
3. **自死锁闭环**：用户态模型（fdinfo flags=02404002，无 O_ASYNC，排除 SIGIO）
   ——`mali-cmar-backe` 是"提交+事件消费"同一线程：841 的 cmar 卡
   `kbase_jd_submit` 时整个事件循环停摆（解锁后该线程转 `do_sys_poll`
   自行消费，实测坐实）；提交线程一旦卡 submit 即永不 read，事件滞留→
   槽位不释放→submit 等槽位永久自锁。

### 17.3 证据

1. **poll 判据**：`revents=1 ready=1`（779/10 与 841/10 同 struct file ino=1098）；
   注意 `open(/proc/<pid>/fd/N)` 是按真实路径重新 open（新 kfile、
   setup_state=NEED_VSN）会假报 revents=8/POLLERR，必须
   `pidfd_open`(sys 434)+`pidfd_getfd`(sys 438) 拿同一 struct file；
2. **干预铁证**：`pidfd_getfd` 后替用户态 read 走 1 个事件
   （`ev code=16387 nr=0`）→ RenderEngine wchan 立即 `kbase_jd_submit`→
   `futex_wait_queue`、779_0 atoms 清空、poll 归零——submit 解锁，死锁链闭合；
3. **对照实验**：停掉后台代取循环 25s 死锁不复发（poll=0、无卡 submit）
   → 一次性自锁事故，用户态恢复后有自消费能力；
4. 快照全量原始采样见 `log-fail.txt`（死锁态）与 `log-pass.txt`（恢复态）。

### 17.4 改动

本节为**定案 + 活体兜底验证**，无内核改动。诊断工具链（可复用于 run80+）：
- `/tmp/pollfd`（freestanding aarch64，poll 判据 + take 干预，pidfd_getfd 版）；
- `/tmp/cdh_run.py`（guest 命令通道客户端，POST /cmd + SSE ack，逐行 readline、
  禁代理；注意 jsengine 单脚本 2s、cmd.exec 命令 5s 上限，长采样须拆分）；
- `/tmp/rfbgrab.py`（RFB 3.8 直抓 VNC 帧，QMP screendump 不可用"no surface"）。

### 17.5 验证

- 活体兜底（后台代取循环 90s）：卡 submit 归零、**boot_completed=1**
  （run79 此前一直卡 boot）、779/817 与 841/1258 cmar 转 `do_sys_poll` 正常消费；
- take.log 6 事件统计：code=1×2、code=16387×4（16387=0x4003 SW_EVENT 高位）；
- VNC 抓帧 272x2400：99.3% 纯灰 #888888 = GPU 假完成、RT 无真实内容
  ——即 B2 v1 的靶子（RT 填纯色后此判据应翻转）。

### 17.6 遗留

1. **死锁初始触发时刻/原因未究**：开机早期事件为何滞留到自锁（时序窗口）；
2. **内核兜底已落地**（§18，v67→v68）：`wait_event_killable_timeout` +
   超时强制 `kbase_event_force_dequeue_atom` 释放滞留事件，见 §18；
3. **B2 v1**（主目标）：RT 填纯色、真 UI 上屏（当前 99.3% 灰）、watchdog 归零；
4. 宽块 RT（blk=12、w2=0x2807）filled=0 例外（todo）；RGB8 折算宽、
   探针清理、`--patch` 后 GPT/liblp 校验和未重算维持登记。

## 18. run80/81（2026-10-12 凌晨，v67→v68）：submit 超时自愈兜底落地 + 超时长 10s→2s

### 18.1 问题

按 §17.6 实现最小内核兜底（补丁 27a/27b/27c，v67）后，run80 实测暴露两个问题：
(1) drops 计数反降（31→5）异常；(2) boot 持续爬不完（约 5 分钟 sys.boot_completed
仍空），guest 反复重启。

### 18.2 根因

- drops 反降 = **guest 整机重启后 dmesg 环形缓冲清零**，非计数错误。重启链与
  既有定案一致：SF 卡顿 → system_server watchdog 判 nativehang →
  `sys.powerctl='reboot,nativehang,/system/bin/surfaceflinger'` 整机重启
  （run81 qemu.log 实锤），boot_progress_start 多轮（run80 七轮、run81 五轮）。
- **path C 是 boot 期慢性复发而非一次性事故**：假 GPU 即时完成 job → 完成事件在
  submit 前后立即投递 → 用户态复用 atom 号必撞滞留事件（真 GPU 有毫秒延迟，
  DDK 来得及消费）。run80 兜底日志自 t=36.5s 起约每 5-20s 触发一次。
- v67 超时长 10s × 高碰撞率 = 提交线程大部分时间卡住，SF binder 调用 39-55s
  无响应（ScoutHelper 实录）→ watchdog 击穿。需压低单次代价。

### 18.3 证据

见 log-pass.txt "run81（v68，超时 2s）关键摘录"段：2s 兜底触发序列（碰撞间隔
~1-2s）、`Rebooting system because: nativehang,/system/bin/surfaceflinger`、
boot_progress_start 多轮、SF 被 watchdog 抓栈（kDebuggerdNativeBacktrace）。
run80 侧（v67 10s）：兜底日志 [36.5/42.7/52.9/63.2/73.4/80.8s]…每次间隔约一个
完整 10s 等待；7 轮 boot_progress；keystore2 await_boot_completed 超时 222s+。

### 18.4 改动

- [wire-kbase.sh](../../../../kernel/modules/mali_vgpu/wire-kbase.sh) 补丁
  (27a) `kbase_event_force_dequeue_atom()`（event_mutex 摘链 → jctx.lock 过程化，
  仅处理 COMPLETED 且挂在 event_list 的目标 atom）、(27b) mali_kbase.h 声明、
  (27c) jd.c submit 等槽改 `wait_event_killable_timeout` + 超时强制释放。
  超时长 **10s→2s**（v68）：健康路径事件毫秒级被消费不误触发，误触发丢 1 条
  完成事件用户态可容忍（run79 take 干预、run80/81 数十次丢弃均不崩）。
- build-kernel.sh `build_recipe` → `...v68-jdtimeout2s`。
- **教训**：增量卷内源码是上次补丁结果，(27c) 幂等标记 `done=` 若与已打状态
  重合会误跳过——本次靠 tar 重导 pristine 源 + 重打才落地；改既有补丁内容时
  必须核对容器卷内源码（`docker run -v <vol>:/src ... grep`）而非只看构建退出码。
- **教训**：build-kernel.sh 输出勿 `| tail` 截断保存，wire-kbase 打补丁日志
  会被丢掉导致无法核对。

### 18.5 验证

run81（v68）：碰撞间隔 ~1-2s（vs v67 ~10s）；bootanim 退出、launcher 起、
**无永久自死锁**（§17 的 path C 已兜住）；boot 每轮 ~100s 后仍被 watchdog
nativehang 重启——属既有"watchdog 循环"（根因 = B2 渲染桥缺失，见 gpu.md
定案），不在本兜底职责内，待 todo #20。

### 18.6 遗留

1. **B2 v1**（主目标）：RT 填纯色、真 UI 上屏、watchdog 归零；
2. boot HAL `android.hardware.boot-service.mtk` 每 10s SIGABRT 崩溃循环
   （side finding，疑 bootctrl 交互问题，另查）；`mali-cmar` 线程偶发 SIGABRT
   （疑与丢弃事件相关，B2 联调时观察）；
3. 宽块 RT 例外、RGB8 折算宽、探针清理、GPT/liblp 校验和未重算维持登记。

## 19. run81→82（2026-10-11，v69）：事件暂存回放（零丢失）

### 19.1 问题

run81 取证：SF 全线程 futex/poll 判"空闲"，但 binder 事务卡 75s+（code 16/9，
system_server main + PhotonicModulat → SF binder 线程）——符合**丢完成事件 →
用户态条件变量永不唤醒**模式。v67/v68 的 force_dequeue 直接丢弃事件是错误设计。

### 19.2 根因

(27a) force_dequeue 摘链后调 `kbase_event_process()` 把 atom 置 UNUSED 释放槽位，
但 `udata`（用户态完成回调凭据）被丢弃——用户态等该信号的线程永久 futex。

### 19.3 改动

wire-kbase.sh (27a) 重写：新增 `cdh_saved_event` 暂存表（kctx/event_code/
atom_number/udata 快照，`cdh_save_event`/`cdh_take_event`/`cdh_purge_events`）；
force_dequeue 改为暂存不丢弃。(27d) `kbase_event_dequeue` 开头优先回放暂存事件
（含前向声明）；(27e) `kbase_event_cleanup` 清表兜底。(27c) 超时 2s→**500ms**
（健康路径事件毫秒级被消费不误触发）。build_recipe → `v69-evreplay`。

### 19.4 验证（run82）

500ms 兜底生效、无永久自死锁；但碰撞日志只出现 `atom tgid_id/0`、`/1` 两个槽位
反复（`5078_32/0`、`5078_32/1`、`5252_34/0`…）——**所有 atom 挤爆两个槽位**，
触发 §20 根因定位。watchdog 循环仍在（7 轮 boot_progress），挂点 19.5s
（较 run81 75s 缩短）。

### 19.5 遗留

回放是否生效缺直接计数日志（回放路径仅在兜底触发后消费，健康路径不可见）；
watchdog 归零待 §20。

## 20. run83（2026-10-11，v70）：72B atom 布局定案修正 —— path C 慢性碰撞根因

### 20.1 问题

run82 碰撞日志只出现 atom 槽 0/1（确定性、每次 submit 必撞），500ms 兜底被限速
到 2 次/秒 → SF 渲染链爬不动 → binder 堆积 55s → nativehang 重启循环。
"path C 慢性复发"从 run80 起一直被视为时序竞态，实为**确定性槽位冲突**。

### 20.2 根因

**72B user atom = v1 `base_jd_atom`（64B，seq_nr 开头）+ 8B 尾部**，
不是 (12) 补丁假设的 v2 布局。run39 hexdump 逐字节吻合 v1 偏移：
+0x00 seq_nr=0、+0x08 jc=真 jc、+0x30 atom_number=序号、+0x34 core_req=真
core_req（(16d) 用 +0x08/+0x34 成功定位 RT 填色亦实证）。
(12) 把 stride=72 当 v2 解析（从 +0x00 拷 56B 到 `&user_atom.jc`）→ 字段整体
错位 8B：kernel 读到的 **atom_number 实为 +0x28 的 nr_extres**（普通 GPU job
恒 0、带 1 extres 的 job=1）→ 全部 atom 挤爆 atoms 槽 0/1 → 每次 submit 必撞
滞留事件。连带 jc←seq_nr(0)、core_req←pre_dep 区（0x209 SOFT_JIT_ALLOC 被
读成 0=DEP 秒完成，JIT 分配真路径从未走过）、事件回传 atom_number 也是错的
（用户态事件匹配错乱）。72B 布局表更新见 gpu.md §7.30。

### 20.3 证据

- run82 qemu.log：碰撞日志仅 `…/0`、`…/1` 两槽（`dmesg | grep "超时强制释放"`）；
- 结构体偏移算术：`base_jd_atom` = seq_nr@0 + v2 字段全体右移 8B
  （mali_base_jm_kernel.h L680-695）↔ hexdump 四组 u64 全吻合；
- run83 修复后：`jd_submit 超时` 计数恒 **0**（run82 同期上百次）——槽位冲突
  根除实锤；
- **真机交叉验证（2026-10-12，adb gold/2312DRAABC + Magisk root）**：
  1. DDK 一致性：真机 `/vendor/lib64/egl/libGLES_mali.so` md5
     `a067235ebc44b2d3c66b1022207c21e5`（53566640B）= 我们反汇编所用
     `out/tmp/libGLES_mali.so`——**同一份 UK 11.46**，布局结论直接迁移；
  2. OEM kbase 反汇编（`/vendor_dlkm/lib/modules/mali_kbase_mt6833_r49.ko`
     拉回 llvm-objdump，`kbase_jd_submit` @0x2d804）：
     - stride 校验 `and w8, w3, #0xfffffff7; cmp w8, #0x40`（0x2d848）——
       **OEM 只接受 stride 64/72**（72 = 64|8 被掩码放行），无 v2 56/48 分支；
     - 清零 x29-0x58 起 **72B** 缓冲后 `copy_from_user(缓冲, user_addr, stride)`
       整拷（0x2d91c-0x2d954）；
     - `ldurb w9, [x29, #-0x28]`（0x2d96c）= **结构体 +0x30 处读 atom_number**
       作 atoms[] 索引（`umaddl x8, w9, #0x248`）；
     - `ldp x8, x10, [x29, #-0x58]`（0x2dac8）= +0x00 seq_nr + +0x08 jc；
     - `ldur w10, [x29, #-0x24]`（0x2dadc）= **+0x34 core_req** 存入
       katom->core_req（str [x27, #0xdc]）。
     **OEM 解析偏移与 v70 修复逐字段一致**，(12) 旧 v2 解析与真机内核行为相悖。

### 20.4 改动

- wire-kbase.sh (12) 重写：`jd_atom_is_v2` 撤销 `|| stride == 72`（72B 走 v1
  copy 路径）；(12c) 新增 v1 copy 限长 `min(stride, sizeof(user_atom))`
  （72B 只拷 64B，防栈溢出 8B）；(12b) padding 豁免保留。
- build-kernel.sh `build_recipe` → `v70-atomv1`。
- 发布链：build-kernel.sh → build-kernel.py → build-vendor_dlkm.py →
  pack-roms.py --patch vendor_dlkm（未改 GPT/liblp 校验和）。

### 20.5 验证（run83）

- `jd_submit 超时`/`强制释放滞留` 计数**恒 0**（v70 前每 500ms 一次）；
- B2 fill 仍在工作（RT0 filled=163200，1080x2400 主 RT 命中）；
- VNC 帧回灰 #888888 99.3%（vs run82 绿）——**填色桥按设计让位**，说明实时
  present 链活跃（run82 的绿是渲染链卡死导致陈旧填充占主导）；
- nativehang 重启循环**仍在**（观察窗内 6 次），但挂起对象已演化：首见
  idmap2d（code 3 卡 38-48s），后为 SF binder 线程 futex（code 16/9 线性爬升）；
  全线程 futex 空闲 + 事务卡 50s+ = 丢唤醒模式依旧，但已不是 atom 槽位冲突
  （TIMEOUTS=0）。SF 侧 mali-cmar 线程实测 futex_wait（FUTEX_WAIT_BITSET）非忙等。

### 20.6 遗留

1. **丢唤醒残留**：槽位冲突根除后 SF binder 仍周期性 futex 丢唤醒——疑 v70 后
   core_req 解析正确使 SOFT_JIT_ALLOC(0x209)/FS 走真路径，JIT 分配/soft job
   完成链或依赖（pre_dep 现在也被正确解析）出现新 stall 点，待续查；
2. watchdog 归零（B2 判据）未达成；真 UI 上屏确认待丢唤醒根除后复核；
3. §18.6 遗留 2/3 维持登记。

## 21. run83/84（2026-10-12，v71）：丢唤醒残留判别 —— 内核事件链健康，卡点在用户态 dma fence

### 21.1 问题

v70 后 `jd_submit 超时` 恒 0（槽位冲突根除）但 SF 仍周期性丢唤醒：全线程
futex、binder 事务堆积 55s、nativehang 重启循环。run83 诊断：SF ctx 堆积
**143 个 atom 全部 St=4（COMPLETED"未交回用户态"）**，含 DEP（core_req=0 秒
完成类）等全类型。

### 21.2 根因（判别结论）

v71 (27h) 在 atoms debugfs 表头加判别计数（ec=event_count/wc=work_count/
cj=completed_jobs 非空）。run84 挂起态实测 **`ec=143 wc=0 cj=0`**：
- 事件**已全部 post 到 event_list**（ec=143）、completed_jobs 空、work_count
  无泄漏 → **内核事件投递链完全健康**，(27f) 假设（completed_jobs 不 flush）
  被证伪；
- tracefs kprobe 实测：`kbase_poll` 3 万次/3s 全部返回 **0x41
  （EPOLLIN|EPOLLRDNORM，明确可读）**，`kbase_read`/`kbase_event_dequeue`
  **0 次**——`mali-cmar-backe`（DDK 事件消费线程）以 ~10k/s 忙轮询、
  `/proc/syscall` 恒显示 running（用户态自旋），**拿到 POLLIN 却从不 read**；
- `dma_fence_signal`/`kbase_sync_fence_out_trigger` 探针 0 次；SF 持有
  **27 个 anon_inode:sync_file 未消化**；RenderEngine 恒 `nanosleep` 轮询；
- vblank hrtimer 存活（/proc/timer_list 60Hz 4ms 待触发）→ 非 vblank 停摆。

**结论**：用户态 DDK 在 poll 等 **dma fence 就绪**（而非 mali 事件），fence
永不 signal → cmar 不进入事件 read 阶段 → 完成事件滞留 event_list →
上游 RenderEngine/binder 全链冻结。fence 不 signal 的源头候选：
(a) mali FENCE_TRIGGER(0x202) 的 `kbase_sync_fence_out_trigger` 因
    `kbase_fence_out_is_ours()` 判否提前 return（不 signal）；
(b) 显示侧 release fence（flip_done 链）未 signal——但 (22) 已 arm vblank
    事件，vblank 活着，需活跃期探针复核。

### 21.3 证据

- run84 实例 debugfs：`cdh: ec=143 wc=0 cj=0`（(27h) 计数，两轮 boot 同型：
  ctx 766_0/787_0 ec=143/142，第二 ctx ec=0）；
- kprobe（tracefs，关噪声事件后）：`cdh_poll=30690`（全部来自
  `mali-cmar-backe-829` 线程）/`cdh_read=0`/`cdh_deq=0`；kretprobe
  `kbase_poll ret=0x41` ×35906；
- SF fd 表：fd10=/dev/mali0（flags 02404002 含 O_NONBLOCK）+ 27 个
  anon_inode:sync_file；
- `/proc/<tid>/syscall`：cmar 线程 12/12 次采样 "running"（用户态自旋）；
  RenderEngine 恒 syscall 101（nanosleep）；
- `/proc/timer_list`：`cdh_vblank_timer_fn` S:01 待触发（60Hz）。

### 21.4 改动

- wire-kbase.sh (27f) done_worker 尾部无条件 flush completed_jobs
  （防御性，顺带根治 work_count 泄漏类滞留；(27h) 证明本例未走此路径）；
- (27g) STOPPED 早退补 dec work_count（计数配平防御）；
- (27h) atoms debugfs 表头判别计数 ec/wc/cj（本判别的关键工具，保留）；
- build_recipe → `v71-evflush`。

### 21.5 验证（run84）

- (27h) 计数实测 = 判别依据（见 21.3）；(27f/27g) 无副作用（TIMEOUTS 恒 0）；
- 丢唤醒仍在（NATIVEHANG 观察窗 6+ 次），但卡点从"疑似内核投递"**收敛为
  "用户态 dma fence 不 signal"**。

### 21.6 遗留（v72 靶子）

1. **dma fence 不 signal 定位**：活跃期 kprobe `dma_fence_signal` 调用者分布
   （mali FENCE_TRIGGER vs 显示 flip_done），核对 `kbase_fence_out_is_ours`
   判定与 (22) flip_done 链覆盖；
2. watchdog 归零 + 真 UI 上屏最终确认（B2 判据）；
3. §18.6 遗留 2/3 维持登记。

## 22. run85-87（2026-10-12）：v72 flip 链修复验证 + v73 fence 链探针定案 —— mali fence 链全绿，卡点转向用户态事件消费死锁

### 22.1 v72（vblank get/put 配对）落地验证

**发布链缺口发现**：run84/85 跑的内核 banner 同为 `#18 Oct 11 09:37 UTC`——
v72 的 Image（10-12 08:38 构建）从未 pack 进 boot.img，两轮验证均在旧内核上
（§12.5 发布链 build-boot.py 一步缺失的复现）。补齐发布链后 run86 首跑 v72
（banner `#24 Oct 12 00:37 UTC`）：

- **vblank refcount WARN 归零** ✅（run85 旧内核 4 次 `drm_vblank_put` WARN →
  run86/87 全程 0 次）——(22) v72 修复（arm 前 get、get 失败立即 send）生效实锤；
- **flip_done 超时归零** ✅（v62-v66 期间 commit 卡 10s → 0 次）——显示 flip 事件
  链通（drm_crtc_arm_vblank_event 契约配对后，vblank 事件经
  drm_handle_vblank_events 正常送达，CRTC out_fence 挂在 event->base.fence 上
  随事件 signal，见 drm_atomic_uapi.c:1225 + drm_vblank.c 发送路径）；
- **nativehang 仍在**：watchdog 65s 击穿 → `Rebooting because:
  nativehang,/system/bin/surfaceflinger` 重启循环（run86 56 次提级、7 次击杀）。

### 22.2 v73 fence 链探针（(27i)-(27l)，recipe v73-evfence）：候选 (a) 排除

run84 双候选判别所需的活跃期探针改为 dmesg 直读（非 root/adb 传输不稳下的
替代路径），run87 实测（限 48 条/类）：

| 探针 | 命中 | 结论 |
|---|---|---|
| (27i) trigger-prep | 18 次，fd 正常 | FENCE_TRIGGER 建 fence 全成功 |
| (27j) out-trigger result=0 | 18/18 | **mali out fence 全部 signal 成功**（候选 (a) 排除） |
| (27j) out-trigger NOT-OURS fence=NULL | 18 次 | 良性噪音：process→finish 双调用，二次调用时 fence 已移除 |
| (27k) wait-in | **0 次** | DDK 从未使用 FENCE_WAIT soft job 等 fence |
| (27l) in-fence fired | 0 次 | 同上（无 fence_in 回调注册） |

- atom 组成（(16b) 摘要）：DEP(0x0)×9 / TILER(0x4e)×6 / SOFT_JIT_ALLOC(0x209)×6 /
  SOFT_JIT_FREE(0x20a)×6 / FS(0x1)×6 / FENCE_TRIGGER(0x202)×3——JIT 配对流动正常；
- **§21.2 的"fence 不 signal"判别结论勘正**：mali 侧 fence 链健康。run84 的
  `dma_fence_signal 0 次` kprobe 采样窗口内 DDK 已停止提交（32s 后无 fence
  活动），0 命中是"无 fence 在途"而非"signal 被吞"。

### 22.3 当前定性（run87 挂起窗口）

- GPU 提交（B2 fill）**恒停在 boot 后 ~32-37s**（三轮 boot 不变式），之后
  cdh-mali/cdh_fence 探针归零；
- 挂起后 SF 全线程 futex 睡眠、`mali-cmar-backe` 线程用户态自旋（run84 kprobe：
  kbase_poll 10k/s 返 POLLIN、kbase_read 0 次）——**用户态事件消费停摆**；
- 内核侧事件链静态+动态双清白：kbase_event_post → kbase_event_wakeup
  （wake_up_interruptible(event_queue)）未被历次补丁触碰，(27h) ec 累积
  （run84 ec=143）；
- 判别结论收敛：**用户态 DDK 内部死锁/丢唤醒**（mali-cmar 线程自旋 + 事件
  不消费 + 上游 RenderEngine/binder 全链冻结），非内核 fence/事件投递问题。

### 22.4 证据

- run85 qemu.log：vblank WARN 4 次（旧内核 #18 对照组）；
- run86/87 qemu.log：WARN 0、flip_done 超时 0、cdh_fence 18/18 result=0、
  wait-in 0、B2 提交停于 32-37s、watchdog 7 次；
- run87-grab.txt / hangsnap（进行中）：挂起窗口用户态线程现场。

### 22.5 遗留（run88 靶子）

1. **DDK 用户态死锁点定位**：挂起窗口 dumpsys SurfaceFlinger fence/latch 状态 +
   cmar/RE 线程 wchan/stat 快照（guest 自持 hangsnap.sh，/data/local/tmp/hangsnap/
   重启后回传）；必要时按 run45 NOP 先例对 libGLES_mali.so 定点取证/修复；
2. watchdog 归零 + 真 UI 上屏最终确认（B2 判据）；
3. §18.6 遗留 2/3 维持登记。
