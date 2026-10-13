# issue-gold15-bprime-p3-int-convert-subset —— B′ P3：整数立即加 + 16→32 位转换放开（`IADD_IMM.i32` / `U16_TO_F32` / `S16_TO_F32`）

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-varying-width-subset](../issue-gold15-bprime-p3-varying-width-subset/solution.md)（遗留 #1 之前置）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

P3 真机 10 样本共 160 条指令，已覆盖 8 种 opcode / 78 条（48.75%）。剩余 82 条中，
**整数 ALU 与 16→32 位整数转换**是自包含、可像素验证、且对后续 `LD_VAR_BUF_IMM.f16`
（flat 16 位）通路有铺垫作用的一批。本批按真机频次选三条：

- `IADD_IMM.i32`（**5 条**）：`opcode=0x110@48`，1 dest + 1 src(A) + **32 位立即数** `constant`（`start=8 size=32`）。
  ISA 描述：「Adds an arbitrary 32-bit immediate embedded within the instruction stream…
  **with the source tied to zero is the canonical immediate move**」。
- `U16_TO_F32`（**2 条**）/ `S16_TO_F32`（**2 条**）：同属 `CONVERT`（16→32 整数转）组，
  `opcode=0x90@48` + `opcode2`（U16=**0x15**@16 / S16=**0x5**@16）；src 声明 `<src lane="28" size="16">`，
  `lane` 为 **1 位**（bit28），枚举「Load lane (16-bit)」= `h0`（低半，默认）/`h1`（高半）。

三条此前均 fail-loud（在 `SUPPORTED` 之外）。本批放开并实现 SPIR-V 映射。

## 2. 根因（决定性发现）

1. **任意 32 位常量只能靠 `IADD_IMM` 自造**：`MOV.i32` 的源是**单一 src 操作数**（无 imm 字段），
   且小常量 LUT 仅 **32 项**（`IMM_LUT`：`0x0/0xffffffff/0x3f800000/0x3dcccccd/…`），
   **不含任意 32 位值**（如 `0xFFFF0001`）。而 `IADD_IMM` 的立即数 `val<<8` 直嵌 bits 8–39，
   32 位全覆盖 —— 即 ISA 所述「src 绑零（LUT 0）即规范的立即数 move」。故本批同时补上
   「造任意常量」与「16 位转换」两块拼图。

2. **源 `mode=1`（`rN^`）= 取值等同 `rN`**：Mesa `disasm.py:44-75` 的 `va_print_src` 中
   `bool discard = (type & 1); dmark = discard ? "^" : ""` —— `^` 只是「最后使用/可释放」
   的活跃性标记（discard），**取到的寄存器值不变**。真机 5 条里 `r8^`（IADD_IMM 源）、
   `r59^`（U16 源）、`r0^.h1`（S16 源）均为 mode 1。翻译器既有 `val_to_ssa`/`emit_mov`
   已把 `reg` 与 `reg_ptr` 等同处理，本批沿用。

3. **16 位 lane 语义**：`h0`=低半（bits15:0，默认）、`h1`=高半（bits31:16）。
   `U16_TO_F32` = 取 16 位 → **零扩展**到 32 位 → 转 float；`S16_TO_F32` = 取 16 位 →
   **符号扩展**到 32 位 → 转 float。

4. **SPIR-V opcode 编号（纠错关键）**：`OpConvertSToF=111`、`OpConvertUToF=112`
   （**二者极易写反**，见 [log-fail.txt](log-fail.txt)）；`OpIAdd=128`；
   `OpShiftRightLogical=194`、`OpShiftRightArithmetic=195`、`OpShiftLeftLogical=196`、
   `OpBitwiseAnd=199`（编号一律用 `spirv-as`/`spirv-dis` 实证）。

5. **SPIR-V 符号扩展技巧（无需求助有符号语义的右移之外的原语）**：
   - S16 lane0：`x` 左移 16 再**算术**右移 16 → 低半符号扩展；
   - S16 lane1：直接**算术**右移 16 → 高半符号扩展（一步到位）；
   - U16 lane1：**逻辑**右移 16 后 `& 0xFFFF`；U16 lane0：直接 `& 0xFFFF`。
   整数域统一用 **`OpTypeInt 32 1`**（有符号）承载位型：`spirv-val` 实测**不校验**
   超范围字面量（`0xFFFF0001`）与 `OpConvertUToF/SToF` 操作数符号（见 §3），故无需引入无符号类型。

