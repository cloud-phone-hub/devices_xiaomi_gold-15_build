# issue-gold15-bprime-p3-varying-width-subset —— B′ P3：LD_VAR_BUF_IMM.f32 插值族放开（宽度 v2/v3 + 采样模式 + 宽度感知 index→Location）

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-varying-subset](../issue-gold15-bprime-p3-varying-subset/solution.md)（遗留 #1/#2/#4）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

上一批（[varying-subset](../issue-gold15-bprime-p3-varying-subset/solution.md)）把片元 varying 输入通路打通，
但**只覆盖 `LD_VAR_BUF_IMM.f32` 的单一形态**：`v4 + src_f32 + center + store + index=0`
（Location 0 的 16B 对齐 vec4）。其余组合（v2/v3 宽度、centroid/sample 采样模式、
非零 index）一律 fail-loud。

而 P3 真机 10 样本中恰恰有**一条** `LD_VAR_BUF_IMM.f32`（[shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md) 的
`bin-007f00037880` 第 0 条，字 `0x005c82841208003d`），其形态为
**`v2 + src_f32 + sample + store + index=8`** —— 即「插值 f32、vec2 宽、按采样位置插值、
字节偏移 8（→ Location 1）」。**上批形态覆盖不到这条真机指令**，故本批放开插值族：

- **向量宽度 v2/v3**（对齐 `vector_size` 枚举 none/v2/v3/v4）；
- **采样模式 center/centroid/sample**（`sample_mode` 枚举，映射到 SPIR-V `Centroid`/`Sample` 装饰）；
- **宽度感知的 index→Location**（`Location = index / (comps*4)`，抽掉「固定 /16」假设）；
- 顶点模板**镜像片元的 varying 接口**（按 (loc, comps) 逐个输出同型常量 varying）。

`source_format` 仍限定 `src_f32`（插值 32 位浮点）；`src_flat32/src_flat16`（flat 无插值）
留作后置（见 §6）。

## 2. 根因（决定性发现）

1. **真机那条 f32 的语义（解码实证）**：`0x005c82841208003d` →
   `vector_size=1(v2)`、`source_format=2(src_f32)`、`sample_mode=2(sample)`、
   `update_mode=0(store)`、`staging_base=2`、`staging_register_count=2`、`index=8`。
   即**插值 f32、vec2、按 sample 位置、写 r2:r3、字节偏移 8**。

2. **宽度→分量数 & Location 折算**：`comps = vector_size + 1`（v2=2/v3=3/v4=4）；
   varying 按分量连续打包，`byte_offset = Location × comps × 4`，
   故 **`Location = index / (comps*4)`**（取代上批「index/16」的 v4 专用假设）。
   真机 index=8、comps=2 ⇒ `8/8 = 1` → **Location 1**。

3. **采样模式→SPIR-V 装饰（纠错关键）**：
   - `center(0)` → **无修饰**（SPIR-V 默认平滑插值）；
   - `centroid(1)` → `OpDecorate %v Centroid`（**编号 16**）；
   - `sample(2)` → `OpDecorate %v Sample`（**编号 17**），且**必须声明
     `OpCapability SampleRateShading`**（**能力编号 35**）。
   两个「编号坑」见 [log-fail.txt](log-fail.txt)：`Centroid/Sample` 曾被误写为 43/44
   （实为 InputAttachmentIndex/Alignment）；`SampleRateShading` 曾被误写为 33
   （实为 `CullDistance` —— SPIR-V 能力枚举在 16 处有跳号，导致整体偏移 2）。

4. **Mesa 权威语义核对**：`kraid/encode_v9.rs:2472-2501` 注明
   `OpLdVarBufFlat`「用 .f32.src_flat32 / .f16.src_flat16 载入 flats」，而**插值** varying
   （`OpLdVarBuf`）用 `src_f32/src_f16`。故 `src_f32` = 插值通路，本批只做它；
   真机 3 条 `LD_VAR_BUF_IMM.f16` 经解码为 `src_flat16(v4)` = **flat 16 位**，属另一通路，后置。

