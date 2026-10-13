# issue-gold15-bprime-p3-shift-subset —— B′ P3：整数移位族放开（`LSHIFT_OR.i32` / `RSHIFT_AND.i32`）

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-int-alu-subset](../issue-gold15-bprime-p3-int-alu-subset/solution.md)
> （上批把整数域从「立即数/转换」补齐到「寄存器加/减/乘」，并**主动遗留**了本批移位族）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

上一批（[int-alu-subset](../issue-gold15-bprime-p3-int-alu-subset/solution.md)）放开整数二元 ALU 时，
`LSHIFT_OR.i32`/`RSHIFT_AND.i32`（真机 3 条）因 **`shift` 操作数属 ISA `lanes="true"` 元数据
（编码非常规 src 域，无法仅凭 ISA.xml 唯一定论位域）** 被主动排除（宁可 fail-loud 也不猜编码）。

P3 真机 160 条中移位族共 3 条：

| 真机指令 | 条数 | 出处 | 说明 |
|---|---|---|---|
| `LSHIFT_OR.i32` | 2 | bin-007f00006c80[05]/[07] | 左移后按位或 `dest=(A<<shift)\|B` |
| `RSHIFT_AND.i32` | 1 | bin-007f00026e00[03] | 右移后按位与 `dest=(A>>shift)&B` |

本批先做 **真机字 + `asm.py` 试装双向核验**确定 `shift` 操作数编码，再放开这两条指令。

## 2. 根因（决定性发现）

1. **两条指令同 opcode、以 opcode2 区分**（ISA.xml 实证，line 2883-2978）：
   - `RSHIFT_AND.i32`：`opcode 0xB4@48` + `opcode2 0x000@24`（mask `0x703`）；
   - `LSHIFT_OR.i32`：`opcode 0xB4@48` + `opcode2 0x101@24`。
   同族 `ARSHIFT_AND.i32` = `opcode2 0x400@24`（**`signed` 位 @34 落在 opcode2 掩码 `0x703` 内**，
   即算术/逻辑右移由 opcode2 而非独立位区分）。三者由 `match_instruction()` 的「多段域最具体者」自然区分。

2. **`shift` 源的编码定位（本批卡点，双向核验后才定论）**：
   三条 src 为 `<src widen="true">A</src>` / `<src lanes="true" size="8">shift</src>` / `<src not="true">B</src>`。
   `Source.__init__` 对 `index==1`（即 shift 源）把 `widen` 字段落在 **offset 26、mask 0xF**（非 index 0/2 的 36）。
   该 4 位即 **移位源的字节选择 swizzle**：`.b00`（低字节）= 枚举 `swizzles_8_bit` 的 `B0000`，索引值 **4**。
   - 关键证据：`asm.py` 组装的 3 条真机字与 P3 采样字**逐位一致**（见 §3），
     `.b00` → widen=4 的判断由此**实证钉死**，而非仅凭 ISA 元数据推断。

3. **位型与符号无关**：左/右移与按位或/与在 32 位下结果位型与有/无符号无关 →
   统一用既有 `t_i32`（`OpTypeInt 32 1`），**无需新增 unsigned 类型**。映射：
   `LSHIFT_OR.i32 → OpShiftLeftLogical(196) + OpBitwiseOr(197)`；
   `RSHIFT_AND.i32 → OpShiftRightLogical(194) + OpBitwiseAnd(199)`（真机取样均为逻辑右移）。

4. **两条未建模修饰必须拦住**（fail-loud，防错译）：
   - **`not_result`**：`Flag("not_result", 30)`（bit30）——「结果取反」语义，未建模；
     `src_mod_mask()` 只覆盖源修饰，**不含 bit30**，故在分派前显式守卫；
   - **B 源 `not`**：`<src not="true">B</src>` → `offset['not']=35`——「B 取反」语义，
     由 `src_mod_mask()`（ISA 元数据通用推导）**自动**纳入守卫；
   - **A 源 `widen`**（bit36-39）：由 `src_mod_mask()` 自动纳入守卫；
   - **shift 源字节选择**：`.b00` 已建模；非 `.b00` 时 `main()` 显式守卫（exit 2）。

   真机三条的 `not_result`/`not`/A-`widen` 位均为 0，`shift` 源均为 `.b00`，故可直译。

