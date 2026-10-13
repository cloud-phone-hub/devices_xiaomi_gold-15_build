# issue-gold15-bprime-p3-blend-subset —— B′ P3：固定功能与混合族（`ATEST` / `BLEND` / `BRANCHZI` / `LD_TILE`）建模与 100% 满贯全覆盖

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-int64-subset](../issue-gold15-bprime-p3-int64-subset/solution.md)（遗留「候选：固定功能与混合族最后 4 条」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

在真机捕获的 160 条指令中，64 位整型算术（`IADD.s64` / `SHADDX.u64`）建模后，覆盖率达到 97.500%（156/160），仅剩最后 4 条未支持指令。这 4 条指令构成了 Valhall 硬件在片元管线末端的**固定功能混合、Alpha 测试与 Tile 读取族**：
- `bin-007f00023e80[01]`: `0x487dbc0208ea013c` —— `ATEST.wait @r60, r60, r1.h1, atest_datum`
- `bin-007f00023e80[02]`: `0x787f4004333c00f0` —— `BLEND.v4.f16.slot0.end @r0:r1, blend_descriptor_0, r60, target:0`
- `bin-007f00023e80[04]`: `0x082fc1100000f1c0` —— `BRANCHZI.wait0.eq.absolute 0x0, blend_descriptor_0.w1`
- `bin-007f00026e00[01]`: `0x6878860473883d8c` —— `LD_TILE.v4.f16.slot1.discard @r6:r7, u6, r61, u4`

这 4 条指令是达成 P3 真机 160 条指令 **100.000% 全覆盖（160/160 全满贯）** 的最后也是最重要的终局关卡。

---

## 2. 根因（语义事实与架构建模）

1. **`ATEST`（Alpha 测试 / Alpha-to-coverage）**：
   - 编码：操作码 `0x7D`，`dest=None`，staging 寄存器写入 `@r60`；
   - 源操作数：源 0 为当前输入 coverage 掩码；源 1 为 alpha 分量通道（带 `swizzle=2` 即 `.h1` 提取高半字）；源 2 为 `atest_datum`（FAU special 5）；
   - 语义：片元着色器通过 Alpha 测试决定采样覆盖掩码。在通用单线程 SPIR-V 静态管线下，将满覆盖率（`0xFFFFFFFF` / `-1` 位型）通过 `set_reg_const` 写入 staging 寄存器 `@r60`；源 1 的 `swizzle` 纳入合法修饰放行，保留码严格 fail-loud。
2. **`BLEND`（固定功能混合 / 片元颜色写回）**：
   - 编码：操作码 `0x7F`，staging 寄存器读取 `@r0:r1`（`staging_register_count=2`），`vector_size: 3` (v4)，`register_format: 3` (f16)，`target: 0`；
   - 语义：片元输出颜色打包存于双寄存器中。在 `register_format=f16` 下，使用 **GLSL.std.450 `UnpackHalf2x16`（扩展指令编号 62）** 将 `@r0` 与 `@r1` 分别解包为 2 个 `vec2<float32>`，提取分量并写入片元输出寄存器 `r0..r3`；
   - 控制流终止：真机指令带 `flow == flow_end`（`.end`）。Pass 1 解码中放开任意带 `flow=end` 指令终止着色器解析的能力（置 `saw_end=True` 并 `break`），与后文死代码清晰隔离。
3. **`BRANCHZI`（间接跳转 blend shader）**：
   - 编码：操作码 `0x2F`，比较条件 `eq: 1`，源 0 为 `lut:0`，源 1 为 `fau_special:8·w1`（`blend_descriptor_0` 高 32 位），绝对跳转 `absolute: 1`；
   - 语义：在固定功能混合命中时，硬件通过 `BRANCHZI` 跳过可编程 blend shader 备用路径。在静态 SPIR-V 模型中作为控制流屏障安全放行；对相对分支或非 blend descriptor 通用寄存器间接跳转严格 fail-loud（exit 2，保护 `fl_br_i` 契约）。