## 3. 证据

- **编码回环（4/4 转换字 + 3/3 立即加字逐位命中）**：用 Mesa `asm.py` 反汇编真机字：
  | 真机字 | 汇编 | 命中 |
  |---|---|---|
  | `0x0090fe001015003b` | `U16_TO_F32 r62, r59.h1` | ✓ |
  | `0x0090c2000015007b` | `U16_TO_F32 r2, r59^` | ✓ |
  | `0x0090c10000050000` | `S16_TO_F32 r1, r0` | ✓ |
  | `0x0090c00010050040` | `S16_TO_F32 r0, r0^.h1` | ✓ |
  | `0x0110c20020000048` | `IADD_IMM.i32 r2, r8^, #0x200000` | ✓ |
  | `0x0110fe00000000c0` | `IADD_IMM.i32 r62, 0x0, #0` | ✓ |
  | `0x6910f600000000c0` | `IADD_IMM.i32 r54, 0x0, #0` | ✓ |
- **失败面（[log-fail.txt](log-fail.txt)）**：`OpConvertUToF/OpConvertSToF` 常量**写反**
  （初版 111=UToF/112=SToF）——`spirv-val` **不报错**（两者皆合法），
  而是 `spirv-dis` 反汇编显示 `U16` 被译成 `OpConvertSToF` 才暴露；`spirv-as` 对超范围
  int32 字面量（`0xFFFF0001`）报「does not fit in a 32-bit signed integer」（故不用它造二进制，
  改由翻译器直出，`spirv-val` 接受）。
- **通过面（[log-pass.txt](log-pass.txt)）**：
  - synthe 用例 `frag_cvt`（64B）：三条 `IADD_IMM`（含「绑零 imm move」与「`rS+imm`」双形态）
    + 四条转换（U16 h0/h1、S16 h1 ×2）；`spirv-dis` 显示
    `OpIAdd ×3 / OpBitwiseAnd / OpConvertUToF / OpShiftRightLogical /
     OpShiftRightArithmetic / OpConvertSToF`，逻辑与预期逐条对齐；
  - 真机取材 `frag_cvt_real`（80B）：**5 条真机字逐位一致** + 前置 `IADD_IMM` 造输入；
  - **`spirv-val` frag/vert 全 PASS**（4 模块）；
  - **MoltenVK 逐像素比对失败 0 / exit 0**：`cvt`=(255,0,0,255)、`cvt_real`=(0,255,255,255)；
  - **负向对照**：误用 U16 的 `frag_cvt_neg` → 图像 (255,0,255,255)，按正确期望跑 **失败 7812 / exit 1**；
    正确 shader 配错期望 → **失败 7812 / exit 1**；
  - **回归**：用新翻译器重跑 `flat/sfu/fma/mod/vary/vary_v2/vary_v3/vary_real` 全部
    **失败 0 / exit 0**（另 `spirv-val` 全 PASS）；
  - **fail-loud**：`IADD_IMM.i32`/`U16_TO_F32` 的 **FAU(uniform) 源**、以及未支持指令
    `IADD.u32`，三例均 **exit 2** 且信息明确。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. 常量区新增 `OpConvertSToF, OpConvertUToF = 111, 112`、`OpIAdd = 128`、
   `OpShiftRightLogical, OpShiftRightArithmetic = 194, 195`、`OpShiftLeftLogical = 196`、
   `OpBitwiseAnd = 199`；
2. `Operand.__repr__` 增加 `lane` 回显（调试可读性）；
3. `decode_meta()` 重构：**立即数提取不再依赖 staging**（`IADD_IMM` 无 staging 也须取
   `constant`）；staging 字段仍在有 staging 时提取；
4. `decode_word()` 源修饰提取增加 `lane` 键；
5. `SUPPORTED` 增 `IADD_IMM.i32`/`U16_TO_F32`/`S16_TO_F32`；新增
   `LANE_MODELED={U16_TO_F32,S16_TO_F32}`，`src_mod_mask()` 对已放开 lane 的指令不计入；
6. 新增发射器 `emit_iadd_imm()`（→ `OpIAdd`）、`_sext16()`/`emit_cvt(signed)`（→
   shift/mask + `OpConvertUToF`/`OpConvertSToF`）；
7. `main()` 分派三条新指令；模块 docstring 更新覆盖范围。