5. **顶点模板须镜像接口**：片元 `Location L` 的 `vec{comps}` 输入，要求顶点侧有**同型**
   `Output`（否则链路不匹配）。故 `build_vert_template(inputs)` 改为按 `inputs=[(loc,comps)]`
   生成同型常量 varying；常量表 `T=[0.2,0.4,0.6,1.0]`、Location L 第 j 分量取 `T[(j+L)%4]`
   （L=0 与上批完全一致，保证向后兼容）。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - 装饰/能力编号两次写错（`Centroid/Sample`=43/44 → 16/17；`SampleRateShading`=33 → 35），
    均由 `spirv-val`/`spirv-dis` 暴露（`CullDistance` 露馅、`expected more operands after 3 words`）；
  - `Sample` 装饰在**未声明 `SampleRateShading`** 时 `spirv-val` 直接 FAIL。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - 三例解码 `meta` 与真机字**逐位一致**（用 `asm.py` 反汇编真机字 → 完全命中）；
  - `spirv-val --target-env vulkan1.1` frag/vert **全 PASS**（v2/v3/real）；
  - `spirv-dis` 显示 v2 两个 `Input v2float`（Location 0/1，均 `Sample`）+ 能力 `SampleRateShading`；
    v3 `Input v3float`（Location 0，`Centroid`）；顶点侧镜像同型 `Output`；
  - **MoltenVK 逐像素比对**全部**失败 0 / exit 0**：
    `vary`=(51,102,153,255)、`vary_v2`=(51,102,102,153)、`vary_v3`=(51,102,153,255)、
    `vary_real`=(0,255,102,153)；
  - **负向对照**全部 `exit 1`（错期望被逐像素揭穿）；
  - **回归**：`frag_flat`/`frag_sfu`/`frag_fma`/`frag_mod` 均失败 0（顶点模板向后兼容）；
  - **fail-loud**：`src_flat32`、`index=4`(非对齐)、`vector_size=none` 三例均 **exit 2** 且错误信息明确。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. 常量区新增 `CAP_SAMPLE_RATE = 35`、`DEC_CENTROID, DEC_SAMPLE = 16, 17`；
2. `FragBuilder.__init__` 新增类型/指针：`t_v2/t_v3`（`OpTypeVector`）、
   `t_in2/t_in3`（Input 指针）、`need_sample_rate` 标志；
3. `input_var(loc, comps, sample)` 重写：按 `comps` 选 vec 类型与 Input 指针，
   `sample∈{1,2}` 时加 `Centroid`/`Sample` 装饰；`sample==2` 置 `need_sample_rate`；
4. `emit_varying(meta)` 重写：放开 `vector_size∈{1,2,3}`（v2/v3/v4）与
   `sample_mode∈{0,1,2}`（center/centroid/sample）；`Location = index/(comps*4)`
   （要求 index 按宽度对齐）；仍限 `source_format==2`；
5. `build()`：`need_sample_rate` 时追加 `OpCapability SampleRateShading`；
6. `build_vert_template(inputs)` 全量重写：按 `inputs=[(loc,comps)]` 镜像片元接口，
   逐 (loc,comps) 输出同型常量 varying（`T[(j+loc)%4]`，L=0 与旧版一致）；
7. 模块 docstring 更新本批覆盖范围。

**新增用例（`out/tmp/p2/`）**：
- `frag_vary_v2.asm`：两条 `LD_VAR_BUF_IMM.f32 ... v2.src_f32.sample.store`（index 0x0/0x8，
  staging r0:r1 / r2:r3）→ 期望 (51,102,102,153)；
- `frag_vary_v3.asm`：`v3.src_f32.centroid.store`（index 0x0）+ `MOV r3,1.0` → 期望 (51,102,153,255)；
- `frag_vary_real.asm`：**真机字 `0x005c82841208003d` 直接取材**（`v2.src_f32.sample.store @r2:r3, index:0x8`）
  + `MOV` 补 r0/r1 → 期望 (0,255,102,153)，用于验证真机那条的 index=8→Location 1 折算。