5. **P2 ABI（r0..r3 为 float RGBA）要求整数结果经转换输出**：`build()` 以 `st[2]` 定类型，
   整数型寄存器直接输出会与 `OpCompositeConstruct vec4<float>` 类型不符 →
   用例须以 `U16_TO_F32` 取低 16 位转 float 输出（承接整数族手法）。
   且颜色输出为 UNORM 映射（`round(clamp(f,0,1)*255)`），整数结果须**缩放**到 [0,1]
   才能得到可区分的中间像素值 —— 本批用 `MOV.i32 rN, 0x3DCCCCCD`（LUT 0.1）作缩放因子。

   > **教训**：缩放常量**不可用 `IADD_IMM.i32`** 物化 —— 它产出 `('ssa', id, 'int')`
   > 类型标签，`val_to_ssa(_, "float")` 会 SSA 直通不做转换，导致 FMA 操作数类型不匹配
   > （`spirv-val` 报 "expected types of all operands to be equal to Result Type"，见 §3 失败面）。
   > 须用 `MOV.i32` + LUT 浮点值（如 0.1 = `0x3DCCCCCD`）物化为 `float` 常量。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - **缩放常量类型不匹配**：初版用例用 `IADD_IMM.i32` 物化 `1/255`(0x3B808081) 作缩放因子，
    `FMA.f32` 报 `GLSL.std.450 Fma: expected types of all operands to be equal to Result Type`，
    `spirv-val` exit 1；根因见 §2.5，改用 `MOV.i32 + LUT 0.1` 后转绿。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **编码回环**：`asm.py` 组装的 3 条真机字与 P3 采样字**逐位一致**：
    `0x00b4c8011107c006`（`LSHIFT_OR.i32 r8, r6, 0x0.b00, r7`，bin-007f00006c80[05]）、
    `0x00b4c8011105c004`（`LSHIFT_OR.i32 r8, r4, 0x0.b00, r5`，bin-007f00006c80[07]）、
    `0x00b4c000108dcc3d`（`RSHIFT_AND.i32 r0, r61, 0x13121110.b00, u6.w1`，bin-007f00026e00[03]）；
  - `spirv-dis` 显示 `OpShiftLeftLogical`+`OpBitwiseOr`、`OpShiftRightLogical`+`OpBitwiseAnd` 逻辑正确；
  - 三个用例 ×frag/vert 共 **6 模块 `spirv-val` 全 PASS**；
  - **MoltenVK 逐像素**：`shift`=(153,51,0,0)、`shift_real`=(0,51,153,0)、`shift_neg`=(0,255,0,0)，
    **失败 0 / exit 0**；
  - **负向对照**：正确 shader 配错期望 → 失败 7812 / exit 1；`shift_neg`（把两条算子互换）
    自身期望 (0,255,0,0) → 失败 0，与正向期望逐通道不同；
  - **回归**：`flat/sfu/fma/mod/vary/vary_v2/vary_v3/vary_real/ialu/ialu_real/ialu_neg/cvt/cvt_real/cvt_neg`
    共 **14 用例重生成后 `spirv-val` 全 PASS、逐像素失败 0 / exit 0**；
  - **fail-loud**：`not_result`(bit30) → 「带未建模的 not_result 修饰」、
    shift 源非 `.b00` → 「shift 源字节选择未建模」，两例均 **exit 2**。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. 常量区新增 `OpBitwiseOr`：`OpBitwiseOr, OpBitwiseAnd = 197, 199`；
2. `SUPPORTED` 增 `"LSHIFT_OR.i32", "RSHIFT_AND.i32"`；
3. 新增 `SHIFT_MODELED = {"LSHIFT_OR.i32", "RSHIFT_AND.i32"}`；`src_mod_mask()` 改为
   `enumerate(ins.srcs)` 并跳过移位族 `idx==1` 的 `widen` 字段（shift 字节选择已建模）；
4. `decode_word()` 源属性捕获循环增 `widen`（回显字节选择）；
5. 新增发射器 `emit_lshift_or`（`OpShiftLeftLogical`→`OpBitwiseOr`）与
   `emit_rshift_and`（`OpShiftRightLogical`→`OpBitwiseAnd`）；
6. `main()` 分派新增分支，并在分派前对移位族做 **`not_result`(bit30)** 与
   **shift 源 `widen!=4`（非 `.b00`）** 两道 fail-loud 守卫；
7. 模块 docstring 更新覆盖范围。

**新增用例（`out/tmp/p2/`）**：
- `frag_shift.asm`：合成用例，输入由 `IADD_IMM` 自造，两条算子各出一通道 → 期望 (153,51,0,0)；
- `frag_shift_real.asm`：**真机字直接取材**（2 条 `LSHIFT_OR.i32` 逐位一致）+ `IADD_IMM` 造输入 → 期望 (0,51,153,0)；
- `frag_shift_neg.asm`：负向对照（两条算子互换）→ 期望 (0,255,0,0)。

