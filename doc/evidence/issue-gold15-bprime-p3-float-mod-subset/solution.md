# issue-gold15-bprime-p3-float-mod-subset —— B′ P3：浮点源修饰（neg/abs）子集

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-sfu-subset](../issue-gold15-bprime-p3-sfu-subset/solution.md)（及其 #2 遗留）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

上批（SFU 三连）遗留 #2 明确：真机浮点指令**大量带源修饰**，而翻译器对任何源修饰一律
`fail-loud` 拒绝。对真机 10 shader 做「修饰分布统计」得：

| 指令 | 带修饰条数 / 总数 | 具体修饰（计数） |
|---|---|---|
| `FMA.f32` | 23 / 36 | `C.neg` ×19、`A.abs` ×2、`B.swizzle(h0/h1)` ×8 |
| `FADD.f32` | 5 / 7 | `B.neg` ×3、`A.abs` ×2、`B.abs` ×2 |

即 **`neg`/`abs` 是绝对主力**（`swizzle` 次之，属 widen 语义）。故本批：
**建模浮点源的 `neg`（`OpFNegate`）与 `abs`（GLSL.std.450 `FAbs`）**——自包含、无需内存/
staging/输入 ABI，直接解锁真机大量 FMA/FADD。

> 另：本批前对「下一批主攻 `LD_ATTR_IMM`+`LD_VAR_BUF_IMM`（属性/插值）」做了可行性核实，
> 结论见 §6 遗留 #3（**需 staging 寄存器 + 输入 ABI 建模，属架构级新增，单独立项**）。

## 2. 根因（决定性发现）

1. **修饰位可由 ISA 元数据定位**（`valhall.py` 的 `Source.offset/mask`）：
   `neg = 34 + 2*(2-i)`、`abs = 35 + 2*(2-i)`（i 为源序 A=0/B=1/C=2）。故 `decode_word`
   可直接按 `ins.srcs[i].offset` 提取每位，无需硬编码——比 FMA/SFU 卡的掩码方案更稳。
2. **SPIR-V 映射**：`neg → OpFNegate`（=127）；`abs → OpExtInst GLSL.std.450 FAbs`（=4，
   编号同样以头文件为准）。次序约定：**`abs` 先于 `neg`**（两者同置即 `-|x|`）。
3. **fail-loud 需收窄而非取消**：把 `src_mod_mask()` 改为「**未建模**修饰掩码」，把已建模的
   `neg/abs` 排除；`swizzle/widen/combine/lane` 等仍拒绝，继续防错译。
4. **验证数值须造出「负源」**：Valhall LUT **无 `-1.0`**（`0xBF800000` 不在 immediates），
   故 `abs` 用例改用 LUT 已有的负常量 `0xC0B0A090`（=-5.5195999）以产生可区分结果。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：`MOV.i32 0xBF800000` 汇编报
  `Unexpected immediate value`（LUT 无 -1.0）；`abs` 若用正数则与「无 abs」同值而失去区分度。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - 解码打印三源修饰字（FMA/FADD/FMAX）；
  - `spirv-val --target-env vulkan1.1` frag/vert **双 PASS**；
  - `spirv-dis` 确认 `OpFNegate`/`FAbs` 按位生成；
  - **逐像素比对：256×256，三角形内 7812 px 全为 (23,255,81,255)，失败 0，exit 0**；
  - **负向对照**（故意错期望）→ 内部像素逐一报 `23,255,81,255`；
  - **回归**：`frag_sfu`(180,5,177,255) / `frag_fma`(28) / P2 `frag_flat`(255) 均失败 0；
  - **fail-loud 仍有效**：真机带 `B.swizzle` 的 FMA（如 `0xb2c20404c08a00`）仍被拦截。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. 常量：`OpFNegate=127`、`GLSL_FABS=4`（编号查 NDK `spirv/1.0/GLSL.std.450.h` 确认）。
2. `decode_word`：为每个源 `Operand` 附加 `neg/abs/swizzle` 位（按 `ins.srcs[i].offset`）。
3. `src_mod_mask()` 语义收窄为「未建模修饰掩码」（`MODELED_SRC_MODS = ("neg","abs")`）。
4. `FragBuilder.mod_val(opnd, ty)`：取 SSA 后按 `abs → neg` 次序施加修饰；
   `emit_fadd/fma/rsq/fmax/frcp` 的源一律改用 `mod_val`。
