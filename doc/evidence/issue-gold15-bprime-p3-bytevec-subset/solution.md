# issue-gold15-bprime-p3-bytevec-subset —— B′ P3：字节向量子族（`v4i8` 移位族 / `ICMP_OR.v4u8` / `U8_TO_U32`）建模 + dest 窄写与 `BRANCHZ` combine 归约，E2E 10/10 收官

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-e2e-closeout](../issue-gold15-bprime-p3-e2e-closeout/solution.md)（遗留 #1「字节向量子族未建模」= 01000 记账根因）
> 关联：[issue-gold15-bprime-p3-e2e-real-shaders](../issue-gold15-bprime-p3-e2e-real-shaders/solution.md)（E2E 记账口径）

---

## 1. 问题

E2E 唯一记账项 `bin-007f00001000`（Compute 样本）卡在字节向量子族——四条连排指令构成 4×i8 打包算术段：

- `[35] 0x00b645210087c044` `LSHIFT_AND.v4i8 r5.h0, r4^.b0101, 0x0, u3.w1`
- `[36] 0x00b644210287c005` `LSHIFT_XOR.v4i8 r4.h0, r5.b0101, 0x0, u3.w1`
- `[37] 0x00f2454300c0c045` `ICMP_OR.v4u8.ne.i1 r5.h0, r5^.b0000, 0x0, 0x0`
- `[38] 0x0090c40000100044` `U8_TO_U32 r4, r4^.b0`

老口径按「逐 lane 归约语义 ISA.xml 无文档依据 → 不猜」记账（run_e2e 口径⑤）。本批把语义依据补齐（kraid `PerCompFoldable` 参考模型 + `hw_tests` 硬件对照），验收标准 = **`run_e2e.sh` 的 01000 由「记账」转 OK，E2E 10/10 纯绿**。

解码推进过 [35]-[38] 后又暴露两层隐藏形态（此前被 [35] 的 fail-loud 截断遮蔽）：

- `[40] 0x501fc03000000545` `BRANCHZ r5^.h0, offset:5` —— 源 **combine=1（h0 半字归约）**，此前「源 combine 非 none 一律 fail-loud」；
- `bin-007f00026e00[30]` `FADD.f32 r63, -r0, 1.0` —— **dest 宽度字段=0**（原错误拦截为「未建模写掩码」）。

---

## 2. 根因（语义事实与架构建模）

1. **4×i8 打包算术 = kraid `PerCompFoldable` 逐分量 fold**（`foldable.rs`：`get_src` 按分量取 `[i*8,(i+1)*8)` 位段、`set_dst` 截回 lane 宽；`hw_tests.rs` `test_op_shift_lop` 对 V4U8 做硬件对照）：
   - `OpShiftLop`（移位族）：`dest_i = ((A_i <移位> shift_i) <AND/OR/XOR> B_i)`，移位量 **lane 宽截断**（`u8::wrapping_shl/shr` 掩 3 位 = 模 8）、`ARSHIFT` 按 **i8 保号**（`(src0 as i8).wrapping_shr`）、逐 lane 截 8 位；
   - `OpICmp`（比较族）：逐 lane 无符号 8 位比较 → `accum_op.fold(pred, C_i != 0)`（**布尔** OR 先于编码）→ `CmpResultType::fold(pred, 8)` 逐 lane 编码（`i1`=0/1、`m1`=0xFFFFFFFF 截 8 位=0xFF、`f1` 对 8 位 panic=不可用、`u1` 同样未建模）。
