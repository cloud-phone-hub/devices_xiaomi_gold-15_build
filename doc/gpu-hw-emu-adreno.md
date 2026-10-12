# 把 Qualcomm Adreno 纳入考虑：与 Mali 路线的对照与结论

> 触发：用户提问"如果还有考虑高通 GPU Adreno 呢"。
> 定位：对 [gpu-hw-emu.md](gpu-hw-emu.md) 的**跨厂商扩展**——把"gold-15（Mali-G57）GPU 硬件仿真 + 桥接宿主 GPU"
> 的结论放到 Adreno 语境下重新判定，并区分三种完全不同的诉求（§0 第一张表）。
> 本文件含**本地实证**（仓库内已有 Adreno 设备）与**外部先例**（含 Adreno 硬件仿真的公开工作）。

---

## 0. 先分清"考虑 Adreno"是哪种诉求（结论完全不同）

| 诉求 | 判定 | 一句话理由 |
|---|---|---|
| **(i) 客机换成/加入 Adreno 设备**（如 CDH 已有 15R-16 / SM8750） | ✅ **最乐观**：**不需要仿真 Adreno 硬件** | 本仓库**已经在用 Mesa（`libEGL_mesa.so` + minigbm + venus/lavapipe）渲染到宿主 GPU**（§1 有实证）；Adreno 侧的开源驱动（freedreno/turnip）比 Mali 成熟得多 |
| **(ii) 把 Adreno 当宿主 GPU 来解决 gold-15（Mali）** | ❌ **不成立** | Adreno 无法执行 Mali 的 job chain/ISA；跨厂商等价于"换一个宿主后端的 ISA 翻译"（仍是 T3-S 的研究级问题） |
| **(iii) 为 Adreno 客机做"硬件仿真"** | ⚠️ **可行且比 Mali 有优势，但公开先例只做到"驱动级、不出画"** | 公开有：`msm` 在 mainline、freedreno/turnip 成熟、Adreno 寄存器/ISA 文档与工具齐全，以及 **39c3 的 LibAFL QEMU 高通 GPU 仿真**（§3）——**这是 Mali 完全没有的**；但该工作目标是 fuzz，**不做渲染桥接** |

> **对 gold-15 本体的直接影响：没有**（Adreno 与 Mali 不能互跑对方的作业）。
> **对 CDH 整体的影响：很大**——它决定了"每个厂商走哪条路"的分厂商策略（§4）。

---

## 1. 本地实证：CDH 的 Adreno 设备**早就是"Mesa + virtio → 宿主 GPU"**

设备：`devices/oneplus/15R-16`（CPH2769 / **SM8750 → Adreno 830**），底包 = lineage-23.2 virtio（`core/host/daemon/device/device.go:265+`）。
> 顺带一个有分量的新事实：**Adreno 830 正在被上游化进 mainline `msm`**——LKML `[PATCH 0/6] drm/msm: Support for Adreno 830 GPU`
> 与 `arm64: dts: qcom: sm8750-qrd: Enable Adreno 830 GPU`（2026-08）。
> 也就是说 **CDH 现有 Adreno 设备的 GPU 正被主线驱动接纳**，这直接抬高 §2 里 Adreno 的 C/D 路线可行性。

| 事实 | 证据 |
|---|---|
| 客机 EGL 驱动 = **`/vendor/lib64/egl/libEGL_mesa.so`**（Mesa，非 Adreno） | `devices/oneplus/15R-16/build/doc/evidence/issue-egl-common-v5-sphal/solution.md:14-21,39-42`（`libEGL: load_driver(/vendor/lib64/egl/libEGL_mesa.so)`） |
| 分配器 = **minigbm**（`allocator-service.minigbm_upstream`），非高通 gralloc | 同仓 `issue-sf-renderengine-primecache-prereq/solution.md:19-24,39` |
| 底包 vendor 的图形件 = `libEGL_mesa.so` / `libGLESv1_CM_mesa.so` / `libGLESv2_mesa.so` / `gralloc.default.so` / **`vulkan.virtio.so`（venus）** / **`vulkan.lvp_mesa3d.so`（lavapipe 软 Vulkan）** | `lineage-rom/lineage-23.2/super/vendor_a.img:/lib64/{egl,hw}`（本轮 `e2ls` 实测） |
| 该设备遇到的图形问题**都是库链/格式兼容**，不是 GPU 仿真：① sphal 命名空间缺 `android.hardware.graphics.common-V5-ndk.so` → 回填官方库；② OPLUS 预编译 hash 清单触发 10-bit（`RGBA_1010102`/`DRM_FOURCC_AB30`）缓冲 → minigbm 不支持 → 改清单 | 上述两份 evidence（均已修复并验证：`common-V5-ndk.so not found` = 0、`input buffer not gpu readable` = 0） |

