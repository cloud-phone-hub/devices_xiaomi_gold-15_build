# gold-15 GPU 硬件仿真方案 —— 不改厂商实现，把仿真 GPU 桥接到宿主 GPU

- 设备：Xiaomi Redmi Note 13 5G（codename `gold`，board `mt6833`，**Mali-G57 MC2**，Valhall v9（tNAx，arch 9.0.8；厂商 DDK 面身份 = `GPU_ID=0x09030010` / `product_id=0x09000003` / r0p1），**JM 架构**）
- 云机：`devices/xiaomi/gold-15`，宿主 macOS 15.3.2 / Apple M4 Pro（16 核 GPU，Metal 3）/ QEMU 11.0.2 + HVF
- 约束：**不修改厂商实现**（含义分层见 §1.1）；**GPU 硬件仿真要桥接到宿主 GPU**
- 前序文档：[gpu.md](gpu.md)（现行「自编 kbase + 虚拟后端」路线的完整实施记录，已到 L1 + B2 v1）
- 本轮取证证据：[issue-gold15-gpu-hw-emu](evidence/issue-gold15-gpu-hw-emu/solution.md)（含原始数据 `raw/` 与工具 `tools/`）

---

## 0. 结论先行

### 0.1 一句话结论

> **"真正的 GPU 硬件仿真"（虚拟 Mali 设备 + 厂商驱动不改）在 gold-15 上是"两段可做、一段研究级"的工程**：
> - **控制面（寄存器 / JM 槽 / MMU / 中断 / 电源域）工程可行**，而且**参考实现就在仓库里**——现行路线用的
>   `mali_kbase_model_dummy.c`（ARM 上游 NO_MALI 软模型）就是同一份寄存器语义的 C 语言版本；
> - **宿主桥接（把渲染落到 M4 Pro）可控**——宿主侧已有 `venus(Vulkan) → MoltenVK → Metal` 与
>   `virgl_render_server` 进程模型（[Display.md](../../../../../core/host/daemon/Display.md) §2.2），
>   设备模型只需产出"宿主能执行的工作"；
> - **执行面（把 Mali 作业真正渲染出来）是研究级**：唯一障碍是 **Mali Valhall ISA → 宿主 GPU 可执行格式**的翻译，
>   公开生态里**没有** Mali 反编译器（Mesa panfrost 只有正向编译器 + 反汇编器）。
>
> 因此本方案主张：**先交付"控制面硬件仿真 + 搬运/填充级宿主桥接"（能让不改的驱动栈活着并出部分画面），
> 把 ISA 翻译单独立项**；并且**优先考虑把宿主换成真 Mali 板**（同 ISA 家族直发），这可以把研究级攻坚直接消掉。
>
> **外部先例调研的补强（§3.7）**：① 公开生态里**确实没有任何**"寄存器级仿真 Mali + 桥接宿主 GPU"的先例；
> ② gfxstream/VirGL/Venus **都是 API 重映射**，要求客机跑它们自己的 ICD，**结构上与"不改 Mali DDK"互斥**；
> ③ **panvk（Vulkan）对 Valhall v9（= G57）完全不支持**——⚠️ 这只打死"客机 **panfrost/panvk**"变体（§3.5 **E2**），
> **不打死"客机 virgl + venus"变体（§3.5 E1）**：venus 是独立的 virtio-gpu Vulkan ICD，与 panvk 无关；
> ④ **ARM Fast Models 的 Mali 模型只有 v10+（G710/G715）、默认只出渐变图案、必须自写 `libnwhal` 后端、GGA 已弃用** → 不能直接落地；
> ⑤ 有实践者指出：**没有真 Mali 做 oracle，寄存器级仿真的正确性无法收敛** → 这条恰好**加强**了路线 C（宿主换真 Mali）的优先级。
>
> **一个必须先说清的事实（影响全部路线排序）**：本仓库**其它设备**（note13-15 等 lineage 底包）用的是
> **Cuttlefish 风格 Mesa 栈**——`minigbm` 分配器 + `hwcomposer.drm.so` + **`vulkan.virtio.so`(venus) + Mesa virgl(GL)**；
> 实测 **GL 与 Vulkan 都已通过 virtio-gpu 落到宿主 GPU（MoltenVK/Metal）并在出画**（1080x2400、滑动 46fps，
> [Display.md](../../../../../core/host/daemon/Display.md) §2.1–2.2，实例 39 实测）。
> gold-15 之所以卡住，是因为它**把这条可用链路换成了厂商 MTK 图形栈**（为满足检测面）。
> → 本方案的根本分叉是：**"要检测面（厂商栈）"还是"要出画（Mesa 栈）"**；§3.14 给出组合打法。
>
> **设备模型规格见 [gpu-hw-emu-spec.md](gpu-hw-emu-spec.md)**（round 3：691 条权威寄存器表 + 状态机 + 数据面 + 宿主接口）。
>
> **round 2 的否定性验证结论（§2.9）**：硬件仿真路线的最大未知量 **R4 已从"未知量"降级为"未移植量"**——
> job 描述符是**硬件格式**、作业类型有 kbase 权威枚举（Type 值 = `JS<n>_FEATURES` 位号），
> 而 **v9 Fragment Job → FBD → RT 的布局已被项目实测解出并用于 B2 v1 填色**；
> 设备模型只剩 **页表遍历 + chain 遍历** 两项确定性工作 → **T1/T2 = GO**；
> **T3 仍 NO-GO（Apple Silicon 宿主）**，因为 TILER/VERTEX/COMPUTE 描述符、图形状态与 **shader ISA** 仍未解。

### 0.2 为什么"照搬 stock 厂商二进制"这条路在当前内核上被证伪

本轮量化实测（[raw/loadability-matrix.txt](evidence/issue-gold15-gpu-hw-emu/raw/loadability-matrix.txt)）：

| 判据 | 实测 |
|---|---|
| 厂商图形/平台闭包（**选取的 14 个** stock 模块：kbase、mgm、prot_alloc、ged、gpufreq×2、gpu_hal、gpu_qos、sspm、iommu_gz、memory-amms、mtk-mbox、met_gpu×2）导入符号引用 | **1005 条** |
| 在**当前 CDH 内核**（6.6.127-4k-cdh）上 CRC 一致 | **207 条（20.6%）** |
| CRC 不一致（类型签名不同） | **232 条** |
| 由闭包内其它厂商模块提供 | 126 条 |
| **内核里根本不存在**（MTK 内部符号） | **45 条**：`KREE_*`（TEE）、`aee_rr_*`（AEE/mrdump）、`mtk_emimpu_*`（EMI MPU）、`mtk_get_gpu_*_symbol`、`mt_get_abist_freq`、`drm_register_fps_chg_callback`、`ipi_monitor_dump`、`mtk_dma_buf_set_name` … |

→ **"stock `mali_kbase_mt6833_r49.ko` 直接 insmod 到现行云机内核"在符号层就不可能**（不是 vermagic 字符串问题，见 §2.4 的 `same_magic` 语义）。
所以"不改厂商实现"若指**内核侧二进制也不改**，则**必须换 MTK 官方内核基座**，并连带仿真整条 MTK 平台面（§3 路线 D）。

### 0.3 路线总览（全部路线，详细论证见 §3；分组 = 思路家族）

**家族 I · 硬件仿真类（不改厂商代码，仿真"GPU 硬件"）**

| 路线 | 一句话做法 | 改 vendor？ | 需仿真硬件？ | 出画 | 工期 | 风险 | 结论 |
|---|---|---|---|---|---|---|---|
| **A** | QEMU/vhost-user 里的 **Mali-JM 设备模型**（MMIO+JM+MMU+中断），渲染交宿主 | 否（仅剩 §2.8 的 ABI 补丁） | 是（GPU 寄存器面） | **T1/T2 = GO**（§2.9） / T3 研究级 | 1.5–3 人月（控制面） | 中（T1/T2）/ T3 极高 | **T1/T2 档主线**（"硬件仿真"最纯正形态） |
| **B** | 同上，但模型写在**客机内核内**（REAL_HW + 自带 MMIO 区） | 否（同上） | 是 | 同 A | 比 A 少 ≈20% | 中 | **A 的先行验证档**（先 B 通语义，再搬进 QEMU） |
| **C**（专项：[routeC](gpu-hw-emu-routeC.md)，判定 **GO**） | 宿主换**同族真 Mali 板**（panfrost/panthor），设备模型转发作业 | 否 | 是（同 A） | **完整**（同族 ISA 免翻译） | 控制面同 A + 1–2 人月 | 中 | **"stock 栈 + 真出画"的唯一高把握路径** |
| **D** | 换 **stock MTK 内核**（CRC 对齐）+ 仿真整条 MTK 平台面 | 否（连 .ko 都用原厂） | 是（GPU + MFG/PMIC/EMI/SSPM/TEE + 显示） | 同 A | 6–12 人月 | 高 | 目标最纯正、代价最大（V1 档专用） |
| **F** | 买 **ARM Fast Models** 的 Mali GRM/GGA 模型 | 否 | 是（ARM 提供） | 需自写 `libnwhal`；默认渐变图案 | 高 | 高（无 v9、GGA 弃用、无 macOS） | **不可直接落地** |

**家族 II · API 重映射 / 换栈类（不仿真硬件）**

| 路线 | 一句话做法 | 改 vendor？ | 出画 | 工期 | 风险 | 结论 |
|---|---|---|---|---|---|---|
| **E1**（专项：[e1j](gpu-hw-emu-e1j.md)） | 客机改回 **Mesa virtio 栈**（virgl GL + venus VK）+ virtio-gpu | **等效替换**（文件可保留） | **完整**（GL+VK，宿主 GPU） | 1–2 人月 | 低 | **保底与对照的首选**；代价 = 不用厂商栈 |
| **E2** | 客机用 **panfrost/panvk + panthor** 驱动 | 替换 | GL 可、**VK 在 v9 无解** | 中高 | 中高 | 不推荐（不如 E1） |
| **H** | **gfxstream/rutabaga + 自研 ICD**（像 Cuttlefish 对 Adreno 那样） | **实质是（要求客机用它的 ICD）** | 完整（但需自研 ICD） | 高 | 高 | 与"用不改的 Mali DDK"**结构互斥** |
| **J**（专项：[e1j](gpu-hw-emu-e1j.md)；**本仓库已有投产实现**） | **用户态 API 拦截**：LD_PRELOAD/linker namespace 劫持厂商 so 的 EGL/GLES/VK 入口 → 转发宿主 venus/virgl | **文件不改**（运行时改行为） | 完整（宿主 GPU） | 1–2 人月 | 中（检测面可发现） | ⭐ **值得评估的"高性价比灰区"**（厂商 so 仍在 maps 里，但不再被用来渲染） |
| **K** | **ioctl 层假 mali0**：在内核里实现"像硬件一样"的 mali0（= 现状 V0 路线） | 否（改的是我们自己的内核模块） | 否（假完成） | 已投入 | 中 | **现状**：L1 达成、B2 v1 静态灰、watchdog 链未断 |

**家族 III · 不虚拟化 GPU 类**

| 路线 | 一句话做法 | 改 vendor？ | 出画 | 工期 | 风险 | 结论 |
|---|---|---|---|---|---|---|
| **G1** | **真机云化**：同款 MT6833 真机 + 串流（不做 GPU 虚拟化） | 没必要（原生） | **完整+性能最好** | 中（工程/运维） | 低（技术）/高（成本规模） | **商业云手机的通行做法**；放弃"单机多实例"密度 |
| **G2** | **Android 容器**（宿主=同款真机内核，容器共享内核） | 没必要 | **完整** | 中 | 中（隔离/多租户） | 与 G1 同族，密度略好 |
| **L** | 宿主=真机/真板，**只虚拟化显示与输入**（GPU 原生） | 没必要 | **完整** | 低–中 | 低 | G1 的工程形态（推流/输入注入已在本仓库有积累） |

**家族 IV · 兜底类**

| 路线 | 一句话做法 | 出画 | 结论 |
|---|---|---|---|
| **I** | 客机**软件渲染**（llvmpipe/SwiftShader），不碰 GPU | 能出，但慢 | 保可用性；不满足"桥接宿主 GPU" |
| **M** | **只做检测面**（厂商栈在位/被加载，不追求出画） | 不出 | 现状路线的极端形态；仅当验收=检测面时成立 |

### 0.4 通用性判定：哪些方案能跨机型/跨宿主复用

> 用户提问归纳："保留厂商 so 并让它**真的工作**（参与渲染），是不是没有通用性？" —— **是**。
> 根因一句话：**让厂商 so 工作 = 满足芯片私有的硬件契约**（描述符格式 + ISA + 能力属性 + 内核驱动 ABI），
> 这个契约**不可移植**。通用性只能来自两个方向：**绕过契约**（换客机用户态驱动）或**用同款硬件履行契约**（同族宿主）。

