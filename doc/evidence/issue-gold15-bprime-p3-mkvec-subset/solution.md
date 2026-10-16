# issue-gold15-bprime-p3-mkvec-subset —— B′ P3：向量构造族（`MKVEC.v2i16`）建模与 `vartex_real.bin` 真机全链贯通

> 状态：**验收门通过（2026-10-14）**，承接
> [issue-gold15-bprime-p3-tex-subset](../issue-gold15-bprime-p3-tex-subset/solution.md) 遗留 #1
> （`vartex_real.bin` 全链卡在 `MKVEC.v2i16`）—— 断点续做优先级第 1 项
> 语义参照：mesa `valhall/ISA.xml` + `bi_opt_constant_fold.c` + kraid
> `ops.rs`/`swizzle.rs`（`deps/mesa/` 只读，未改动）

---

## 1. 问题

`vartex_real.bin`（run106 B2 渲染桥 GR01 帧 48B 真机 fragment shader）全链
翻译被 fail-loud 拦截：`错误：不支持的指令 MKVEC.v2i16（P2 子集外）` → exit 2。
该 shader 是真机 UI 纹理绘制链（VAR_TEX_BUF_SINGLE 采样 → MKVEC → FMA.v2f16
×2 → ATEST → BLEND）的完整代表，MKVEC 是像素回显验收（优先级第 6 项）的前置
语义缺口。要求 MVP 建模 `MKVEC.v2i16` 向量构造族（超出面 fail-loud）。

---

## 2. 根因（语义事实与架构建模）

1. **`MKVEC.v2i16` 语义三源钉死**：
   - `ISA.xml:2567`：`dest = A | (B << 16)`，`<src lane="28">A</src>`
     `<src lane="26">B</src>`（lane=1 位，h0=0 低半 / h1=1 高半）；
   - `bi_opt_constant_fold.c:44`：`MKVEC_V2I16: return (b << 16) | (a & 0xFFFF)`
     —— 与 ISA desc 逐项吻合；
   - kraid `ops.rs:3352`：`OpMkVecV2I16 { #[dst_type(V2I16)] dst, #[src_type(I16)]
     srcs: [Src; 2] }` —— 双源各取 16 位语义吻合。
   → 建模 = 两源各取 `lane` 位选择的 16 位半字（`_extract_half`），按
   `_pack_halves([ha, hb])` 打包（lo=A 半字、hi=B 半字）。编码用
   `assembler-cases.txt:52-146` 的既有参考字（`MKVEC.v2i16 r1, r0^.h0,
   0x3C000000.h1` 等）回环核对。
2. **顺带修 `FMA.v2f16` 源 swizzle 语义错译**（解锁真机链的第二个卡点）：
   真机链 `FMA.v2f16` 源带 `swz2/swz3`，而旧建模只放行 swizzle 0/2 且**都当
   恒等**——swz2 恰好恒等蒙对，swz3 必被拦，且 swz0/1 的真实语义被掩盖。
   权威语义（`kraid/swizzle.rs:309-312` `H00=B0101/H01=B0123/H10=B2301/
   H11=B2323` + `compiler.h:33-39` 16 位枚举序 H00=0/H01=1/H10=2/H11=3 +
   `valhall.py` swizzle 编码位 + `assembler-cases.txt:20-22` 编码回环）：
   **输出 lane i 取源半字 `SWIZ16_HALFS[码][i]`**，码 0-3 = h00/h10/h01/h11
   （注意 ISA.xml `Swizzles (16-bit)` 的 h10/h01 值序与 compiler.h 枚举序相反，
   按 asm.py `bare_values.index()` 编码：码 1=h10（交换）、码 2=h01（恒等））。
   → `emit_fma_v2f16` 改为逐 lane 按 `SWIZ16_HALFS` 取半字，四码全建模。
   （`V2F32_TO_V2F16` 源是 32 位标量（ISA.xml 无 swizzle 属性），不受影响。）
3. **fail-loud 面**：`MKVEC.v2i8`（8 位四源构造，ISA.xml:3212）未建模 → SUPPORTED
   不含即拦；dest 窄写（wrmask=h0/h1）到未定义寄存器（保留半字旧值无法建模）
   → 既有守卫拦截。两面均入矩阵。

