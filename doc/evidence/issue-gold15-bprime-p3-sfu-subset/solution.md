# issue-gold15-bprime-p3-sfu-subset —— B′ P3：Valhall 指令子集扩充之 SFU 浮点三连（FRSQ/FRCP/FMAX）

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-fma-subset](../issue-gold15-bprime-p3-fma-subset/solution.md) 遗留 #4
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

`FMA.f32` 扩充后（见 FMA 卡点），`va2spirv.py` 仍只有 4 类指令。按 P3 真机 10 shader 的
opcode 频次，FMA 之后**可直接映射到 GLSL.std.450、且自包含（不涉内存/varying）**的高频浮点指令是：
`FRSQ.f32`(4)、`FMAX.f32`(1)、`FRCP.f32`(1)。这三条是光照/法线归一化/最大值选择的常见语义
（`FRSQ` 在样本中与 FMA 密集同现，出现在 2 个 Fragment 着色器里）。本卡点即：
**按「扩一条验一条（同 harness 逐像素比对）」扩这三条**，并证明语义正确而非仅「能出 SPIR-V」。
（停留于 `LD_PKA.i64`/`LD_ATTR_IMM`/`LD_VAR_BUF_IMM` 等需属性/纹理通路建模的指令，留下一批。）

## 2. 根因（决定性发现）

1. **编码（Mesa ISA.xml 权威）**：
   - `FRSQ.f32`/`FRCP.f32`：opcode2 域在 bits[16:20]（`0x2`/`0x0`），与 base `0x9C`(bits48-56)；
     属 **SFU 组，1 个源**，源带 `swizzle/absneg`。
   - `FMAX.f32`：base `0xA4`、opcode2=`0x3`（mask 0xF）；属 FMIN/FMAX 组，**2 个源 A/B**，
     带 `swizzle/absneg`，且组另有 **`clamp` 修饰（bits32-33）**。
2. **GLSL.std.450 编号必须以头文件为准（关键陷阱）**：权威表
   `GLSLstd450InverseSqrt = 32`、`GLSLstd450FMax = 40`、`GLSLstd450Fma = 50`
   （来源：NDK `spirv-headers/include/spirv/1.0/GLSL.std.450.h`）。凭记忆的编号表**全错**
   （记忆里 `Fract=8`，实为 10；会连带把 Fma/FMax 全算错）。
3. **`FRCP.f32` 无 GLSL.std.450 等价指令**：扩展集里没有 `Recip`。须用 **`OpFDiv`（=136，注意不是
   等差推出来的 135）** 计算 `1.0 / x`。
4. **源修饰位可从 ISA 元数据通用推导**，不必硬编码：
   `neg = 34 + 2*(2-i)`、`abs = 35 + 2*(2-i)`、`swizzle = 24 + 2*(2-i)`（i 为源序 A=0/B=1/C=2），
   `valhall.py` 的 `Source.offset/mask` 已带这些位。故把 FMA 的硬编码掩码升级为
   `src_mod_mask(ins)` 通用检查（fail-loud，防错译），同时覆盖新三条。
5. **验证数值须避开 UNORM8 舍入中点**：Valhall LUT 无 0.5，`FRCP(2.0)=0.5→127.5` 恰在舍入中点
   （不可靠）。改用：

   | 通道 | 表达式 | 真值 | ×255 | 期望字节 | 误译对照 |
   |---|---|---|---|---|---|
   | r0 | `FRSQ(2.0)` | 0.7071068 | 180.31 | **180** | sqrt/原值→255 |
   | r1 | `FRCP(50.0)` | 0.02 | 5.1 | **5** | 原值→255 |
   | r2 | `FMAX(0.1f, ln2)` | 0.6931472 | 176.75 | **177** | FMIN→26 |

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：编号凭记忆出错（须以头文件为准）；`OpFDiv` 等差推断
  会得 135（真值 136）；`FRCP` 误以为有 GLSL 等价；LUT 无 0.5 致 `FRCP(2.0)` 落在舍入中点。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - 解码：`FRSQ.f32 dest=r0 srcs=[reg:4]` / `FRCP.f32 dest=r1 srcs=[lut:1112014848=50.0]` /
    `FMAX.f32 dest=r2 srcs=[lut:0.1f, lut:ln2]`；
  - `spirv-val --target-env vulkan1.1` frag/vert **双 PASS**；
  - `spirv-dis` 确认 `%10 = OpExtInst … InverseSqrt %float_2`、
    `%13 = OpFDiv %float %float_1 %float_50`、
    `%16 = OpExtInst … FMax %float_0_100000001 %float_0_693147182`；
  - **逐像素比对：256×256，三角形内 7812 px 全为 (180,5,177,255)，失败 0，exit 0**；
  - **负向对照**：故意用错期望 255 → 内部像素逐一报 `180,5,177,255`，证明像素确为该值；
  - **回归**：`frag_fma`（期望 28）与 P2 `frag_flat`（期望 255）均失败 0 → 改动向后兼容。

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - 常量：新增 `OpFDiv=136`、`GLSL_RSQ=32`/`GLSL_FMAX=40`（`GLSL_FMA=50` 保留）；
   - `SUPPORTED` 增 `FRSQ.f32`/`FRCP.f32`/`FMAX.f32`；
   - **`src_mod_mask(ins)`**：由 ISA 元数据推导源修饰位掩码，取代 FMA 硬编码的 `FMA_FLAG_MASK`；
     所有非 MOV 指令在翻译前统一 `fail-loud` 拒绝带未建模修饰的字；
   - `FragBuilder`：新增 `emit_rsq`（`OpExtInst InverseSqrt`）、`emit_fmax`（`OpExtInst FMax`）、
     `emit_frcp`（`OpFDiv 1.0/x`）；
   - `main()`：三条分派；`FMAX` 额外拒绝 `clamp` 修饰（bits32-33）。
