# issue-gold15-bprime-p3-int-alu-subset —— B′ P3：整数二元 ALU 子集放开（`IADD.u32` / `ISUB.u32` / `IMUL.i32`）

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-int-convert-subset](../issue-gold15-bprime-p3-int-convert-subset/solution.md)（把整数域从「立即数」延伸到「寄存器二元运算」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

上一批（[int-convert-subset](../issue-gold15-bprime-p3-int-convert-subset/solution.md)）只放开了「整数立即加」（`IADD_IMM.i32`）
与「16→32 位转换」（`U16_TO_F32`/`S16_TO_F32`），**寄存器之间的整数二元运算仍全部 fail-loud**。
而 P3 真机 160 条指令里，整数算术是继浮点之后第二大类：

| 真机指令 | 条数 | 说明 |
|---|---|---|
| `IADD.u32` | 1 | 整数加（寄存器） |
| `ISUB.u32` | 2 | 整数减 |
| `IMUL.i32` | 2 | 整数乘 |

本批按真机频次放开这三条 **32 位整数二元 ALU**（`opcode 0xA0@48` + `opcode2 0x0/0x1/0xA@16`），
把整数域从「立即数/转换」补齐到「寄存器加/减/乘」。

> 说明：`LSHIFT_OR.i32`/`RSHIFT_AND.i32`（真机 3 条）虽同属整数族，但其 `shift` 操作数用
> ISA 的 `lanes="true"` 元数据（编码非常规 src 域，无法仅凭 ISA.xml 唯一定论），本批**主动排除**，
> 见 §6 遗留——宁可 fail-loud 也不猜编码。

## 2. 根因（决定性发现）

1. **三条指令同 opcode、以 opcode2 区分**（ISA.xml 实证）：
   - `IADD.u32`：`0xA0@48` + opcode2 `0x0@16`（mask `0xF`）；
   - `ISUB.u32`：`0xA0@48` + opcode2 `0x1@16`；
   - `IMUL.i32`：`0xA0@48` + opcode2 `0xA@16`。
   三者共享 opcode，仅 opcode2 不同 → 由 `match_instruction()` 的「多段域最具体者」自然区分。

2. **真机源形态干净**（无需 16 位打包）：解码为 `reg` / `reg_ptr`（`rN^`）/ `lut` / `fau`，
   例如 `IADD.u32 r4, r8, r10^`、`ISUB.u32 r6, r9, r7`、`IMUL.i32 r2, r10, r2^`。
   **不存在**上一批 f16 那类「4 个 half 打包进 2 寄存器」的问题 → 可直接用既有 `regno→SSA` 模型。

3. **位型与符号无关**：加/减/乘在 32 位下的结果位型与有/无符号无关，故统一用既有
   `t_i32`（`OpTypeInt 32 1`）即可，**无需新增 unsigned 类型**。映射：
   `IADD.u32→OpIAdd(128)`、`ISUB.u32→OpISub(130)`、`IMUL.i32→OpIMul(132)`（编号查 `spirv.hpp`）。

4. **两条未建模修饰必须拦住**：
   - IADD 组带 `<saturate/>`，`Flag("saturate", 30)`（bit30）——未建模则**饱和语义丢失**；
   - 两个源都带 `widen` 字段（`widen` 掩码 = `0xF@36 | 0xF@26`）——已有 `src_mod_mask()`
     （ISA 元数据通用推导）自动拦截。

   真机三条的 saturate/widen 位均为 0，故可直译。

5. **P2 ABI（r0..r3 为 float RGBA）要求整数结果经转换输出**：`build()` 以 `st[2]` 定类型，
   整数型寄存器直接输出会与 `OpCompositeConstruct vec4<float>` 类型不符 →
   用例须以 `U16_TO_F32`/`S16_TO_F32` 取低 16 位转 float 输出（承接上一批手法）。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：`saturate` 位手工构造时算错位（置成 bit38=widen）→
  被 `src_mod_mask` 以「源修饰」名义拦下，暴露「位域归属」而非「有无拦截」；改用表达式重建后命中
  saturate 分支。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **编码回环**：`asm.py` 组装的 3 条真机字与 P3 采样字**逐位一致**：
    `0x00a0c40000004a08`（`IADD.u32 r4, r8, r10^`，bin-007f00001000[09]）、
    `0x00a0c60000010709`（`ISUB.u32 r6, r9, r7`，bin-007f00001000[11]）、
    `0x00a0c200000a420a`（`IMUL.i32 r2, r10, r2^`，bin-007f00006c80[15]）；
  - `spirv-dis` 显示 `OpIAdd/OpISub/OpIMul` 逻辑正确；
  - 三个用例 ×frag/vert 共 **6 模块 `spirv-val` 全 PASS**；
  - **MoltenVK 逐像素**：`ialu`=(0,0,255,255)、`ialu_real`=(0,255,255,255)，**失败 0 / exit 0**；
  - **负向对照**：正确 shader 配错期望 → 失败 7812 / exit 1；`ialu_neg`（把三算子互换）
    自身期望 (255,255,0,0) → 失败 0，与正确期望逐通道不同；
  - **回归**：`flat/sfu/fma/mod/vary/vary_v2/vary_v3/vary_real` 重生成后 **8 模块 `spirv-val` 全 PASS**、
    逐像素**失败 0 / exit 0**；
  - **fail-loud**：`saturate` 位 → 「带未建模的 saturate 修饰」、`widen` 位 → 「带未建模的源修饰」、
    `LSHIFT_OR.i32` → 「不支持的指令」，三例均 **exit 2**。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. 常量区新增 `OpISub, OpIMul = 130, 132`；