| 方案 | 客机侧通用性 | 宿主侧通用性 | SoC/器件闭包 | 结论 |
|---|---|---|---|---|
| **A/B** 设备模型（T1/T2） | 仅该 **GPU 家族**（Mali JM 链解析可跨 Bifrost/Valhall 复用，但每个家族仍要一套） | 宿主任意（但只能做 fill/copy 等非可编程工作） | 与 SoC 无关（V2 档） | **半通用**（框架通用、契约不通用） |
| **C** 同族宿主直通 | 同代 JM 内可复用 | ❌ **只对同族 Mali 宿主**（且 ISA 小版本/型号越近越好） | 与 SoC 无关 | **不通用于宿主**（换宿主＝重做） |
| **D** stock 内核 + 平台仿真 | ❌ 每 SoC 一套（MFG/SCPSYS/PMIC/efuse/EMI/TEE…） | 同 A/B | ❌ **强绑 SoC** | 最不通用 |
| **T3-S** ISA→SPIR-V | ❌ 每个 ISA 一代一套反编译器 | 任意 Vulkan 宿主 | — | 宿主通用、客机不通用；**研究级** |
| **E1/J** 换客机 Mesa + venus/virgl | ✅ **任意客机 GPU**（Mesa 有驱动即可） | ✅ **任意宿主 GPU**（含 Apple Metal） | 与 SoC 无关 | **最通用**（代价：厂商 so 不参与渲染） |
| **J 的身份面**（interposer + 扫描面回填） | ✅ 模式可跨 Mali 机型复用（每机型一份 `gpu.json` 配置） | 与宿主无关 | 与 SoC 无关 | **通用模式**（但只解决"看起来像"，不产像素） |
| **G1/G2/L** 真机/容器 | 绑该机型 | 不适用 | 绑机型 | 不虚拟化，天然不通用 |

**可复用的资产（"半通用"到底通在哪）**：
- **设备模型/桥接的框架**：QEMU sysbus/vhost-user 骨架、页表遍历（kbase MMU 编码跨代基本一致）、
  JM 链解析（Bifrost/Valhall JM 同构）、T2 的宿主 `fill/copy` 通道；
- **身份与探测面模式**：note13 的 `android.hardware.gpu`（interposer + 扫描面回填 + 属性）是**模式级复用**，
  换机型只需换 `gpu.json` 的 targets/文件清单；
- **宿主 GPU 通路**：`venus/virgl → MoltenVK/Metal` 与宿主 GPU 型号无关。

**对产品决策的直接含义**：
1. **单机型云机**（如只做 gold-15）："每机型一套内核 + 设备模型/宿主绑定"是可以接受的成本结构（与现在每设备一个 `build/` 目录同构）；
2. **多机型共用一套方案**：只有 **E1/J**（换客机 Mesa + venus）能跨客机 GPU × 跨宿主 GPU 成立 —— 这也是 CDH 目前对 note13/15R/findx9 的既有做法；
3. **"厂商 so 真渲染"与"通用性"本质互斥**：前者要求履行芯片私有契约，后者要求不依赖该契约。
   折中形态 = **E1/J 出画 + J 的身份面保留厂商 so**（探测面像真机、渲染走宿主），需要"真·厂商渲染"的机型再单独立项走 C。

---

> **排序结论**：① 要"**stock 厂商栈 + 真出画**"→ **C**（唯一高把握），其次 D（代价大）；
> ② 要"**硬件仿真闭环（T1/T2）**"→ **B → A**；
> ③ 要"**今天就出画**"→ **E1**（或用 J 保留厂商文件的运行时替换）；
> ④ **不建议**把"A + ISA 翻译到 Apple GPU（T3）"当主线（无先例、无反编译器、无 oracle）；
> ⑤ F/H 基本可判死；G1/G2/L 是"不做 GPU 虚拟化"的正解（商业上已被证明）。
>
> **跨厂商对照（Qualcomm Adreno）见 [gpu-hw-emu-adreno.md](gpu-hw-emu-adreno.md)**：Adreno 客机在 CDH 里**已在用 Mesa+venus 打到宿主 GPU**（不必仿真），
> 且 Adreno **有公开的 GPU 硬件仿真先例**（LibAFL QEMU，驱动级、不出画）——可作为"是否投入 Mali 设备模型"的工作量标尺。

---

## 1. 目标与边界

### 1.1 "不修改厂商实现"的三种解读（必须先钉死）

| 档 | 含义 | 技术后果 |
|---|---|---|
| **V0（现状）** | 不改厂商 **用户态 so**（名字/md5 不变）；内核侧可自编 kbase 并打补丁 | 现行路线（[gpu.md](gpu.md) §7）：已 L1，B2 v1 只出静态灰 |
| **V1（严格）** | 厂商**内核模块二进制**也不改（stock `mali_kbase_mt6833_r49.ko` + `depends:` 闭包原样） | **必须**用 MTK 官方内核（符号 CRC 匹配）+ 仿真整条 MTK 平台面 → 路线 D |
| **V2（折中，本方案默认档）** | 内核侧**使用上游 ARM kbase 原样编译、零补丁**（`CONFIG_MALI_REAL_HW=y`，不再用 NO_MALI），厂商用户态 so 不动；硬件面由我们新增的**设备模型**承担 | 设备模型对 kbase 呈现"真硬件"（MMIO + 中断）；**所有自定义都在硬件侧，不在驱动侧** → 路线 A/B |

> 本轮结论按 **V2 为主线**推进；V1 作为"检测面二进制一致性"要求的可选项（路线 D）单列代价。

### 1.2 本轮工作边界（用户指令）

> **用户明确指示（2026-10-12）：本轮只产出方案文档，不改代码；并要求"列出所有路线和方案"。**
>
> 因此：
> 1. **不修改**任何构建/运行配置、内核源码、厂商文件、QEMU/virglrenderer 源码——本轮零代码改动；
> 2. 文档内所有"做法"都是**可执行的设计描述**（含 file:line 依据与验收判据），不是已落地的实现；
> 3. 证据目录里的 `tools/*.py` 是**只读取证脚本**（解析 `.ko`/`boot.img`/DTB/Module.symvers），供复现使用，不参与构建；
> 4. 路线清单见 §0.3 与 §3（家族 I–IV，共 **A/B/C/D/F、E1/E2/H/J/K、G1/G2/L、I/M**）。

### 1.3 硬约束与事实边界

1. 宿主 **Apple Silicon 无 Mali**，无法直通 Mali 硬件；宿主能执行的工作只有 **Metal（经 MoltenVK）/ macOS OpenGL(CGL)**，以及已有 virglrenderer 通路。
2. 客机是 **arm64 Android 15**，厂商图形栈是 **MEOW 包装 + Mali DDK r49**（[gpu.md](gpu.md) §1.2）。
3. 现行 QEMU（CDH 定制，11.0.2）**没有任何 Mali 设备模型**（`-device help` 实测只有 `virtio-gpu*` / `vhost-user-gpu-pci`）。
4. gold-15 的 GPU 是 **JM（Job Manager）** 架构，不是 CSF → 设备模型只需实现 **MMIO + 3 条中断 + GPU 内存里的 job chain 解析**（CSF 的 MCU 固件/共享接口区**不涉及**）。

### 1.4 厂商图形 so「生效」的内核前提（三层，逐层需求不同）

> 用户提问："gold-15 厂商 vendor 的图形栈 so 如果生效，是不是需要内核驱动支持？" —— **是**，
> 但"生效"要分三层，需求完全不同：

| 层次 | 是否需内核驱动 | 需要什么 | 现状/证据 |
|---|---|---|---|
| **L-a 文件在位 / 可被 dlopen**（探测面） | ❌ **不需要** | 只要 so 文件在、路径与 soname 对、SELinux 标签可读 | note13 的 `android.hardware.gpu` 扫描面回填即此层（[gpu-hw-emu-e1j.md](gpu-hw-emu-e1j.md) §1.3） |
| **L-b 初始化成功**（`eglInitialize` / DDK `Device::Initialize`） | ✅ **需要** | `/dev/mali0` 存在 + **内核上报的 GPU 身份**与 DDK 期望一致 + `KBASE_IOCTL_VERSION_CHECK` 握手 + 依赖模块（GED 等）可用 | 厂商 DDK 会拒绝错误身份：`E mali: ERROR: The DDK (built for 0x9000003 r0p1 status range [0..15]) is not compatible…`（[gpu.md](gpu.md):577）；把内核身份改成 `0x3 arch 9.0.0 r0p1`（product_id `0x09000003`）后四项计数 **16/16/32/46 → 0/0/0/0**（[gpu.md](gpu.md):287,614） |
| **L-c 真正渲染出画** | ✅✅ **需要（且不止 mali0）** | 上面全部 + **job 被真实执行**（真硬件 / 设备模型后端）+ dma-buf 内存路径（`dma_heap`/ion + Mali UMM） | 现状 `--disable MALI_REAL_HW --enable MALI_NO_MALI`（`kernel/build/build-kernel.sh:202-204`）→ 内核侧是"虚拟 Mali"：job 完成为假 → B2 v1 只出静态灰 |
| **L-d 合成上屏（HWC）** | ✅ **需要** | `/dev/dri/card0` + **MTK 语义 KMS**（+ cmdq/smi/m4u）；或改用 `hwcomposer.drm` | [gpu.md](gpu.md) §1.4 需求表：`hwcomposer.mt6833` 要 card0（MTK 语义）+ DDP/cmdq/smi；`libged` 要 `/dev/ged`；gralloc 要 ion/`dma_heap/mtk_*` |

**"内核支持"的范围比 `/dev/mali0` 大得多**（本会话实测）：

- 厂商 kbase 的模块依赖：`depends: mtk_gpufreq_wrapper_legacy,ged,mtk_gpu_hal,mrdump,sspm,mtk_gpu_qos`（+ `softdep: memory_group_manager`；`ged` 又 `depends: …,mediatek-drm,sspm`）；
- 未定义符号 **406** 个，其中 **46** 个是 MTK 专有（`ged_dvfs_*`、`mtk_get_gpu_*_fp`、`sspm_reserve_mem_*`、`aee_rr_*`…）；
- 在我们当前 CDH 内核（6.6.127）上，这 14 个厂商模块的 **1005 条符号引用只有 207 条 CRC 一致、45 个符号根本不存在**
  （[raw/loadability-matrix.txt](evidence/issue-gold15-gpu-hw-emu/raw/loadability-matrix.txt)）→ **stock `.ko` 连加载都不可能**。
- 另有**用户态↔内核 ABI 形状**问题：厂商 UK 11.46 发 **72 字节 atom**，上游 kbase 只认 64 字节 → 需补丁 12/12b/12c
  （与硬件无关，纯 ABI 配套）。

**三条推论（对方案选择的影响）**：

1. 若"不修改厂商实现"**只约束用户态 so**，则**内核侧必然要动**——真厂商 kbase（V1）/ 自编 kbase + 虚拟后端（现状 V2）/
   自研设备模型，三者都是"动内核侧"，不存在"两边都不动还能让厂商栈生效"的组合；
2. 反过来，**要内核侧也完全不动，就只能换掉用户态驱动**——这正是路线 **E1/J** 能"零内核改动"的原因：
   客机改用 Mesa（+ venus/virgl），它需要的内核面是 `virtio-gpu`/DRM，云机本来就有；
3. **纯身份/字符串层不需要内核**（`glGetString` 覆写、EGL 加载策略、文件在位）——这就是 note13 的
   interposer + 扫描面设计能在不碰内核的前提下让 App "看到 Mali"的原因。

**内核侧到底收到什么数据（绘制路径 ABI）**：见专文 [gpu-hw-emu-abi.md](gpu-hw-emu-abi.md) ——
ioctl 只传 `jc`（GPU VA）等**指针+元数据**，真正的绘制内容（job 链/FBD/RT/shader 二进制/顶点缓冲/状态）
全在 GPU 内存里、由 so 自己填好，内核/设备模型必须**经 kbase 页表去读**。

---

## 2. 本轮实测取证（全部可复现，脚本见 `tools/`）

### 2.1 宿主侧：宿主 GPU 的通路已经存在