2. **`out/tmp/p2/frag_sfu.asm`（新建，6 指令/48B）**：
   `MOV r4←2.0` + `FRSQ r0←r4` + `FRCP r1←LUT(50.0)` + `FMAX r2←LUT(0.1f),LUT(ln2)`
   + `MOV r3←1.0` + `NOP.end`（覆盖 寄存器 / LUT / 双 LUT 三种源形态）。
3. `harness.c` 复用 FMA 卡点已加的「可选期望 RGBA」参数，无改动。

产物指纹：`frag_sfu.bin` 48B md5 `a8d5a265ba6da92e9f5b49122da2eae1`；
`frag_sfu.spv` 440B md5 `77f4995d8039228a76a7703610a6bc71`；
`vert_sfu.spv` 392B md5 `09c8f5fa8ecc6d1dce3c7bcc8f724142`（与 vert_fma 同模板）。

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| Mesa `asm.py` 汇编 | `frag_sfu.bin` 48B |
| `va2spirv.py` 解码 | `FRSQ dest=r0 srcs=[reg:4]`；`FRCP dest=r1 srcs=[lut:1112014848]`；`FMAX dest=r2 srcs=[lut:1036831949, lut:1060205080]` |
| `spirv-val` vulkan1.1 | frag **PASS** / vert **PASS** |
| `spirv-dis` | `InverseSqrt %float_2` / `OpFDiv %float %float_1 %float_50` / `FMax %float_0_100000001 %float_0_693147182` |
| **MoltenVK 逐像素比对** | 三角形内 **7812 px 全为 (180,5,177,255)**、外 0 px、**失败 0 / exit 0** |
| 负向对照（期望 255） | 内部像素逐一报 `180,5,177,255` → 证明结果确为该值（非误译的 255/255/26） |
| 回归 `frag_fma`(28) / P2 `frag_flat`(255) | 均失败 0 → 向后兼容 |

结论：**`FRSQ.f32`/`FRCP.f32`/`FMAX.f32` 解码与 SPIR-V 翻译语义正确**，在 Metal 上逐像素可证。

## 6. 遗留

1. **`FRCP` 用 `OpFDiv(1.0,x)` 而非专用倒数**：语义等价（真机 `FRCP.f32` 为全精度倒数，非
   `FRCP_APPROX`），但指令数略增；后续若遇到 `FRCP_APPROX.f32`（近似倒数，opcode2=0xE）需另行建模。
2. **仍拒绝源修饰（neg/abs/swizzle）与 FMAX 的 clamp**：真机样本中 `FRSQ` 源常见 `reg_ptr`(rN^)
   形态，本用例只覆盖 寄存器/LUT；带修饰的真机字会被 `fail-loud` 拒绝，待逐位解码修饰语义后放开。
3. **FAU（uniform）源仍未支持**（同 FMA 卡点遗留）。
4. **下一批指令（按 P3 样本频次）**：`LD_PKA.i64`(11)/`LD_ATTR_IMM`(10)/`LOAD.i64`(5)/
   `IADD_IMM.i32`(5) 等。其中 **`LD_ATTR_IMM` + varying 源 `LD_VAR_BUF_IMM`** 是属性/插值通路
   的关键，但需先建「属性/varying 输入」建模（配合 P2 约定 ABI 扩展），建议作为下一批主攻方向。