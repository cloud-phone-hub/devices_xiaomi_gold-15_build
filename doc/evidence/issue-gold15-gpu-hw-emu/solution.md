# issue-gold15-gpu-hw-emu —— gold-15「GPU 硬件仿真 + 桥接宿主 GPU」可行性取证

> 状态：**取证完成（2026-10-12）**，方案文档 = [../../gpu-hw-emu.md](../../gpu-hw-emu.md)
> 关联：现行路线 [gpu.md](../../gpu.md)、既有图形证据 [../issue-gold15-vendor-gpu/solution.md](../issue-gold15-vendor-gpu/solution.md)

---

## 1. 问题

现行 gold-15 图形路线（[gpu.md](../../gpu.md) §7）走的是「**自编上游 kbase + NO_MALI 内核内软模型 + 大量驱动补丁**」，
已达成 L1 与 B2 v1（RT 填色），但：

1. 它**修改了内核侧驱动实现**（`wire-kbase.sh` **2127 行**、6 组 kbase 补丁），与"不修改厂商实现"的目标不符；
2. 出画停留在 **VNC 静态灰 `#888888` 99.3%**，且 `nativehang` 重启链未断（[../issue-gold15-vendor-gpu/log-pass.txt](../issue-gold15-vendor-gpu/log-pass.txt) run81-83）；
3. 用户新目标：**探讨 GPU 硬件仿真方案**——让仿真出来的"GPU 硬件"承担驱动面对的一切，
   **不改厂商实现**，并把这台仿真 GPU **桥接到宿主 GPU**（Apple M4 Pro）。
4. 但"硬件仿真"到底要仿真到什么程度、厂商栈能不能不改而活着、宿主 GPU 能不能真被用上，**此前没有量化依据**。

本轮要回答的就是这四个问题的**可判定版本**，并给出分档路线与门。

---

## 2. 根因（决定性发现）

### 2.1 "不改厂商二进制"在当前内核上不可能——不是 vermagic 问题，是符号 CRC 问题

- 厂商 `mali_kbase_mt6833_r49.ko` vermagic 为 `…-g4d05d8779bfd-4k`，官方内核（boot.img）为 `…-ge56cf6b09cca-ab15511674-4k`，**两者不同**；
- 但内核源码 `same_magic()` 在 `has_crcs` 时**忽略第一个空格之前的版本串**（`kernel/source/kernel/module/version.c:77-88`）→ 字符串不同**不是**障碍；
- 真正的门是**逐符号 genksyms CRC**。实测厂商图形/平台闭包（**选取的 14 个** stock 模块：kbase、mgm、prot_alloc、ged、gpufreq×2、gpu_hal、gpu_qos、sspm、iommu_gz、memory-amms、mtk-mbox、met_gpu×2）**1005 条符号引用**里：
  **仅 207 条与当前 CDH 内核 CRC 一致**、232 条不一致、**45 条在内核里根本不存在**（`KREE_*`/`aee_rr_*`/`mtk_emimpu_*`/`mtk_get_gpu_*_symbol`/`drm_register_fps_chg_callback`…）。
- → 「不改厂商内核模块」= 必须换成 **MTK 官方内核源码基座**（并重建出 CRC 一致的内核），**并连带仿真整条 MTK 平台面**（MFG/SCPSYS/PMIC/efuse/EMI/TEE/SSPM）。

### 2.2 "GPU 硬件仿真"不必从零——现行路线用的就是 ARM 的 GPU 模型

- 现行内核编译形态 `--disable MALI_REAL_HW --enable MALI_NO_MALI --set-str MALI_NO_MALI_DEFAULT_GPU tNAx`（`kernel/build/build-kernel.sh:202-204`）；
- 即"虚拟硬件"本体 = ARM 上游 kbase 的 `backend/gpu/mali_kbase_model_dummy.c`（寄存器读写 switch + 槽状态机 + `gpu_model_raise_irq`）；
- kbase 的 MMIO 抽象只有一处落点：`kbase_reg_read/write32/64`（`kbase/hw_access/Kbuild:29-33` 二选一：`real_hw` → `readl/writel`；否则 → `midgard_model_read/write_reg`）。
- → **升级路径清晰**：把内核内模型"提升"为真正的设备模型（`REAL_HW` + MMIO 窗口 + 3 条中断），
  于是**驱动侧补丁全部消失**，自定义只存在于硬件侧。