**结论（重要）**：在 CDH 现有架构里，**Adreno 客机从来不用 Adreno 驱动渲染**——厂商 GPU 面只要"文件在位/可被探测"即可（与 note13 的 `android.hardware.gpu` 扫描面回填同思路）。
**Adreno 的"驱动级可替代性"远高于 Mali**：因为有成熟开源驱动（freedreno GL / turnip Vulkan），而 Mali Valhall v9 的 panvk **完全没有支持**（见 [gpu-hw-emu.md](gpu-hw-emu.md) §3.5 E2）。

---

## 2. 13 条路线在 Adreno 语境下的重排

| 路线 | Mali（gold-15）现状 | Adreno 语境 | 差异原因 |
|---|---|---|---|
| **E1** 客机 Mesa + virtio（GL+VK→宿主） | ✅ 可行（virgl + **venus**，与 panvk 无关） | ✅ **更成熟**（freedreno/turnip 一等公民） | Adreno 开源驱动成熟；Mali 仅靠 venus 绕开 panvk |
| **J** 用户态 API 拦截（身份对齐） | ✅ 已有投产实现（note13 GPU HAL） | ✅ 可复用同一套（interposer + 扫描面回填） | 机制与厂商无关 |
| **A/B** GPU 硬件设备模型（不出画→出画） | ⚠️ T1/T2 GO、**T3 研究级**；**无公开先例** | ⚠️ **有公开先例（驱动级、不出画）**，寄存器面文档更好 | Adreno：`msm`/kgsl 文档 + `freedreno/registers/adreno/a6xx.xml` + `afuc` + `cffdump` + `ir3`；Mali：仅 kbase + panfrost 逆向 |
| **C** 宿主换**同族**真 GPU 直通 | ✅ 可行（panfrost + MT8186 等） | ✅ **更现实**：`msm` 在 **mainline**，Adreno 板卡（DragonBoard 845c / RB5 等）能跑主线 Linux + freedreno/turnip | Mali 的 MTK kbase 是 out-of-tree，宿主侧只能靠 panfrost |
| **D** stock 内核 + 全平台仿真 | 6–12 人月，无先例 | ✅ **有强参考**：39c3 正是"高通内核虚拟化 + **>10 个自研 QEMU 设备**"的公开实践 | Adreno 有公开成果可复用/对照 |
| **T3**（ISA→宿主渲染） | ❌ 无 ISA 反编译器 | ⚠️ 仍研究级，但 ISA 资料**好得多**（ir3 编译器、a6xx 寄存器/ISA 文档、`afuc` 固件工具） | 两边都**没有** ISA→SPIR-V 翻译器 |
| G1/G2/L（不虚拟化 GPU） | ✅ 商业通行 | ✅ 同样成立 | 与厂商无关 |

---

## 3. 外部先例：**Adreno 已有公开的 GPU 硬件仿真**（Mali 没有）

**39C3（2025-12）演讲**：*"Build a Fake Phone, Find Real Bugs: **Qualcomm GPU Emulation** and Fuzzing with LibAFL QEMU"*
（<https://fahrplan.events.ccc.de/congress/2025/fahrplan/event/build-a-fake-phone-find-real-bugs-qualcomm-gpu-emulation-and-fuzzing-with-libafl-qemu>）

要点（原文摘录式归纳）：
- 目标：把 **Qualcomm Android kernel + KGSL 图形驱动**从零虚拟化到 QEMU，用于**大规模 fuzz**（替代真机农场）；
- 手段：*"partially reimplementing the hardware as a normal software to run on a computer"* —— 在 QEMU 里**实现了自定义 board（>10 个自定义设备）**，并**部分仿真了最新一代 Adreno GPU**；
- 成果：找到 GPU 内核驱动的一个**新的严重漏洞**；
- **范围边界**：目标是**驱动级覆盖与 fuzz**，**不是渲染**（无"把作业桥接到宿主 GPU 出画"的诉求）。