产物指纹（md5）：
| 文件 | 大小 | md5 |
|---|---|---|
| frag_shift.asm | 1270B | 7c8733f48caecb980a0ad97b502e30a9 |
| frag_shift.bin | 128B | 68d9e9b10ec8e0acc926e8a1b2e3bd34 |
| frag_shift_real.asm | 914B | 0e2f773c8de1cd4d04531eba72580716 |
| frag_shift_real.bin | 112B | a0716b79b75384dca36d1831f7efa6db |
| frag_shift_neg.asm | 708B | 46123ec1928faefcf52db43e62c68115 |
| frag_shift_neg.bin | 128B | a0db2fc45dd0fda9a076978159cc2c0f |
| out_shift.frag.spv | 832B | 0e629bac82589fd1f17421d4075c03e6 |
| out_shift.vert.spv | 504B | f0228fea070b6ef15ae9629a5ae52a9f |
| out_shift_real.frag.spv | 776B | da774f5e17a29ce8f984c7440c5345e2 |
| out_shift_neg.frag.spv | 832B | 4b2574b63c39f6efbf872154f3dbd261 |
| va2spirv.py | — | 6590208933115cd168f58a065d8afa04 |

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 用例 | 形态 | spirv-val (frag/vert) | 逐像素期望 | harness |
|---|---|---|---|---|
| `shift` | `LSHIFT_OR`+`RSHIFT_AND` 合成（reg 源） | PASS/PASS | (153,51,0,0) | 失败 0 / exit 0 |
| `shift_real` | **真机字** ×2（`LSHIFT_OR`，shift=0） | PASS/PASS | (0,51,153,0) | 失败 0 / exit 0 |
| `shift_neg` | 两算子互换（对照） | PASS/PASS | (0,255,0,0) | 失败 0 / exit 0 |
| 回归 14 用例 | flat/sfu/fma/mod/vary/v2/v3/real/ialu×3/cvt×3 | PASS/PASS | 各自期望 | 失败 0 / exit 0 |

- **编码回环**：3 条真机字逐位命中（见 §3）；
- **负向对照**：正确 shader + 错期望 → 失败 7812 / exit 1；`shift_neg` 与正向用例逐通道不同；
- **fail-loud**：`not_result`(bit30) / shift 源非 `.b00` 两例均 **exit 2**。

结论：**整数移位族（`LSHIFT_OR.i32`/`RSHIFT_AND.i32`）端到端语义正确，
真机 3 条字可直接翻译并在 Metal 上逐像素可证**。

覆盖统计：真机 160 条中已覆盖 **87→92→95 条**（**16 种 opcode，59.4%**）。

## 6. 遗留

1. **`ARSHIFT_AND.i32`（算术右移，真机 0 条）**：编码已定论（`opcode2 0x400@24`，signed@34），
   SPIR-V 侧映射为 `OpShiftRightArithmetic(195)`，待真机出现或需要时一并放开。
2. **同族变体**：`RSHIFT_OR`、`LSHIFT_AND`、`ARSHIFT_OR`、以及 `v2i16/v4i8/i64` 宽度变体
   （真机 0 条）——需 16 位打包 / 64 位寄存器对建模。
3. **`not`/`not_result` 修饰**（真机 0 条）：B 取反（`OpNot`）、结果取反，可在需要时建模。
4. **非 `.b00` 的 shift 字节选择**（真机 0 条）：当前 fail-loud；`.b01/.b10/.b11`（值 5/6/7）
   对应高字节选择，需扩建模。
5. **64 位整数**（`IADD.s64` 1 条、`SHADDX.u64` 1 条）：需 `OpTypeInt 64` 与寄存器对建模。
6. **`LD_VAR_BUF_IMM.f16`（真机 3 条，`src_flat16`）**：flat 16 位 varying，
   4 个 half 打包进 2 寄存器 + SPIR-V `Flat` 装饰 + 16 位类型 —— 架构级，单独立项。
7. **内存族（`LD_PKA.i64`/`LOAD.*`/`STORE.*`/`LEA_BUF_IMM`，真机约 33 条，最大块）**：需地址计算与切分建模。
8. **控制流（`BRANCHZ`/`BRANCHZI`/`FCMP_OR.f32`/`ICMP_OR.u32`）**：需 SPIR-V 基本块/分支（CFG）建模。
9. **`LD_ATTR_IMM`（真机 10 条）**：顶点属性通路，ABI 与 varying 不同，单独立项。