5. `main()`：fail-loud 分支文案不变（现在仅拦未建模修饰）。

**`out/tmp/p2/frag_mod.asm`（新建，7 指令/56B）**：三通道各探一种修饰组合
（`FMA.A.neg` / `FADD.A.abs` / `FMAX.A.neg`），规避 LUT 无 `-1.0` 的限制。

产物指纹：`frag_mod.bin` 56B md5 `bb6a749c267dbbd03513ec57769eda6c`；
`frag_mod.spv` 488B md5 `804a55e3b3acf417e034ceb24db4261d`；
`vert_mod.spv` 392B md5 `09c8f5fa8ecc6d1dce3c7bcc8f724142`（同模板）。

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| `va2spirv.py` 解码 | `FMA dest=r0 srcs=[reg:4·neg, reg:4, reg:4]`；`FADD dest=r1 srcs=[lut:-5.5196·abs, reg:4]`；`FMAX dest=r2 srcs=[reg:6·neg, lut:0.31831]` |
| `spirv-val` vulkan1.1 | frag **PASS** / vert **PASS** |
| `spirv-dis` | `OpFNegate`（A.neg）、`OpExtInst … FAbs`（A.abs）、`OpFNegate`（FMAX A.neg）逐位生成 |
| **MoltenVK 逐像素比对** | 三角形内 **7812 px 全为 (23,255,81,255)**、外 0 px、**失败 0 / exit 0** |
| 负向对照（期望 28/0/255/255） | 内部像素逐一报 `23,255,81,255` → 证明三通道修饰确已生效 |
| 回归 `frag_sfu`/`frag_fma`/`frag_flat` | 均失败 0 → 向后兼容 |
| fail-loud 回归 | 真机带 `B.swizzle` 的 FMA 仍被拒绝（未建模） |

结论：**浮点源 `neg`/`abs` 修饰解码与 SPIR-V 翻译语义正确**，在 Metal 上逐像素可证。

## 6. 遗留

1. **`swizzle`/`widen` 未建模**：真机 FMA 的 `B.swizzle`(h0/h1) ×8 仍被 fail-loud 拒绝；
   对 f32 源，2 位 swizzle 域实为 `widen` 码（`none/h0/h1/reserved…`），需先厘清其语义
   （可能指 16 位半向量选择）再放开。
2. **整数/其他修饰未建模**：真机 `CLPER.i32`/`LSHIFT_OR.i32`/`IMUL.i32`/`SHADDX.u64` 等
   亦带修饰，随对应指令扩充时一并处理。
3. **属性/插值通路（下一批主攻，已核实为架构级）**：
   - `LD_ATTR_IMM`：ISA 描述为**顶点属性加载**，`sr write=true`（写 staging）、源为
     Vertex ID/Instance ID + 属性表；属顶点路径，当前 `vert` 是宿主硬编码模板。
   - `LD_VAR_BUF_IMM.f32/.f16`：**片元 varying 插值**，同样 `sr write=true`，
     另有 `vector_size(28)/staging_register_count(33)/source_format(24)/sample_mode(38)/
     update_mode(36)/slot(30)` + `index(16,8)`。
   - **两者都无普通 dest（结果落在 bits40-45 的 staging 基址 + 连续寄存器）**，现有
     `decode_word` 完全不提取 staging，`reg_ptr`(rN^) 读取亦无 SSA 映射。
   - 即：需新增 **staging 寄存器模型 + 片元 varying 输入 ABI（Input/Location + OpLoad）**，
     并扩展 harness 的顶点侧提供 varying。**建议单独立项**，不宜并入本批。
4. **下一批候选**：可先做「片元 varying 输入 ABI + `LD_VAR_BUF_IMM.f32` 最小切片」
   （vecsize=v4、source_format=src_f32、sample=center、update=store、index=0 对 Location 0），
   以合成 shader + harness 顶点侧常量 varying 验证；随后再扩 slot/vecsize 组合与 `LD_ATTR_IMM`。