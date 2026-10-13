# issue-gold15-bprime-p3-cmp-subset —— B′ P3：比较族放开（`ICMP_OR.u32` / `FCMP_OR.f32`）

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-shift-subset](../issue-gold15-bprime-p3-shift-subset/solution.md)
> （上批放开整数移位族，并把**比较族**列入遗留 #8 —— 因其引入「谓词/布尔」infrastructure，
> 是后续控制流（`BRANCHZ`）的前置）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

上批（[shift-subset](../issue-gold15-bprime-p3-shift-subset/solution.md)）遗留把比较族列入
「需 SPIR-V 基本块/分支（CFG）建模」——本批论证后**降级为可独立放开**：比较指令本身**不产生
CFG**，只产出「谓词 + 按 `result_type` 编码的值」；真正需要 CFG 的是其下游 `BRANCHZ`。
故本批先把**比较语义**（`ICMP_OR.u32` / `FCMP_OR.f32`）与 **SPIR-V 谓词基础设施**落地，
为下一批控制流铺路。

P3 真机 160 条中比较族共 4 条：

| 真机指令 | 条数 | 出处 | 说明 |
|---|---|---|---|
| `ICMP_OR.u32` | 3 | bin-007f00001000[10]/[12]/[14] | 无符号整数比较：`dest=(A cond B) ? 编码 : 0` |
| `FCMP_OR.f32` | 1 | bin-007f00037880[02] | 浮点比较：`(r2==0.0)` → 该字下游接 `BRANCHZ` |

**真机用法**（4 条全部 `condition`/`result_type`/`C` 干净、`C` 恒为零）：
`(A>B) ? 0xFFFFFFFF : 0`（ICMP，`condition=gt`、`result_type=m1`）与
`(A==0.0) ? 0xFFFFFFFF : 0`（FCMP，`condition=eq`、`result_type=m1`）。

## 2. 根因（决定性发现）

1. **编码定位**（ISA.xml 实证，line 3288-3372）：
   - `ICMP_OR.u32`：`opcode 0xF0@48` + `opcode2 0x000@24`（mask `0x3`）；
   - `FCMP_OR.f32`：`opcode 0xF4@48` + `opcode2 0x000@24`；
   - 同族 `ICMP_AND`/`FCMP_AND` 用 `opcode2 0x1@24` 区分（本批只放开 OR 变体）；
     `v2u16/v4u8/v2f16` 用不同 opcode（`0xF1/0xF2/0xF5`）；有符号 `ICMP_OR.s32` 用 `0xF8`。
     由 `match_instruction()` 的「多段域最具体者」自然区分。

2. **两个修饰位域**（`valhall.py` MODIFIERS 实证）：
   - `condition`：`Modifier("condition", 32, 3)`（3 位，0 起枚举）——
     `0=eq、1=gt、2=ge、3=ne、4=lt、5=le、6=gtlt、7=total`；
   - `result_type`：`Modifier("result_type", 30, 2)`（2 位，0 起枚举）——
     `0=i1（int 0/1）、1=f1（float 0.0/1.0）、2=m1（Direct3D 0/~0）、3=u1（64 位比较低半，内部）`。
   - `decode_meta()` 的固定名列表增 `"condition"`/`"result_type"` 后即随 ISA 元数据自动解码。

3. **语义与 SPIR-V 映射**：`dest = (A <cond> B) OR C`，`C` 以 `result_type` 域编码（真机恒 0）。
   先产出 **`%bool` 谓词**，再按 `result_type` 用 `OpSelect` 编码：
   - 整数（无符号 `.u32`）：`eq/ne → OpIEqual/OpINotEqual`，其余 → `OpUGreaterThan` 系列；
   - 浮点（IEEE 754）：`eq → OpFOrdEqual`（NaN→false）、`ne → OpFUnordNotEqual`（NaN→true），
     其余 → `OpFOrd*` 有序系列；
   - `i1 → OpSelect(%bool, int 1, int 0)`、`m1 → OpSelect(%bool, int 0xFFFFFFFF, int 0)`、
     `f1 → OpSelect(%bool, float 1.0, float 0.0)`；`u1 → fail-loud`。
   - 所有 SPIR-V 编号**查头文件实证**（`spirv/1.0/spirv.hpp`：`OpTypeBool=20`、`OpLogicalOr=166`、
     `OpSelect=169`、`OpIEqual..OpFUnordGreaterThanEqual=170..191`），不凭记忆。

4. **C 源（OR 逻辑）**：仅建模**常量** `C`：`C=0` 短路（等价于单条比较）；
   `C≠0` 且整数域（i1/m1）→ 与编码结果 **`OpBitwiseOr`**（位型或 = 该域的逻辑或）；
   `C≠0` 而浮点域（f1）、或 `C` 非常量 → **fail-loud**。
   真机 4 条 `C` 恒为 0（docstring 亦建议「不需要时绑零」），故短路路径覆盖全部真机用法。

