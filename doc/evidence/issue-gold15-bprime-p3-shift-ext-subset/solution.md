# issue-gold15-bprime-p3-shift-ext-subset —— B′ P3：移位族宽度变体（`v2i16`/`i64`）与 rotate（`LROT`/`RROT`）建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-bytevec-subset](../issue-gold15-bprime-p3-bytevec-subset/solution.md)（遗留 #1「移位族宽度变体与修饰残余」）
> 关联：[issue-gold15-bprime-p3-shift-subset](../issue-gold15-bprime-p3-shift-subset/solution.md)（i32 全族 9 变体）、[issue-gold15-bprime-p3-e2e-closeout](../issue-gold15-bprime-p3-e2e-closeout/solution.md)

---

## 1. 问题

bytevec 批后移位族残余 = **宽度变体**（`v2i16` 2×16 打包、`i64` 1×64）与 **rotate**（`LRot`/`RRot`）仍 fail-loud（i32 全 9 变体 + v4i8 全 9 变体已放）。本批把移位族推进到**编码空间全宽收口**：

- `LSHIFT_*/RSHIFT_*/ARSHIFT_*.v2i16`（9 变体，opcode `0xB5@48`）；
- `LSHIFT_*/RSHIFT_*/ARSHIFT_*.i64`（9 变体，opcode `0x1B7@48`，源/目的为寄存器对）；
- `LROT_*/RROT_*`（**rotate 全宽** i32/v2i16/v4i8/i64 × AND/OR/XOR = 24 变体）。

**rotate 是首个 valhall ISA.xml 完全缺失的族**（仅 kraid `isa-v9-v14.xml` 有）→ 解码与汇编双缺口：`match_instruction` 认不出 rotate 字、`asm.py` 无 rotate 助记符。本批须同时解决**解码表合成**与**测试字生成**两条基建。

真机 10 个重捕 shader（E2E 语料）全部可译 = **无本批形态实测样本**——本批属编码空间前瞻补齐，验收以合成用例逐像素 + 解码回环为准（见 §3）。

---

## 2. 根因（语义事实与架构建模）

1. **rotate 与移位同族同布局**（kraid `isa-v9-v14.xml` R3/D3 `SHIFT_BITWISE_*` 字段表）：`src0@0/8`、`src1@8/8`、`src2@16/8`、`bitwise_operation@24/2`（and=0/or=1/xor=2）、`lanes1@26/4`、`not_result@30`、**`shift_operation@32/3`（rshift=0/lshift=1/rrot=2/lrot=3/arshift=4）**、`not2@35`、swz/widen@36/4、dest@40、dest_width@46（i64 为 `double_width` exact=3）、opcode1@48（i32=4/v2i16=5/v4i8=6/i64=7 → valhall opcode `0xB4/0xB5/0xB6/0x1B7`）、class@52（R3；i64 为 D3）。**双源自洽**：valhall opcode2（mask `0x703@24` = comb@24-25 | shift_op@32-34）的 RSHIFT=`0x000`/LSHIFT=`0x100`/ARSHIFT=`0x400` 与 kraid 枚举逐位吻合 → rotate 的 opcode2 = **RROT=0x200 系 / LROT=0x300 系**（`LROT` 纯形 = `LROT_OR` 且 `src2=LUT0`，kraid 别名机制）。
2. **逐 lane 语义 = kraid `OpShiftLop` PerCompFoldable**（`hw_tests::test_op_shift_lop` 对 V4U8/V2U16/U32/U64 硬件对照）：lane 宽 8/16/32/64，移位量掩到 lane 宽（`wrapping_shl/shr`），`LRot/RRot` = 同量掩 `rotate_left/right`，`ARSHIFT` 按 lane 有符号保号，逐 lane 逻辑组合。
3. **移位量 lane 码宽度相关**（同一 `widen@26` 字段，按指令位宽取不同枚举）：
   - `v4i8` = `lanes_int_m`（=`swizzles_8_bit` 字节置换，默认 b0123=0）；
   - `v2i16` = `shift_bitwise_i16_lanes_m`（**b02=0 默认**：各 lane 移位量取对应半字的低字节=字节 [0,2]；b00=4/b11=5/b22=6/b33=7/b01=10/b23=11）；
   - `i32/i64` = `shift_bitwise_i32_lanes_m`（b0..b3 = 码 4..7 → 单字节选择）。
4. **数据源 swizzle**（`widen@36`，按位宽取枚举）：`v2i16`=`swiz_int_m`（= `swizzles_16_bit`，h00/h10/h01/h11 半字置换，默认 h01；b** 字节构造形态未建模）；`i32/i64`=`widen_int_m`（none/h0/h1/b0..b3/w0 子元素加宽）→ 本批仅放行 none（子元素加宽的符号扩展约定无据，fail-loud）；`i64` 源/目的为**寄存器对**（`SourceEncoding64`，读 rN:rN+1），LUT 小常量仅放行 0（纯 rotate 的 src2=LUT0 形态；非零 32 位常量的 64 位扩展约定未建模）。

---

## 3. 证据

- **rotate 解码回环 4/4**（生成器补丁字 → `va2spirv` 解码，字段逐位核对，见 [log-pass.txt](log-pass.txt) §1）：
  `0x00b4c40311c00b0a`→`LROT_OR.i32`（comb=or/lrot）、`0x00b4c50210c10e0d`→`RROT_AND.i32`（and/rrot）、
  `0x00b5c62302c01110`→`LROT_XOR.v2i16`、`0x01b7c80211571654`→`RROT_OR.i64`（`reg_ptr:20` 对读）。
  rotate 字由 `gen_rot_words.py` 按 kraid 字段表打 `shift_operation@32/3` 补丁生成（asm.py 汇编移位族占位字 + 3 位补丁，补丁前断言占位 shift_op 防漂移）。