产物指纹（md5）：
| 文件 | 大小 | md5 |
|---|---|---|
| frag_vary.bin | 16B | 64b88d53ba10a85631639d00419c74f3 |
| frag_vary.spv | 500B | 181e77d6aa958c8225af33fd2096dc5a |
| frag_vary_v2.bin | 24B | 7f936d44ee7d76845ac3c1ca870e004c |
| frag_vary_v2.spv | 584B | 751f51dbbb90e8500a2e45e3aab0e0dd |
| frag_vary_v3.bin | 24B | 73260b6b6080fc4d2ec8a7af4a09da49 |
| frag_vary_v3.spv | 508B | 85adf8d5618c2b6143bb4d761316dc7c |
| frag_vary_real.bin | 32B | 7a158c2f5243292d744544857d5ad05f |
| frag_vary_real.spv | 512B | af6a9c19a6de32b653494ea0913c9d33 |

> 注：`frag_vary.spv` 由 436B→500B（`__init__` 现固定声明 v2/v3 类型），
> 上批指纹随之失效；对上批结论无影响（spirv-val/逐像素仍全过）。

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 用例 | 形态 | spirv-val (frag/vert) | 逐像素期望 | harness |
|---|---|---|---|---|
| `vary` | v4·src_f32·center·idx0（回归） | PASS/PASS | (51,102,153,255) | 失败 0 / exit 0 |
| `vary_v2` | 两个 v2·src_f32·**sample**·idx0/8 | PASS/PASS | (51,102,102,153) | 失败 0 / exit 0 |
| `vary_v3` | v3·src_f32·**centroid**·idx0 | PASS/PASS | (51,102,153,255) | 失败 0 / exit 0 |
| `vary_real` | **真机字** v2·src_f32·sample·idx8 | PASS/PASS | (0,255,102,153) | 失败 0 / exit 0 |
| 回归 flat/sfu/fma/mod | ALU 三连/常量 | — | 各自期望 | 失败 0 / exit 0 |

- **负向对照**：`vary_v2` 给 (51,102,153,255)/(102,51,153,102)、`vary_v3` 给 (153,102,51,255)、
  `vary_real` 给误映射值 (0,255,51,102) → 均**逐像素报错 / exit 1**（证明用例有区分度，
  且真机 index=8 **确实**落到 Location 1）；
- **fail-loud**：`src_flat32`→`仅支持 src_f32`、`index=4`→`index 须按向量宽度对齐(index=4, span=16)`、
  `vector_size=none`→`仅支持 v2/v3/v4`，三例均 **exit 2**。

结论：**`LD_VAR_BUF_IMM.f32` 插值族（v2/v3/v4 × center/centroid/sample × 宽度感知 Location）
端到端语义正确，真机那条 `0x005c82841208003d` 已可直接翻译并在 Metal 上逐像素可证**。

## 6. 遗留

1. **`source_format` 仅 src_f32**：`src_flat32`（flat 32 位）与 `src_flat16`（flat 16 位）
   仍 fail-loud。真机 3 条 `LD_VAR_BUF_IMM.f16` 即 `src_flat16(v4)` = **flat 16 位**，
   需 16 位打包/cratype 建模，属另一通路，后置。
2. **`update_mode` 仅 store**：`retrieve/clobber` 未建模。真机那条为 store，暂覆盖。
3. **`slot` 未参与语义**：本批忽略 slot（真机那条 slot0），多 slot 语义待查。
4. **非对齐 index**：要求 index 按 `comps*4` 对齐；同一 Location 内的分量偏移（如
   index=4 取 v2 的后半）待扩。
5. **`LD_ATTR_IMM`（顶点属性加载）单独立项**：属顶点路径（源为 Vertex/Instance ID + 属性表），
   ABI 与 varying 不同，混做会放大复杂度。
6. **swizzle/widen 源修饰**与 **FAU 源**仍待建模（承接上批遗留）；
   真机样本中的 `FRSQ.f32/FRCP.f32/FMAX.f32`（各 4/1/1 条）已在上批覆盖指令层，待用真机形态回归。