2. `SUPPORTED` 增 `"IADD.u32", "ISUB.u32", "IMUL.i32"`；
3. 新增 `FragBuilder._emit_ialu(opcode, dest, a, b)`（统一 i32 二元发射）+
   `emit_iadd`/`emit_isub`/`emit_imul` 三个薄封装；
4. `main()` 分派新增三分支，并在分派前对整数组做 **saturate（bit30）fail-loud** 守卫；
5. 模块 docstring 更新覆盖范围。

**新增用例（`out/tmp/p2/`）**：
- `frag_ialu.asm`：合成用例，`r10=1`/`r11=0xFFFF` 由 `IADD_IMM` 自造，三条算子各出一通道 →
  期望 (0,0,255,255)（仅用 U16/S16 低半取值，避开 P2 输出 float 约束）；
- `frag_ialu_real.asm`：**真机字直接取材**（3 条逐位一致）+ `IADD_IMM` 造输入 → 期望 (0,255,255,255)；
- `frag_ialu_neg.asm`：负向对照（IADD/ISUB/IMUL 全互换）→ 期望 (255,255,0,0)。

产物指纹（md5）：
| 文件 | 大小 | md5 |
|---|---|---|
| frag_ialu.asm | — | c8c76c6a66c7b58e2185758a017840e6 |
| frag_ialu.bin | 88B | 967e5f08aa92482140771d1ec8988b13 |
| frag_ialu_real.asm | — | a3596e0e61d100e6ad0bd565ddf8e4a7 |
| frag_ialu_real.bin | 104B | 4c42a1577eaa3e9dfbce324d09df8b7d |
| frag_ialu_neg.asm | — | 3ef5615faadce3d39d964149b6ab6103 |
| frag_ialu_neg.bin | 88B | 9e5113c5afe2b577a758d2e33a7c8f24 |
| out_ialu.frag.spv | 716B | 2b4f7c24d6f8cee8513942b78c85b31d |
| out_ialu.vert.spv | 504B | f0228fea070b6ef15ae9629a5ae52a9f |
| out_ialu_real.frag.spv | 752B | 98564e1ea20b85a7dc68e535bad1d6d5 |
| out_ialu_neg.frag.spv | 716B | 70fb79b7c6424b33b43e383416c9924d |

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 用例 | 形态 | spirv-val (frag/vert) | 逐像素期望 | harness |
|---|---|---|---|---|
| `ialu` | `IADD/ISUB/IMUL` 合成（reg 源） | PASS/PASS | (0,0,255,255) | 失败 0 / exit 0 |
| `ialu_real` | **真机字** ×3（含 `rN^` mode1） | PASS/PASS | (0,255,255,255) | 失败 0 / exit 0 |
| `ialu_neg` | 三算子互换（对照） | PASS/PASS | (255,255,0,0) | 失败 0 / exit 0 |
| 回归 flat/sfu/fma/mod/vary/v2/v3/real | — | PASS/PASS | 各自期望 | 失败 0 / exit 0 |

- **编码回环**：3 条真机字逐位命中（见 §3）；
- **负向对照**：正确 shader + 错期望 → 失败 7812 / exit 1；`ialu_neg` 与正确用例逐通道不同；
- **fail-loud**：saturate / widen / `LSHIFT_OR.i32` 三例均 **exit 2**。

结论：**整数二元 ALU（`IADD.u32`/`ISUB.u32`/`IMUL.i32`）端到端语义正确，
真机三条字可直接翻译并在 Metal 上逐像素可证**。

覆盖统计：真机 160 条中已覆盖 **78→87→92 条**（**14 种 opcode，57.5%**）。

## 6. 遗留

1. **`LSHIFT_OR.i32` / `RSHIFT_AND.i32`（真机 3 条）**：`shift` 操作数为 ISA `lanes="true"` 元数据
   （编码非常规 src 域，`asm.py`/ISA 无法唯一定论其位域），需先做 **真机字 + `asm.py` 试装**
   双向核验再放开；同族的 `RSHIFT_OR/XOR`、`ARSHIFT_AND` 一并后置。
2. **64 位整数**（`IADD.s64` 1 条、`SHADDX.u64` 1 条）：需 `OpTypeInt 64` 与寄存器对建模。
3. **16 位向量整数**（`IADD.v2u16` 等）：需 16 位打包寄存器模型（与 §6.4 同源）。
4. **`LD_VAR_BUF_IMM.f16`（真机 3 条，`src_flat16`）**：flat 16 位 varying，
   4 个 half 打包进 2 寄存器 + SPIR-V `Flat` 装饰 + 16 位类型 —— 架构级，单独立项。
5. **内存族（`LD_PKA.i64`/`LOAD.*`/`STORE.*`/`LEA_BUF_IMM`，真机约 33 条，最大块）**：需地址计算与切分建模。
6. **控制流（`BRANCHZ`/`BRANCHZI`/`FCMP_OR.f32`/`ICMP_OR.u32`）**：需 SPIR-V 基本块/分支（CFG）建模。
7. **`LD_ATTR_IMM`（真机 10 条）**：顶点属性通路，ABI 与 varying 不同，单独立项。