| 项 | 实测 | 证据 |
|---|---|---|
| 宿主 | `Darwin 24.3.0 / macOS 15.3.2 (24D81)`，`Apple M4 Pro`，Metal 3，16 GPU 核 | `uname -a` / `system_profiler SPDisplaysDataType` |
| QEMU | `QEMU emulator version 11.0.2 (v11.0.2-112-g1002758ca35-dirty)` | `out/qemu/dist/darwin/bin/qemu-system-aarch64 --version` |
| 可用 GPU 设备模型 | `virtio-gpu-pci`、`virtio-gpu-gl-pci`（alias `virtio-gpu-gl`）、`vhost-user-gpu-pci`、`vhost-user-gpu`、`virtio-gpu-gl-device` | `-device help` |
| **无** Mali 设备模型 | 设备列表中没有 `mali*` | 同上 |
| 宿主渲染进程 | `out/qemu/dist/darwin/libexec/virgl_render_server` + `lib/libMoltenVK.dylib` + `lib/libvulkan.1.dylib` + `MoltenVK_icd.json` | 产物实测 |
| 宿主 GPU 通道（darwin） | QEMU 内嵌 virglrenderer（`-device virtio-gpu-gl-pci,...,venus=on`）→ **venus(Vulkan) → MoltenVK → Metal**；GL 走 `cgl-headless,gl=on` | [Display.md](../../../../../core/host/daemon/Display.md) §2.2、`core/host/daemon/qemu/qemu.go:82-105` |
| 宿主 GPU 通道（linux） | `vhost-user-gpu-pci` + 独立 renderer 进程（virglrenderer，`venus=true`） | `qemu.go:235-243`、[Display.md](../../../../../core/host/daemon/Display.md) §2.3 |
| virglrenderer 定制 | 21 个 macOS 补丁，含 **venus host-pointer / guest identity / portability allocator**（即 venus 共享内存直通） | `qemu/patch/virglrenderer/07-14*.patch` |

> **对本方案的意义**：宿主桥接**不需要从零造**。"设备模型产出宿主可执行的工作"这件事，可以复用
> `virgl_render_server` 的**独立渲染进程 + socket 协议**模式，把 Mali 翻译器作为一个新的 renderer 后端接进去。

### 2.2 客机侧：现行路线其实已经是一个"内核内 GPU 模型"

| 项 | 实测 | 证据 |
|---|---|---|
| 现行编译形态 | `--disable MALI_REAL_HW --enable MALI_NO_MALI --set-str MALI_NO_MALI_DEFAULT_GPU tNAx` | `kernel/build/build-kernel.sh:202-204` |
| "虚拟硬件"本体 | ARM 上游 kbase 的 **软模型** `backend/gpu/mali_kbase_model_dummy.c`（~2000 行，寄存器读写 switch + 槽状态机 + `gpu_model_raise_irq`） | 子代理报告 §6；`kbase/backend/gpu/mali_kbase_model_dummy.c:1177/1560/1099/1110/1113` |
| 寄存器访问落点 | `kbase_reg_read32/64`、`kbase_reg_write32/64` → `midgard_model_read_reg/write_reg`（NO_MALI）或 `readl/writel`（REAL_HW） | `kbase/hw_access/Kbuild:29-33` |
| MTK 侧桥 | 仓库自研 `ged-stub/ged_stub.c`（`/proc/ged` ioctl 桥）+ `mtk-props/virtgpu_mtk_props.c`（HWC 的 49 个私有 CRTC 属性）+ `plat/cdh_mali_plat.c`（platform_device 注册） | 见 [gpu.md](gpu.md) §7.17–7.21 |
| 驱动被改的规模 | `wire-kbase.sh` **2127 行**，6 组 kbase 补丁（GPU_ID 身份、issues 表 case、72B atom 放行、事件自愈、job chain 探针…） | `kernel/modules/mali_vgpu/wire-kbase.sh` |
| 当前达成 | L1（`boot_completed=1`）；B2 v1（RT 填色 + scanout 桥）→ **VNC 抓帧 99.3% 纯灰 `#888888`**；`nativehang` 重启链仍在 | [log-pass.txt](evidence/issue-gold15-vendor-gpu/log-pass.txt)（run81-83） |

> **对本方案的意义**：路线 B = "把 `model_dummy.c` 提升为真正的设备模型（REAL_HW）"，
> 语义已有 C 参考实现，**不是从零开始**；同时说明**改动面在驱动侧（2127 行补丁）正是本方案要消除的东西**。

### 2.3 厂商图形的接口面与依赖闭包（原厂件实测）

`roms/gold_global_images_OS3.0.5.0.VNQMIXM_15.0/vendor_dlkm_a.img`（EROFS，195 个模块）中图形相关：

```
mali_kbase_mt6833_r49.ko   mali_mgm_mt6833_r49.ko   mali_prot_alloc_mt6833_r49.ko
ged.ko   mtk_gpu_hal.ko   mtk_gpu_qos.ko   mtk_gpufreq_mt6833.ko
mtk_gpufreq_wrapper_legacy.ko   sspm.ko   iommu_gz.ko   mtk-mbox.ko
met_gpu_api.ko   met_gpu_adv_api.ko   memory-amms.ko   mtk_disp_sec.ko …
```

`mali_kbase_mt6833_r49.ko` 的元信息（`tools/analyze_ko.py`）：

| 字段 | 值 |
|---|---|
| vermagic | `6.6.118-android15-8-g4d05d8779bfd-4k SMP preempt mod_unload modversions aarch64` |
| of alias | `of:N*T*Carm,mali-valhall`、`of:N*T*Carm,mali-valhallC*` |
| depends | `mtk_gpufreq_wrapper_legacy,ged,mtk_gpu_hal,mrdump,sspm,mtk_gpu_qos` |
| softdep | `pre: memory_group_manager` |
| 未定义符号 | **406**（其中 MTK/平台相关 **46**：`ged_dvfs_*`、`mtk_get_gpu_*_fp`、`mtk_notify_gpu_*`、`sspm_reserve_mem_*`、`aee_rr_*`、`regulator_*`、`clk_*`、`of_clk_get` …） |

`ged.ko`：alias `mediatek,ged`，depends `mtk_gpufreq_wrapper_legacy,mtk_gpu_hal,system_heap,mtk_gpu_qos,mediatek-drm,sspm`，226 个未定义符号。
`mtk_gpufreq_wrapper_legacy.ko`：alias `mediatek,gpufreq_wrapper`，depends `mtk_gpu_hal,mtk_low_battery_throttling,mtk_battery_oc_throttling`。

（完整 modinfo：`raw/official-modinfo.txt`）

### 2.4 官方内核事实：**"vermagic 字符串不同"不是障碍，符号 CRC 才是**

从官方 `boot.img` 抽出内核（37 MB，gzip）+ 内嵌 config：

| 项 | 值 |
|---|---|
| `linux_banner` | `Linux version 6.6.118-android15-8-ge56cf6b09cca-ab15511674-4k (kleaf@build-host)` |
| 官方 kernel vermagic | `6.6.118-android15-8-ge56cf6b09cca-ab15511674-4k SMP preempt mod_unload modversions aarch64` |
| GKI 模块（`system_dlkm`，78 个）vermagic | 与内核**一致**（`ge56cf6b09cca-ab15511674-4k`） |
| 厂商模块（`vendor_dlkm`）vermagic | **不同**（`g4d05d8779bfd-4k`）——但 `vendor_boot` 里 75 处同串，属同批 |
| `CONFIG_MODVERSIONS` / `CONFIG_MODULE_SIG` | `=y` / `=y`（**未** FORCE） |
| `CONFIG_ARM64_4K_PAGES` / `LOCALVERSION` / `LOCALVERSION_AUTO` | `=y` / `"-4k"` / `=y` |
| virtio 支持 | `VIRTIO=y`、`VIRTIO_PCI=m`、`VIRTIO_BLK=m`、`VIRTIO_CONSOLE=m`、`VIRTIO_BALLOON=m`、`VIRTIO_FS=y`、`VIRTIO_VSOCKETS=m` |
| **virtio 缺口** | **无 `VIRTIO_NET` / `VIRTIO_INPUT` / `VIRTIO_GPU` / `DRM_VIRTIO_GPU` / `VIRTIO_SND`**；`system_dlkm` 清单亦无 |
| 显示/DRM | `DRM=y`、`DRM_KMS_HELPER=y`、`DMABUF_HEAPS=y`；**`CONFIG_DRM_MEDIATEK` 未出现在 config 中**（`mediatek-drm` 模块应在 vendor_boot 首阶段 ramdisk） |

为什么"vermagic 串不同"仍能加载——内核源码原文（`kernel/source/kernel/module/version.c:77-88`）：

```c
/* First part is kernel version, which we ignore if module has crcs. */
int same_magic(const char *amagic, const char *bmagic, bool has_crcs)
{
	if (has_crcs) {
		amagic += strcspn(amagic, " ");
		bmagic += strcspn(bmagic, " ");
	}
	return strcmp(amagic, bmagic) == 0;
}
```

→ 有 `modversions` 时**只比较第一个空格之后的段**（`SMP preempt mod_unload modversions aarch64`），
**内核版本串被忽略**。真正的门是**每个导入符号的 genksyms CRC**——这正是 §0.2 那张表的意义。

**另一个结论同样重要**：官方内核**没有 virtio-net/gpu/input/snd**，所以"换官方 stock 内核"会**同时丢掉现行云机的网络/输入/显示虚拟化**（路线 D 必须连 MTK 显示子系统一起仿真，或重编官方内核源码加 virtio 而尽量不改符号 CRC）。

### 2.5 可加载性矩阵（V1 严格档的量化门）

见 §0.2 表（全量：`raw/loadability-matrix.txt`）。关键样例：

- CRC 不符（连通用内核符号都变了）：`__alloc_pages`、`__free_pages`、`__platform_driver_register`、`__pm_runtime_*`、`__arch_copy_to_user`、`__of_parse_phandle_with_args` …
- **完全缺失**（必须来自 MTK 内核）：`KREE_CreateSession`/`KREE_TeeServiceCall`（TEE）、`aee_rr_curr_gpu_dvfs_status`、`mtk_emimpu_set_protection`、`mt_get_abist_freq`、`mt_spower_get_leakage`、`mtk_get_gpu_loading_symbol`、`drm_register_fps_chg_callback`、`ipi_monitor_dump`、`met_gpu_api_ready` …

### 2.6 官方 DTB 的"硬件契约"（原厂硬件描述，设备的地址/中断/时钟事实）

从官方 `vendor_boot.img` 抽出 DTB（偏移 `0x2459040`，194 871 字节），图形相关节点（全文：`raw/official-dtb-gpu-nodes.txt`）：

| 节点 | 关键属性 | 值 |
|---|---|---|
| `mali@13000000` | compatible | `mediatek,mali`、**`arm,mali-valhall`** |
| | reg | **`0x13000000` + `0x4000`（16 KiB MMIO 窗口）** |
| | interrupts / names | **`0 364 4`(GPU) `0 365 4`(MMU) `0 366 4`(JOB) `0 367 4`(EVENT) `0 368 4`(PWR)** |
| | 其它 | `ged-supply`、`operating-points-v2`、`#cooling-cells=<2>` |
| `gpufreq` | reg-names | `mfg_top_config`(0x13fbf000) `mfg_pll`(0x13fa0000) `mfg_rpc`(0x13f90000) `sleep`(0x10006000) `nth_emicfg_reg` `infracfg` `fmem_ao_debug_ctrl` `infracfg_ao` `infra_ao_debug_ctrl` `infra_ao1_debug_ctrl` `topckgen` `efuse`(0x11c10000) `mfg_cpe_control` `mfg_cpe_sensor` `infra_bcrm` |
| | clocks | `clk_mux clk_ref_mux clk_main_parent clk_sub_parent subsys_bg3d clk_fhctl` |
| | supplies | `_vgpu`(mt6359vgpu)、`_vsram_gpu`、`fhctl`、`gpufreq_wrapper` |
| | nvmem | `efuse_segment_cell`（**GPU 漏电/速度分档要读 efuse**） |
| `gpufreq_wrapper` | compatible / 属性 | `mediatek,gpufreq_wrapper`，`gpufreq-version=2`、`gpueb-support=0`、`dual-buck=0` |
| `ged` | compatible | `mediatek,ged`，`gpufreq-supply` |
| `power-controller` | compatible | `mediatek,mt6833-scpsys`，含 `mfg1`/`mfg2` 域 |
| `syscon` | compatible | `mediatek,mt6833-mfg` @ `0x13fbf000`（MFG 时钟控制器） |
| `mt6359vgpu` | compatible | `mediatek,mt6359vgpu`，配 `buck-vgpu11` regulator（0.4–1.19 V）+ `vgpu-temp` auxadc |
| `mtk_lpm/irq-remain-list` | — | `level_mali0..4`（5 条，对应 5 个中断的 LPM 保留） |

→ **这就是设备模型 + 平台仿真的"接口规格书"**：MMIO 基址/尺寸、5 条中断号、MFG/SCPSYS/PMIC/efuse 的依赖面一应俱全。

> ⚠️ 注意 **5 条 vs 3 条**：DT 里是 **5** 条（MTK 在 `arm,mali-valhall` 上加了 `EVENT`/`PWR`，给 MTK 自己的 kbase fork 用）；
> **上游 ARM kbase 只取 `JOB`/`MMU`/`GPU` 这 3 条**（`mali_kbase_core_linux.c:439-475`）。
> 因此 **V2 档（上游 kbase）设备模型只需导 3 条中断**；只有走 V1 档（stock MTK kbase）才需要 5 条。