**这对本方案的意义**：
1. ✅ **Adreno 的硬件仿真有公开、可对标的工程先例**（含工程量参照：>10 个 QEMU 设备）；
2. ✅ 它验证了我们"设备模型只需覆盖驱动需要的寄存器/命令面"的判断（与 [gpu-hw-emu-spec.md](gpu-hw-emu-spec.md) 的 T1 同构）；
3. ⚠️ 但它**没有解决 T3**：不在宿主 GPU 上执行 Adreno shader。所以"硬件仿真 + 桥接宿主 GPU 出画"在**两条厂商路线上都仍是开放的研究级问题**。

其它可引用资产：

| 资产 | 用途 | 来源 |
|---|---|---|
| `msm`（Adreno 内核驱动）**在 mainline** | 宿主/客机的 Adreno 内核侧基础 | [MSM Display/Graphics Update (XDC 2023)](https://indico.freedesktop.org/event/4/contributions/194/attachments/164/232/XDC2023%20DRM%20MSM%20Display%20and%20Graphics%20Update-1.pdf) |
| freedreno **寄存器定义**（寄存器级权威） | 设备模型/解码器的字段来源 | [Mesa `src/freedreno/registers/adreno/a6xx.xml`](https://github.com/chaotic-cx/mesa-mirror/blob/52b5500e/src/freedreno/registers/adreno/a6xx.xml) |
| `afuc`（Adreno 固件汇编/反汇编） | 命令流/SQE 固件分析 | [Mesa `src/freedreno/afuc/README.rst`](https://android.googlesource.com/platform/external/mesa3d/+/3f91d819450386676ab7997b5c50320c3f248135/src/freedreno/afuc/README.rst) |
| ir3（freedreno 编译器）现状 | ISA 语义与工具链成熟度参照 | [“What's new in ir3” (XDC)](https://indico.freedesktop.org/event/10/contributions/422/attachments/244/330/ir3.pdf) |
| Turnip 的第三方 Android 发行版 | 说明 turnip 在 Adreno 上可用性广 | [Banners-Turnip releases](https://github.com/The412Banner/Banners-Turnip/releases/tag/v26.2.0-20260624-r10) |
| **Adreno 830（SM8750，= CDH 15R-16 的那颗）主线化** | Adreno 宿主/客机支持正在进入 mainline `msm` | [LKML `[PATCH 0/6] drm/msm: Support for Adreno 830 GPU`](https://lkml.iu.edu/hypermail/linux/kernel/2608.1/00592.html)、[`sm8750-qrd` 使能 Adreno 830](https://lkml.iu.edu/2608.1/00598.html)、[`sm8750: Add GPU and GMU nodes`](https://lists.freedesktop.org/archives/dri-devel/2026-August/587662.html) |
| **DragonBoard 845c**（Adreno 630）：AOSP 参考板，**开源图形 + mainline 内核** | 路线 C 的宿主候选（Adreno→Adreno） | [CNX Software 报道](https://www.cnx-software.com/2020/04/08/dragonboard-845c-android-aosp-reference-board-supports-open-source-graphics-mainline-kernel/) |
| Cuttlefish GPU 加速文档（gfxstream/Venus 等） | API 重映射路线的官方口径 | <https://source.android.com/docs/devices/cuttlefish/gpu>（本轮抓取超时，未引用其具体结论） |

---

## 4. 由此得到的分厂商策略（建议）

| 客机 GPU | 推荐主路线 | 理由 |
|---|---|---|
| **Adreno**（15R-16 / ace3-15 / 更多高通机） | **E1/J（Mesa + virtio/venus，宿主 GPU 出画）** | 已验证在跑；遗留是库链/格式兼容（已有 issue 范式）；**不必做 GPU 仿真** |
| **Mali（新世代，panvk 有支持）** | E1/J 同上 | Mesa 驱动可用 |
| **Mali Valhall v9（gold-15）** | E1/J（venus 补 Vulkan）或 A/B（T1/T2）+ **C（要出真画）** | panvk v9 无支持，故出真画只能靠 venus 或同族宿主 |
| 需要"驱动级研究/漏洞面" | **A/B（设备模型）** | Adreno 可直接对标 39c3；Mali 需自建且无先例 |
| 要求"stock 厂商栈 + 真出画" | **C（同族宿主）** | Adreno 侧更现实（mainline `msm` + 现成板卡） |

**一句话**：**"考虑 Adreno"最大的价值不是解决 gold-15，而是证明了两件事**——
① **"guest 用开源的客机驱动 + virtio/venus 打到宿主 GPU"是跨厂商通用且最省事的正解**（CDH 的 Adreno 设备已在这么做）；
② **"硬件仿真"在 Adreno 上是有公开先例的工程**（驱动级、不出画），在 Mali 上则是空白——这为"是否投入 Mali 设备模型"提供了一个现实的工作量标尺。

---

## 5. 若要把 Adreno 也纳入"GPU 硬件仿真"项目：最小路线图

1. **接口面取证**：kgsl（`/dev/kgsl-3d0` ioctl 面）+ `msm`（DRM）双路径；对照 39c3 的 board 设备清单；
2. **寄存器/命令面**：以 `src/freedreno/registers/adreno/a6xx.xml` 为权威，建"设备模型寄存器表"（可复用 [extract_regmap.py](evidence/issue-gold15-gpu-hw-emu/tools/extract_regmap.py) 的抽取思路）；
3. **命令流解码**：`cffdump`/`afuc`（SQE 固件）建立"链上对象"字典；
4. **宿主桥接**：优先 **C 类比（同族 Adreno 宿主）**，因为 `msm` 在 mainline；
5. **验收**：与 39c3 的"驱动级"目标对齐（先 T1 等价：驱动活着 + 覆盖），再谈渲染（T3）。

---

## 6. 证据与来源索引

| 事实 | 来源 |
|---|---|
| CDH 的 Adreno 设备用 Mesa 渲染（`libEGL_mesa.so`） | `devices/oneplus/15R-16/build/doc/evidence/issue-egl-common-v5-sphal/solution.md:14-21,39-42` |
| 分配器为 minigbm、10-bit 格式不支持导致 SF 崩溃 | `devices/oneplus/15R-16/build/doc/evidence/issue-sf-renderengine-primecache-prereq/solution.md:19-24,39` |
| lineage-23.2 底包图形件（Mesa/venus/lavapipe） | `lineage-rom/lineage-23.2/super/vendor_a.img:/lib64/egl`、`:/lib64/hw`（`e2ls` 实测） |
| 15R-16 设备规格（SM8750 / CMF 端口） | `core/host/daemon/device/device.go:265+` |
| Adreno 硬件仿真公开先例（LibAFL QEMU） | <https://fahrplan.events.ccc.de/congress/2025/fahrplan/event/build-a-fake-phone-find-real-bugs-qualcomm-gpu-emulation-and-fuzzing-with-libafl-qemu> |
| `msm` 主线化进展 | <https://indico.freedesktop.org/event/4/contributions/194/attachments/164/232/XDC2023%20DRM%20MSM%20Display%20and%20Graphics%20Update-1.pdf> |
| Adreno 寄存器定义 | <https://github.com/chaotic-cx/mesa-mirror/blob/52b5500e/src/freedreno/registers/adreno/a6xx.xml> |
| `afuc` 固件工具 | <https://android.googlesource.com/platform/external/mesa3d/+/3f91d819450386676ab7997b5c50320c3f248135/src/freedreno/afuc/README.rst> |
| ir3 现状 | <https://indico.freedesktop.org/event/10/contributions/422/attachments/244/330/ir3.pdf> |
| DragonBoard 845c（主线+开源图形） | <https://www.cnx-software.com/2020/04/08/dragonboard-845c-android-aosp-reference-board-supports-open-source-graphics-mainline-kernel/> |
| Mali 侧对应结论 | [gpu-hw-emu.md](gpu-hw-emu.md)、[gpu-hw-emu-routeC.md](gpu-hw-emu-routeC.md)、[gpu-hw-emu-e1j.md](gpu-hw-emu-e1j.md) |