### 2.3 宿主 GPU 桥接的阻塞点不在宿主——在"Mali ISA → 宿主可执行工作"

- 宿主侧通路**已经存在**：darwin = QEMU 内嵌 virglrenderer（`venus=on`）+ `virgl_render_server` + MoltenVK → Metal；
  linux = `vhost-user-gpu` + 独立 renderer 进程（[Display.md](../../../../../../../core/host/daemon/Display.md) §2.2/§2.3）；
- 但 Mali 的设备级工作是**硬件格式的 job chain**（kbase 内核完全不解析，只把 jc 指针写进 `JS_HEAD_NEXT`），
  要真正渲染必须做 **Mali Valhall ISA → 宿主可执行格式**的翻译；公开生态**没有 Mali 反编译器**（panfrost 只有正向编译器 + 反汇编器）→ **研究级**；
- 例外：**宿主换成同族真 Mali 板（panfrost/panthor）**时，ISA 同族可直接执行/重编码，翻译退化为描述符重写 → **把研究级风险降为工程风险**。

---

## 3. 证据

### 3.1 原始数据（本目录 `raw/`）

| 文件 | 内容 | 关键数字 |
|---|---|---|
| [raw/loadability-matrix.txt](raw/loadability-matrix.txt) | 14 个厂商模块 × 当前 CDH 内核的符号级可加载性 | 1005 引用 / 207 CRC 一致 / 232 不符 / 126 兄弟模块 / 45 缺失 |
| [raw/official-modinfo.txt](raw/official-modinfo.txt) | 官方模块 `.modinfo`（vermagic/alias/depends）+ 未定义符号分类 | `mali_kbase` 406 未定义、46 条 MTK；`depends: mtk_gpufreq_wrapper_legacy,ged,mtk_gpu_hal,mrdump,sspm,mtk_gpu_qos` |
| [raw/official-kernel-config-key.txt](raw/official-kernel-config-key.txt) | 官方 `boot.img` 内核内嵌 config（IKCFG）关键项 | `MODVERSIONS=y`、`MODULE_SIG=y`、`VIRTIO=y`、`VIRTIO_PCI/BLK/CONSOLE=m`、**无 VIRTIO_NET/INPUT/GPU/SND** |
| [raw/official-dtb-gpu-nodes.txt](raw/official-dtb-gpu-nodes.txt) | 官方 DTB 的图形相关节点全文 | `mali@0x13000000+0x4000`、IRQ 364–368（GPU/MMU/JOB/EVENT/PWR）、gpufreq 15 个 reg 区、scpsys `mfg1/mfg2`、`mt6359vgpu` |
| [raw/external-prior-art.md](raw/external-prior-art.md) | 外部先例调研（含全部来源 URL） | ❌ 无公开"寄存器级仿真 Mali + 宿主 GPU"先例；❌ gfxstream/VirGL/Venus 是 API 重映射、结构上与"不改 Mali DDK"互斥；❌ **PanVK 对 Valhall v9 不支持**；❌ 无 Mali ISA 反编译器、Apple Air 无公开汇编器；⚠️ ARM Fast Models 只有 v10+ 且需自写 `libnwhal`；⚠️ **无真 Mali oracle 则正确性难收敛**；✅ QEMU 无 Mali 设备模型 |

### 3.2 源码级证据（file:line）

