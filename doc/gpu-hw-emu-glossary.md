# gold-15 GPU 方案术语表（含 ISA 详解）

> 用途：本目录文档涉及大量图形/虚拟化术语，这里给出一句话定义 + "在本项目里为什么重要" + 证据出处。
> 排序：从最基础的分层概念开始。

---

## 1. 三个"语言层级"（理解本文档集的钥匙）

| 层 | 是什么 | 例子 | 可移植性 |
|---|---|---|---|
| **API（应用接口）** | App 调用的图形接口 | OpenGL ES、Vulkan、EGL | 标准化，跨厂商可移植 |
| **IR（中间表示）** | 着色器编译器的中间语言（**不是**硬件指令） | SPIR-V（Vulkan）、DXIL（D3D）、NIR（Mesa 内部） | **跨厂商可移植**——宿主的编译器能把 IR 编成自己的 ISA |
| **ISA（指令集架构）** | **硬件真正执行机器码的"母语"**：指令编码、寄存器、数据类型、寻址方式 | ARM64（CPU）、Mali ISA（GPU）、Adreno ISA、SASS（NVIDIA）、Air（Apple，私有） | **不可移植**：只有实现了同一 ISA 的硬件能直接跑 |

**本项目全部困难都源自一件事**：厂商 Mali DDK 把着色器编成了 **Mali ISA 机器码**（而不是 SPIR-V），
所以：
- 宿主若是**同 ISA** 的 Mali → 直接把机器码交给它执行（路线 C，"搬运"）；
- 宿主若是**别的 ISA**（Apple Metal / Adreno / NVIDIA）→ 必须先做 **ISA 翻译**（T3-S，研究级）；
- 而 `venus/gfxstream` 之所以通用，是因为它们停在 **IR/API 层**（把 SPIR-V 或 API 调用转给宿主，由**宿主自己的编译器**生成宿主 ISA）。

---

## 2. ISA 详解（回答"ISA 是什么"）

**定义**：ISA = *Instruction Set Architecture*，一套处理器"机器语言"的完整契约：
① 有哪些指令（opcode 与功能）；② 指令的**二进制编码**（每一位什么意思）；
③ 有哪些寄存器/特殊寄存器；④ 数据类型与精度；⑤ 内存寻址与对齐规则；⑥ 异常/同步语义。

**为什么它是"契约"**：编译器按 ISA 生成机器码，硬件按 ISA 解释这些比特。
两边必须一致——**换 ISA 就等于换语言**，机器码不能跨 ISA 复用。

**在本项目里的具体形态**：

| 事实 | 说明 | 证据 |
|---|---|---|
| 客机 GPU 的 ISA = **Mali Valhall（v9.0，arch 9.0.8，产品 G57）** | 厂商 DDK 编译出的 shader 只能被 Valhall v9 硬件执行 | [gpu-hw-emu-spec.md](gpu-hw-emu-spec.md) §2.2A（身份 `0x09030010`） |
| 编译器在**用户态 so 里** | 所以 `libGLES_mali.so` 有 53 MB——它内含 shader 编译器与整套 ISA 后端 | [gpu.md](gpu.md) §1.2 |
| ISA 的**编码表**公开（但只有编码，没有语义编译器） | Mesa `src/panfrost/compiler/bifrost/valhall/ISA.xml` = Valhall 指令编码表；另有 `valhall_dis` 反汇编器 | [gpu-hw-emu-refs.md](gpu-hw-emu-refs.md) §4 |
| ISA 的代际互不兼容 | Midgard(v4/v5) ≠ Bifrost(v6/v7) ≠ **Valhall(v9)** ≠ v10+(CSF) | [gpu-hw-emu-routeC.md](gpu-hw-emu-routeC.md) §1.1 第②层 |
| ISA 级属性会变成工程约束 | **程序计数器只有 24 位** ⇒ shader 不能跨 2²⁴ B 边界，blob 用 2²⁴ B 池；这直接限制了宿主侧可执行 BO 的摆放 | [gpu-hw-emu-refs.md](gpu-hw-emu-refs.md) §3/§5（外部原文） |
| B2 v1 之所以能出"部分画"，正因为它**不碰 ISA** | 只解析描述符找到 RT 并填色；T3 卡死也正因为必须执行 ISA | [gpu-hw-emu-abi.md](gpu-hw-emu-abi.md) §2/§3 |

