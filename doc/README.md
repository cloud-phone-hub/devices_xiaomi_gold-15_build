# gold-15 构建文档索引

> 设备：Xiaomi Redmi Note 13 5G（codename `gold`，board `mt6833`）**全局版 ROM** `OS3.0.5.0.VNQMIXM`
> 模板：`devices/xiaomi/note13-5G-15`（同硬件中国版）

## 构建流水线状态（2026-10-09）

`main.py` 尚未移植，各阶段按流程顺序直接调用。

| 阶段 | 脚本 | 状态 | 产物 |
|---|---|---|---|
| unpack | `scripts/unpack-roms.py` | ✅ | `roms/gold_global_images_OS3.0.5.0.VNQMIXM_15.0/`（super.img 展开 + 8 个逻辑分区） |
| kernel | `scripts/build-kernel.py` → `kernel/build/build-kernel.sh` | ✅ | `out/kernel/Image`（`6.6.127-4k-cdh`）+ `out/dist/kernel/modules/*.ko`（11） |
| boot | `scripts/build-boot.py` | ✅ | `out/dist/rom/boot.img` |
| vendor_boot | `scripts/build-vendor-boot.py` | ✅ | `out/dist/rom/vendor_boot.img` |
| vendor | `scripts/build-vendor.py` | 🟡 **仅 keymint 已移植** | `out/dist/rom/vendor.img` |
| vendor_dlkm | `scripts/build-vendor_dlkm.py` | ✅ | `out/dist/rom/vendor_dlkm.img` |
| super | `scripts/build-super-lpmake.py`（官方 super 基底 + 原位替换 vendor/vendor_dlkm） | ✅ | `out/dist/rom/super.img` |
| pack | `scripts/pack-roms.py` | ✅ | `out/device/gold-15/15/os3.0.5.0.vnqmixm/{vda.img,data.qcow2}` |

未移植：`build/main.py`、`build/vendor.py`(=`build-vendor.py`)、`build/vendor/build/**`（12 个驱动模块）、
`build/vendor/source/**`（17 个 HAL 子项目，1499 文件）、`build-patch.py`、`build_odm.py`、`build/odm/`。

> ⚠️ `build/vendor/source/**` 目前**只移植了 `android.hardware.keymint`**；
> `build/vendor/build/sepolicy.py` 目前**只移植了 keymint 段**（note13 该文件含约 20 段，
> 含 `pioneer`、audio/radio/wifi/camera 等）。详见 `issue-gold15-keymint-port` 的「遗留」。

前置配置：`build/manifest.json`（schema 2）、`build/baselines/<build-id>.json`（`rom` 段与
`vendor-dlkm.baseline-build-prop` 已实测回填）、`kernel/{build,modules,source}`（source 走 `gold-15` 分支）。

## 证据目录索引

