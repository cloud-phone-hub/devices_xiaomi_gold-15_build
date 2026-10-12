# 外部先例调研原始结论（2026-10-12）

> 用途：`gpu-hw-emu.md` §3.6/§3.7 的一手来源汇总。调研问题：为「在虚拟化环境中运行**未修改的 ARM Mali（Valhall / Mali-G57）**驱动栈」做**寄存器级 GPU 仿真**并把渲染桥接到宿主 GPU，是否有先例/可行路径。
> 标注：✅=已确证；❌=已确证不存在/不适用；⚠️=部分成立或代价高。

## 1. 结论速览

| # | 结论 | 级别 |
|---|---|---|
| 1 | **"寄存器级仿真 Mali + 桥接到宿主 GPU"无任何公开先例**，应判定为高风险研究项目 | ❌ |
| 2 | gfxstream / VirGL / Venus / vDRM **全部是 API 重映射**（要求客机运行**它们自己的** ICD），**不能驱动真实 GPU 硬件**，与"stock Mali DDK 不可修改"**结构上互斥** | ❌ |
| 3 | 不可约瓶颈 = **Mali ISA → 宿主 GPU 的反编译**：只有 disassembler，**无任何** Mali ISA → SPIR-V/NIR/LLVM 翻译器；Apple **Air 无公开汇编器**，唯一官方 IR 入口是 **metal-shaderconverter**（DXIL/SPIR-V → Metal） | ❌ |
| 4 | **PanVK 对 Valhall v9（G57/G68）完全不支持**（Mesa 支持矩阵为空；Collabora 2025-07 原文 "completely lacking support"）→ "改客机 Mesa 栈"的 Vulkan 半边破产 | ❌ |
| 5 | **QEMU 没有任何 Mali 设备模型**，也没人提交过 | ❌ |
| 6 | ARM Mali DDK 自带寄存器级假模型 `CONFIG_MALI_NO_MALI`（`mali_kbase_model_dummy.c`），但启用需**换掉 `mali_kbase_mt6833_r49.ko`**（违反约束）且**不出画** → 价值在于**当设备模型的规格书** | ⚠️ |
| 7 | **ARM Fast Models 有 Mali GRM/GGA 模型**，但公开型号 **Mali_G710/G715（v10+，无 v9）**、默认只出**渐变图案**、**必须自写 `libnwhal`**、**GGA 已弃用**、无 macOS 形态 | ⚠️ |
| 8 | 唯一找到的"QEMU 上仿真 Mali"公开实践 = `CONFIG_MALI_NO_MALI` + 自注册 `platform_device` 让 `/dev/mali0` 出现（与本仓库现行路线同构，不出画，用于内核调试/fuzz） | ⚠️ |
| 9 | **"没有真 Mali 做 oracle，寄存器级仿真的正确性无法收敛"**（Collabora："reverse-engineering and driver development are better together"）→ 抬高了纯仿真路线的隐性成本，抬高"宿主换真 Mali"的优先级 | ⚠️ |

## 2. 来源清单

### 2.1 API 重映射类（不能驱动真实 GPU）

- Android Emulator Internals：<https://aospbooks.github.io/android-emulator-internal-book/14-gfxstream-protocol/>（Ch.12 guest GPU drivers、Ch.13 host rendering）
- gfxstream capset 一览（无 Mali）：<https://gist.github.com/mtjhrc/6a78a127bd141165e0a454a52d6e6096>
- Cuttlefish `GpuMode` 枚举（无 mali）：<https://google.github.io/android-cuttlefish/cvd/libs_2config_2gpu__mode_8h.html>

### 2.2 Mali ISA / 反编译现状

- Valhall ISA 文档（Alyssa Rosenzweig）：<https://people.collabora.com/~alyssa/Valhall-Documentation.pdf>
- Mesa `ISA.xml`（Valhall 编码表，disassembler 驱动）：<https://github.com/intel-lgci-fdo-gitlab-mirror/mesa.mesa/blob/93339463/src/panfrost/compiler/bifrost/valhall/ISA.xml>
- Collabora：反向工程 Mali-G78：<https://www.collabora.com/news-and-blog/news-and-events/reverse-engineering-the-mali-g78.html>
- Apple Shader Converter 集成 RFC（DXIL/SPIR-V → Metal 是唯一官方 IR 入口）：<https://github.com/o3de/sig-graphics-audio/blob/main/rfcs/AppleShaderConverterIntegration/AppleShaderConverterIntegration.md>

### 2.3 Mesa panfrost/panvk 支持现状（v9 无 Vulkan）

- Mesa Panfrost 文档（支持矩阵）：<https://docs.mesa3d.org/drivers/panfrost.html>
- PanVK 支持 Vulkan 1.4 通告（含 V9 完全缺支持的表述）：<https://test.www.collabora.com/news-and-blog/news-and-events/panvk-now-supports-vulkan-1.4.html>

### 2.4 ARM Mali DDK 的假模型（NO_MALI）