### 2.7 Mali 硬件寄存器接口面（设备模型要实现什么）

子代理已完成 kbase r49p0（ARM 上游 pristine，单提交 `06b46c9`）的接口面清点，摘要：

| 块 | 基址 | 设备模型必须实现的关键内容 |
|---|---|---|
| `GPU_CONTROL` | `0x0000` | `GPU_ID`(0x000) 必须返回 **`0x09030010`**（= `GPU_ID2_MAKE(9,0,0,3,0,1,0)`，product_id `0x09000003`，r0p1）；`L2/TILER/MEM/MMU_FEATURES`、`AS_PRESENT=0xFF`、`JS_PRESENT`、`JS<n>_FEATURES`、`THREAD_*`、`TEXTURE_FEATURES_0..3`、`SHADER/TILER/L2_PRESENT/READY`、`GPU_IRQ_RAWSTAT/CLEAR/MASK/STATUS`、`GPU_COMMAND`/`GPU_STATUS`、电源域 `*_PWRON/PWROFF/PWRTRANS/READY`、`JM_CONFIG/SHADER_CONFIG/TILER_CONFIG/L2_MMU_CONFIG` 读写回环 |
| `JOB_CONTROL` | `0x1000` | `JOB_IRQ_*`(0x1000-0x100C)、`JOB_IRQ_JS_STATE`(0x1010)；**槽寄存器 `JS<n>` 基址 `0x1800 + n*0x80`**：`HEAD/TAIL/CONFIG/AFFINITY/FLUSH_ID` + `*_NEXT` + `COMMAND/COMMAND_NEXT/STATUS` |
| `MMU_CONTROL` | `0x2000` | `MMU_IRQ_*`(0x2000-0x200C)；**AS 块 `0x2400 + n*0x40`**：`TRANSTAB/MEMATTR/LOCKADDR/COMMAND/FAULTSTATUS/FAULTADDRESS/STATUS/TRANSCFG/FAULTEXTRA` + **AArch64 页表遍历** |
| 中断 | 3 条 | tag `JOB=0 / MMU=1 / GPU=2`（`kbase/backend/gpu/mali_kbase_irq_internal.h:27-29`） |
| 关键状态机 | — | ① `GPU_COMMAND=SOFT_RESET(0x01)` → **必须回 `GPU_IRQ_RAWSTAT` 的 `RESET_COMPLETED(1<<8)`**（否则 probe 必失败，500 ms 超时后转 HARD_RESET 再失败即 `-EINVAL`）；② `JS_COMMAND_NEXT=START` 后完成 → `JOB_IRQ_RAWSTAT` **bit n（完成）/ bit n+16（失败）**，且 `COMMAND_NEXT` 必须归零（`kbasep_jm_wait_js_free` 依赖它）；③ 电源域 `PWRON → READY + PWRTRANS 清零` |
| 可桩项 | — | `PWR_KEY`/`PWR_OVERRIDE*`（kbase 主干不写）、`CORE_FEATURES(0x008)`/`THREAD_TLS_ALLOC(0x310)`（**v9.0 LUT 已清，根本不会访问**）、`PTM_*`（仅 v9.14）、devfreq/DVFS（平台回调本身即空桩）、CSF 整支（JM 路径编译期排除） |

**工程量（子代理估算）**：必须档 **≈2 250–4 050 行 C**；次要档 ≈700–1 600 行；可桩档 ≈80–150 行。
**已知最大未知量（R4）= job chain 内存格式**：kbase 内核**完全不解析**它（只把 jc 指针写进 `JS_HEAD_NEXT`），
设备模型要"真正执行"图形作业就必须自己解析 Mali job descriptor —— 这是从"控制面仿真"跨到"执行面仿真"的门槛。

### 2.8 现行驱动补丁在 V2 档下的去留（"零补丁"要打多少折扣）

`wire-kbase.sh`（2127 行）里的补丁分两类：**与"虚拟硬件"耦合的**（换真设备模型后自然消失）与
**与"上游 kbase ↔ 厂商 DDK 的 ABI 差"耦合的**（换硬件模型不解决）。逐条判定：

| 补丁组 | 内容 | 换 V2（REAL_HW + 设备模型）后 | 理由 |
|---|---|---|---|
| 1 | `model_dummy.c` 的 `-Wimplicit-fallthrough` 编译修复 | **不需要** | `NO_MALI=n` 时该文件不参与编译 |
| 2 | `power_control_init()` 在无 `of_node` 时早退 | **看 DT 注入方式**：走 DT 节点（QEMU/overlay）→ 不需要；走 `platform_device`（现状 `cdh_mali_plat.c`）→ 仍需要 | `of_clk_get(NULL)` Oops 是"没有 DT 节点"的后果 |
| 3 | dummy model 的 `GPU_ID` 改成 `0x09030010` | **不需要** | 身份改由设备模型 `GPU_ID` 寄存器返回 |
| 4 | `mali_kbase_hw.c` 两处 switch 加 `case GPU_ID_MODEL_MAKE(9,3)` | **仍可能需要** | 上游 issues/features 表里**没有 product_model (9,3)**，而厂商 DDK 要求 `product_id=0x09000003` → 身份自洽与"零补丁"存在真实张力 |
| 12 / 12b / 12c | 放行 UK 11.46 的 **72 字节 job atom**（v1 布局）+ padding 豁免 + 拷贝限长 | **仍需要** | 这是**上游 kbase ↔ 厂商 DDK 的用户态 ABI 差**（与硬件模型无关）；只有换回 stock MTK kbase（V1）才消失 |
| 16 / 16b / 16c | job chain / atom 诊断打印 | 可删 | 纯诊断 |
| 27*（a/c/f/i/j/k/l） | 事件强制摘除/回放、submit 500 ms 自愈、fence 诊断 | **可能不需要，建议先去掉再用证据决定** | 上游 kbase 的"立即完成 + 用户态消费慢"自锁，在设备模型按序产生 JOB 中断后**未必复现**；保留则掩盖设备模型的时序错误 |
| 14 / 15 / 17 | virtio-gpu vblank 模拟、sfdump 自愈、ptrace deny | **与 Mali 硬件模型无关** | 属显示/稳定性域；V2 是否仍需要取决于上屏路径（S1 vs S2，§3.1） |

**结论（对"零补丁"的诚实口径）**：
V2 能把 kbase 侧补丁从 **6 组压缩到约 1–2 组（补丁 4 的 1 处 case + 12 家族的 3 处 ABI）**，
并把"魔改硬件语义"整类补丁（1/2/3/16/27）清零；但**做不到字面上的零补丁**——除非走 V1（stock MTK kbase）。
这正是 §9 第 1 问的实质：**"不改厂商实现"要不要严格到"连上游 kbase 的 ABI 适配都不许有"**。

### 2.9 路线 A/B 的否定性验证（round 2）：作业格式边界定案 → **T1/T2 GO、T3 NO-GO**

上一轮把硬件仿真路线 A/B 的剩余风险归到 **R4（job chain / job descriptor 内存格式未知）**。本轮按外部调研的建议
先做**否定性验证**，结论是**好消息**：R4 已从"未知量"降级为"未移植量"。完整证据：
[issue-gold15-gpu-jm-format](evidence/issue-gold15-gpu-jm-format/solution.md)（六段式 + [raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md)）。

| # | 问题 | 结论 | 依据 |
|---|---|---|---|
| 1 | **谁写 job chain？** | **用户态 DDK 写**：`jc` 由 atom（uapi `base_jd_atom_v2.jc`）传入，kbase 只把它写进 `JS_HEAD_NEXT` | `kbase/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h:633`、`kbase/backend/gpu/mali_kbase_jm_hw.c:116-124`（项目实测 72B atom 的 `jc` 在 `+0x08`，见 §2.2 表） |
| 2 | **kbase 里有描述符布局吗？** | **没有**（全树 grep `exception_status\|job_type\|jd_job\|job_descriptor` = 0 命中）→ 描述符是**硬件格式**，因此 **Mesa panfrost genxml 是同格式的公开参考** | [raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md) §1 |
| 3 | **作业类型有权威枚举吗？** | **有**：descriptor 的 Type 字段编号 = kbase 的 `JS<n>_FEATURES` 位号（NULL=1、SET_VALUE=2、CACHE_FLUSH=3、COMPUTE=4、VERTEX=5、GEOMETRY=6、TILER=7、FUSED=8、**FRAGMENT=9**） | `kbase/mali_kbase_gpuprops.c:181-189` + `kbase/hw_access/regmap/mali_kbase_regmap_jm_macros.h:228-237` |
| 4 | **格式真的解出来了吗？** | **是（关键正面结论）**：项目上一阶段的 B2 v1 桥（`wire-kbase.sh` 补丁 **16d**）已把 **v9 Fragment Job(64B) → FBD → RT 数组** 的偏移全部定案并落地，且**填色成功**（`filled=163200`、含 AFBC solid 头） | `wire-kbase.sh:920-933`（布局规格）+ [issue-gold15-vendor-gpu/log-pass.txt](evidence/issue-gold15-vendor-gpu/log-pass.txt)（run81-83） |
| 5 | **设备模型还缺什么？** | 只剩 **2 项确定性工作**：① GPU VA → guest PA 的**页表遍历**（kbase AArch64 模式：4 级、8 B 项、L3 有效 ATE=3 / L0-2=1 / PTE=3、phys=`entry & ~0xFFF`；`kbase/mmu/mali_kbase_mmu_mode_aarch64.c:29-45,89-105`、`kbase/mali_kbase_defs.h:98-102`）② **chain 遍历 + renderpass 四链语义**（`mali_base_jm_kernel.h:475-523`） | 同上 |
| 6 | **仍然未知的是什么？** | ① TILER/VERTEX/GEOMETRY/COMPUTE 描述符；② 图形状态（blend/depth/viewport/scissor/MSAA/shader 绑定）；③ **shader ISA 执行（无公开反编译器）**；④ AFBC 解 tile；⑤ tiler heap / polygon list | [raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md) §5 |

**分档判定（否定性验证的输出）**：

| 档 | 判定 | 说明 |
|---|---|---|
| **T1 控制面（保活）** | **GO** | 寄存器语义有 C 参考（`model_dummy.c`）；不需要任何描述符解析 |
| **T2 搬运/填充（部分出画）** | **GO，且风险低于先前估计** | 描述符路径**已被实测打通**；设备模型只补"页表遍历 + chain 遍历"两项确定性工作（AFBC 解 tile 是主要工程点） |
| **T3 真渲染** | **NO-GO（Apple Silicon 宿主）** | 剩余未知 ①–⑤；其中 ③（ISA）无公开工具链 → 要 T3 只能走**路线 C**（宿主换同族 Mali 板，ISA 免翻译/重编码） |

> **自洽性校验（可作为"已解对"的判据）**：实测解出的 Fragment Job `Type` 字段 = **9**，
> 与 kbase 的 `JS_FEATURE_FRAGMENT_JOB (1u << 9)` **完全一致**——两套独立来源（实测字节 vs 内核头文件）互证。
> 作业类型枚举与"项目只解出 FRAGMENT(9)"的对照，也让 **T3 的剩余范围**变成可数的清单（①–⑤）。

---

## 3. 方案候选（逐条展开）

### 3.0 共同骨架（硬件仿真家族共享的四层；其余家族的落点标注在各自小节）

```
L4 宿主 GPU 桥接    Metal(MoltenVK/venus) | Mali 板(panfrost/panthor) | 其它 Vulkan
L3 工作翻译         Mali job chain → 宿主可执行工作（blit/fill → 或 ISA→SPIR-V/同族重编码）
L2 Mali 硬件模型     MMIO 寄存器 + JM 槽状态机 + MMU/页表 + 电源域 + 3 中断
L1 驱动（不改）      上游 ARM kbase 原样（REAL_HW）或 stock MTK kbase（V1）
L0 平台面            DT 节点 + 地址/中断；V1 另需 MFG/SCPSYS/PMIC/efuse/EMI/TEE 仿真
```

**分层解耦的意义**：L2 的语义已有 C 参考（`mali_kbase_model_dummy.c`），
L3 可分档交付（搬运/填充 → ISA 翻译），L4 已有现成宿主通路。**风险集中在 L3 与 L0（V1 档）**。

### 3.1 路线 A：QEMU / vhost-user 里的 Mali-JM 硬件设备模型（T1/T2 档主线）

**架构**