| 结论 | 依据 |
|---|---|
| vermagic 串被忽略、CRC 才是门 | `kernel/source/kernel/module/version.c:77-88`、`kernel/module/main.c:2127-2141` |
| MMIO 抽象只有一处落点 | `kbase/hw_access/Kbuild:29-33`、`kbase/hw_access/backend/mali_kbase_hw_access_real_hw.c`、`.../model_linux.c` |
| 现行虚拟硬件 = ARM 软模型 | `kbase/backend/gpu/mali_kbase_model_dummy.c:1177/1560`（读写）、`:1099/1110/1113`（抛 JOB/GPU/MMU IRQ） |
| 设备模型只需 MMIO + 3 条 IRQ | `kbase/mali_kbase_core_linux.c:4345-4385`（`registers_map`）、`:439-475`（`get_irqs` byname JOB/MMU/GPU）、`kbase/backend/gpu/mali_kbase_irq_internal.h:27-29`（tag） |
| **`RESET_COMPLETED` 是 probe 硬依赖** | `kbase/backend/gpu/mali_kbase_pm_driver.c:3296-3400`、`kbase/mali_kbase_defs.h:79`（500 ms 超时） |
| GPU_ID 身份要求 | `kbase/backend/gpu/mali_kbase_model_dummy.c:276`（上游值 `9,0,8,1,…`）vs `wire-kbase.sh:114-145`（补丁改 `9,0,0,3,0,1,0` = `0x09030010`） |
| WA 固件风险（`valhall-1691526.wa`） | `kbase/mali_kbase_dummy_job_wa.c:33/220-259`、`kbase/backend/gpu/mali_kbase_device_jm.c:325`、`:220-226`（`TTRX_3485`）；r0p1 或 impl_tech=SOFTWARE 可绕开 |
| 现行驱动改动规模 | `kernel/modules/mali_vgpu/wire-kbase.sh`（2127 行） |
| 宿主 GPU 通路 | `core/host/daemon/qemu/qemu.go:82-105`（darwin virgl+venus）、`:235-243`（linux vhost-user-gpu）、`run/run.go:509-512`（render server + MoltenVK）、`qemu/patch/virglrenderer/*.patch`（21 个，含 venus host-pointer） |

### 3.3 复现命令

见 [../../gpu-hw-emu.md](../../gpu-hw-emu.md) 附录 A；工具已固化在 [tools/](tools/)（`analyze_ko.py`/`loadability_matrix.py`/`parse_boot.py`/`fdt_dump.py`，均无第三方依赖）。
判据现场记录：[log-pass.txt](log-pass.txt)；被证伪路径的记录：[log-fail.txt](log-fail.txt)。

---

## 4. 改动

本轮为**只读取证 + 文档产出**（用户 2026-10-12 明确："**只出方案文档，不改代码**" / "**列出所有路线和方案**"）：
**未改动**任何构建/运行配置、内核源码、厂商文件、QEMU/virglrenderer 源码。新增文件：

```
devices/xiaomi/gold-15/build/doc/gpu-hw-emu.md                        # 方案主文档（新增；4 大家族 13 条路线全清单）
devices/xiaomi/gold-15/build/doc/evidence/issue-gold15-gpu-hw-emu/
├── solution.md                                                       # 本文件
├── log-pass.txt / log-fail.txt                                       # 判据现场
├── raw/{loadability-matrix,official-modinfo,official-kernel-config-key,official-dtb-gpu-nodes}.txt
├── raw/external-prior-art.md                                         # 外部先例调研（含来源 URL）
└── tools/{analyze_ko,loadability_matrix,parse_boot,fdt_dump}.py      # 只读取证脚本（不参与构建）
```

另：`build/doc/README.md` 的文档索引与证据索引各加一行（沿用既有索引风格）。

**round 3 追加**（同属本 issue 的取证产物）：
- `build/doc/gpu-hw-emu-spec.md`（设备模型实现规格，T1/T2 可落地）；
- `raw/kbase-regmap-v9_0.md`（**691 条**权威寄存器表，机器抽取）；
- `tools/extract_regmap.py`（只读抽取器；解析 `kbase_regmap_jm.c` 的继承链并剔除 v9.0 作废项）。
  抽取结果已用**反向校验**核对文档中每一个手写偏移：发现并修正 4 处（`JS*_FEATURES` 实际在 `0x0C0+n*4` 而非 `0x100`；
  AS 枚举名为 `ST1MMU__AS<n>`；`TIMESTAMP_OFFSET` 不在 v9.0；`SHADER_PWRFEATURES` 不在 v9.0）。
- **新发现**：stage-2 MMU 寄存器在 `0x10000-0x107F8`，**超出官方 DT 声明的 0x4000 窗口** → 设备模型窗口须 ≥0x10800。
分析中间产物在 `out/tmp/gold-hw-emu/`（官方模块/内核/DTB 解包），可随时重建。

**路线清单（用户要求"列全"）**——文档 §0.3 + §3 已按家族列全 13 条：

