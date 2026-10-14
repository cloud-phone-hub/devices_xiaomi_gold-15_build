# issue-gold15-bprime-p3-widen-src-subset —— B′ P3：移位/旋转族源形态残余（子元素加宽 / v2i16 b** 字节构造 / i64 LUT 扩展）建模，族内全清

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-shift-mod-subset](../issue-gold15-bprime-p3-shift-mod-subset/solution.md)（遗留 #1「移位/旋转族源形态残余」）
> 关联：[issue-gold15-bprime-p3-shift-ext-subset](../issue-gold15-bprime-p3-shift-ext-subset/solution.md)（宽度变体批留下的源形态 fail-loud）

---

## 1. 问题

移位/旋转族（宽度变体、rotate、not_result/B.not 均已放）最后一层源形态残余：

1. **v2i16 A 源 b\*\* 字节构造 swizzle**（`swiz_int_m` 码 4-13：b00/b20/b02/…）——半字 lane 由单字节构造；
2. **i32/i64 A 源子元素加宽**（`widen_int_m`：h0/h1/b0..b3/w0）——扩展符号约定待核；
3. **i64 非零 LUT 64 位扩展**——32 位小常量到 64 位源的扩展约定未定（原 fail-loud）。

本批三项全放 → **移位/旋转族编码空间全清**（宽度 × 方向 × 组合 × 修饰 × 源形态）。另澄清关键约束：**B 源在 ISA 层面无 widen 字段**（valhall `<src not="true">B</src>`）→ 子元素加宽仅存在于 A 源（与 shift 源 lanes 字段），B 只有 `not`。三例源形态 fl 负例随之**语义反转为正例**。

---

## 2. 根因（语义事实与架构建模）

1. **扩展符号约定 = U 类型零扩展**（kraid `swizzle.rs` `fold_u32` + `proc/swizzle.rs` widen 映射）：
   - `SwizzleByte` 三态 `Byte(i)`（取字节）/ `Sign(i)`（**符号位填充整个输出字节**）/ `Zero`；widen 构造按 **src_type 符号性**选 Byte/Sign——移位族 `dst_type` 全为 **U 类型**（`OpShiftLop` `#[variants(dst_type in [U8,V2U8,V4U8,U16,V2U16,U32,U64])]`）→ **零扩展**；
   - **硬件实证**：`hw_tests::test_op_shift_lop` 对 U32/U64 × {None, B0, H0} 做 `Precision::Exact` 硬件对照（fold = to_swizzle + fold_u32/fold_u64）；
   - i64 位宽路径：`fold_u64` 对 32 位 swizzle `fold_u32(u as u32)? as i32 as u64`——U-widen 结果恒 <2^31 → 符号扩展无害、**等价零扩展**。
2. **v2i16 b\*\* 构造** = kraid `Swizzle::widen_bx(dt, X, Y)`：半字 0 ← byte X、半字 1 ← byte Y（零扩展）。命名 `bXY` 与 shift 源 lanes 枚举（`b02` 等）同序法（comp i ← byte 索引）。
3. **i64 LUT 扩展 = 零扩展**：`ir.rs` `u64::try_from(&SrcRef)`：`Imm32(nz) => Ok(nz.get().into())`（u32→u64 into）。
4. 编码位置（沿袭已建模字段）：A 源 `widen@36`（32/64 位源 `widen_int_m`、v2i16 `swiz_int_m`）；B 源仅 `not@35`。

---

## 3. 证据

- **编码回环**（[log-pass.txt](log-pass.txt) §1）：4 条形态字 asm.py 汇编后 A.widen@36 位型核对（`r10.h0`=2 / `r15.b2`=6 / `r16.b20`=5 / `r22^.h1`=3）；
- 失败面（[log-fail.txt](log-fail.txt)）：`widen_src_neg`（A.h0 加宽误漏）错误实现对照 exit 1、真值像素 `0,255,255,255`；正例错期望逐像素报真值；三例语义反转说明；仍拦边界（reserved 码/lane 码越界/B 无 widen 字段）留档；
- 通过面：`frag_widen_src`（i32 h0 零扩展 / i32 b2 / v2i16 b20 构造 / i64 h1+LUT 零扩展，四通道 eq.f1）与 `shift16_swz`/`shift64_widen`/`shift64_lut`（三例 fl 反转正例）MoltenVK 逐像素各 **7812 px 失败 0**，输出 `(255,255,255,255)`；**回归矩阵 92/92**（91 基线 -3 fl 反转 +4 正例 +1 对照 +1 去重 `fl_br_combine` 重复项）；**E2E 10/10 纯绿**。

---

## 4. 改动清单

1. **翻译器（`out/tmp/p2/va2spirv.py`）**：`SWIZ16_BUILD`（b\*\* 构造表，码 4-13）；`_widen32`（h0/h1/b0..b3 零扩展构造）+ `_wide_src`（i32/i64 A 源读值+加宽，64 位码 0/8 走 `read_64` 既有路径、2-7 低字构造后零扩展 u64）；`_half_lane_vals` 放开码 4-13；`emit_shift`（i32）与 `emit_shift_packed` A 源走 `_wide_src`；`_shift64_src` 删除（LUT-0 限制解除，`read_64(is_signed=False)` = kraid 零扩展）；`src_mod_mask` 对 `SHIFT_MODELED` 全 idx 豁免 widen（emit 内校验）。
2. **测试件**：`frag_widen_src.asm`（综合正例）+ `frag_widen_src_neg.asm`（A.h0 误漏对照）；`fl_shift16_swz`/`fl_shift64_widen`/`fl_shift64_lut` → `shift16_swz`/`shift64_widen`/`shift64_lut`（补输出检查转正例），FAIL_LOUD 移出三例（23→20）并去重 `fl_br_combine` 重复登记。

---

## 5. 验证

| 验证项 | 命令 | 结果 |
|---|---|---|
| 编码回环 | asm.py 汇编 4 形态字 | A.widen@36 位型逐位核对 ✓ |
| 源形态逐像素 | `harness rg_ws…` / 三例反转正例 | 各 7812 px 失败 0 |
| 负向对照 | `widen_src_neg` / 错期望 | 均按预期失败（真值 0,255,255,255） |
| 回归 | `python3 run_matrix.py` | **92/92** |
| 端到端 | `bash run_e2e.sh` | **10/10 纯绿** |

---

## 6. 遗留

1. **移位/旋转族已全清**（宽度变体 × 方向 × 组合 × 修饰 × 源形态）；仍 fail-loud 的仅为防御性边界：v2i16 swizzle reserved 码（14/15）、移位量 lane 码越界（i32/i64 仅 b0..b3）、widen 码越界——均为非法编码形态；
2. **S 类型 widen（符号扩展）未建模**——移位族 dst_type 全 U 类型故不适用；若未来其他指令族复用 `_widen32` 需按 src_type 符号性扩展（kraid Sign 语义=符号位填充字节）；
3. 比较族残余（`ICMP_AND.v4u8`、`gtlt/total`、u1）、`BRANCHZ` combine `and/lowbits`、非 fragment 输出组装、真实指针 ABI → B2 渲染桥沿袭不变。