**"ISA 翻译"是什么（以及两种做法的性能差异）**：
把一种 ISA 的机器码转成另一种 ISA 可执行的形式。三条路：

| 做法 | 例子 | 性能量级 |
|---|---|---|
| **同 ISA 直发**（不翻译） | 宿主 = 同款 Mali（路线 C） | 宿主原生 |
| **编译式翻译**（反编译 → IR → 宿主 ISA，编译一次并缓存） | 类比：Rosetta 2（x86→ARM64，CPU）；Metal Shader Converter（DXIL→Air） | 宿主原生 0.3–0.8×（本项目的粗略估计，见 [t3 §3.5](gpu-hw-emu-t3.md)） |
| **CPU 解释执行** | QEMU TCG、软件光栅 | 差 1–2 个数量级（只适合做正确性 golden） |

> ⚠️ **术语歧义提醒**：本文档集里的 ISA **一律指"指令集架构"**；
> 与 PC 老总线 *Industry Standard Architecture*（8/16 位 ISA 插槽）无关。

---

## 3. 作业/驱动层术语

| 术语 | 一句话 | 在本项目里 |
|---|---|---|
| **atom（原子作业）** | 用户态提交给内核的**一个工作单元**描述（64/72 B），含 `jc`、`core_req`、依赖等 | 厂商 UK 11.46 发 **v1 形状 + stride 72**；内核必须按 v1 解析（`jc@0x08`）——[abi.md](gpu-hw-emu-abi.md) §1.3 |
| **job chain（作业链）** | atom 的 `jc` 指向的**GPU 内存里的描述符链表**（硬件格式） | 链上执行顺序由 **dep1/dep2 scoreboard** 决定，不是 `next` 顺序——[refs](gpu-hw-emu-refs.md) §1 |
| **job（作业）** | 链上的一项，有类型：NULL/SET_VALUE/CACHE_FLUSH/COMPUTE/VERTEX/GEOMETRY/TILER/FUSED/FRAGMENT（Valhall 另有 MALLOC_VERTEX/FULLSCREEN） | gold-15 实测只出现 **FRAGMENT + TILER**——[ddk-job-mix](evidence/issue-gold15-gpu-jm-format/raw/ddk-job-mix.md) |
| **JM / CSF** | 两种 GPU 提交模型：**JM**=Job Manager（v9 及以前，寄存器槽 + 作业链）；**CSF**=Command Stream Frontend（v10+，MCU 固件 + 共享接口区） | gold-15 = **JM**；CSF 整支不在范围——[spec](gpu-hw-emu-spec.md) §2.2D |
| **kbase** | ARM 的 Mali 内核驱动（厂商版本是 MTK fork，闭源 `.ko`） | 现行路线用**上游 kbase 源码自编**；stock `.ko` 在本内核上符号 CRC 不符——[hw-emu](gpu-hw-emu.md) §0.2 |
| **panfrost / msm** | 主线的 Mali 驱动 / 高通 Adreno 驱动 | 路线 C 的宿主侧基础：panfrost 收 `SUBMIT.jc`（硬件作业链）——[routeC](gpu-hw-emu-routeC.md) §1 |
| **FBD** | Frame Builder Descriptor：片元输出描述（宽高、RT 数组、tiler 结构、clear、CRC/AFBC） | T2 从 FBD 里取到 RT 并填色——[abi.md](gpu-hw-emu-abi.md) §2 |
| **RT（render target）** | 渲染目标缓冲（屏幕上某一层画到哪块内存） | 实测为 **AFBC** 压缩格式；`fill` 已成功但上屏链未通 |
| **tiler / polygon list / tiler heap** | Mali 分块渲染：顶点阶段产出图元列表（16×16 tile），片元阶段逐 tile 消费；堆是它的临时内存 | JM 下 kbase 不管理它 → 设备模型必须自己实现——[ddk-job-mix](evidence/issue-gold15-gpu-jm-format/raw/ddk-job-mix.md) §5 |
| **AFBC** | ARM Frame Buffer Compression：帧缓冲压缩格式（含 solid-color 头） | 解释了"填色与理论值吻合（含 AFBC solid 头）"这一实测 |
| **TBDR** | Tile-Based Deferred Rendering（分块延迟渲染）：Mali 与 **Apple GPU 都是** | 所以"跳过 tiler 整帧光栅化"在 Apple 宿主上仍保留分块带宽优势——[t3 §3.5](gpu-hw-emu-t3.md) |