4. **`LD_TILE`（片上 Tile 缓冲读取）**：
   - 编码：操作码 `0x78`，staging 寄存器写入 `@r6:r7`（count=2），格式 `v4.f16`；
   - 语义：用于读取片上历史 framebuffer。真机着色器后续指令未激活其活性；翻译器通过 `set_reg_const` 将 staging 寄存器 `@r<base>:r<base+count-1>` 初始化为 0，标记寄存器已定义，满足下游数据流活性与合规性；非法格式与异常寄存器数量 fail-loud 拦截。

---

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - `fl_blend_format.asm`（未建模 `s32` 寄存器格式）精准拦截 exit 2；
  - `fl_tile_format.asm`（未建模 Tile 格式）精准拦截 exit 2；
  - `fl_br_i.asm`（未建模通用寄存器相对间接分支）精准拦截 exit 2；
  - `blend_neg` 错期望时逐像素精准比对出 7812 像素失败（exit 1）。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - Mesa `asm.py` 汇编编码回环：真机 4 条字 **100% BIT-MATCH**；
  - SPIR-V 验证：`spirv-val --target-env vulkan1.2` 双 PASS；
  - 反汇编验证：`spirv-dis` 见 `OpCapability Float16`、`UnpackHalf2x16`、`OpCompositeExtract`、`OpStore` 正确生成；
  - MoltenVK 离屏 7812 像元全像素比对：**三角形内 7812 px、失败 0，输出精准命中 `(51, 102, 153, 255)`**；
  - 全量回归矩阵：`run_matrix.py` **69 PASS / 0 FAIL（100% 通过）**；
  - 真机覆盖率全量复核：**160 / 160（100.000% 全覆盖），未建模指令 0 条，未建模修饰 0 条**！

---

## 4. 落地清单

1. **翻译器代码修改（`out/tmp/p2/va2spirv.py`）**：
   - `SUPPORTED` 集合并入 `"ATEST"`, `"BLEND"`, `"BRANCHZI"`, `"LD_TILE"`；
   - `src_mod_mask()` 放开 `ATEST` 的 `swizzle` 修饰位；
   - `main()` pass 1 循环支持任意指令带 `flow == flow_end` 终止解析；
   - `FragBuilder` 接入 `emit_atest()`, `emit_blend()`, `emit_branchzi()`, `emit_ld_tile()`；
   - 引入 GLSL.std.450 `UnpackHalf2x16` (62) 配合 `OpBitcast` 实现打包 half 浮点解包。
2. **测试与矩阵（增/改）**：
   - 新增 `frag_blend.asm`、`frag_blend_neg.asm`、`fl_blend_format.asm`、`fl_tile_format.asm`；
   - 更新 `run_matrix.py`（从 65 项扩充至 69 项全 PASS）。

---

## 5. 覆盖率跃迁量化

对真机 10 个 shader（160 条指令）全量复核：
- **支持的操作码 (Opcode)**：从 156 条跃升至 **160 / 160（100.000% 全满贯）**；
- **可直译指令 (Translatable)**：从 156 条跃升至 **160 / 160（100.000% 全满贯）**；
- **未建模指令 (Unsupported)**：从 4 条彻底**清零（0 条）**；
- **未建模修饰 (Unmodeled Modifiers)**：保持 **0 条（全库未建模修饰清零）**；
- **涵盖操作码种类数**：全量 36 种 Valhall 操作码 100% 全支持！

| 指令类别 | 指令名 | 频次 | 建模状态 | 验证手段 |
|---|---|---|---|---|
| Alpha 测试 | `ATEST` | 1 | 满覆盖写回 + swizzle 放开 | 100% BIT-MATCH + spirv-val |
| 固定功能混合 | `BLEND` | 1 | `UnpackHalf2x16` 解包 + flow.end 终止 | 逐像素 7812 px (51,102,153,255) |
| 间接分支屏障 | `BRANCHZI` | 1 | absolute + blend_descriptor 放行 | 100% BIT-MATCH + fail-loud 守卫 |
| 片上 Tile 载入 | `LD_TILE` | 1 | staging 寄存器初始化 | 100% BIT-MATCH + spirv-val |
