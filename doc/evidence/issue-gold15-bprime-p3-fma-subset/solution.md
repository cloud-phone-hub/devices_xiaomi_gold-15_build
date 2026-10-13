# issue-gold15-bprime-p3-fma-subset —— B′ P3：Valhall 指令子集扩充之 FMA.f32（融合乘加）

> 状态：**验收门通过（2026-10-12）**，承接 [issue-gold15-bprime-p2-va2spirv](../issue-gold15-bprime-p2-va2spirv/solution.md) 遗留 #2
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

P2 翻译器只覆盖 4 类指令（`MOV.i32`/`FADD.f32`/`NOP`），其遗留 #2 要求「扩一条验一条」。
P3 真机 10 个 shader 的 opcode 分布中 **`FMA.f32` 出现 36 次（全局第一）**，是 UI 渲染
路径最核心的算术语义（变换/插值/着色都依赖它）→ **`FMA.f32` 是子集扩充的第一优先项**。
本卡点即：让解码器/翻译器支持 `FMA.f32`，并用 P2 的 MoltenVK 逐像素比对证明语义正确
（而非仅「能翻译出 SPIR-V」）。

## 2. 根因（决定性发现）

1. **`FMA.f32` 编码（Mesa ISA.xml 权威）**：opcode=178（bits48-56）；**3 个 32 位浮点源**
   A/B/C，A 在 bits[7:0]（mode 2 位@6、value 6 位@0）、B 在 bits[15:8]、C 在 bits[23:16]，
   与现有 `decode_src` 逐字节取源一致；语义 = **A×B + C（融合乘加，单次舍入）**。
2. **SPIR-V 无原生 FMA 指令**：须走 **`OpExtInst` + GLSL.std.450 扩展集**，
   且 **`Fma` 的扩展指令编号 = 50**（非 10；10 是 `Fract`，极易写错）。
3. **`OpExtInstImport` 的逻辑布局位置**：SPIR-V 要求它在 **Capability/Extension 之后、
   `OpMemoryModel` 之前**（逻辑布局第 3 段）。放进 types 段会被 spirv-val 判
   `ExtInstImport is in an invalid layout section`。
4. **验证设计（关键）**：Valhall 小常量 LUT 无 0.5/0.25 等常用分数，只有 `0.0/1.0/2.0/0.1f/…`。
   要证明「真的算了 a×b+c」而非误译，需选一个 **fused 结果与常见误译都能区分、且量化字节
   远离 0.5 舍入边界** 的用例：`FMA(0.1f, 0.1f, 0.1f)` = `0.1099999994` → 字节 **28**；
   而误译成 `a+b+c`=0.3→**77**、丢 C（退化乘法）=0.01→**3**、丢乘法=0.1→**26**，四者互异。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：`OpExtInstImport` 放错段导致 spirv-val
  `invalid layout section`；GLSL.std.450 `Fma` 编号易错（10/50）。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - 解码：`0x00b2c0000004d104` → `FMA.f32 dest=r0 srcs=[r4, lut:0x3DCCCCCD, r4]`；
  - `spirv-val --target-env vulkan1.1` frag/vert **双 PASS**；
  - `spirv-dis` 确认 `%10 = OpExtInst %float %8 Fma %float_0_100000001 …`；
  - **逐像素比对：256×256，三角形内 7812 px 全为 (28,0,0,255)，失败 0，exit 0**；
  - **负向对照**：故意用错期望 255 → 每个内部像素报 `28,0,0,255`，证明像素确为 28；
  - **P2 flat 回归**：`harness frag.spv vert.spv`（默认期望 255）仍失败 0 → 改动向后兼容。

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - `SUPPORTED` 增 `FMA.f32`；新增 SPIR-V 常量 `OpExtInstImport=11`/`OpExtInst=12`/`GLSL_FMA=50`；
   - `FragBuilder`：新增 `set_glsl` id 与 **`OpExtInstImport "GLSL.std.450"`**（置于
     header 段 Capability 之后、MemoryModel 之前）；新增 `emit_fma()` 产出
     `OpExtInst %f32 %glsl Fma a b c`；
   - `main()`：FMA 分派；并**拒绝带 swizzle/neg/abs 的 FMA**（`FMA_FLAG_MASK` =
     bits[33:39]∪[23:29]）以**防错译**（MVP 未建模这些修饰）。
2. **`out/tmp/p2/frag_fma.asm`（新建，6 指令/48B）**：
   `MOV.i32 r4,0x3DCCCCCD` + `FMA.f32 r0, r4, 0x3DCCCCCD, r4`（覆盖 寄存器/LUT 两种源形态、
   且 C 非零）+ 三个常量通道 + `NOP.end`。
3. **`out/tmp/p2/harness.c`（改）**：新增可选 `expect_r/g/b/a` 4 参数
   （默认 `255,0,0,255` 保持既有用例不变）。

产物指纹：`frag_fma.bin` 48B md5 `e8fa05973848f1f08acac4f1286fab1b`；
`frag_fma.spv` 368B md5 `2a2f28c4e468732a0ff26414924a1a56`；
`vert_fma.spv` 392B md5 `09c8f5fa8ecc6d1dce3c7bcc8f724142`。

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| Mesa `asm.py` 汇编 | `frag_fma.bin` 48B（FMA 字 `0x00b2c0000004d104`） |
| `va2spirv.py` 解码 | `FMA.f32 dest=r0 srcs=[reg:4, lut:1036831949, reg:4]`（1036831949=0x3DCCCCCD=0.1f） |
| `spirv-val` vulkan1.1 | frag **PASS** / vert **PASS** |
| `spirv-dis` | `%10 = OpExtInst %float %8 Fma %float_0_100000001 %float_0_100000001 %float_0_100000001` |
| **MoltenVK 逐像素比对** | 三角形内 **7812 px 全为 (28,0,0,255)**、外 0 px、**失败 0 / exit 0** |
| 负向对照（期望 255） | 内部像素逐一报 `28,0,0,255` → 证明结果确为 28（非误译的 77/3/26） |
| P2 flat 回归 | `harness frag.spv vert.spv`（期望 255）失败 0 → 向后兼容 |

结论：**`FMA.f32`（A×B+C 融合乘加）解码与 SPIR-V 翻译语义正确**，在 Metal 上逐像素可证。

## 6. 遗留

1. **仍未建模 FMA 的 swizzle/neg/abs**：探针会**拒绝**（fail-loud）带这些修饰的 FMA；
   真机样本里若出现需逐位解码（A swz@28/neg@38/abs@39、B swz@26/neg@36/abs@37、
   C swz@24/neg@34/abs@35）。当前真机 36 个 FMA 中带修饰的比例待统计。
2. **FAU（uniform）源未支持**：真机 FMA 大量使用 `uN`（FAU 常量页）源，需解析 FAU
   page/uniform 缓冲；本用例只覆盖 寄存器 + LUT。
3. **测试用例依赖 LUT 数值**：Valhall LUT 缺 0.5/0.25 等，耦合了「可用常量集」；
   后续可改为「harness 侧用 `fmaf()` 现算期望」以摆脱对具体数值的硬编码。
4. **下一批指令（按 P3 样本频次）**：`NOP`(25)/`LD_PKA.i64`(11)/`LD_ATTR_IMM`(10)/
   `FADD.f32`(7 已支持)/`LOAD.i64`… 建议先做 `FRSQ.f32`/`FRCP.f32`/`FMAX.f32`（同族可直接套用
   GLSL.std.450）与 varying 源 `LD_VAR_BUF_IMM`。