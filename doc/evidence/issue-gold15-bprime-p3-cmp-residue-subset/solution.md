# issue-gold15-bprime-p3-cmp-residue-subset —— B′ P3：比较族残余（`ICMP_AND` accum / `gtlt` 条件 / `BRANCHZ` combine `and`·`lowbits`）建模 + `total`/`u1`/`f1`(8 位) 归档定性

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-widen-src-subset](../issue-gold15-bprime-p3-widen-src-subset/solution.md)（下一步优先级 1「比较族残余」）
> 关联：[issue-gold15-bprime-p3-cmp-subset](../issue-gold15-bprime-p3-cmp-subset/solution.md)（比较族基建始作俑者）、[issue-gold15-bprime-p3-bytevec-subset](../issue-gold15-bprime-p3-bytevec-subset/solution.md)（BRANCHZ combine none/h0/h1）

---

## 1. 问题

比较族残余四项（widen-src 批后 README 优先级 1）：

1. **`ICMP_AND.v4u8`**（accum 布尔 AND 的逐 lane 宽度变体）；
2. **`gtlt` 比较条件**（cmp-subset 批起 fail-loud）；
3. **`BRANCHZ` combine 的 `and`/`lowbits` 归约**（bytevec 批放了 none/h0/h1）；
4. **`result_type` 的 `f1`（8 位）/`u1`、条件 `total`**——本批取证后**定性归档**（编码层非法 / 归属未建模指令），维持 fail-loud 有据不猜。

---

## 2. 根因（语义事实与架构建模）

1. **ICMP_AND accum = 布尔与先于编码**（kraid `OpICmp` fold：`accum_op.fold(c, accum != 0)`，`CmpAccumOp::And = orig && accum`）：`pred_i = (A_i cond B_i) && (C_i != 0)`。编码：`ICMP_AND.v4u8`=`0xF2@48`+opcode2 `0x1@24/0x3`、`ICMP_AND.u32`=`0xF0@48`+`0x1@24/0x3`（ISA.xml ICMP_AND 组）。v4u8 逐 lane `OpLogicalAnd`；u32 复用 `emit_cmp(combine="and")`（m1/i1 谓词编码下位与≡布尔与，真机 C 源为谓词常量形态）。
2. **`gtlt` = 有序不等**（kraid `CmpOp::GtLt = ord.is_some_and(!is_eq)`）：整数域 = ne（`OpINotEqual`）；浮点域 = **`OpFOrdNotEqual`（182）**——与 `ne` 的 `OpFUnordNotEqual`（无序→true）镜像区分（gtlt 无序→false）。
3. **`BRANCHZ` combine `and`/`lowbits`**（kraid `BranchCombineOp` 注释语义）：`and` = 「整字 ==0xFFFFFFFF」判定；`lowbits` = 「bits 3..20 非全 0 且非全 1」判定；`.eq`（=kraid `not`）整体取反。`OpLogicalNot`（168）/`OpLogicalAnd`（167）编号 spirv-dis 实证。
4. **归档定性（维持 fail-loud 的依据）**：
   - `total`：kraid `total_cmp_data` 仅浮点（IEEE 754 **totalOrder**，整数直接 panic）且 **SPIR-V 无 totalOrder 原语**→ 合成=猜，不建；
   - `u1`：kraid `CmpResultType::C` 仅 `ICMP_MULTI`（64 位比较构造，opcode2=0x2）使用，普通 ICMP `fold` 直接 panic——归属指令未建模故无独立语义；
   - `f1`（8 位 lane）：kraid `CmpResultType::fold` 对 8 位 `panic!("Invalid float length")`（仅 32/16 有效）= **编码层非法**，非"未建模"。

---

## 3. 证据