5. **P2 ABI（r0..r3 为 float RGBA）要求比较结果转换后输出**：`build()` 以 `st[2]` 定类型，
   比较产物对 `i1/m1` 为 **int SSA**，直接输出会与 `OpCompositeConstruct vec4<float>` 类型不符 →
   用例须先 `U16_TO_F32`（取低 16 位）再用 `MOV.i32 rN, 0x3DCCCCCD`（LUT 0.1）缩放，
   使 `m1`(0xFFFF→65535→255) 与 `i1`(1→0.1→26) 在 UNORM 映射下**可区分**。

6. **FAU 特殊寄存器源**：真机 `0x00f0c50180c08508` 的 B 源为 `u2.w1`
   （`valhall` FAU page0 index2 = **WARP_ID**，硬件特殊寄存器；其值不在 ISA 元数据的 LUT 常量表中）
   → 未建模，`val_to_ssa()` 遇 `fau` 形态即 **fail-loud**（防错译）。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - **负向对照**：正确 shader `frag_cmp`（期望 (255,26,255,255)）配错期望 (0,0,0,0) →
    逐像素**失败 7812 / exit 1**（证像素确为真实比较结果，非条件被忽略）；
  - **fail-loud 四例**（均 **exit 2**）：`condition=gtlt`、`result_type=u1`、
    源为 FAU `u2.w1`、`C` 源非常量 → 分别被相应守卫拒绝。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **编码回环**：`asm.py` 组装的 4 条真机字与 P3 采样字**逐位一致**（`frag_cmp_real.bin`[06..08]
    与 `fl_cmp_fau.bin`[02]），即 `.u32.gt.m1` / `.f32.eq.m1` / `u2.w1` 的 asm 语法实证钉死；
  - `spirv-dis` 显示 `OpUGreaterThan`+`OpSelect %int %int_n1`（m1）、
    `OpUGreaterThan`+`OpSelect %int %int_1`（i1）、`OpFOrdEqual`+`OpSelect %int`、
    `OpFOrdGreaterThan`+`OpSelect %float` 逻辑正确；
  - 四个用例 ×frag/vert 共 **8 模块 `spirv-val` 全 PASS**；
  - **MoltenVK 逐像素**：`cmp`=(255,26,255,255)、`cmp_real`=(255,0,255,0)、
    `cmp_or`=(255,255,0,0)、`cmp_neg`=(0,0,0,0)，**失败 0 / exit 0**；
  - **回归**：既有 **14 用例**重生成后 `spirv-val` 全 PASS、逐像素失败 0 / exit 0。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. 常量区新增比较族 opcode（`OpTypeBool=20`、`OpLogicalOr=166`、`OpSelect=169`、
   `OpIEqual..OpFUnordGreaterThanEqual=170..191`，编号查头文件）；
2. `FragBuilder.__init__` 增 `t_bool`（`OpTypeBool`）类型注册；
3. `decode_meta()` 固定名列表增 `"condition"` / `"result_type"`；
4. 新增模块级表：`CMP_CONDITIONS` / `CMP_RESULTS` / `CMP_INT_OPS` / `CMP_FLOAT_OPS`；
5. `SUPPORTED` 增 `"ICMP_OR.u32", "FCMP_OR.f32"`；
6. 新增 `_const_bits()`（常量操作数取位型）、`_cmp_pred()`（条件→`%bool` 谓词）、
   `_encode_bool()`（`result_type` 编码）、`emit_cmp()`（C 源短路/位或/fail-loud）；
7. `main()` 分派新增 `ICMP_OR.u32`/`FCMP_OR.f32` 分支；
8. 模块 docstring 更新覆盖范围与比较族 C 源约束。

**新增用例（`out/tmp/p2/`）**：
- `frag_cmp.asm`：合成（ICMP `gt`×`m1/i1` + FCMP `eq`/`gt`×`m1/f1`，C=0）→ 期望 (255,26,255,255)；
- `frag_cmp_real.asm`：**真机字直接取材**（3 条逐位一致：2 ICMP + 1 FCMP）→ 期望 (255,0,255,0)；
- `frag_cmp_or.asm`：**C 源非零**（`C=0xFFFFFFFF`）→ 期望 (255,255,0,0)（证明位或生效）；
- `frag_cmp_neg.asm`：负向对照（条件互补）→ 期望 (0,0,0,0)；
- `fl_cmp_cond.asm` / `fl_cmp_rtype.asm` / `fl_cmp_fau.asm` / `fl_cmp_csrc.asm`：四条 fail-loud。