- 失败面（[log-fail.txt](log-fail.txt)）：5 条 fl_* 精准拦截 exit 2（v2i16 `.b00` 字节构造 swizzle / i64 `.b0` 子元素加宽 / i64 非零 LUT / rotate `not_result` / i32 rotate 移位量 lane 码越界）；`rot_neg`（RROT 误作 RSHIFT）与 `shift_ext_neg`（ARSHIFT 误作 RSHIFT）错误实现对照均 exit 1、真值像素 `255,0,255,255`。
- 通过面：`frag_shift_ext`（v2i16 b02 默认移位量 + `.b11` 位移字节选择 + ARSHIFT 保号 + i64 寄存器对移位）与 `frag_rot`（LROT_OR.i32 纯形 / RROT_AND.i32 / LROT_XOR.v2i16 逐半字 / RROT_OR.i64）MoltenVK 逐像素各 **7812 px 失败 0**，输出 `(255,255,255,255)`；spirv-val 双 PASS；**回归矩阵 89/89**（既有 80 零破坏 + 2 正例 + 2 对照 + 5 fail-loud）；**E2E 10/10 纯绿**。

---

## 4. 改动清单

1. **翻译器（`out/tmp/p2/va2spirv.py`）**：
   - `_synth_rotate_instrs()`：rotate 解码表合成（valhall ISA.xml 缺失 → 由 `LSHIFT_*` 模板复制，opcode2 = comb | (sh_op<<8)，24 条）；
   - `SHIFT_V2I16`/`SHIFT_I64`/`ROT_ALL`/`SHIFT_EXT` 族集合，`SUPPORTED` 并入 42 个名字；
   - 移位族泛化 `emit_shift_packed`（宽 8/16/32/64 × 5 方向 × 3 组合，`emit_shift_v4i8` 并入；i32 移位族沿用 `emit_shift` 不动）+ `_shift_lane`/`_shift64_lane`（LROT/RROT 合成 `(x<<s)|(x>>((w-s)&(w-1)))`，s=0 自洽）/`_shift64_src`（i64 对读 + LUT-0 收口）/`_half_lane_vals`/`_shift_lanes`/`_pack_halves`/`_sext_w`；
   - `SWIZ16_HALFS`/`SHIFT16_BYTES` 常量表（kraid `swiz_int_m`/`shift_bitwise_i16_lanes_m` 映射）；`src_mod_mask` 豁免 `SHIFT_EXT` 的 widen（emit 内按宽度校验，fail-loud 不降级）；`not_result`（bit30）/B 源 `not` 保持 fail-loud。
2. **测试基建**：`gen_rot_words.py`（rotate 测试字生成器：asm.py 汇编占位 + kraid 字段表 3 位补丁 + 占位断言）；`run_matrix.py` 增 `GEN_CASES` 钩子与 9 用例（80→89）。
3. **测试件**：`frag_shift_ext.asm`（v2i16/i64 移位正例）+ `frag_shift_ext_neg.asm`；`frag_rot.asm`（rotate 全宽正例，4 形态：纯 LROT / RROT_AND / LROT_XOR.v2i16 / RROT_OR.i64）+ `rot_neg`（生成器复用 frag_rot.asm 少打一补丁 = 错误实现）；`fl_shift16_swz`/`fl_shift64_widen`/`fl_shift64_lut`/`fl_rot_notres`/`fl_rot_shiftsel` 五条 fail-loud。

---

## 5. 验证

| 验证项 | 命令 | 结果 |
|---|---|---|
| rotate 解码回环 | `gen_rot_words.py rot` → `va2spirv` 解码 | 4/4 字段逐位自洽 |
| v2i16/i64 逐像素 | `harness rg_shift_ext…` | 7812 px 失败 0 |
| rotate 逐像素 | `harness rg_rot…` | 7812 px 失败 0 |
| 负向对照 | 错期望 / `rot_neg` / `shift_ext_neg` | 均按预期失败（真值 255,0,255,255） |
| fail-loud | 5 条 fl_* | 全部 exit 2 |
| 回归 | `python3 run_matrix.py` | **89/89** |
| 端到端 | `bash run_e2e.sh` | **10/10 纯绿** |

---

## 6. 遗留

1. **修饰残余**（沿袭）：`not_result`（bit30）与 B 源 `not`（bit35）仍 fail-loud——移位/旋转族最后两个修饰位；
2. **源 swizzle/widen 残余**：v2i16 的 b** 字节构造形态（半字由单字节构造）、i32/i64 的子元素加宽（h0/h1/b0..b3/w0，符号扩展约定待核）、i64 非零 LUT 的 64 位扩展——均 fail-loud 未猜；
3. **rotate 真机字回环缺口**：rotate 不在 valhall ISA.xml（asm.py 无助记符），本批以 kraid 字段表推导 + 解码回环代替 asm.py 回环；待真机样本出现后补真机字逐位命中；
4. **v4i8/v2i16/i64 rotate 正例抽测形态有限**（LROT_XOR.v2i16 / RROT_OR.i64 各一，i32/v4i8 rotate 经同构代码路径），9×2×4 全组合为表驱动同构；
5. 比较族残余（`ICMP_AND.v4u8`、`gtlt/total`、u1）、`BRANCHZ` combine `and/lowbits`、非 fragment 输出组装、真实指针 ABI → B2 渲染桥沿袭不变。