| 家族 | 路线 |
|---|---|
| I 硬件仿真 | A（QEMU 设备模型）/ B（客机内核内模型）/ C（宿主换真 Mali 板，首选）/ D（stock MTK 内核+全平台仿真）/ F（ARM Fast Models） |
| II API 重映射与换栈 | E1（Mesa virgl+venus，保底首选）/ E2（panfrost+panvk，v9 无 Vulkan）/ H（gfxstream+自研 ICD）/ J（用户态 API 拦截，灰区高性价比）/ K（ioctl 层假 mali0 = 现状） |
| III 不虚拟化 GPU | G1（真机云化）/ G2（Android 容器）/ L（只虚拟显示与输入） |
| IV 兜底 | I（客机软件渲染）/ M（只做检测面） |

---

## 5. 验证

| 判据 | 结果 |
|---|---|
| 厂商闭包符号可加载性可量化 | ✅ 1005 引用逐条分类（`raw/loadability-matrix.txt`） |
| 官方内核版本/配置可取证 | ✅ `Linux version 6.6.118-android15-8-ge56cf6b09cca-ab15511674-4k`，内嵌 config 211 851 字节成功解压 |
| 官方硬件契约可取证 | ✅ DTB 解析出 `arm,mali-valhall` 节点 + 5 条中断 + MFG/SCPSYS/PMIC 依赖 |
| 设备模型接口面可枚举 | ✅ kbase r49p0 源码逐项定位（寄存器/中断/MMU/电源域，file:line） |
| 宿主 GPU 通路存在 | ✅ `out/qemu/dist/darwin/{bin/qemu-system-aarch64,libexec/virgl_render_server,lib/libMoltenVK.dylib}` + `-device help` 无 Mali 设备模型 |
| 方案分档与门可落地 | ✅ [gpu-hw-emu.md](../../gpu-hw-emu.md) §3/§4/§6 |
| 外部先例可判定 | ✅ 调研完成（[raw/external-prior-art.md](raw/external-prior-art.md)）：无公开先例；PanVK v9 无支持；ARM 模型仅 v10+；QEMU 无 Mali 设备模型 |

---

## 6. 遗留

1. **用户需裁决三问**（[gpu-hw-emu.md](../../gpu-hw-emu.md) §9）："不改厂商实现"取 V1 还是 V2；出画目标 T1/T2/T3；宿主能否换 ARM+Mali 板。
2. **路线 C 的宿主选型**未做（需确认 panfrost/panthor 对候选板卡的 Valhall v9 支持度与 job chain 兼容矩阵；候选：MT8370/Genio 700、MT8186）。
3. ~~**job chain 内存格式**（设备模型 R4）尚无实测样本~~ → **round 2 已完成取证并降级**：
   见 [../issue-gold15-gpu-jm-format/solution.md](../issue-gold15-gpu-jm-format/solution.md) ——
   `jc` 由用户态 DDK 传入、kbase 不解析描述符；作业类型 = `JS<n>_FEATURES` 位号（与实测 Fragment Type=9 自洽）；
   **v9 Fragment Job → FBD → RT 布局已被项目解出并落地**；设备模型仅剩"页表遍历 + chain 遍历"两项确定性工作
   → **T1/T2 = GO、T3 = NO-GO（Apple Silicon 宿主）**。
4. **MTK 官方内核源码**（路线 D 前置）尚未获取/验证可用性；CRC 预言机脚本已就绪（可用厂商模块 `__versions` 作为对照）。
5. ~~外部先例调研~~ **已完成**（见 [raw/external-prior-art.md](raw/external-prior-art.md)）：**"寄存器级仿真 Mali + 桥接宿主 GPU"无任何公开先例**；
   PanVK 对 Valhall v9 无支持；ARM Fast Models 只有 v10+ 且需自写 `libnwhal`；**"无真 Mali oracle 则正确性难收敛"** →
   结论已并入方案文档 §3.5/§3.6/§3.7、§5.4、§6(R6/R7)、§8、§9。
6. ~~**下一轮首件事已按调研建议改为"否定性验证"**~~ → **round 2 已完成**（见上条与
   [../issue-gold15-gpu-jm-format/](../issue-gold15-gpu-jm-format/solution.md)）；方案文档 §8 的下一轮优先级已据此更新
   （① 设备模型规格表；② E1/J 出画方案；③ T3 剩余取证与路线 C 选型——后两者取决于 §9 第 2/3 问的裁决）。