产物指纹（md5）：
| 文件 | 大小 | md5 |
|---|---|---|
| frag_cmp.asm | 1407B | 1ebf7261df85a45b39afad2c8fba53cd |
| frag_cmp.bin | 136B | 7b07356ff701e0908bd4f02b1e206d0e |
| frag_cmp_real.asm | 1217B | 236dca02ab848e12039d681a823a76fa |
| frag_cmp_real.bin | 136B | f874b678aafde36c22f07adf04f4c47e |
| frag_cmp_or.asm | 945B | ba368788bd5ad72f8cebcde7d1be4949 |
| frag_cmp_or.bin | 104B | 9d49e8ab805938826e688d24a59c1b0b |
| frag_cmp_neg.asm | 842B | 88cfaffe490294b5d24183dd204e0c3c |
| frag_cmp_neg.bin | 136B | 3a35f9d26c3ac60c9dfb5af2422dbe28 |
| fl_cmp_cond.bin | 72B | cfa246ae7905d32393a8e26a8f14d92f |
| fl_cmp_rtype.bin | 72B | 987bdcf7631d68aacc3dcc4af947ddaa |
| fl_cmp_fau.bin | 64B | 5b5a5c668441781d00fcc4db0ad000cd |
| fl_cmp_csrc.bin | 72B | 22425a4af1ee34d8be2f6e6939a63713 |
| out_cmp.frag.spv | 972B | 0658b4bae84b948084f23bb5229041c2 |
| out_cmp.vert.spv | 504B | f0228fea070b6ef15ae9629a5ae52a9f |
| out_cmp_real.frag.spv | 920B | 66dc2593d361ac10cb205ace6c083b56 |
| out_cmp_or.frag.spv | 824B | 6c99ec03ceae8767c3377fe67620fd80 |
| out_cmp_neg.frag.spv | 972B | 4a5bc72e7653f9d9f72699e10528a642 |
| va2spirv.py | — | 24e22bb5cdf140efeccc0757b35e6651 |

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 用例 | 形态 | spirv-val (frag/vert) | 逐像素期望 | harness |
|---|---|---|---|---|
| `cmp` | ICMP `gt`×`m1/i1` + FCMP `eq`/`gt`×`m1/f1`，C=0 | PASS/PASS | (255,26,255,255) | 失败 0 / exit 0 |
| `cmp_real` | **真机字** ×3（2 ICMP + 1 FCMP） | PASS/PASS | (255,0,255,0) | 失败 0 / exit 0 |
| `cmp_or` | C 源非零（`0xFFFFFFFF`，整数域位或） | PASS/PASS | (255,255,0,0) | 失败 0 / exit 0 |
| `cmp_neg` | 条件互补（对照） | PASS/PASS | (0,0,0,0) | 失败 0 / exit 0 |
| 回归 14 用例 | flat/sfu/fma/mod/vary×4/ialu×3/cvt×3 | PASS/PASS | 各自期望 | 失败 0 / exit 0 |

- **编码回环**：4 条真机字逐位命中（见 §3）；
- **负向对照**：正确 shader + 错期望 → 失败 7812 / exit 1；`cmp_neg` 与正向用例逐通道不同；
- **fail-loud**：`condition=gtlt` / `result_type=u1` / FAU 源 / 非常量 C 四例均 **exit 2**。

结论：**比较族（`ICMP_OR.u32`/`FCMP_OR.f32`）端到端语义正确，
真机 3 条（2 ICMP + 1 FCMP）字可直接翻译并在 Metal 上逐像素可证**；
SPIR-V 谓词基础设施（`%bool` + `OpSelect` 编码）已就位，为控制流铺路。

覆盖统计：真机 160 条中已覆盖 opcode **16→18 种**，条数 **95→99 条（61.9%）**
（含 1 条 ICMP 因 B 源为 FAU `u2.w1` 走 fail-loud，可直译 98 条）。

## 6. 遗留

1. **控制流 `BRANCHZ`（真机 4 条）/ `BRANCHZI`（1 条）**：本批已产出 `%bool` 谓词，
   下一批需 SPIR-V 基本块 + `OpBranchConditional`（CFG）建模 —— 比较族是其直接前置。
2. **`ICMP_AND`/`FCMP_AND`（真机 0 条）**：C 源按「逻辑与」组合；结构与 OR 同源，
   可在需要时一并放开。
3. **`condition` 的 `gtlt`（6）/`total`（7）**（真机 0 条）：依赖 NaN 全序语义，当前 fail-loud。
4. **`result_type=u1`（真机 0 条）**：64 位比较低半，内部编码，需与 64 位整数建模合并。
5. **浮点域（f1）非零 C**、**非常量 C**（真机 0 条）：当前 fail-loud。
6. **有符号整数比较 `ICMP_OR.s32`（真机 0 条）**：编码 `0xF8@48` 已知，
   SPIR-V 侧换 `OpSGreaterThan` 系列即可，待需要时放开。
7. **宽度变体 `v2u16/v4u8/v2s16/v4s8/v2f16`（真机 0 条）**：需 16/8 位打包建模。
8. **FAU 特殊寄存器源（`BIR_FAU_WARP_ID` 等）**：其值属硬件特殊寄存器/统一常量表
   （不在 ISA.xml 元数据中），需另行建模；当前 fail-loud。
9. **其余大块**（承接上批遗留）：内存族（`LD_PKA.i64`/`LOAD.*`/`STORE.*`/`LEA_BUF_IMM`，约 33 条）、
   `LD_ATTR_IMM`（10 条，顶点属性 ABI）、`LD_VAR_BUF_IMM.f16`（3 条，flat16 打包）、
   64 位整数（`IADD.s64`/`SHADDX.u64`）、`CLPER.i32`（3 条，subgroup cross-lane）。