---

## 3. 证据

- **真机全链贯通**：`vartex_real.bin`（LE 读）6 字全解码
  `VAR_TEX_BUF_SINGLE → MKVEC.v2i16 → FMA.v2f16(sws2/3/2) → ATEST →
  FMA.v2f16 → BLEND`，翻译 **exit 0** + spirv-val vulkan1.2 **PASS**（frag+vert）。
- **编码回环**：`frag_mkvec.asm` 四形态（reg 双 h0 广播 / h1:h0 交换 / 双 LUT
  立即数 / reg+FAU）经 asm.py 汇编 → 翻译 → 期望值逐条比对（r0..r3 各比较全等
  → 全 255），若 lane 位未生效/A-B 高低放反/FAU 字选择错则对应通道变 0。
- 通过面（[log-pass.txt](log-pass.txt)）：`mkvec` 逐像素 **7812 px 失败 0**；
  run_matrix **114/114**（109 基线 + mkvec/mkvec_neg/fl_mkvec_i8/fl_mkvec_wr/
  vartex_real）；run_e2e **10/10** 零回退。
- 失败面（[log-fail.txt](log-fail.txt)）：`mkvec_neg` 负向对照（错期望）
  **exit 1** 逐像素报真值；`fl_mkvec_i8`/`fl_mkvec_wr` **exit 2** 精准拦截。

---

## 4. 改动清单

1. **`out/tmp/p2/va2spirv.py`**：SUPPORTED +`MKVEC.v2i16`；新增
   `emit_mkvec_v2i16`（`_extract_half`×2 + `_pack_halves`）；`emit_fma_v2f16`
   源 swizzle 由「0/2 恒等」改为 `SWIZ16_HALFS` 半字置换（码 0-3 全建模）；
   `LANE_MODELED` +`MKVEC.v2i16`（源 lane 位已建模）。
2. **`out/tmp/p2/run_matrix.py`**：EXPECT +`mkvec`、NEG +`mkvec_neg`、
   FAIL_LOUD +`fl_mkvec_i8`/`fl_mkvec_wr`；REAL_BIN/VAL_ONLY +`vartex_real`
   （真机全链常驻回归）。
3. **测试件**：`frag_mkvec.asm`/`frag_mkvec_neg.asm`/`fl_mkvec_i8.asm`/
   `fl_mkvec_wr.asm`。

---

## 5. 验证（2026-10-14 全跑）

1. `python3 run_matrix.py` → **114 PASS / 0 FAIL**（基线 109 零破坏 + 新增 5）；
2. `bash run_e2e.sh` → **10/10** 可译 + val PASS（零回退）；
3. `mkvec` 逐像素失败 **0**（7812 px），`mkvec_neg` 对照 exit 1（像素逐一报真值）；
4. `vartex_real.bin` 翻译 exit 0 + spirv-val frag/vert 双 PASS（VAL_ONLY 常驻）；
5. `fl_mkvec_i8`（MKVEC.v2i8）/`fl_mkvec_wr`（窄写）双 exit 2。

---

## 6. 遗留

1. **`MKVEC.v2i8`（8 位四源构造）未建模**：真机 160 条子集中无此形态，暂无
   需求面；语义已知（`A | B<<8 | CD<<16`，ISA.xml:3212 + fold 双源），按需补；
2. **FMA.v2f16/v2f16 族的 swizzle 全面放开后**，`V2F32_TO_V2F16`/`FADD.v2f16`
   等同族指令（源 16 位 swizzle）若后续真机样本出现，同款 `SWIZ16_HALFS` 语义
   可直接复用；现建模面仍限 FMA.v2f16；
3. 承接 tex-subset 遗留 #2 真纹理数据面、#5 UV 近似复核，及优先级清单
   第 2 项 B2 回执段闭环——像素回显验收的下一卡点。

---

## 附：换机续做入口

```bash
cd devices/xiaomi/gold-15/out/tmp/p2
python3 run_matrix.py   # 基线 114/114 全绿
bash run_e2e.sh         # 基线 10/10 纯绿
```