| issue | 主题 | 结论 |
|---|---|---|
| [issue-gold15-gpu-jm-format](evidence/issue-gold15-gpu-jm-format/solution.md) | **路线 A/B 否定性验证：JM 作业格式边界定案**（round 2） | **R4 从"未知量"降级为"未移植量"**：`jc` 由**用户态 DDK** 传入、kbase **不解析**描述符（全树 0 命中）→ 描述符是**硬件格式**，panfrost genxml 即公开参考；作业类型 = `JS<n>_FEATURES` 位号（NULL=1…TILER=7、**FRAGMENT=9**），与项目实测 Fragment `Type=9` **自洽**；**v9 Fragment Job(64B)→FBD→RT 布局已被项目解出并落地**（`wire-kbase.sh:920-933`，run81-83 `filled=163200` 含 AFBC solid 头）；设备模型仅剩 2 项确定性工作（**页表遍历** + **chain/renderpass 四链遍历**）→ 判定 **T1/T2 = GO、T3 = NO-GO（Apple Silicon 宿主）**，剩余未知 = TILER/VERTEX/GEOMETRY/COMPUTE 描述符 + 图形状态 + **shader ISA** + AFBC 解 tile + tiler heap |
| [issue-gold15-gpu-hw-emu](evidence/issue-gold15-gpu-hw-emu/solution.md) | **GPU 硬件仿真可行性取证**（不改 vendor 实现 + 仿真 GPU 桥接宿主 GPU） | **取证闭环（2026-10-12）**：厂商图形闭包 14 模块 **1005 条符号引用，当前内核仅 207 条 CRC 一致、232 条不符、45 个符号不存在** → 「连厂商 .ko 都不改」必须换 MTK 官方内核基座；vermagic 串差异**不是**门（`same_magic` 忽略版本串，`kernel/module/version.c:77-88`）；官方内核**无 virtio-net/gpu/input/snd**；官方 DTB = `mali@0x13000000+0x4000` + IRQ 364–368 + MFG/SCPSYS/PMIC 契约；设备模型只需 **MMIO + 3 条中断**（必须档 ≈2.3k–4.1k 行）。**外部调研**：无公开"寄存器级仿真 Mali + 宿主 GPU"先例、gfxstream/VirGL/Venus 是 API 重映射（与不改 Mali DDK 互斥）、**PanVK 对 Valhall v9 不支持**、无 Mali ISA 反编译器、ARM Fast Models 仅 v10+ 且需自写 `libnwhal`、**无真 Mali oracle 则正确性难收敛** → **"stock 栈 + 真出画"只有"宿主换真 Mali 板"有成功把握** |
| [issue-gold15-kernel-pipeline](evidence/issue-gold15-kernel-pipeline/solution.md) | 内核构建流水线接入（四段跑通） | kernel/boot/vendor_boot/vendor_dlkm 通过 |
| [issue-gold15-keymint-port](evidence/issue-gold15-keymint-port/solution.md) | 移植 Remote KeyMint（原地替换官方 mitee 三件套） | **keymint 端到端可用**（4 实例注册 + keystore2 建钥 + `/data` 元数据加密挂载成功）；引导仍未到 `sys.boot_completed`（已定位：32 位件 ENOEXEC / `/vendor/bin/daemon` 缺失 / `pioneer` 域未移植） |
| [issue-gold15-vendor-gpu](evidence/issue-gold15-vendor-gpu/solution.md) | 让厂商图形栈跑起来：内核侧虚拟 Mali + virtio-gpu 的 MTK KMS 形态对齐 | **M1 + M2（L1）达成，稳定性三层攻坚闭环**（run21-66）：`/dev/mali0` 身份 `0x09000003/r0p1`、DDK 兼容归零；connector DSI + 49 MTK KMS 属性 + CREATE_SESSION/dumb/fb-modifiers 全链通；GED 桥 stub；stride72 放行；cmar NOP + type=1 跳过（SEGV 归零）；`ro.frp.pst` 置空 + mi_display 桩 → **L1（boot_completed=1）**；vblank 死状态自愈（waitNextVsync 2820→0）；**补丁 15 ptrace 自愈：crash_dump64 读 guest state EFAULT 挂死不 detach → SF 永久 tracing stop → watchdog 155 次/33min；内核检测 TASK_TRACED 杀 tracer 自动复活 SF → watchdog 155→3 次（-98%）**。遗留：自愈限频穿透、AtomicCommit 慢（Scout 判挂上游诱因，B2 渲染桥层）、M3 出画 |

各自目录内 `log-fail.txt`（阻塞现场）/ `log-pass.txt`（通过现场）为配套原始记录。

## 专项方案文档