**新增用例（`out/tmp/p2/`）**：
- `frag_cvt.asm`：合成三指令全覆盖 → 期望 (255,0,0,255)；
- `frag_cvt_real.asm`：真机 5 字取材 + 1 条同族补 r3 → 期望 (0,255,255,255)；
- `frag_cvt_neg.asm`：负向对照（把 S16 故意误用 U16）→ 图像应为 (255,0,255,255)。

产物指纹（md5）：
| 文件 | 大小 | md5 |
|---|---|---|
| frag_cvt.bin | 64B | bbfdbe3f0bf1346e76bc787d22eea2b0 |
| out_cvt.frag.spv | 672B | 75201734dbc59446d2225bcd9aa36915 |
| out_cvt.vert.spv | 504B | f0228fea070b6ef15ae9629a5ae52a9f |
| frag_cvt_real.bin | 80B | 20c374097b9e6a3e02ad5f855e676ca0 |
| out_cvt_real.frag.spv | 784B | ad6a1805a87d2db3fce9ef88cc3c5c8b |
| frag_cvt_real.vert.spv | 504B | f0228fea070b6ef15ae9629a5ae52a9f |
| frag_cvt_neg.bin | 64B | 55b0324a3e9c6884cf9d89f0c37b50b4 |
| out_cvt_neg.frag.spv | 692B | a215a37ae3318db94f0577e79e51ee79 |
| va2spirv.py | 28058B | 43a6043bcb7ffbd9babf0ce154ea6622 |

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 用例 | 形态 | spirv-val (frag/vert) | 逐像素期望 | harness |
|---|---|---|---|---|
| `cvt` | 3×IADD_IMM + U16 h0/h1 + S16 h1×2 | PASS/PASS | (255,0,0,255) | 失败 0 / exit 0 |
| `cvt_real` | **5 条真机字** + 1 同族 | PASS/PASS | (0,255,255,255) | 失败 0 / exit 0 |
| `cvt_neg` | S16 误用为 U16（负向） | PASS/PASS | 应为 (255,0,255,255) | 按正确期望 → 失败 7812 / exit 1 |
| 回归 flat/sfu/fma/mod/vary/vary_v2/vary_v3/vary_real | — | PASS/PASS | 各自期望 | 失败 0 / exit 0 |

- **负向对照**：`frag_cvt_neg` 按正确期望 (255,0,0,255) → **失败 7812 / exit 1**，按自身期望
  (255,0,255,255) → 失败 0（证误译确实把 r2 从 0 翻成 255）；`out_cvt` 配错期望 → **失败 7812 / exit 1**；
- **fail-loud**：`IADD_IMM.u0.w0` / `U16_TO_F32 u0.w0` → `暂不支持操作数形态 fau`、
  `IADD.u32` → `不支持的指令`，三例均 **exit 2**。

结论：**`IADD_IMM.i32`（含 32 位立即数与 `rS+imm`）与 `U16_TO_F32`/`S16_TO_F32`
（含 16 位 lane h0/h1 与零/符号扩展）端到端语义正确，真机 5 条转换/立即加字已可直接翻译
并在 Metal 上逐像素可证**。覆盖：78/160 → **87/160（11 种 opcode，54.38%）**。

## 6. 遗留

1. **16 位饱和/舍入变体**：仅 `U16_TO_F32`/`S16_TO_F32`；同组 `S16_TO_S32`(0x4)/`U16_TO_U32`(0x14)
   未放开（真机样本未出现）。
2. **lane `w0`/`d0`**：ISA 枚举含「零扩展到 32/64 位」，但 `lane` 域仅 1 位，
   实际只有 `h0/h1` 可达，w0/d0 走别的字段语义，待查。
3. **整数域类型**：统一 `OpTypeInt 32 1`（有符号承载位型）；超范围字面量与
   `OpConvertUToF/SToF` 的符号校验已实测放行，但后续若接入更严的验证器（如 `spirv-as` 路径）
   需改无符号类型 + `OpBitcast`。
4. **IADD_IMM 的 FAU/uniform 源**（`uN`）与 **swizzle/widen 源修饰**仍 fail-loud（承接上批）。
5. **下一批**：`LD_VAR_BUF_IMM.f16`（flat `src_flat16`，需 16 位打包/接口建模；本批转换指令
   已为其铺路）、`LD_PKIM.i64`/LOAD/STORE 内存族（33 条，最大块，需地址/切分建模）、
   `LD_ATTR_IMM`（顶点路径，单独立项）、`FCMP_OR.f32`/`ICMP_OR.u32`、`BRANCHZ` 控制流。