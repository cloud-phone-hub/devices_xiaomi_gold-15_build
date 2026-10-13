# issue-gold15-bprime-p3-widen-subset —— B′ P3：widen 源修饰（h0/h1 半字选择 + f16→f32 加宽）

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-float-mod-subset](../issue-gold15-bprime-p3-float-mod-subset/solution.md)（遗留 #1）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

上批（float-mod）遗留 #1：真机 FMA 的 `B.swizzle`(h0/h1) ×8 仍被 fail-loud 拦截——
`neg/abs` 建模后，**widen（半字选择）是真机浮点指令最常见的剩余未建模修饰**。
本批建模 f32 源的 `h0/h1`：取 32 位位型的一半解释为 f16 并加宽为 f32。

## 2. 根因（决定性发现）

1. **字段定位（ISA 元数据实证）**：f32 源的 h0/h1 落在 **`swizzle` 字段**（2 位，
   FMA src0@28/src1@26/src2@24），值域 = `Widen` 枚举（none=0/h0=1/h1=2/reserved=3+）；
   与整数指令的 `widen` 字段（字节选择 .b00，@36/@26）**是两个不同字段**——
   整数侧继续 fail-loud 不受影响。
2. **语义**：h0/h1 = 取寄存器 32 位的低/高 16 位，按 **f16 解释并加宽 f32**
   （widen 命名即此意；真机 mediump 双 f16 打包场景）。
3. **SPIR-V 映射**：常量源翻译期折叠（struct `<e` 半精度读写）；寄存器源生成
   `OpShiftRightLogical+OpBitwiseAnd`(取半字) → `OpUConvert u16` → `OpBitcast f16`
   → `OpFConvert f32`，需 **Float16(9)/Int16(22)** 能力（spike 实证：
   spirv-val vulkan1.1 全过、MoltenVK 0.5→128 精确出图）。
4. **施加次序**：widen → abs → neg（widen 是操作数重解释，先于修饰）。
5. **fail-loud 收窄而非取消**：`src_mod_mask()` 排除 WIDEN_MODELED
   （FADD/FMA/FMAX/FRSQ/FRCP/FCMP_OR 的 .f32）的 `swizzle` 字段；
   **reserved 码（3）由 mod_val 显式拦截**（语义未定义，防错译）。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：编码回环首试 dest 位不符（漏看解码
  dest=r56/r59）；valhall.py 同进程二次解析 ISA 撞全局断言（"appeared 2 times"）；
  汇编器无法构造 reserved 之外的坏值但 `.reserved` 可编码（枚举首 reserved 索引=3）。
- 通过面（[log-pass.txt](log-pass.txt)）：37/37 回归矩阵；spirv-dis 通路
  （`OpUConvert %ushort → OpBitcast %half → OpFConvert %float`）；负向对照
  逐像素实证 (128,255,128,255)；真机 2 条 `FMA+B.h1` 字 asm.py 回环逐位命中。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. 常量：`OpUConvert=113`、`OpFConvert=115`、`CAP_F16=9`、`CAP_I16=22`；
2. `WIDEN_MODELED` 集 + `src_mod_mask()` 排除 `swizzle` 字段；
3. `mod_val()`：swizzle≠0 时走 `_widen_half()`（sw>2 fail-loud），次序 widen→abs→neg；
4. `_widen_half()`：lut 翻译期折叠 / reg 生成 f16 通路（t_u16/t_f16 惰性创建）；
5. `build()`：`need_f16` 时声明 Float16/Int16 能力。

**`out/tmp/p2/frag_widen.asm`（新建，6 指令）**：三通道覆盖三种路径——
reg 运行时 h0/h1（r0/r1）、LUT 常量折叠（r2 的 A=`0x3F800000.h0`=f16 0.0）、
混用（r2 的 C=`r4.h0`）；期望 (128,255,128,255)。
**`out/tmp/p2/fl_widen_res.asm`（新建）**：`r4.reserved`（swizzle=3）→ 必须 exit 2。
**`out/tmp/p2/run_matrix.py`（新建）**：全量回归矩阵固化（汇编→翻译→spirv-val→
harness 逐像素；fl_* 期望 fail-loud；ialu_neg/cvt_neg 期望对照失败）。

## 5. 验证（2026-10-13，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| 回归矩阵 run_matrix.py | **37 PASS / 0 FAIL**（25 渲染正例 + 2 错误对照 + 10 fail-loud） |
| frag_widen 逐像素 | 三角形内 7812 px 全 (128,255,128,255)、失败 0、exit 0 |
| 负向对照（错期望） | 逐像素报 `128,255,128,255`（证 widen 三路径确已生效） |
| spirv-dis | `OpCapability Float16/Int16` + `OpUConvert %ushort → OpBitcast %half → OpFConvert %float` 按位生成 |
| fl_widen_res | 翻译 exit 2（reserved 码拦截生效） |
| 真机编码回环 | `0x00b2f80408c0da0b`/`0x00b2fb0408c0da0c`（`FMA.f32 r56/r59, r11/r12, 0x38000000.h1, 0x0.neg`）**2/2 逐位命中** |

**真机覆盖率（160 字全量重算，p3-decode-be.txt）**：opcode 覆盖 **103/160 不变**
（本批不新增 opcode，是修饰建模）；**可直译 69→71（+2）**；未建模修饰 15→13；
带 swizzle 真机字共 9 条：2 条（lut.h1）现已可直译，**7 条仍被 FAU 源拦**
（FMA×6 + ATEST×1，属 FAU 遗留）。

## 6. 遗留

1. **FAU 源（uniform/特殊寄存器）未建模**：真机带 swizzle 的 FMA 大头（6 条）
   B 源为 `fau:N·swzN`——FAU 建模后 widen 即自动生效（mod_val 已就位）；
2. **整数字节/半字选择**（`widen` 字段 .b00/.h0 系）未建模：IADD/ICMP 等的
   src0@36/src1@26 仍 fail-loud（本批只动 f32 源 `swizzle` 字段）；
3. reserved 码防线上界为汇编可构造面（swizzle=3 已覆盖；4–7 无法经 asm.py 构造，
   mod_val 的 `sw>2` 拦截由代码审查保障）；
4. 承接前批：`LD_VAR_BUF_IMM.f16`（src_flat16）、update_mode retrieve/clobber、
   slot 语义、非对齐 index、内存族（33 条）、`LD_ATTR_IMM`。
