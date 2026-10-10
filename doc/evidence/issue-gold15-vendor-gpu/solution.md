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
  cmdline 现场读取**，不得沿用记忆或他处记录；串口 qemu.log 是设备无关的
  第一现场，计数类判据优先走串口。

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