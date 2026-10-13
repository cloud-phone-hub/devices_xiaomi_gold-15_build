# issue-gold15-bprime-p3-int-mod-subset —— B′ P3：整数字节选择（.b0）与异步 flow 修饰收尾建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-attr-subset](../issue-gold15-bprime-p3-attr-subset/solution.md)（遗留 #1「候选 ③：整数字节选择等小修饰与异步 flow 标志收尾」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

在真机捕获的 160 条指令中，内存族（33 条）与顶点属性通路（10 条）建模后，不支持指令已降至 14 条。此时剩余的 33 条未直译阻碍全部分布在修饰字段：
- **整数字节选择修饰**：真机 `bin-007f00006c80` 第 1 条指令 `0x08a0c200100ac739` 为 `IMUL.i32.wait0 r2, r57, 0xC0B0A090.b0`，其 `src1` 包含 `widen=4`（即 `.b0` 提取低字节），被旧版 `src_mod_mask()` 统一当作未建模修饰拦截（exit 2）；
- **异步 flow 槽等待修饰**：真机 30 条指令携带 `wait0`（23 条）、`wait01`（2 条）、`wait`（2 条）、`wait02`（1 条）、`wait0126`（1 条）、`wait1`（1 条），另有 4 条携带 `reconverge`，因语义未归位而被阻断在直译门外。

本批针对整数字节选择与异步 flow 标记实施精确的架构级建模与收尾。

## 2. 根因（语义事实与架构建模）

1. **整数字节选择语义与 ISA 元数据解析（.b0）**：
   - 在 Mesa Valhall ISA 中，32 位整型指令（`IADD.u32` / `ISUB.u32` / `IMUL.i32`）的源修饰重用 `widen` 字段（`src0` offset 36, `src1` offset 26, mask 0xF），映射到 `swizzles_32_bit` 枚举：
     - `widen=0`：`none`（全 32 位字取值）；
     - `widen=4`：`b0`（提取第 0 字节，即 `(x >> 0) & 0xFF`）；
     - 其余 `h0`（2）、`h1`（3）、`b1`..`b3`（5..7）等在当前真机样本中未出现，严格执行 fail-loud；
   - **SPIR-V 翻译**：
     - 常量源（`lut`）：直接在翻译期进行低 8 位折叠（`val & 0xFF`），例如 `0xC0B0A090.b0` 折叠为 32 位常量 `144`（`0x90`）；
     - 寄存器源（`reg`）：生成 `OpBitwiseAnd %t_i32 %r %v 0xFF` 掩码指令；
     - 守卫校验：在 `_int_src(opnd)` 中检验 `widen in (0, 4)`，非 0/非 4 抛出 `ValueError`。

2. **异步 flow 槽等待与合流修饰在 SPIR-V 中的架构归位**：
   - Valhall 硬件架构中，`wait0/wait1/wait01/wait02/wait0126/wait` 用于指示硬件调度器等待特定异步槽（内存/纹理/属性）返回，`reconverge` 用于分支重聚合；
   - 在 SPIR-V / Vulkan / MoltenVK 阶段内单线程数据流模型中，指令以严格静态 SSA 数据流顺序执行。前序载入（如 `LOAD` / `LD_PKA` / `LD_ATTR_IMM`）在 SSA 数据流中已被定义为即时就绪的值，无需硬件插槽等待；
   - **架构决策**：对上述 11 种槽等待及合流标记（`FLOW_PASSTHROUGH`）建模为**逻辑屏障无操作放行**；
   - **安全守卫**：保留码及未建模的流控制（如 `flow=discard`、`wait_resource`）保持显式 fail-loud（exit 2），严防错误流转。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - 修改前 `0x08a0c200100ac739` 被 `src_mod_mask` 拒绝（exit 2）；
  - `fl_ialu_widen`（widen=2，.h0 半字选择）精准拦截并报告 `未建模的整型源 widen=2（仅支持 .none 与 .b0）`（exit 2）；
  - `imul_b0_neg` 配错期望时 7812 像素精准报错（exit 1），证实测试灵敏度与无假阳性。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **全量回归矩阵 50/50 PASS**（31 渲染正例含新增 imul_b0 + 5 错误对照 + 14 fail-loud 含 fl_ialu_widen）；
  - `imul_b0` 逐像素比对（期望 255, 255, 255, 255）**7812 像素全部通过，失败 0，exit 0**；
  - 覆盖真机字 `0x08a0c200100ac739`（`IMUL.i32.wait0 r2, r57, 0xC0B0A090.b0`）：**100% BIT-MATCH**；
  - 30 条异步等待与 4 条合流标记全部通过 `FLOW_PASSTHROUGH` 放行。

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - 定义 `INT_ALU_MODELED = {"IADD.u32", "ISUB.u32", "IMUL.i32"}`；
   - `src_mod_mask()`：放行 `INT_ALU_MODELED` 的 `widen` 字段；
   - 新增 `FragBuilder._int_src(opnd)`：实现 `widen in (0, 4)` 守卫、常量折叠及 `OpBitwiseAnd(0xFF)` 提取；
   - `_emit_ialu()`：调用 `_int_src(a)` 与 `_int_src(b)`；
   - `main()`：加入 `FLOW_PASSTHROUGH` 集合及非白名单 flow 拦截校验。