```
guest: 厂商 libGLES_mali.so ─→ /dev/mali0 ─→ 上游 kbase(REAL_HW, 零补丁) ─ readl/writel ─┐
                                                                                          │ MMIO trap
QEMU:  sysbus/pci Mali 设备模型：寄存器 + JM 状态机 + MMU + 3 条 GIC 中断 + 客机内存 DMA ◄─┘
                     │
                     ├─ L3 翻译器（独立进程，复用 virgl_render_server 的 socket 模式）
                     └─ L4 宿主后端：Vulkan(MoltenVK) / Metal / 真 Mali 板
```

**实现要点**

1. **设备模型形态**：优先 **vhost-user 风格独立进程**（与现有 `vhost-user-gpu`/`vhost-user-media-pci` 一致，崩溃不拖垮 QEMU），
   备选 QEMU 内建 sysbus 设备（调试更快）。需要：`MemoryRegion`(0x4000)、GIC SPI 364–368（**按 v9.0 LUT 语义**）、
   `AddressSpace` 读写客机内存（页表遍历 + job chain 解析）。
2. **DT 注入**：guest 必须看到 `arm,mali-valhall` 节点。三条可选：
   (a) **QEMU `hw/arm/virt.c` 生成节点**（最干净：与真设备模型一样由设备实例 `dt` 属性生成；本仓库已有 QEMU fork 与补丁流程）；
   (b) **GRUB 注入**：gold-15 的引导链是 **EDK2 + GRUB**（`pack-roms.py:112-193` 已在改 `/boot/grub/grub.cfg`），可用 GRUB 的 `devicetree` 命令喂 DTB。
       ⚠️ **坑**：GRUB 的 `devicetree` 是**替换**而非叠加，QEMU 生成的 virt 节点（内存/GIC/virtio-mmio）不能丢 →
       需**离线合并**（先用一次带 hook 的实例从 `/sys/firmware/fdt` dump 出 QEMU 的 DTB，合并 mali 节点后再进 `vda.img`），
       代价是 DTB 与 QEMU 版本/内存参数耦合；因此 (a) 仍更干净。
   (c) 保留一个**极薄的** `platform_device` 注册模块（即现状 `plat/cdh_mali_plat.c` 的形态：无 DT 节点、按 name 匹配）——
       **注意**：这不是"改 vendor"，而是"补硬件描述"，代价是 kbase 的 `power_control_init()` 需要现有补丁 2（`of_node==NULL` 早退）→ 与"零补丁"目标有轻微冲突，故优先 (a)/(b)。
3. **kbase 形态**：`CONFIG_MALI_REAL_HW=y`、`CONFIG_MALI_NO_MALI=n`、`MALI_USE_CSF=n`；§2.8 已列出换档后仍会保留的补丁（补丁 4 + 12 家族）。
4. **执行面分档**（决定"能否出画"）：
   - **T1 控制面**：作业提交即完成（不产像素）→ 目标 = 不改驱动栈活着 + `boot_completed=1`（等价现状 L1，但**零驱动补丁**）。
   - **T2 搬运/填充保真**：解析 job chain 中可安全理解的子集（清屏/填充/位块搬运/RT 描述），在**宿主 GPU 上执行**（`vkCmdFillBuffer`/`vkCmdCopyImage`/`vkCmdClearColorImage`）→ 等价现状 B2 v1，但上屏路径由设备模型直接掌握（无需 virtio-gpu 补丁）。
     **round 2 已判定此档为 GO**：Fragment Job → FBD → RT 布局已有实测规格（§2.9 第 4 行），设备模型只补页表遍历与 chain 遍历。
   - **T3 完整渲染**：`ISA → SPIR-V`（或宿主同族 Mali ISA 重编码）+ 状态机（viewport/scissor/blend/depth/texture/varyings/interpolation）→ 真出图。**研究级**（见 §4 的 G4）。
5. **上屏路径**（两条，互不依赖客机显示栈）：
   - **S1 设备模型直采**：设备模型认识的 RT dma-buf（guest 物理页）→ 直接读成帧 → 经现有 VNC/websocket 出图（绕开 SurfaceFlinger 合成链，M3 判据可先在此建立）；
   - **S2 回归 guest 合成**：让 MTK HWC 走 `/dev/dri/card0`（需 MTK KMS 语义）或继续用 virtio-gpu 作 scanout —— 现状路线已投入很多（49 个私有属性、connector DSI 等）。

**结论**：控制面与宿主桥接都"有参考实现 + 有宿主通路"，是**最贴合目标**的路线；出画上限受 T3 制约。

### 3.2 路线 B：客机内核内设备模型（推荐的先行档）

把 `mali_kbase_model_dummy.c` 的语义**扩展为"真硬件等价"**（保留 NO_MALI 的进程内模型，但补齐 RESET/MMU/电源域/槽状态机），
或把设备模型写成**独立内核模块**（自建 MMIO region + 中断）而 kbase 走 REAL_HW。

- **优点**：不动 QEMU、不动 DTB、调试与发版链路最短（现状工具链已具备：`--patch vendor_dlkm=` 等）；
  可以**先把 §2.7 的"必须档"语义在最小代价下验证正确**，再原样搬进 QEMU。
- **缺点**：严格意义上不是"虚拟机硬件仿真"（模型在 guest 内核里），且宿主桥接仍需经 virtio-gpu。
- **定位**：路线 A 的**语义验证阶段**（先 B 后 A），降低"一次写对 4 000 行 QEMU 设备模型"的风险。

### 3.3 路线 C：宿主换成真 Mali 板（消除唯一研究级攻坚）——**"stock 栈 + 真出画"的首选**

**核心洞察**：guest 是 **Mali-G57（Valhall v9.0，JM）**。若宿主也是一块 **panfrost/panthor 支持的同族 Mali**，
则**渲染作业不需要 ISA 翻译**：设备的 job chain/描述符格式与宿主 GPU 同族，翻译退化为"**描述符重写 + 同族 ISA 直发**"。

