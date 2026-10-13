# issue-gold15-bprime-p3-flat16-subset —— B′ P3：`LD_VAR_BUF_IMM.f16`（flat16 片元 varying）建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-int-mod-subset](../issue-gold15-bprime-p3-int-mod-subset/solution.md)（遗留 #1「候选：`LD_VAR_BUF_IMM.f16` 3 条」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

在真机捕获的 160 条指令中，整数字节选择与异步 flow 修饰收尾后，剩余未支持指令降至 14 条。其中 **`LD_VAR_BUF_IMM.f16`（3 条）与 `CLPER.i32`（3 条）并列为剩余指令中最多的核心算子**。
该指令分布于 P3 样本中的 3 个 Fragment / Composition 着色器：
- `bin-007f00023e80[00]`: `0x405d8014310000c0` —— `LD_VAR_BUF_IMM.f16.slot0.v4.src_flat16.center.retrieve.wait0126 @r0:r1, 0x0, index:0x0`；
- `bin-007f00026e00[02]`: `0x005d8414b10000c0` —— `LD_VAR_BUF_IMM.f16.slot2.v4.src_flat16.center.retrieve @r4:r5, 0x0, index:0x0`；
- `bin-007f00037880[01]`: `0x085d8014710000c0` —— `LD_VAR_BUF_IMM.f16.slot1.v4.src_flat16.center.retrieve.wait0 @r0:r1, 0x0, index:0x0`。

此前的 MVP 仅支持 `LD_VAR_BUF_IMM.f32`，阻断了 `flat16` varying 通路的直译。本批对 `LD_VAR_BUF_IMM.f16` 通路实施架构建模。

## 2. 根因（语义事实与架构建模）

1. **指令元数据与 Valhall ISA 格式解析**：
   - 操作码为 `0x5D`（bits 48..56），不同于 `f32` 变体的 `0x5C`；
   - 真机元数据特征：
     - `vector_size` 为 3（`v4`，4 个半精度浮点分量）；
     - `source_format` 为 1（`src_flat16` 平面着色半精度）；
     - `staging_register_count` 为 2（`@r0:r1` 或 `@r4:r5`，2 个 32 位寄存器容纳 4 个 16 位半精度浮点数）；
     - `update_mode` 为 1（`retrieve` 读回复用）；
     - `index` 为 0（映射 Location 0）；
     - `src0` 为立即数偏移 0（`0x0`）。
2. **寄存器打包压缩建模（PackHalf2x16）**：
   - 4 个半精度浮点分量 `(x, y, z, w)` 由 SPIR-V 阶段输入 `OpVariable %tptr %vid StorageClass_Input` 载入；
   - 经 GLSL.std.450 扩展指令 `PackHalf2x16`（操作码 58）分别将 `(x, y)` 与 `(z, w)` 压缩为 32 位整型字，依次写入 `base` 与 `base + 1` staging 寄存器；
   - 后续着色器可通过已支持的 `.h0`/`.h1` 半字修饰解出对应浮点数，形成与硬件行为 100% 同构的读写闭环。
3. **SPIR-V 平面着色装饰（DEC_FLAT）**：
   - 当 `source_format` 为 1（`src_flat16`）或 0（`src_flat32`）时，对片段着色器 Input 变量声明 `OpDecorate %vid Flat`（操作码 14）；
   - 顶点模板 `build_vert_template()` 动态感知 `is_flat` 标志，在对应的顶点输出变量上对称声明 `OpDecorate %vv Flat`，确保 Vulkan / MoltenVK 管线接口契约完全一致。