2. **`out/tmp/p2/frag_imul_b0.asm` / `frag_imul_b0_neg.asm`（新建）**：
   - 覆盖真机真实切片 `IMUL.i32.wait0`、常量源 `.b0`、寄存器源 `.b0`、`IADD.u32` 混合计算及负向对照。
3. **`out/tmp/p2/fl_ialu_widen.asm`（新建）**：
   - 非法 widen（widen=2）fail-loud 拦截用例。
4. **`out/tmp/p2/run_matrix.py`（改）**：
   - 矩阵接入 `"imul_b0"`、`"imul_b0_neg"`、`"fl_ialu_widen"`，全量用例数扩充至 50 项。

## 5. 验证（2026-10-13，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| 回归矩阵 run_matrix.py | **50 PASS / 0 FAIL**（31 正例 + 5 对照 + 14 fail-loud） |
| frag_imul_b0 逐像素 | 三角形内 7812 px 全 (255, 255, 255, 255)、失败 0、exit 0 |
| 负向对照（imul_b0_neg） | exit 1，逐像素失败 7812 px（期望与渲染值不符） |
| fl_ialu_widen 拦截 | 翻译 exit 2（未建模 widen fail-loud 守卫） |
| 真机指令回环 | 0x08a0c200100ac739 100% 逐位命中（100% BIT-MATCH） |

**真机覆盖率重算（160 字全量，同口径）**：

| | 可直译 | 未建模修饰 | 不支持指令 | 源形态不支持 | 覆盖率 |
|---|---|---|---|---|---|
| 基线（LD_ATTR_IMM 建模后） | 113 | 33 | 14 | 0 | 91.25% |
| **本批（整数字节修饰 + 异步 flow 收尾）** | **145** | **1** | **14** | **0** | **91.25%** |

- **可直译指令数从 113 跃升至 145 条（+32 条，直译率高达 90.62%）**；
- **未建模修饰从 33 条骤降至 1 条（-32 条，仅剩 1 条 `flow=discard`）**；
- 不支持指令数维持 14 条（总覆盖率 91.25%）。

## 6. 遗留

1. **剩余 14 条不支持指令**：
   - `LD_VAR_BUF_IMM.f16`（3 条）：flat16 顶点/片元传递；
   - `CLPER.i32`（3 条）：subgroup warp shuffle 算子；
   - `LEA_BUF_IMM`（2 条）：缓冲区有效地址计算；
   - 其余长尾各 1 条：`IADD.s64`、`ATEST`、`BLEND`、`BRANCHZI`、`LD_TILE`、`SHADDX.u64`；
2. **剩余 1 条未建模 flow**：
   - `bin-007f00023e80[03]`：`IADD_IMM.i32 flow=discard`。