2. **编码**（ISA.xml 元数据 + asm.py 回环钉死）：
   - `v4i8` 移位族 = opcode `0xB6@48` + 同 i32 的 opcode2@24 表（mask `0x703`）；
   - A 源 `widen@36`、shift/B 源（序 1）`widen@26` = **`swizzles_8_bit` 字节 lane 置换**（`b<abcd>` = 输出 lane i 取源字节 abcd[i]；`b0000` 广播码与 `lanes_8_bit` 的 `b00` 同值同义；索引 12-15 = reserved）——与 32 位源的 `swizzles_32_bit`（`.b0` 提取）同名不同义，**由 `typesize(指令名)` 决定枚举**；
   - `ICMP_OR.v4u8` = `0xF2@48` + opcode2 `0x0@24`，`condition@32/3`、`result_type@30/2` 同 u32 表；C 源无 swizzle；
   - `U8_TO_U32` = `0x90@48` + opcode2 `0x10@16`，源 `lane@28/2`（`lane_8_bit` b0..b3）→ 提字节零扩展。
3. **dest 宽度字段（bits46-47，`dest_width_m`：1=h0、2=h1、3=none；0 未列码）**：
   - `h0/h1` = **窄写对应半字、未写半字保留旧值**——kraid `DstLanes::u32_mask()` + `hw_tests::test_lower_copy` 硬件实测 `expected = (INIT_DST & !mask) | (SOURCE & mask)`；
   - **width=0 实证为真写全字**：`26e00[30] FADD.f32 r63(width=0)` 之后 `[32] FMA.v2f16` **整字读 r63**（v2f16 双分量），若不写则读预置值=坏 shader；同 `dest_width_narrow_m` 的 `0=h01`（双半字）语义。
4. **`BRANCHZ` 源 `combine@37/3`（`branch_combine_m` / kraid `BranchCombineOp`）**：`none`=整字非零、`h0/h1`=对应半字非零、`and`=全 1、`lowbits`=bits3..20 非全 0/全 1。01000[40] 的 `r5^.h0` = 对 v4u8 逐 lane 谓词取低半字归约后判零。本批建模 none/h0/h1，and/lowbits fail-loud。

---

## 3. 证据

- **编码回环 4/4 逐位命中**（asm 文本 → Mesa `asm.py` → 与真机字比对，见 [log-pass.txt](log-pass.txt) §1）：
  `LSHIFT_AND.v4i8 r5.h0, r4^.b0101, 0x0, u3.w1` → `0x00b645210087c044`；`LSHIFT_XOR.v4i8 r4.h0, r5.b0101, 0x0, u3.w1` → `0x00b644210287c005`；`ICMP_OR.v4u8.ne.i1 r5.h0, r5^.b0000, 0x0, 0x0` → `0x00f2454300c0c045`；`U8_TO_U32 r4, r4^.b0` → `0x0090c40000100044`。
- 失败面（[log-fail.txt](log-fail.txt)）：
  - 5 条 fl_* 负例精准拦截 exit 2（reserved swizzle 码 / not_result / v4u8 `result_type=f1` / `.h0` 窄写未定义寄存器 / `BRANCHZ.and` combine）；
  - `bytevec_neg` 错误实现（ARSHIFT 误作 RSHIFT）逐像素对照失败（真实像素 `255,255,0,0`，exit 1）；
  - 正例错期望负向对照逐像素报真值 `(255,255,255,255)`。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - 真机样本 `bin-007f00001000` 整 shader 翻译 exit 0 + `spirv-val --target-env vulkan1.2` **双 PASS**；
  - `spirv-dis` 见逐 lane 序列（`OpBitwiseAnd %int_255` 提字节、`OpShiftRightArithmetic` 保号、`OpLogicalOr`+`OpSelect` 的 i1/m1 编码）；
  - MoltenVK 离屏逐像素：**7812 px 失败 0**，输出 `(255,255,255,255)`（四通道 eq.f1 检查全真）；
  - 全量回归矩阵 **80/80**（既有 73 零破坏 + bytevec 正例 + bytevec_neg 对照 + 5 fail-loud）；
  - **E2E 10/10 纯绿**（01000 记账 → OK，记账数归零）。

---

## 4. 改动清单