4. **守卫与 fail-loud 防御**：
   - 若 `LD_VAR_BUF_IMM.f16` 传入非 `src_flat16(1)`/`src_f16(3)` 的源格式，严格抛出 `ValueError` 并 exit 2；
   - 若 flat varying 附带非 0（`sample`/`centroid`）的采样模式，严格 fail-loud 拦截；
   - 若 staging 寄存器数与分量数不匹配，严格 fail-loud 拦截。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - 修改前 `0x405d8014310000c0` 触发不支持指令拦截（exit 2）；
  - `fl_vary_srcfmt`（`source_format=2`，`src_f32` 用于 `f16` 指令）精准拦截并报错（exit 2）；
  - `vary_f16_neg` 配错期望时 7812 像素精准报错（exit 1），证实测试通道无假阳性。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **全量回归矩阵 53/53 PASS**（32 渲染正例含新增 vary_f16 + 6 错误对照 + 15 fail-loud 含 fl_vary_srcfmt）；
  - `vary_f16` 逐像素比对（期望 51, 102, 153, 255）**7812 像素全部通过，失败 0，exit 0**；
  - 真机 3 条指令：**全部 3/3 条 100% 逐位命中（100% BIT-MATCH）**。

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - 定义 `DEC_FLAT = 14`；
   - `SUPPORTED` 集合：纳入 `"LD_VAR_BUF_IMM.f16"`；
   - `FragBuilder.input_var()`：支持 `is_flat` 标志并装饰 `DEC_FLAT`；
   - `FragBuilder.emit_varying()`：增加 `is_f16` 支持，实现源格式校验、v2/v4 的 `PackHalf2x16` 压缩写入与 staging 寄存器数校验；
   - `translate()`：分发 `LD_VAR_BUF_IMM.f16` 进 `emit_varying(meta, is_f16=True)`；
   - `build_vert_template()`：镜像对称生成 `DEC_FLAT` 顶点输出装饰。
2. **`out/tmp/p2/frag_vary_f16.asm` / `frag_vary_f16_neg.asm`（新建）**：
   - 覆盖真机切片 `LD_VAR_BUF_IMM.f16.slot0.v4.src_flat16.center.retrieve.wait0126`、PackHalf2x16 打包、widen 解包全闭环及负向对照。
3. **`out/tmp/p2/fl_vary_srcfmt.asm`（新建）**：
   - 非法 `source_format` fail-loud 拦截用例。
4. **`out/tmp/p2/run_matrix.py`（改）**：
   - 矩阵接入 `"vary_f16"`、`"vary_f16_neg"`、`"fl_vary_srcfmt"`，全量用例数扩充至 53 项。

## 5. 验证（2026-10-13，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| 回归矩阵 run_matrix.py | **53 PASS / 0 FAIL**（32 正例 + 6 对照 + 15 fail-loud） |
| frag_vary_f16 逐像素 | 三角形内 7812 px 全 (51, 102, 153, 255)、失败 0、exit 0 |
| 负向对照（vary_f16_neg） | exit 1，逐像素失败 7812 px（期望与渲染值不符） |
| fl_vary_srcfmt 拦截 | 翻译 exit 2（非法 source_format fail-loud 守卫） |
| 真机 3 条指令回环 | 3/3 条 100% 逐位命中（100% BIT-MATCH） |

**真机覆盖率重算（160 字全量，同口径）**：

| | 可直译 | 未建模修饰 | 不支持指令 | 源形态不支持 | 覆盖率 |
|---|---|---|---|---|---|
| 基线（修饰收尾后） | 145 | 1 | 14 | 0 | 91.25% |
| **本批（LD_VAR_BUF_IMM.f16 建模后）** | **148** | **1** | **11** | **0** | **93.125%** |

- **不支持指令数从 14 降至 11（-3，LD_VAR_BUF_IMM.f16 全部清零）**；
- **可直译指令数从 145 跃升至 148 条（直译率高达 92.50%）**；
- **总覆盖率跃升至 93.125%（149/160，突破 93% 大关）**；
- 剩余 11 条不支持指令清单：`CLPER.i32` (3)、`LEA_BUF_IMM` (2)、`IADD.s64` (1)、`SHADDX.u64` (1)、`ATEST` (1)、`BLEND` (1)、`BRANCHZI` (1)、`LD_TILE` (1)。

## 6. 遗留

1. **下阶段核心候选**：
   - `CLPER.i32`（3 条）：subgroup / warp shuffle 算子；
   - `LEA_BUF_IMM`（2 条）：缓冲有效地址计算；
2. **长尾指令（各 1 条）**：
   - 64 位整数算术：`IADD.s64`、`SHADDX.u64`；
   - 固定功能与间接跳转：`ATEST`、`BLEND`、`BRANCHZI`、`LD_TILE`；
3. **剩余 1 条未建模 flow**：
   - `bin-007f00023e80[03]`：`IADD_IMM.i32 flow=discard`。