---

## 4. 宿主桥接与虚拟化术语

| 术语 | 一句话 | 在本项目里 |
|---|---|---|
| **virtio-gpu / blob / EDID** | QEMU 的虚拟 GPU 前端；`blob=on` 让 guest 缓冲以共享内存存在 | CDH darwin/linux 两条宿主通路都基于它 |
| **virgl** | 把 guest 的 **OpenGL** 命令转给宿主 GL 的协议 | 与 `venus` 并列的两条通路之一 |
| **venus** | 把 guest 的 **Vulkan** 命令转给宿主 Vulkan 的协议（**独立于客机 GPU 厂商**） | gold-15 的 Vulkan 出画只能靠它（panvk 对 v9 无支持） |
| **MoltenVK** | Vulkan → Metal 的翻译层（Apple 平台唯一可用路径） | `out/qemu/dist/darwin/lib/libMoltenVK.dylib` 已就位 |
| **interposer** | 自写一层同名 so"顶替"API 面：转发给真实实现并覆写返回值 | note13 的 `libGLES*_meow.so`（覆写 `glGetString` 等，让 App 看到 Mali 身份）——[e1j](gpu-hw-emu-e1j.md) §1.3 |
| **扫描面（scan face）** | 只为让 stat/access/dlopen 类探测看到真机文件而**逐字节回填**的库 | note13 的 `scan-libs`（并明确"严禁回填三条"）——[e1j](gpu-hw-emu-e1j.md) §1.3 |
| **host pointer / blob** | 宿主与客机共享同一块内存（免拷贝） | virglrenderer 的 venus host-pointer 补丁已在本仓库落地 |
| **IOMMU_NOEXEC** | 宿主侧把导入的 dma-buf 映射为**不可执行** | 所以 **shader 必须拷进宿主自建的"可执行 BO"**——[routeC](gpu-hw-emu-routeC.md) §3 第 1 条 |
| **dma-buf / udmabuf** | 跨设备共享缓冲的 Linux 机制；`udmabuf` 可把 guest 内存变成 dma-buf | 路线 C 内存分类处置里的"数据面零拷贝"通道 |
| **SAME_VA** | Mali 用户态与 GPU 用同一虚拟地址的语义 | 所以实测 atom 的 `jc` 与用户态 VA 相同（免换算，仅限客机内） |

---

## 5. 一句话总览

**API 是"点什么菜"，IR 是"菜谱的世界语"，ISA 是"厨师母语"**。
厂商 DDK 只给"厨师母语写的菜谱"（Mali ISA 二进制）——所以要么换一个**说同样母语的厨师**（同族 Mali 宿主），
要么请一个**翻译**（ISA 翻译，研究级），要么**换掉写菜谱的人**（客机改用 Mesa，走 IR/API 层 → 任意宿主）。