- 候选宿主：**同族 Mali Valhall（v9）** 的 Linux 板卡。要点不是"同 SoC"，而是"**同 ISA 家族 + panfrost/panthor 成熟支持**"。
  已见主线动作：`drm/panfrost` 增加 **MT8370**（Genio 700，Mali-G57）支持的补丁系列（[LKML 2025-01](https://lkml.org/lkml/2025/1/30/394)、[LKML 2025-05](https://lkml.org/lkml/2025/5/7/1377#1)）；
  MediaTek **MT8186（Kompanio 820）** 的 GPU 配置与 MT6833 同为 **Mali-G57 MC2**，是"同配置"的候选（**需核对 panfrost/panthor 对该 SoC 的成熟度与 job chain 兼容性**）。
  宿主 SoC 与客机 SoC 不必相同，但 **GPU 架构代号（arch 9.x）越接近，ISA 重编码的差异面越小**。
- 在这条路线上，Mali 设备模型可以直接把 guest 的 job chain **转发给宿主 `drm/panfrost`**（页表/VA 需重映射），
  ISA 相同则**shader 二进制原样可用**；ISA 不同（如同一家族的 v9.0 vs v9.2）需**重编码器**（比 Mali→SPIR-V 简单一个量级：无语义/类型恢复）。
- **代价**：宿主从 Apple Silicon 换成 ARM 板（生产形态变化），或"开发用 Mac、生产用 ARM 板"双轨。
- **结论**：如果目标是"**完整出画**"，这是唯一把研究级风险变成工程风险的路径，**建议与 A 并行评估**（先做 1 周纸面+招标式可行性确认）。

### 3.4 路线 D：stock MTK 内核 + 全 MTK 平台仿真（V1 严格档）

要满足"连 `mali_kbase_mt6833_r49.ko` 都原样加载"，必须：

1. **拿到 MTK 官方内核源码**（Xiaomi 开源 `gold` 对应分支，6.6.118）并**重建**出与厂商模块 **407 个 `__versions` CRC 全一致**的内核
   （判据脚本现成：`kernel/build/verify-module-versions.py` + `tools/loadability_matrix.py` 反向当"CRC 预言机"）；
2. **补齐平台面**：MFG 时钟/PLL/RPC（`gpufreq` 15 个 reg 区）、SCPSYS `mfg1/mfg2` 电源域、PMIC `vgpu` regulator、
   `efuse` nvmem（GPU 速度分档）、EMI MPU（`mtk_emimpu_*`）、SSPM（`sspm_reserve_mem_*`）、AEE/mrdump（`aee_rr_*`）、
   TEE（`KREE_*`）——这些是 `depends:` 闭包强制要求的；
3. **同时丢掉现行 virtio 虚拟化**（官方内核无 virtio-net/gpu/input/snd）→ 必须连 **MTK 显示子系统（DDP/DSI/MMSYS）**一起仿真，
   否则连画面都出不来；
4. 以及 §2.7 的 GPU 设备模型本身。

**代价判断**：平台面数量级大于 GPU 面，且**每加一个仿真设备都要与厂商驱动的隐式假设对齐**。
→ 只有在"**App 检测面必须看到与原厂二进制完全一致的 vendor 目录**"时才值得；否则 V2 档已能满足"不改厂商实现"的实质。

### 3.5 路线 E：改用客机 Mesa 栈（E1 virgl+venus：推荐保底；E2 panfrost/panvk：不推荐）

**变体 E1（推荐）：客机回到 Mesa virtio 栈（= 本仓库其它设备的既有形态）**

- 组成：`minigbm` 分配器 + `hwcomposer.drm.so` + **Mesa virgl（GL）** + **`vulkan.virtio.so`（venus，Vulkan）**，
  全部走已有的 `virtio-gpu-gl-pci,...,venus=on` → **宿主 GPU（MoltenVK/Metal）**。
- **实测已成立**：note13-15（同 lineage 底包）实例 39：`card0-Virtual-1` connected+enabled、1080x2400、60/50/90Hz，
  SF 的 GL 渲染器为 Mesa（virgl 扩展串），**滑动 46fps**（[Display.md](../../../../../core/host/daemon/Display.md) §2.1）。
- **GL 与 Vulkan 都有**——因为 **venus 是独立的 virtio-gpu Vulkan ICD，与 panvk 无关**（外部调研的 ③ 不适用于本变体）。
- 代价：**不使用厂商 Mali 栈**。厂商 so 文件可原样保留在镜像里（检测面"文件在不在"仍可满足；"是否被真正使用"则满足不了）。
- 工期 1–2 人月（本质是**去掉** gold-15 对厂商栈的替换，恢复 lineage 底包形态 + 保留 gold-15 的 vendor 其它内容）。
- 结论：**保底与对照的首选**；也是验证"宿主 GPU 端到端链路健康"的最快手段（把 L3/L4 从 L0–L2 里解耦出来）。

**变体 E2（不推荐）：客机用 panfrost/panvk + panthor**

- 需要客机内有"真 Mali 设备"（panthor/panfrost 也是 MMIO 驱动）→ 又回到路线 A/B 的设备模型问题，**且多一层维护**。
- 更致命：**panvk 对 Valhall v9（G57）完全不支持**——Mesa 支持矩阵里 G57/G68（v9）的 Vulkan 一栏为空，
  Collabora 2025-07 原文 "the first generation of Valhall (V9) … currently completely lacking support"
  （[Mesa Panfrost 文档](https://docs.mesa3d.org/drivers/panfrost.html)、
  [PanVK 1.4 通告](https://test.www.collabora.com/news-and-blog/news-and-events/panvk-now-supports-vulkan-1.4.html)）。
- 结论：**不如 E1**（E1 用 venus，反而有完整 Vulkan）。

### 3.6 路线 F：ARM Fast Models 的 Mali 模型（GRM/GGA）

| 事实 | 结论 |
|---|---|
| ARM Fast Models **确实提供 Mali 寄存器模型**（GRM = Graphics Register Model；GGA = Graphics Acceleration） | 有模型，但不是"拿来做云机"的形态 |
| 公开型号为 **Mali_G710 / Mali_G715（v10+）**，**未找到 v9 / G57 模型** | 与 gold-15 的 G57 不匹配 |
| GRM 默认只输出**渐变图案**（无真实渲染） | 出画需 GGA+GRM |
| **GGA+GRM 集成 Mali 驱动时必须"build your own libnwhal"** | ARM 不提供可直接落地的宿主 GPU 后端 |
| GGA 在新版文档中**已标记弃用** | 长期路线不确定 |
| 宿主支持 | 无 macOS/Apple Silicon 形态 |

依据：[Fast Models GRM 文档](https://developer.arm.com/documentation/100965/1100-00/Graphics-Register-Model/Using-Graphics-Register-Model/Modeling-a-GPU-system-with-GRM)、
[GRM 与 Android 图形栈](https://documentation-service.arm.com/static/5f48caf06e73485d721e97d7)、
[GGA+GRM 需自写 libnwhal](https://documentation-service.arm.com/static/60dda7f70320e92fa40b5b62)、
[Mali G710 模型默认渐变](https://documentation-service.arm.com/static/6229d3bd8804d00769e9cd28)、
[GGA 弃用](https://developer.arm.com/documentation/100965/1122/Graphics-Acceleration-in-Fast-Models/Deprecation-of-GGA)。
→ **不可直接落地**；只在 ARM 授权合作范围内有讨论价值。

---

### 3.7 外部先例与生态（调研结论，含来源）

| 事项 | 结论 | 依据 |
|---|---|---|
| QEMU 是否已有 Mali 设备模型 | ❌ **无**（也没人向 qemu-devel 提交过）。本仓库 QEMU 源码全树检索 `hw/`/`include/hw/` 的 `mali` 只命中注释里的 "malicious/minimalist"；构建产物 `-device help` 只有 `virtio-gpu*`/`vhost-user-gpu*` | [QEMU hw/display](https://gitlab.com/qemu-project/qemu/-/tree/master/hw/display)；本仓库 `qemu/source` 实测 |
| 公开的"寄存器级仿真 Mali"先例 | ❌ **无**。唯一找到的同类实践是把 kbase 编译成 `CONFIG_MALI_NO_MALI`（内核内假模型）+ 自注册 `platform_device` 让 `/dev/mali0` 出现——**与本仓库现行路线同构**，**不出画**，只用于内核调试/fuzz | [Emulate Mali on QEMU](https://1nzag.github.io/study/linux_kernel/android/emulate_mali_on_qemu)（实践记录：需自加 `platform_device`、注释 `asm/arch_timer.h`） |
| gfxstream / VirGL / Venus / vDRM 能否驱动 stock Mali 栈 | ❌ **不能**。它们都是 **API 重映射**（要求客机运行**它们自己的** GLES/Vulkan ICD），**无法驱动真实 GPU 硬件**；gfxstream 的 capset 只有 gfxstream-vulkan/gles/composer/magma，**无 Mali**；Cuttlefish 的 `GpuMode` 枚举亦无 mali | [Android Emulator Internals Ch.12-14](https://aospbooks.github.io/android-emulator-internal-book/14-gfxstream-protocol/)、[gfxstream capset 一览](https://gist.github.com/mtjhrc/6a78a127bd141165e0a454a52d6e6096)、[cvd gpu_mode.h](https://google.github.io/android-cuttlefish/cvd/libs_2config_2gpu__mode_8h.html) |
| Mali ISA → 宿主 GPU 的反编译（**不可约瓶颈**） | ❌ **无公开实现**。只有 disassembler（`ISA.xml` 驱动），**没有任何** Mali ISA → SPIR-V/NIR/LLVM 翻译器；Apple **Air 格式无公开汇编器**，唯一官方 IR 入口是 **metal-shaderconverter（DXIL/SPIR-V → Metal）** | [Valhall ISA 文档](https://people.collabora.com/~alyssa/Valhall-Documentation.pdf)、[Mesa ISA.xml](https://github.com/intel-lgci-fdo-gitlab-mirror/mesa.mesa/blob/93339463/src/panfrost/compiler/bifrost/valhall/ISA.xml)、[Collabora 反向工程 G78](https://www.collabora.com/news-and-blog/news-and-events/reverse-engineering-the-mali-g78.html)、[Apple Shader Converter 集成 RFC](https://github.com/o3de/sig-graphics-audio/blob/main/rfcs/AppleShaderConverterIntegration/AppleShaderConverterIntegration.md) |
| **无真 Mali 做 oracle 的正确性风险** | ⚠️ **强论据**：Collabora 的经验是"reverse-engineering and driver development are **better together**"——纯 in-vitro（无真机对照）会出现"spec 说得通但硬件行为不同"（Valhall 指针对齐即为例证） | [Writing an open source GPU driver without the hardware](https://www.collabora.com/news-and-blog/blog/2022/01/27/writing-an-open-source-gpu-driver-without-the-hardware/) |
| 宿主换真 Mali（路线 C）的先例 | ✅ 通用形态成熟（GPU 直通/半直通），但 **MT6833 + stock MTK 栈**没有公开先例；需自己处理 MTK 时钟/电源域/ACP 等 | 同上调研；§3.3 的 panfrost/MT8370 主线补丁 |
| 商业云手机如何做 Mali 渲染 | ✅ 但**全依赖真 ARM GPU**（不做 GPU 仿真） | 外部调研结论（见 [raw/external-prior-art.md](evidence/issue-gold15-gpu-hw-emu/raw/external-prior-art.md)） |
| **必读的宿主桥接踩坑记录** | ⭐ Haven 的 Android venus 工程记录（R1–R8）：minigbm 分配墙 → AHB 分配器、`VN_PERF=no_fence_feedback` 修 fence 挂死、virglrenderer `vkr_device_destroy` mutex 生命周期 bug、**vtest host-visible 内存缓存一致性需 `dc cvac` 刷写** | [Haven: GPU acceleration](https://glasshaven.github.io/Haven/gpu-acceleration.html) |

> 本节结论对 §3 排序的影响：**路线 A 的"控制面仿真"仍有价值（规格书 + 语义参考）**，
> 但**"控制面 + ISA 翻译到 Metal"不应作为主线**（无先例、无反编译器、无 oracle）；
> **"stock 栈 + 真出画"只有路线 C 有成功把握**。

---

### 3.8 路线 G1/G2/L：不虚拟化 GPU（真机云化 / Android 容器 / 只虚拟显示输入）

**做法**：宿主本身就是能跑厂商栈的真硬件（同款 MT6833 真机、或同族 MTK 板），
GPU 由厂商驱动原生使用；云机只做**显示编码 + 输入注入 + 设备管理**（本仓库的推流/输入/ADB 经验可直接复用）。

| 变体 | 说明 | 优点 | 代价 |
|---|---|---|---|
| **G1 真机云化** | 一台真机 = 一个实例，画面经 VNC/WebRTC 推流 | **零图形适配风险**；性能与真机一致；商业云手机的通行做法 | 密度 1:1（成本高）；硬件采购/运维 |
| **G2 Android 容器** | 宿主跑真机内核（或同族内核），容器共享内核，多个 Android 容器共享 GPU | 密度优于 G1 | 需要容器化内核/驱动支持；多租户隔离；MTK 私有驱动在容器里的资源冲突风险 |
| **L 只虚拟显示/输入** | 宿主=真板，Android 跑在**轻量虚拟化/容器**里，只有显示与输入被虚拟化 | 图形零适配；可复用现有推流栈 | 与 G2 的边界模糊；仍需 Android 侧适配 |

**结论**：如果目标是"**稳定出画 + 性能**"而不是"单机多实例 + 检测面"，**G1/G2 是工程上最省心的答案**；
本项目现有架构（daemon + 推流 + 输入注入 + 多实例端口池）在 G1 上几乎可以原样复用（把 QEMU 换成真机 agent）。

### 3.9 路线 H：gfxstream / rutabaga + 自研 ICD（像 Cuttlefish 对 Adreno 那样）

**做法**：在客机内提供 gfxstream 自己的 EGL/Vulkan ICD，把 GL/VK 命令编码给宿主解码执行。
**判据（外部调研）**：gfxstream 的 capset 只有 gfxstream-vulkan/gles/composer/magma，**没有 Mali**；
而它的工作前提是**客机使用它的 ICD** → 与"使用不改的厂商 Mali DDK"**结构互斥**。
**结论**：**不可用于本目标**（除非接受"替换厂商图形栈 = 路线 E1"，那就没必要绕 gfxstream）。
可作为**架构参考**（"渲染在独立进程 + 描述符转发"）。

### 3.10 路线 I：客机软件渲染兜底（llvmpipe / SwiftShader）

**做法**：客机用 CPU 光栅化（Mesa llvmpipe）或 SwiftShader，完全不碰 GPU。
**结论**：能出画、能保 UI 可用，但**性能不可接受**且**不满足"桥接宿主 GPU"**；
只建议作为"证明软件栈本身没问题"的对照手段（把图形问题与虚拟化问题分离）。

### 3.11 路线 J：用户态 API 拦截（LD_PRELOAD / linker namespace）⭐ 值得评估的灰区

**做法**：`/vendor/lib64/egl/libGLES_mali.so` 等**文件不改**，但在 zygote/SF/HWC 的加载阶段用
`LD_PRELOAD`、`/etc/ld.so.preload` 或 Android **linker namespace** 让其 `eglGetDisplay/dlsym` 落到我们的实现，
把 GLES/Vulkan 调用转发给已打通的宿主通路（**venus/virgl → MoltenVK → Metal**）；
厂商 so 仍存在于镜像中（maps 里可见），但**不再承担渲染**。

| 维度 | 评估 |
|---|---|
| 是否"修改 vendor 实现" | **文件不改**（只改运行时加载顺序）——灰色地带，取决于验收口径 |
| 出画能力 | **完整**（GL + Vulkan，宿主 GPU）——等于路线 E1 的能力 |
| 工期 | 1–2 人月（含 namespace 与 dlopen 拦截、GLES/VK 入口覆盖） |
| 风险 | **检测面可发现**（maps/dlopen 轨迹、`GL_RENDERER` 串、`eglQueryString` 差异）；Android linker namespace 限制（`/vendor` 与 `/system` 的命名空间隔离）会让 preload 需要平台侧配合 |
| 与 K 的区别 | K 拦截的是**内核 ioctl**（要仿真硬件语义）；J 拦截的是**用户态 API**（不需要仿真硬件） |

**结论**：如果验收口径是"**文件级不改厂商实现**"，J 是**性价比最高的"能出画"路径**；
如果验收口径含"**运行时必须真的在用厂商 Mali**"，J 不成立（那就只剩 C/D）。

### 3.12 路线 K：ioctl 层"假 mali0"驱动（= 现状 V0 路线）

**做法**：内核里实现一个**像 Mali 硬件一样回应 DDK** 的 mali0（不仿真寄存器，而是承接 `KBASE_IOCTL_*`），
job 提交立即完成或做搬运桥接（现状 B2 v1）。
**现状证据**：已 L1（`boot_completed=1`）、B2 RT 填色工作、**但 VNC 仍 99.3% 静态灰、`nativehang` 重启链未断**，
驱动侧改动 **2127 行 / 6 组补丁**（§2.2、§2.8）。
**与 A/B 的关系**：K 是 A/B 的**"低层近似"**——A/B 在 MMIO 层仿真硬件（改动面在硬件侧），K 在 ioctl 层仿真行为（改动面在驱动侧）。
**结论**：**已被证明能保活但不能保画**；若继续，只能作为"检测面 + 保活"的交付（对应路线 M）。

### 3.13 路线 M：只做检测面（不出画）

**做法**：目标收敛为"厂商 so 在位/被加载、`/dev/mali0` 存在、DDK 初始化成功"，不追求 `screencap` 出正确画面。
**结论**：这是现状 V0 路线的**验收降级版**；若用户的真实目标是"云手机可用"，则 M 不够（需要 E1/C/G 之一）。

### 3.14 组合打法（推荐）

| 目标优先级 | 推荐组合 | 理由 |
|---|---|---|
| **先"能出画、能跑业务"** | **E1**（Mesa virgl+venus 恢复）+ 保留厂商 so 文件 | 1–2 人月；GL+VK 全通；有实例 39 的实测先例 |
| **再"检测面"** | **J**（用户态拦截，让厂商 so 参与加载但不承担渲染）或 **M**（只保检测面） | 文件级不改厂商实现；J 还能保留"运行时出现在 maps 里"的表象 |
| **要"stock 厂商栈 + 真出画"** | **C**（宿主换同族 Mali 板）为第一优先；D 仅在"必须原厂 .ko"时 | 外部调研 + §3.3 的唯一高把握路径 |
| **要"硬件仿真本身"（研究/演练）** | **B → A**（先内核内语义，再 QEMU 设备模型），只做到 T1/T2 | 控制面有 C 语言参考（`model_dummy.c`），工程量可控；T3 单独立项 |
| **要"密度/成本最优"** | **G2**（Android 容器，宿主=真机内核） | 不做 GPU 虚拟化的正解；商业上已验证 |

---

## 4. 关键技术攻坚清单（按门排序，带判据）

> 每道门都要产出六段式证据（AGENTS.md 约束 2）。判据尽量用"可 grep 的日志/寄存器回读"而非主观描述。

### G0 · 设备模型探测门（控制面最小闭环）

- **目标**：不改任何驱动代码，kbase（REAL_HW）能 probe 成功并生成 `/dev/mali0`。
- **必须项**：`GPU_ID=0x09030010`；特性/present 常量自洽；**`SOFT_RESET → RESET_COMPLETED`**；
  3 条 named IRQ（JOB/MMU/GPU）；电源域 `PWRON/READY/PWRTRANS`；`AS_COMMAND=UPDATE` 与 `AS_STATUS` 空闲语义。
- **判据**：`dmesg | grep -i mali` 无 fault/超时；`/dev/mali0` 存在；`/sys/class/misc/mali0/device/gpuinfo` 可读。
- **已知坑**：
  - **`/lib/firmware/valhall-1691526.wa`**：JM + "真硬件"路径下，若 `THREAD_FEATURES[31:30]=SILICON` 且版本报 **r0p0**，
    则 `kbase_hw_set_issues_mask` 会命中 `BASE_HW_ISSUE_TTRX_3485` → `open(/dev/mali0)` 时 `request_firmware` 失败即 `-ENODEV`。
    **规避**：`THREAD_FEATURES[31:30]=SOFTWARE(3)`（dummy model 就是这么做的）或版本报 **r0p1**。
  - v9.0 的 LUT 已清 `CORE_FEATURES(0x008)`/`THREAD_TLS_ALLOC(0x310)` → **不要照抄旧 DDK 寄存器表**。

### G1 · 厂商 DDK 门（用户态 `Device::Initialize` + 一次真实 atom）

- **目标**：不改的 `libGLES_mali.so` 完成初始化、建 context、提交 atom 并被正确回收。
- **判据**：`KBASE_IOCTL_VERSION_CHECK` 通过；一次 `FS`（`core_req=0x1`）atom 从提交到事件完成；
  `/sys/kernel/debug/mali0/ctx/*/atoms` 无滞留（对照现状 run79 的"atom 滞留 COMPLETED 自锁"）。
- **判据工具**：现状已具备（job chain 探针、`mali-cmar-backend` 线程快照、`sfdump`）。

### G2 · 内存门（MMU + dma-buf + 缓存）

- **目标**：页表遍历正确、`FLUSH_PT/FLUSH_MEM` 语义正确、dma-buf/ion/`dma_heap` 能进 GPU VA、缓存操作有回执。
- **判据**：MMU fault 计数为 0；`AS_FAULTSTATUS` 无 `TRANSLATION_FAULT`；`GPU_COMMAND=CLEAN_INV_CACHES → CLEAN_CACHES_COMPLETED` 有回。
- **坑**：ATE 编码必须与 `kbase/mmu/mali_kbase_mmu_mode_aarch64.c:36-49` 一致；`AS_COMMAND` 顺序必须 `TRANSCFG→TRANSTAB→MEMATTR→UPDATE`。

### G3 · 出画门（T2 搬运/填充 → 上屏）

- **目标**：**宿主 GPU** 执行 fill/copy/clear，帧真正变化（不再 99.3% 纯灰）。
- **判据**：VNC websocket 抓帧与上一帧 md5 **不同**；RT 颜色统计命中预期纯色；
  `screencap` 非单调灰。
- **现状基础（round 2 取证，§2.9）**：**v9 Fragment Job(64B) → FBD → RT 数组** 的布局**已被项目解出并落地**
  （`wire-kbase.sh:920-933` 规格 + run81-83 `filled=163200` 判据），因此 G3 在设备模型里是**移植**而非探索。
- **设备模型仍需补的两件确定性工作**：① GPU VA → guest PA 的**页表遍历**（kbase AArch64 模式，见 §2.9 第 5 行）；
  ② **chain 遍历 + renderpass 四链语义**（`mali_base_jm_kernel.h:475-523`）。
- **坑**：RT 的 dma-buf 常是 **AFBC（tiled/压缩）**格式 → 需要解 tile（run74 实测"含 AFBC solid 头"）；
  `stride` 与 `bpp` 语义要按 MTK gralloc 约定（现状已踩过 stride=72/atoms 与 8bpp 的坑）。

### G4 · 渲染门（T3：ISA 翻译）

> 展开版（五层分解 + 落地路线 + 取证清单）：[gpu-hw-emu-t3.md](gpu-hw-emu-t3.md)。

- **目标**：Mali Valhall 顶点/片元 shader 在宿主 GPU 上产生正确像素。
- **前置结论（外部调研 + round 2 取证）**：
  1. **公开生态无任何 Mali ISA → SPIR-V/NIR/LLVM 翻译器**（只有 disassembler，由 Mesa 的 `ISA.xml`/`v9.xml` 驱动）；
     Apple **Air 无公开汇编器**，唯一官方 IR 入口是 **metal-shaderconverter（DXIL/SPIR-V → Metal）** → 目标端必须走 **SPIR-V/Vulkan**（MoltenVK）。
  2. **作业类型有权威枚举**（Type 值 = `JS<n>_FEATURES` 位号）：NULL=1、SET_VALUE=2、CACHE_FLUSH=3、COMPUTE=4、
     VERTEX=5、GEOMETRY=6、TILER=7、FUSED=8、FRAGMENT=9；**项目目前只解出 FRAGMENT(9)** → T3 的待解清单 = 其余 8 类 + 图形状态。
- **三条技术路线（按可行性排序）**：
  1. **宿主 = 同族 Mali（路线 C）**：描述符重写 + 同族 ISA 直发/重编码。**推荐优先**（也是外部调研的结论）。
  2. **ISA → LLVM IR → SPIR-V**：需要 Mali ISA 反编译（控制流重建/SSA/类型恢复）。参考资产：Mesa `panfrost` 的
     [Valhall ISA 文档](https://people.collabora.com/~alyssa/Valhall-Documentation.pdf) 与
     [ISA.xml](https://github.com/intel-lgci-fdo-gitlab-mirror/mesa.mesa/blob/93339463/src/panfrost/compiler/bifrost/valhall/ISA.xml)。**研究级、无先例**。
  3. **ISA → 宿主 CPU（软件光栅/解释器）**：正确性优先、性能最低；**不满足"桥接到宿主 GPU"**，仅作正确性参照（可用于建立 golden image）。

---

## 5. 宿主 GPU 桥接设计

### 5.1 三条宿主后端

| 后端 | 可用性 | 桥接方式 | 适用 |
|---|---|---|---|
| **Apple Silicon（当前开发机）** | ✅ 已就绪 | venus(Vulkan) → MoltenVK → Metal；或 renderer 进程内直接用 MoltenVK | 开发/演示 |
| **Linux + Mali 板（路线 C）** | ✅ 路径现成（`vhost-user-gpu` + 独立 renderer） | panfrost/panthor 直发；同族 ISA 免翻译 | **生产推荐（若要完整出画）** |
| **Linux + 其它 Vulkan GPU** | ✅ | 与 Apple 同（走 Vulkan），差在无 MoltenVK 层 | 备选 |

### 5.2 与现有基建的复用点（不要重造）

1. **渲染进程模型**：`virgl_render_server`（darwin）/ vhost-user-gpu backend（linux）已验证"渲染在独立进程 + socket 通信"的可靠性补丁（`qemu/patch/virglrenderer/02-05*.patch`）。
2. **宿主内存直通**：virglrenderer 的 **venus host-pointer / guest-identity** 补丁（`07-14*.patch`）已经把"guest 内存给宿主 GPU 用"打通，可直接用于把 RT dma-buf 交给宿主渲染。
3. **出图链路**：`-display cgl-headless,gl=on,readback=on` + VNC(websocket) 已是稳定判据通道（[Display.md](../../../../../core/host/daemon/Display.md) §2.5）。
4. **判据工具**：`out/tmp/15r16-shot/ws_shot.py`（websocket 抓帧）、现状的 `sfdump`/job chain 探针。

### 5.3 权限/资源注意

- QEMU 设备模型要读客机内存 → 必须处理 **IOMMU/SMMU**（当前 `virt` 机器默认无 SMMU，直接物理地址访问即可，但需与 kbase 的 MMU 页表区分：**GPU MMU 页表是 kbase 自己管的，设备模型要"照着遍历"**）。
- 中断必须走 **GIC SPI 364–368**（DT 里的 `0 364 4` …），并且**只在 `gpu_powered` 之后拉高**（kbase 的 IRQ handler 有早退条件）。
- QEMU 侧所需机制（写设备模型的最小集）：`memory_region_init_io()` + `MemoryRegionOps->read/write`（MMIO 陷阱）、
  `AddressSpace`（`address_space_read/write/map` 做 DMA）、`memory_region_init_iommu()` + `MemoryListener`（若要 SMMU）、
  `EventNotifier`/eventfd（宿主异步完成 → guest 中断）。
  参考：[The memory API](https://www.qemu.org/docs/master/devel/memory.html)、[vhost-user-gpu 协议](https://www.qemu.org/docs/master/interop/vhost-user-gpu.html)。

### 5.4 宿主桥接的已踩坑清单（来自 Haven 的 Android venus 工程记录）

在 macOS/Metal 宿主上，这些问题**大概率以不同形态重现**（[来源](https://glasshaven.github.io/Haven/gpu-acceleration.html)）：

| 坑 | 表现 | 对本项目的提示 |
|---|---|---|
| minigbm 分配墙 | guest 分配路径与宿主不兼容 | 可能需要自建 AHB/dma_heap 分配器（本仓库已有 `dma_heap` 别名与 heap 补丁的经验） |
| fence 反馈挂死 | `VN_PERF=no_fence_feedback` 才稳定 | 设备模型的"完成中断/围栏"语义必须与 guest 期望一致（对应 §2.7 的 JOB 中断时序） |
| virglrenderer 对象生命周期 | `vkr_device_destroy` mutex 竞态 | 渲染进程与设备模型的生命周期要显式管理（本仓库已有"渲染服务重启后围栏回收"补丁） |
| **host-visible 内存缓存一致性** | 需 `dc cvac` 刷写 | 设备模型/翻译器读写 guest 可见内存时**必须显式处理 cache 维护**（否则出现"数据明明写了却读不到"的随机错） |
| Apple IOMMU 16KB 页 | 4096 假设失效（Collabora 在 M1 上踩过） | 若走 DMA/IOMMU 路径，不要假设 4KB 页 |

---

## 6. 工作量 / 风险 / 回退

| 工作项 | 规模 | 风险 | 回退 |
|---|---|---|---|
| L2 GPU 设备模型（必须档） | 2.3k–4.1k 行 C | 中（时序/位语义） | 先用路线 B（内核内）验证语义 |
| L2 MMU/页表 | 0.7k–1.2k 行 | 高（fault 风暴） | 先"直接映射"降级（仅 RT/命令缓冲） |
| L0 平台面（仅路线 D） | 6–12 人月 | 高 | 放弃 V1，退到 V2 |
| L3 搬运/填充桥（T2） | 0.5–1 人月 | 中（tile/stride） | 退 T1（只保活） |
| **L3 ISA 翻译（T3）** | **研究级（人年）** | **高** | **走路线 C（同族 Mali 宿主）绕过** |
| L4 宿主后端 | 0.5–1 人月 | 低（基建现成） | 用路线 E 验证链路 |

**全局风险 R**：

- **R1 出画天花板**：T3 若不做，宿主 GPU 只能承担搬运/填充 → 与现状 B2 同级，"桥接到宿主 GPU"只有形式意义。**这是本方案最需要用户先决策的点**（决定是否上路线 C）。
- **R2 原生 context 的诱惑**：路线 E 便宜且成熟，但偏离"用厂商栈"；若检测面不是硬需求，E 才是性价比最优。
- **R3 双轨维护**：路线 A/B/C 的组合会同时存在两套 GPU 语义实现（内核内 + QEMU 内），需明确"谁是唯一真源"（建议：**QEMU 内模型为真源，内核内模型只作为语义试验台**）。
- **R4 job chain 格式（round 2 已降级 ✅）**：~~内核不解析、无公开完整文档~~ → 实测**已解出** v9
  **Fragment Job(64B) → FBD → RT** 布局（§2.9 第 4 行），且作业类型有 kbase 权威枚举（Type 值 = `JS<n>_FEATURES` 位号）。
  **残余**：TILER/VERTEX/GEOMETRY/COMPUTE 描述符、图形状态、AFBC 解 tile、tiler heap —— 归并到 **R1/R6** 与 §4 G4。
- **R5"A 类补丁消失"不等于"补丁归零"**：V2 仍会留下 §2.8 里的 **1–2 组 ABI/身份补丁**（72B atom 家族、`product_model (9,3)` case）；
  如果验收口径是"驱动侧目录零改动"，则只有 V1 档能满足，代价见路线 D。
- **R6 没有真 Mali 做 oracle，正确性无法收敛**（外部调研的核心风险论据）：Collabora 的经验是
  "reverse-engineering and driver development are **better together**"——纯 in-vitro 仿真会出现
  "规格说得通但硬件行为不同"（Valhall 指针对齐即为例证）。
  **round 2 的缓解**：**T1/T2 有了"半 oracle"**——项目自有的内核内 B2 v1 实现 + Mesa panfrost genxml + 实测判据
  （`filled=163200`、AFBC solid 头）构成了可对照的参考实现；**T3 仍然缺 oracle**（需要真 Mali 设备），
  这也是路线 C 优先级高的原因之一。
- **R7 目标端只能是 Vulkan/SPIR-V**：Apple Air 无公开汇编器，唯一官方 IR 入口是 metal-shaderconverter（DXIL/SPIR-V→Metal）
  → 若坚持 Apple Silicon 宿主，T3 的终点被锁死在 **SPIR-V**，不能"直接生成 Metal"。

---

## 7. 明确不做

1. 不做 Mali 硬件透传（宿主无 Mali；路线 C 也不是透传，而是"同族转发+重编码"）。
2. 不在本方案内改 `libGLES_mali.so` / `libgpud` / `hwcomposer.mt6833.so` 的字节与路径。
3. 不实现 CSF（`MALI_USE_CSF=n`，G57 是 JM；CSF 的 MCU 固件/共享接口区不在范围）。
4. 不追求性能计数（`PRFCNT_*`）、robustness 测试项等次要面（可桩）。
5. 不在 V2 档内仿真 MTK 显示子系统（DDP/DSI/MMSYS）——上屏走 S1（设备模型直采 RT）或继续 virtio-gpu。

---

## 8. 下一步（分轮）

| 轮次 | 动作 | 产出/判据 |
|---|---|---|
| round 1（✅ 已完成） | 取证：宿主通道、厂商闭包、官方内核/DTB、可加载性矩阵、kbase 接口面、外部先例；**列全路线（§0.3/§3）** | 本文档 + [证据目录](evidence/issue-gold15-gpu-hw-emu/solution.md)（六段式 + raw + tools） |
| round 2（✅ 本轮完成，**否定性验证**） | 定案 job 格式边界：谁写 chain、kbase 有无布局、作业类型枚举、已解出的 Fragment/FBD/RT、设备模型剩余工作、仍未知清单 → **T1/T2 GO、T3 NO-GO** | [issue-gold15-gpu-jm-format](evidence/issue-gold15-gpu-jm-format/solution.md) + [raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md)；本文档新增 §2.9，修订 §4 G3/G4、§6 R4/R6 |
| round 3（✅ 已完成，文档） | 设备模型规格表：**691 条权威寄存器表**（机器抽取 + 反向校验）+ 约 60 个行为寄存器与常量值 + 5 套状态机 + 数据面（页表/chain/FBD/RT）+ T2 宿主接口 + 验收判据 + 9 项风险 | [gpu-hw-emu-spec.md](gpu-hw-emu-spec.md) + [raw/kbase-regmap-v9_0.md](evidence/issue-gold15-gpu-hw-emu/raw/kbase-regmap-v9_0.md) + [tools/extract_regmap.py](evidence/issue-gold15-gpu-hw-emu/tools/extract_regmap.py) |
| round 4（✅ 已完成，文档） | T3（真渲染）可行性矩阵：五层分解 + 范围界定（实测硬件作业 = Fragment + Tiler）+ 三条落地路线 + 7 项只读取证清单 + go/no-go | [gpu-hw-emu-t3.md](gpu-hw-emu-t3.md) + [raw/ddk-job-mix.md](evidence/issue-gold15-gpu-jm-format/raw/ddk-job-mix.md)、[raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md) |
| 下一轮 B（文档） | **T3 剩余取证**：TILER/VERTEX/GEOMETRY/COMPUTE 描述符 + 图形状态（blend/depth/viewport）与 panfrost genxml 的对照；AFBC 解 tile 方案 | "T3 待解清单 + 取证方法"文档（含 go/no-go 复评） |
| round 5（✅ 已完成，文档） | **路线 C 桥接可行性 go/no-go**（本地源码确证）：寄存器级同构、VA 翻译管线、内存三分处置（着色器拷贝/数据导入/heap scratch）、8 条硬约束、GO 判定 | [gpu-hw-emu-routeC.md](gpu-hw-emu-routeC.md) |
| 下一轮 C（文档） | **路线 C 专项**：宿主板卡候选（MT8370/Genio 700、MT8186 等）× panfrost/panthor 版本 × job chain 重写与 ISA 兼容矩阵；**oracle 来源确认** | 宿主选型 go/no-go 报告（文档） |
| round 6（✅ 已完成，文档） | **路线 E1/J 实施方案**：取证 gold-15 官方 vendor 图形属性、lineage 图形文件清单、**发现并复用 note13 已投产的 `android.hardware.gpu`**（路线 J 实现）；定位唯一硬冲突（显示/分配面）；给出组合验收矩阵与内核连带收益 | [gpu-hw-emu-e1j.md](gpu-hw-emu-e1j.md) |
| round 7（✅ 已完成，文档） | **外部权威补齐 + 交叉验证**：取到 Mesa panfrost 的 JM 描述符文档与作业链语义，**独立验证 round 2/3 的实测布局**（作业类型枚举、Fragment Job 字段、24 位 PC/16 MB 约束）；据此把 T3 的 J-D/J-F/J-Y 三层从"待取证"升级为"结构已知 + 字段落点"；并新增 Valhall 特有作业类型与 **scoreboard 依赖**要求 | [gpu-hw-emu-refs.md](gpu-hw-emu-refs.md)；同步修订 [gpu-hw-emu-t3.md](gpu-hw-emu-t3.md) §0/§1/§2.1/§2.3/§2.5、[gpu-hw-emu-routeC.md](gpu-hw-emu-routeC.md) §2.2/§3 |
| 下一轮 D（文档） | **路线 E1 / J 的实施方案**：E1 需要移除/停用哪些厂商图形替换项才能恢复 Mesa virtio 栈；J 需要哪些 linker namespace / preload 配合、覆盖哪些入口、检测面风险清单 | 两份"实施方案 + 验收判据 + 回退"文档（只写不落地） |
| 下一轮 E（文档） | **组合打法与验收矩阵**：把 §3.14 的五种目标组合展开成分阶段计划（含每阶段判据、回退、失败升级路径） | 阶段计划文档 + §9 决策记录 |

> ⚠️ 按用户指令，上述"下一轮"动作**只产出文档**；任何代码/配置改动需用户另行明确授权。
>
> **round 2 之后建议的新优先级**（依据否定性验证结果）：
> ① **下一轮 A（设备模型规格表）** 优先级上升——T2 已 GO，规格表可直接指导 QEMU 设备模型；
> ② 同时推进 **下一轮 D（E1/J 实施方案）** 作为"立刻能出画"的保底；
> ③ **T3 取证（B）与路线 C 选型（C）** 取决于 §9 第 2/3 问的裁决。

---

## 9. 待裁决的决策点（用户 2026-10-12 已明确的两条：① 只出文档不改代码；② 要求列全路线）

| # | 决策点 | 选项 | 影响 |
|---|---|---|---|
| 1 | **验收口径** | (a) 文件级不改厂商 so；(b) 连 stock `.ko` 也用原厂（V1）；(c) 运行时必须真的在用厂商 Mali | (a) 允许路线 J/E1；(b) 只能路线 D；(c) 只能路线 C/D |
| 2 | **出画目标** | T1 保活 / T2 搬运填充 / **T3 真渲染** | T3 在 Apple Silicon 宿主 = 研究级 ISA 翻译；要 T3 就上路线 C |
| 3 | **宿主形态** | 保持 Apple Silicon / 换成 ARM + Mali 板 / 真机 | 唯一把 T3 从"无先例研究"变"有把握工程"的开关；也是 R6（缺 oracle）的解法 |
| 4 | **密度与成本** | 单机多实例（虚拟化）/ 1:1 真机（G1）/ 容器（G2） | G1/G2 不需要 GPU 虚拟化，是商业上已验证的答案 |
| 5 | **下一步要"深挖哪条"** | A/B（硬件仿真规格）、C（宿主选型）、E1/J（恢复出画）、组合计划 | 决定"下一轮 0–D"的优先级与文档深度（我建议：**先做下一轮 0 的否定性验证**，再按组合打法同时深化 C 与 E1/J） |

> 我的建议顺序：**下一轮 0（否定性验证，1 天内可完成）→ 路线 E1/J 方案（保出画）→ 路线 C 选型（保真出画）→ A/B 规格表（硬件仿真本体）**。
> 理由：E1/J 成本最低且立刻恢复"能出画"，能把"gold-15 现在为什么不行"这一问题与"GPU 硬件仿真"彻底解耦；
> A/B 的规格表在否定性验证有结论后再写，避免为不可行路线投入文档工作量。

---

## 附录 A · 本轮取证复现命令

```bash
# 1) 解出官方 vendor_dlkm（EROFS）并分析模块元信息/符号
fsck.erofs --extract=out/tmp/gold-hw-emu/official-dlkm --no-preserve-owner \
  devices/xiaomi/gold-15/roms/gold_global_images_OS3.0.5.0.VNQMIXM_15.0/vendor_dlkm_a.img
python3 tools/analyze_ko.py official-dlkm/mali_kbase_mt6833_r49.ko

# 2) 厂商闭包 vs 当前 CDH 内核的可加载性矩阵（CRC 级）
python3 tools/loadability_matrix.py \
  devices/xiaomi/gold-15/out/kernel/Module.symvers official-dlkm

# 3) 官方 boot.img 内核 + 内嵌 config（IKCFG）
python3 tools/parse_boot.py official-boot official-boot/boot.img

# 4) 官方 DTB（在 vendor_boot.img 内）图形节点
python3 tools/fdt_dump.py official-boot/vendor_boot.img mali gpufreq ged scpsys vgpu

# 5) round 2：JM 作业格式取证（只读源码 + 既有实测；现场见
#    evidence/issue-gold15-gpu-jm-format/log-pass.txt 的 P1–P5）
cd devices/xiaomi/gold-15/kernel/modules/mali_vgpu/kbase
grep -rn "exception_status\|job_type\|jd_job\|job_descriptor" drivers include  # 期望 0 命中
sed -n '116,124p' backend/gpu/mali_kbase_jm_hw.c                # select_job_chain：jc 来自 atom
sed -n '228,237p' hw_access/regmap/mali_kbase_regmap_jm_macros.h # JS_FEATURE_* = 作业类型编号
sed -n '181,189p' mali_kbase_gpuprops.c                          # fragment = bit 9
sed -n '920,933p' ../wire-kbase.sh                               # 已解出的 Fragment/FBD/RT 规格
sed -n '29,45p'  mmu/mali_kbase_mmu_mode_aarch64.c               # 页表 ATE/PTE 编码
```

## 附录 B · 关键结论的依据索引

| 结论 | 依据 |
|---|---|
| 现行路线 = ARM 内核内 dummy 模型 | `kernel/build/build-kernel.sh:202-204`；`kbase/backend/gpu/mali_kbase_model_dummy.c` |
| 驱动被改 2127 行 / 6 组 kbase 补丁 | `kernel/modules/mali_vgpu/wire-kbase.sh` |
| stock 厂商模块无法在当前内核加载 | `raw/loadability-matrix.txt`（1005 / 207 / 232 / 45） |
| vermagic 串不同但可加载 | `kernel/source/kernel/module/version.c:77-88` |
| 官方内核无 virtio-net/gpu/input/snd | `raw/official-kernel-config-key.txt` |
| 官方硬件契约（地址/中断/时钟） | `raw/official-dtb-gpu-nodes.txt` |
| Mali 寄存器接口面与工程量 | 子代理报告（kbase r49p0 源码 file:line 清单）；`raw/` 未含（源码级证据） |
| 宿主 GPU 通路已存在 | `out/qemu/dist/darwin/{bin,lib,libexec}`；[Display.md](../../../../../core/host/daemon/Display.md) §2 |
| DTB 与模块依赖闭包 | `raw/official-modinfo.txt` |
| **job chain 只由用户态写、kbase 不解析** | uapi `base_jd_atom_v2`（`kbase/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h:633`）+ `kbase/backend/gpu/mali_kbase_jm_hw.c:116-124`；全树 grep 描述符关键字 0 命中 → [issue-gold15-gpu-jm-format](evidence/issue-gold15-gpu-jm-format/solution.md) |
| **作业类型枚举（Type 值 = `JS<n>_FEATURES` 位号）** | `kbase/mali_kbase_gpuprops.c:181-189`、`kbase/hw_access/regmap/mali_kbase_regmap_jm_macros.h:228-237`；自洽校验 = 实测 Fragment Type 9 |
| **v9 Fragment Job → FBD → RT 布局已解出** | `wire-kbase.sh:920-933`（规格）+ run81-83 `filled=163200`（[log-pass.txt](evidence/issue-gold15-vendor-gpu/log-pass.txt)）→ [raw/jm-descriptor-facts.md](evidence/issue-gold15-gpu-jm-format/raw/jm-descriptor-facts.md) §3 |
| **设备模型剩余两项确定性工作（页表/chain 遍历）** | `kbase/mmu/mali_kbase_mmu_mode_aarch64.c:29-45,89-105`、`kbase/mali_kbase_defs.h:98-102`、`mali_base_jm_kernel.h:475-523` |