1. **翻译器（`out/tmp/p2/va2spirv.py`）**：
   - 新增 `SHIFT_V4I8`（9 变体）/ `BYTE_VEC_MODELED`，`SUPPORTED` 并入 `v4i8` 移位族 + `ICMP_OR.v4u8` + `U8_TO_U32`；
   - `swizzle8_lanes()`：ISA 枚举驱动的字节 lane 置换解析（reserved → fail-loud）；
   - 逐 lane 基建 `_binop`/`_pred`/`_extract_byte`/`_byte_lane_vals`/`_pack_bytes`；
   - `emit_shift_v4i8`（lane 宽截断移位 + ARSHIFT 保号 + 逐 lane 组合）、`emit_cmp_v4u8`（逐 lane 比较 + accum 布尔 OR + i1/m1 逐 lane 编码）、`emit_cvt_u8`（lane 提取零扩展）；
   - `emit_instr` 统一 dest 宽度处理：h0/h1 掩码合并保留旧半字（未定义即被掩码写 → fail-loud），width=0 按全字写（实证，见 §2.3）；
   - `branch_cond` 增 `combine` 归约（none/h0/h1），`_branch_kind` 常量路径同步；`decode_word` 源 `combine` 解码回显（`cmbN`）；
   - `src_mod_mask` 豁免：字节向量源 widen / `U8_TO_U32` lane / `BRANCHZ` combine（emit 内校验，fail-loud 不降级）。
2. **测试件（`out/tmp/p2/`）**：`frag_bytevec.asm`（正例，四通道 eq.f1 检查：b0101 置换+逐 lane 移位量 / h0 窄写保留高半字 / ARSHIFT 保号 / v4u8 逐 lane i1 + U8_TO_U32 四 lane 加权链）；`frag_bytevec_neg.asm`（ARSHIFT 误 RSHIFT 对照）；`fl_bytevec_swz`/`fl_bytevec_notres`/`fl_v4u8_rtype`/`fl_bytevec_wrmask` 四条新负例；`fl_br_combine` 语义更新（原「combine 非 none」负例随 h0/h1 放行改为 `.and` 归约拦截）；`run_matrix.py` 注册 7 用例（73→80）。
3. **E2E**：`run_e2e.sh` 零改动（记账⑤口径自然消除）。

---

## 5. 验证

| 验证项 | 命令 | 结果 |
|---|---|---|
| 编码回环 | `asm.py` 汇编 4 条手写 asm ↔ 真机字 | 4/4 逐位命中 |
| 真机整 shader | `va2spirv.py e2e/bin-007f00001000.le.bin …` | exit 0 + spirv-val 双 PASS |
| 逐像素（正例） | `harness rg_bytevec.frag.spv … 255 255 255 255` | 7812 px 失败 0 |
| 负向对照 | 错期望 / `bytevec_neg` | 均按预期失败（exit 1） |
| fail-loud | 5 条 fl_* | 全部 exit 2 |
| 回归 | `python3 run_matrix.py` | **80/80** |
| 端到端 | `bash run_e2e.sh` | **10/10 纯绿**（0 记账） |

---

## 6. 遗留

1. **移位族宽度变体**（沿袭）：`v2i16`/`i64` 移位族、rotate（`LRot`/`RRot`）、`not_result`、B 源 `not` 仍 fail-loud；`v4i8` 族的 9 变体为表驱动同构放开，正例抽测 `LSHIFT_AND`/`LSHIFT_XOR`/`ARSHIFT_AND` 三形态（未逐一逐像素）；
2. **比较族 v4u8 残余**：`ICMP_AND.v4u8`、`result_type` 的 `f1`（8 位无浮点编码，kraid 直接 panic）/`u1`、`condition` 的 `gtlt/total` 仍 fail-loud；
3. **`BRANCHZ` combine 的 `and`/`lowbits` 归约**未建模（kraid 注释有一句描述但无样本需要，不猜）；
4. **dest 宽度=0 的精确语义**（`h01` 双半字写 vs `none` 全宽写）为实证推断（26e00 [30]→[32] 读链），像素级差异待 B2 真机数据面复核；
5. **真实指针 ABI / 探针清理 / B2 渲染桥 → P3 验收门**沿袭不变（r56-r63 预置仍为常量 0）。