- **编码回环**（[log-pass.txt](log-pass.txt) §1）：3 条形态字 asm.py 汇编后 opcode2/condition/result_type 位型核对（v4u8 and=1/ne=3/i1=0；u32 and/gt=1/m1=2；f32 gtlt=6/m1=2）；
- **NaN 探针**（[log-fail.txt](log-fail.txt) §1，关键平台取证）：`OpFOrdNotEqual(NaN, 1.0)` 按规范应 false，**MoltenVK/Metal 实渲 true**（Metal `!=` 对 NaN 为无序语义，平台层与 SPIR-V 有序语义不符）→ 翻译器保持规范 `OpFOrdNotEqual`，gtlt 测试以**有序输入双向锁定映射**（2.0≠1.0→true 防误译 lt/le/eq；1.0==1.0→false 防误译 ne/ge/le），NaN 行为待 B2/真机复核；
- 失败面：`cmp_and_neg`（accum 误作 OR）对照 exit 1、真值像素 `0,255,255,255`；正例错期望逐像素报真值；`total/u1/f1` 三者归档依据留档（fl_cmp_rtype/fl_v4u8_rtype 守住）；
- 通过面：`frag_cmp_and`（v4u8 布尔 AND + u32 AND + gtlt 双向锁定，四通道 eq.f1）与 `frag_br_combine`（`.and`/`.lowbits` 双分支场景）MoltenVK 逐像素各 **7812 px 失败 0**，输出 `(255,255,255,255)`/`(102,102,255,255)`；`spirv-dis` 见 `OpLogicalAnd`/`OpFOrdNotEqual`（167/182 实证）；**回归矩阵 93/93**（92 零破坏，-2 fl 反转 +2 正例 +1 对照）；**E2E 10/10 纯绿**。

---

## 4. 改动清单

1. **翻译器（`out/tmp/p2/va2spirv.py`）**：`OpLogicalAnd=167`/`OpLogicalNot=168`；`CMP_INT_OPS`/`CMP_FLOAT_OPS` 增 `gtlt` 映射；`emit_cmp_v4u8` 增 `accum` 参数（`OpLogicalAnd`）；`ICMP_AND.u32` 走 `emit_cmp(combine="and")`；`_cmp_pred`/`emit_cmp_v4u8` 的 total 拦截改精确（gtlt 放行）；`branch_cond` 增 combine 3/4 归约（`_pred_not` 单目谓词取反）；`_pred_not()` helper（修 `OpLogicalNot` 误用二目 `_pred` 的 TypeError）；SUPPORTED/`BYTE_VEC_MODELED` 并入 `ICMP_AND.v4u8`/`ICMP_AND.u32`。
2. **测试件**：`frag_cmp_and.asm`（综合正例）+ `frag_cmp_and_neg.asm`（accum OR 误实对照）；`frag_br_combine.asm`（`.and`/`.lowbits` 双分支正例）；`fl_cmp_cond`/`fl_br_combine` 随放开语义反转并入正例（删除），FAIL_LOUD 移出两项（23→21）。

---

## 5. 验证

| 验证项 | 命令 | 结果 |
|---|---|---|
| 编码回环 | asm.py 汇编 3 形态字 | opcode2/cond/rtype 位型核对 ✓ |
| NaN 探针 | `gtlt(NaN,1)` MoltenVK | 实渲 true = 平台偏差实证（避开输入+归档） |
| 逐像素 | `harness rg_cmp_and…` / `rg_brc…` | 各 7812 px 失败 0 |
| opcode 实证 | `spirv-dis \| grep` | `OpLogicalAnd`/`OpFOrdNotEqual` ✓ |
| 负向对照 | `cmp_and_neg` / 错期望 | 均按预期失败（真值 0,255,255,255） |
| 回归 | `python3 run_matrix.py` | **93/93** |
| 端到端 | `bash run_e2e.sh` | **10/10 纯绿** |

---

## 6. 遗留

1. **比较族已放**：`ICMP_AND`（u32/v4u8）、`gtlt`、`BRANCHZ` combine 全 5 形态（none/h0/h1/and/lowbits）；
2. **维持 fail-loud 且已归档定性**：`total`（SPIR-V 无 totalOrder 原语）、`u1`（归属 ICMP_MULTI 未建模）、`f1`（8 位 lane 编码层非法）——如未来放 `ICMP_MULTI`（64 位比较构造）需连带 `u1` 语义；
3. **平台语义待复核**：MoltenVK/Metal `OpFOrdNotEqual` 对 NaN（无序）判 true 与 SPIR-V 规范不符（探针实证）——gtlt/`ne` 镜像语义在 NaN 输入上的像素行为待 B2/真机数据面复核；
4. **emit_cmp 的 and/or accum 保持位与/位或**（m1/i1 谓词编码下≡布尔，既有 FCMP_AND/cmp_or 用例兼容）——对脏常量 C 与 kraid 布尔语义有偏差，注释已列明；
5. 非 fragment 输出组装、真实指针 ABI → B2 渲染桥、探针清理、B2 验收门沿袭不变。
