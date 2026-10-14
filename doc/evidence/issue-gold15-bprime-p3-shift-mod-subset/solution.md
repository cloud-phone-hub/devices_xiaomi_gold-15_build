# issue-gold15-bprime-p3-shift-mod-subset —— B′ P3：移位/旋转族修饰残余（`not_result` / B 源 `not`）建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-shift-ext-subset](../issue-gold15-bprime-p3-shift-ext-subset/solution.md)（遗留 #1「移位/旋转族修饰与源形态残余」）
> 关联：[issue-gold15-bprime-p3-shift-subset](../issue-gold15-bprime-p3-shift-subset/solution.md)（i32 族 not_result 守卫始作俑者）

---

## 1. 问题

移位/旋转族最后两个修饰位仍 fail-loud：

- **`not_result`（bit30）**：组合结果按位取反——历史守卫「未建模则拦截」（i32/v4i8/rotate 三处各有一个 fl 负例钉住）；
- **B 源 `not`（bit35）**：逻辑组合的第二源先按位取反——`src_mod_mask()` 通用拦截。

本批放开两者（宽度 8/16/32/64 × 移位/旋转全族），`fl_shift_and_notres`/`fl_bytevec_notres`/`fl_rot_notres` 三例**语义反转为正例**（沿袭 `fl_br_nest`→`br_nest` 先例）。真机 10 样本（E2E 语料）无带此二修饰的字——属编码空间补齐，验收以合成用例逐像素 + 编码回环为准。

---

## 2. 根因（语义事实与架构建模）

1. **语义 = kraid `OpShiftLop` fold 同序两步**（`ops.rs` `PerCompFoldable::fold_comp`）：
   - `SrcMod::BNot`（`ir.rs`：`fold_u32/fold_u64 → Some(!u)`）：**组合前**对源值全宽按位取反——与逐 lane 提取可交换，故等价于逐 lane 取反后截 lane 宽；
   - `not_result`：**组合后** `data = !data`，`set_dst` 截 lane 宽（v4i8/v2i16 逐 lane 截 8/16 位）。
2. **编码**：两者是移位族 group 级修饰（valhall ISA.xml：`not_result` 在 6 个移位组 `<not_result/>`；`not="true"` 仅 6 处且全在移位组 B 源）→ **全 ISA 仅移位/旋转族有此二修饰**，族内整族放行即可，其余指令继续由 `src_mod_mask` 拦（防御未来合成指令）。
3. **SPIR-V**：`OpNot`（编号 **200**，spirv-dis 实证）单目按位取反；8/16 位 lane 取反后 `& 0xFF/0xFFFF` 截断，32/64 位域天然全宽。

---

## 3. 证据

- **编码回环**（[log-pass.txt](log-pass.txt) §1）：4 条带修饰形态字经 asm.py 汇编，bit30/bit35 位型核对（`LSHIFT_AND.i32.not_result r4, r10, r11.b00, r12.not` 等 i32/v4i8/v2i16/i64 四宽度）；
- 失败面（[log-fail.txt](log-fail.txt)）：`shift_mod_neg`（B 源 `.not` 误漏）错误实现对照 exit 1、真值像素 `255,0,255,255`；正例错期望逐像素报真值 `(255,255,255,255)`；三例语义反转说明留档；
- 通过面：`frag_shift_mod`（B.not × i32/v2i16/i64 + 双修饰叠加 ×2）与 `shift_notres`/`bytevec_notres`/`rot_notres`（not_result × i32/v4i8/rotate）MoltenVK 逐像素各 **7812 px 失败 0**，输出 `(255,255,255,255)`；`spirv-dis` 见 `OpNot`×8（**OpNot=200 实证**）；spirv-val 双 PASS；**回归矩阵 91/91**（89 基线零破坏，-3 fl 反转 +4 正例 +1 对照）；**E2E 10/10 纯绿**。

---

## 4. 改动清单

1. **翻译器（`out/tmp/p2/va2spirv.py`）**：`OpNot=200` 常量；`_not()` 单目取反；`emit_shift`（i32）与 `emit_shift_packed`（8/16/32/64）支持 `not_result` 参数与 B 源 `.not`（fold 同序：组合前取反源 / 组合后取反结果 + lane 截断）；`emit_instr` 两分支删 not_result 守卫改传参；`src_mod_mask` 对 `SHIFT_I32|SHIFT_EXT` 豁免 `not` 键；`decode_word` 源修饰回显 `not`（`Operand` repr `·not`）。
2. **测试件**：`shift_notres.asm`/`bytevec_notres.asm`/`rot_notres.asm`（三例 fl 语义反转为正例，补输出检查）；`frag_shift_mod.asm`（B.not 综合正例：i32/v2i16/i64 + `not_result` 叠加 ×2）+ `frag_shift_mod_neg.asm`（`.not` 误漏对照）；`gen_rot_words.py`/`run_matrix.py` 同步（`fl_rot_notres`→`rot_notres`），FAIL_LOUD 移出三例（25→23）。

---

## 5. 验证

| 验证项 | 命令 | 结果 |
|---|---|---|
| 编码回环 | asm.py 汇编 4 宽度带修饰字 | bit30/bit35 位型逐位核对 ✓ |
| 修饰逐像素 | `harness rg_shift_mod…` / 三个 notres 正例 | 各 7812 px 失败 0 |
| OpNot 实证 | `spirv-dis \| grep OpNot` | ×8（编号 200） |
| 负向对照 | `shift_mod_neg` / 错期望 | 均按预期失败（真值 255,0,255,255） |
| 回归 | `python3 run_matrix.py` | **91/91** |
| 端到端 | `bash run_e2e.sh` | **10/10 纯绿** |

---

## 6. 遗留

1. **源形态残余**（沿袭）：v2i16 的 b** 字节构造 swizzle、i32/i64 的子元素加宽（h0/h1/b0..b3/w0，符号扩展约定待核）、i64 非零 LUT 的 64 位扩展——仍 fail-loud；
2. **移位/旋转族修饰已全清**（not_result / B.not 均放行）——族内仅剩上述源形态与 i32 shift 源 `.b00` 单一字节选择约束（b1/b2/b3 在 packed 路径已放、i32 emit_shift 路径守卫未动）；
3. 比较族残余（`ICMP_AND.v4u8`、`gtlt/total`、u1）、`BRANCHZ` combine `and/lowbits`、非 fragment 输出组装、真实指针 ABI → B2 渲染桥沿袭不变。