- `mali_kbase_model_dummy.c`（Google Android 内核仓库）：<https://android.git.googlesource.com/kernel/google-modules/gpu/+/fddc4666fd6cf021c0b2bd496f3ca5a857703926/borr_mali_kbase/backend/gpu/mali_kbase_model_dummy.c>
- `mali_kbase_ioctl.h`（用户态 ABI）：<https://nest-open-source.googlesource.com/manifest_repos/mali-driver/+/0bde759bc5682bba7de558e308410bbc6d08d2d8%5E%21/dvalin/kernel/include/uapi/gpu/arm/midgard/mali_kbase_ioctl.h>
- 「Emulate Mali on QEMU」实践记录：<https://1nzag.github.io/study/linux_kernel/android/emulate_mali_on_qemu>
  （要点：`CONFIG_MALI_NO_MALI` + 注释 `asm/arch_timer.h` + **自加 `platform_device`**（因为驱动只注册 platform_driver）→ `/dev/mali0` 出现；**不出画**）

### 2.5 ARM Fast Models 的 Mali 模型

- Fast Models：用 GRM 建模 GPU 系统：<https://developer.arm.com/documentation/100965/1100-00/Graphics-Register-Model/Using-Graphics-Register-Model/Modeling-a-GPU-system-with-GRM>
- GRM 与 Android 图形栈集成：<https://documentation-service.arm.com/static/5f48caf06e73485d721e97d7>
- GGA+GRM 集成 Mali 驱动**必须自写 libnwhal**：<https://documentation-service.arm.com/static/60dda7f70320e92fa40b5b62>
- Mali G710 模型默认输出渐变图案：<https://documentation-service.arm.com/static/6229d3bd8804d00769e9cd28>
- Mali_G710/G715 组件清单：<https://documentation-service.arm.com/static/682771d48f79851ff2c3ed18>
- GGA 弃用公告：<https://developer.arm.com/documentation/100965/1122/Graphics-Acceleration-in-Fast-Models/Deprecation-of-GGA>

### 2.6 QEMU 设备模型机制（写 Mali 设备模型的最小集）

- `hw/display` 目录（无 Mali 设备）：<https://gitlab.com/qemu-project/qemu/-/tree/master/hw/display>
- vhost-user-gpu 协议（DMABUF_SCANOUT/UPDATE，UNIX socket + ancillary fd）：<https://www.qemu.org/docs/master/interop/vhost-user-gpu.html>
- virtio-gpu-rutabaga 提案：<https://patchew.org/QEMU/20240605152832.11618-1-weifeng.liu.z@gmail.com/>
- gfxstream + rutabaga（v5）：<https://patchwork.ozlabs.org/project/qemu-devel/cover/20230815003526.631-1-gurchetansingh@chromium.org/>
- vDRM / DRM native context（v4）：<https://patchwork.ozlabs.org/project/qemu-devel/cover/20241110221838.2241356-1-dmitry.osipenko@collabora.com/>
- The memory API（`memory_region_init_io`/`AddressSpace`/`MemoryListener`/`EventNotifier`）：<https://www.qemu.org/docs/master/devel/memory.html>

### 2.7 宿主桥接踩坑（Apple Silicon / venus）

- Haven：Android venus 工程记录（R1–R8）：<https://glasshaven.github.io/Haven/gpu-acceleration.html>
  （minigbm 分配墙 → AHB 分配器；`VN_PERF=no_fence_feedback` 修 fence 挂死；virglrenderer `vkr_device_destroy` mutex 生命周期；**vtest host-visible 内存需 `dc cvac` 刷 cache**）
- Collabora：无硬件写开源 GPU 驱动（M1 Apple IOMMU 16KB 页 vs 4096 假设；"RE 与驱动开发必须一起做"）：<https://www.collabora.com/news-and-blog/blog/2022/01/27/writing-an-open-source-gpu-driver-without-the-hardware/>

### 2.8 宿主可用 Mali 板（路线 C 的支撑）

- `drm/panfrost` 支持 MT8370（Genio 700，Mali-G57）：<https://lkml.org/lkml/2025/1/30/394>、<https://lkml.org/lkml/2025/5/7/1377#1>

## 3. 对方案的直接映射

| 调研结论 | 对方案的影响 |
|---|---|
| 1/2/3 | 路线 A 的 **T3（ISA 翻译到 Apple GPU）不作主线**；A 保留为**控制面仿真 + 规格书**价值 |
| 4 | 路线 E 只能覆盖 **GLES 侧**，Vulkan 在 v9 上无解（§3.5 已修正） |
| 5/8 | 设备模型要自己写；但可复用"NO_MALI + platform_device"的既有实践做**语义验证**（= 本方案路线 B） |
| 6 | `mali_kbase_model_dummy.c` 升格为**设备模型规格书**（本方案 §2.7/§4 已按此定位） |
| 7 | 路线 F 不可直接落地（§3.6） |
| 9 | **R6**（§6）：需要真 Mali 做 oracle → 强化路线 C |
| 2.6 | §5.3 的 QEMU 机制清单 |
| 2.7 | §5.4 的踩坑清单 |