| 文档 | 主题 |
|---|---|
| [gpu-hw-emu-glossary.md](gpu-hw-emu-glossary.md) | **术语表（含 ISA 详解）**：三个语言层级（API/IR/**ISA**）→ 为什么"厂商 DDK 的 shader 是 Mali ISA 机器码"是本项目全部困难的根源；ISA 定义六要素、Mali Valhall v9 的实际形态与证据（ISA.xml 只是编码表、24 位 PC、B2 不碰 ISA 所以只能出部分画）、ISA 翻译三种做法与性能量级；另含作业/驱动层（atom/job chain/JM-CSF/kbase/panfrost-msm/FBD/RT/tiler/AFBC/TBDR）与宿主桥接层（venus/virgl/MoltenVK/interposer/扫描面/NOEXEC/udmabuf/SAME_VA）术语 |
| [gpu-hw-emu-abi.md](gpu-hw-emu-abi.md) | **厂商 so → 内核驱动的数据面（绘制路径 ABI）**——回答"绘制时 so 传给内核什么"：**两条通道** = ① ioctl 只传**指针+元数据**（`JOB_SUBMIT{addr,nr_atoms,stride}` + **v1 `base_jd_atom` 布局**（`jc@0x08`/`core_req@0x34`，厂商 stride=**72**））；② 真正的绘制内容（**job 链/FBD/RT/shader 二进制/顶点缓冲/状态**）全在 **GPU 内存**里由 so 自己填好，内核必须**按 GPU VA 经 kbase 页表去读**。含内存/同步/完成事件（`base_jd_event_v2`）接口、结构树、以及在"so 不改+内核可改+宿主 GPU 干活"下的三条实现形态与**最小数据面清单**（T1/T2 与 T3 的增量） |
| [gpu-hw-emu-adreno.md](gpu-hw-emu-adreno.md) | **把 Qualcomm Adreno 纳入考虑**（用户提问）：区分三种诉求——(i) **客机换成 Adreno 设备 → 不必仿真**（本地实证：CDH 的 15R-16/SM8750 早就是 `libEGL_mesa.so` + minigbm + `vulkan.virtio`(venus)/lavapipe 打到宿主 GPU，遗留只是库链/格式兼容）；(ii) **拿 Adreno 当宿主解决 gold-15 → 不成立**（跨厂商仍要 ISA 翻译）；(iii) **为 Adreno 做硬件仿真 → 有公开先例**（39c3 LibAFL QEMU：从零虚拟化高通内核 + KGSL + **部分 Adreno 仿真**，>10 个自研 QEMU 设备，用于 fuzz，**不出画**）→ 13 条路线逐条对照 + 分厂商策略矩阵 + 最小路线图 |
| [gpu-hw-emu-refs.md](gpu-hw-emu-refs.md) | **外部权威资料索引与结构事实**（round 7）：Mesa panfrost 的 JM 描述符文档/作业链语义/genxml 权威位置——**交叉验证本地实测**：作业类型枚举双源一致、Fragment Job 逐字段确认（`mali_payload_fragment.framebuffer` = 我们实测的 `FBD@0x28`）、24 位 PC/2²⁴ shader 池印证宿主 16 MB 约束；给出 **job 链 = 依赖 scoreboard**（执行顺序≠`Next` 链）、Tiler/Vertex/Fused/Compute 结构、**图形状态落点**（`mali_shader_meta`/`mali_viewport`/`midgard_tiler_descriptor`/`bifrost_fb_extra`）、Valhall 新增作业类型 `MALLOC_VERTEX`/`FULLSCREEN` |
| [gpu-hw-emu-e1j.md](gpu-hw-emu-e1j.md) | **路线 E1/J 实施方案（出画保底）**：发现本仓库**已有投产的路线 J 实现**——note13-5G-15 的 `android.hardware.gpu`（EGL 三件套 interposer + Vulkan interposer 包装 venus + 扫描面逐字节回填 + **严禁回填三条** + 属性/标签），**gold-15 尚未移植**；gold-15 官方 vendor 的 `ro.hardware.egl=meow`/`vulkan=mali` **恰好与 interposer suffix 对齐**（无需改属性）；**唯一硬冲突 = 显示/分配面**（MTK gralloc AFBC/AFRC + `hwcomposer=mtk_common` vs minigbm + `hwcomposer=drm`）→ 完整形态必须改 `/vendor/build.prop` 一行并停用 MTK composer；含 **E1 vs J 的关系**、内核侧连带收益（2127 行补丁整类失效）、六种组合的验收矩阵 |
| [gpu-hw-emu-routeC.md](gpu-hw-emu-routeC.md) | **路线 C（宿主=同族真 Mali）桥接可行性 go/no-go**——全部机制性结论**本地源码确证**：panfrost 的 `SUBMIT.jc` 就是「硬件 job 描述符的 GPU 地址」、内核写 `JS_HEAD_NEXT`+`JS_COMMAND_NEXT=START`，与客机 kbase **寄存器级同构** → 桥接是「**搬运 + 指针重写**」而非翻译；GPU VA 由内核分配（不能保 VA）→ 必须重写链；**导入 dma-buf BO 一律 `IOMMU_NOEXEC`** → 着色器须拷进自建可执行 BO（且**不跨 16 MB**，PC 24 位）；tiler heap 用 `PANFROST_BO_HEAP` 当宿主 scratch；数据面走 udmabuf/prime 导入零拷贝；宿主支持面（`arm,mali-valhall-jm`、`mediatek,mt8186-mali`、`g57 0x9001/0x9003`）；**判定 GO**（前置=同族 v9.0 宿主 + oracle） |
| [gpu-hw-emu-t3.md](gpu-hw-emu-t3.md) | **T3（真渲染）可行性矩阵与取证清单**：把 T3 拆成五层（J-D 描述符 / **J-S 着色(ISA)** / J-F 固定功能 / J-M 内存格式 / J-Y 同步）逐层判定——**J-D/J-F/J-M/J-Y = 工程可做，J-S = 唯一研究级（无 ISA→SPIR-V 翻译器、Air 无公开汇编器）**；本地取证界定范围（DDK 实测硬件作业只有 **Fragment + Tiler**，软作业由 kbase 承担、JM 下 kbase 不管 tiler heap）；三条落地路线（**T3-C 宿主换同族 Mali → J-S 消失（首选）**、T3-S ISA→SPIR-V、T3-CPU 解释器）；7 项只读取证清单（标🔒需授权动探针）；go/no-go |
| [gpu-hw-emu-spec.md](gpu-hw-emu-spec.md) | **设备模型实现规格（T1/T2 可落地）**：设备形态（sysbus/vhost-user）、MMIO 窗口（**新发现：stage-2 MMU 在 0x10000+，官方 DT 只声明 0x4000 → 必须给 ≥0x10800**）、3 条中断（GIC SPI 364–366）与掩码语义、**691 条权威寄存器表**（[raw/kbase-regmap-v9_0.md](evidence/issue-gold15-gpu-hw-emu/raw/kbase-regmap-v9_0.md)，机器抽取 + 反向校验）、约 60 个需实现"行为"的寄存器（含 tNAx 常量值表：`GPU_ID=0x09030010`、`THREAD_FEATURES[31:30]=SOFTWARE` 等）、probe/复位/电源域/JS 槽/MMU 五套状态机、数据面（页表遍历/chain 遍历/Fragment→FBD→RT）、与宿主渲染进程的 T2 接口消息集、G0–G3 验收判据、9 项风险 |
| [gpu-hw-emu.md](gpu-hw-emu.md) | **GPU 硬件仿真方案**（不改 vendor 实现、仿真 GPU 桥接宿主 GPU）——**列全 4 大家族共 13 条路线**：家族 I 硬件仿真（A QEMU 设备模型 / B 客机内核内模型 / C **宿主换真 Mali 板（首选）** / D stock MTK 内核+全平台仿真 / F ARM Fast Models）；家族 II API 重映射与换栈（E1 **Mesa virgl+venus（保底首选）** / E2 panfrost+panvk（v9 无 Vulkan）/ H gfxstream+自研 ICD / J **用户态 API 拦截（灰区高性价比）** / K ioctl 层假 mali0 = 现状）；家族 III 不虚拟化 GPU（G1 真机云化 / G2 Android 容器 / L 只虚拟显示输入）；家族 IV 兜底（I 客机软件渲染 / M 只做检测面）。含四道门 G0–G4、宿主桥接三后端 + 踩坑清单、工作量与风险表（R1–R7）、组合打法（§3.14）、下一步文档计划 |
| [gpu.md](gpu.md) | 让「厂商图形栈」在云机跑起来的完整计划与实施记录（可行性分档 L1/L2/L3、选型对比、Phase A~E、里程碑 M0~M4、Phase A/B 实测结论） |