# issue-gold15-bprime-p3-clper-subset —— B′ P3：`CLPER.i32`（跨 lane 置换 / Subgroup Shuffle）与 `flow=discard` 建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-flat16-subset](../issue-gold15-bprime-p3-flat16-subset/solution.md)（遗留「候选：`CLPER.i32` 3 条」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

在真机捕获的 160 条指令中，flat16 varying 建模后，剩余未支持指令降至 11 条。其中 **`CLPER.i32`（3 条）为剩余指令中数量最多的单一核心算子**。
该指令分布于 P3 样本中的 Fragment 比较/钳位着色器 `bin-007f00037880`:
- `bin-007f00037880[07]`: `0x00a0c3101c0fc402` —— `CLPER.i32.subgroup4.zero r3, r2, 0x1000000.b3333`
- `bin-007f00037880[08]`: `0x00a0ff10100fc002` —— `CLPER.i32.subgroup4.zero r63, r2, 0x0.b0000`（目标 r63 discard 寄存器）
- `bin-007f00037880[09]`: `0x68a0c510180fc802` —— `CLPER.i32.subgroup4.zero.discard r5, r2, 0x3020100.b2222`（带 `flow=discard` 修饰）

同时，第 3 条附带的 `flow=discard` 为全库 160 条指令中**唯一剩余的未建模修饰**（另有一条 `IADD_IMM.i32` 也带此修饰）。此前翻译器将两者均作为不支持指令/未建模修饰进行拦截（exit 2）。

---

## 2. 根因（语义事实与架构建模）

1. **Valhall `CLPER.i32` 算子语义**：
   - 操作码为 `0xA0`（bits 48..56），二级操作码 `0xF`（bits 16..19）；
   - Mesa ISA 语义：`dest = A[lane B]`（Cross-lane permute / Subgroup Broadcast）；
   - 在片元着色器中，硬件将 4 个相邻线程打包为 Quad（`subgroup4`，bits 36..39 值为 1），用于计算屏幕空间偏导数（`dFdx`/`dFdy`）及相邻像元混合；
   - 目标寄存器：支持常规寄存器以及 `r63`（无副作用丢弃寄存器）；
   - 修饰位域：`lane_operation`（bits 32..33，真机全为 `none=0`）、`inactive_result`（bits 22..25，真机全为 `zero=0`）。
2. **SPIR-V 跨车道置换映射（`OpGroupNonUniformShuffle`）**：
   - 声明 SPIR-V 能力 `OpCapability GroupNonUniformShuffle`（编号 65）；
   - 映射操作码 `OpGroupNonUniformShuffle`（编号 345）：
     `OpGroupNonUniformShuffle %ResultType %ResultId %ExecutionScope %Value %InvocationId`
     - `%ExecutionScope`：`Scope_Subgroup`（uint 常量 3）；
     - `%Value`：源 A 的 32 位整型 SSA；
     - `%InvocationId`：由源 B 的字节选择字段（`widen`，`swizzles_8_bit` 枚举值 4/5/6/7 分别对应字节偏移 0/8/16/24）提取低 8 位，转为 uint SSA / 常量。
3. **`flow=discard` 语义对齐**：
   - Valhall ISA 规范中，`flow=discard` 仅用于片元着色器中指示“终止 helper 线程（导数计算后不再需要其参与）”；
   - 在 Vulkan / SPIR-V 执行模型下，Quad 内 helper 线程（`HelperInvocation`）的生命周期与掩码完全由硬件与 Vulkan 运行时透明接管；
   - 故 `flow=discard` 在静态 SSA 数据流层面为合法屏障标记，安全纳入 `FLOW_PASSTHROUGH` 放行。
4. **守卫与 fail-loud 防御**：
   - `lane_operation != 0`（如 xor/accumulate）严格抛出 `ValueError` exit 2；
   - `inactive_result != 0` 严格 fail-loud exit 2；
   - 源 B 字节选择使用非复制选择码（非 0/4/5/6/7）严格 fail-loud exit 2。

---

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - 修改前 `0x00a0c3101c0fc402` 报不支持指令 exit 2；
  - `fl_clper_op`（`lane_operation=xor`）精准拦截 exit 2；
  - `fl_clper_widen`（非复制选择 `b3210`）精准拦截 exit 2；
  - `clper_neg` 错期望时逐像素精准比对出 7812 像素失败（exit 1）。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - Mesa `asm.py` 汇编编码回环：真机 3 条字 **100% BIT-MATCH**；
  - SPIR-V 验证：`spirv-val --target-env vulkan1.2` 双 PASS；
  - MoltenVK 离屏渲染：`harness` 7812 像元全像素全为 `(51, 102, 153, 255)`，失败 0，exit 0；
  - 全量回归矩阵：`run_matrix.py` **57/57 PASS（0 FAIL）**。

---

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - 定义 `CAP_GROUP_NON_UNIFORM_SHUFFLE = 65`、`OpGroupNonUniformShuffle = 345`、`SCOPE_SUBGROUP = 3`；
   - `SUPPORTED` 集合：纳入 `"CLPER.i32"`；
   - `decode_meta()`：提取 `subgroup_size`、`lane_operation`、`inactive_result`；
   - `src_mod_mask()`：放开 `CLPER.i32` 源 B 的 `widen` 字段；
   - `FragBuilder`：支持 `_u32_type()` 与 `type_const_bits(..., "uint")`；实现 `emit_clper()`；在 `build()` 中按需声明 `GroupNonUniformShuffle` 能力；
   - `FLOW_PASSTHROUGH`：纳入 `"discard"`（解锁 1 条 CLPER 与 1 条 IADD_IMM）。
2. **测试与矩阵（增/改）**：
   - 新增 `frag_clper.asm`、`frag_clper_neg.asm`、`fl_clper_op.asm`、`fl_clper_widen.asm`；
   - 更新 `run_matrix.py`（从 53 扩充至 57 项全 PASS）。

---

## 5. 覆盖率跃迁量化

对真机 10 个 shader（160 条指令）全量复核：
- **支持的操作码 (Opcode)**：从 149 条跃升至 **152 / 160（95.000%，突破 95% 大关）**；
- **可直译指令 (Translatable)**：从 148 条跃升至 **152 / 160（95.000%）**；
- **未建模指令 (Unsupported)**：从 11 条降至 **8 条**（CLPER 3 条全清零）；
- **未建模修饰 (Unmodeled Modifiers)**：从 1 条降至 **0 条（全库未建模修饰彻底清零）**！

### 剩余 8 条不支持指令清单（已无任何修饰阻碍）：
- `LEA_BUF_IMM` (2 条，缓冲有效地址计算)
- `IADD.s64` (1 条，64 位加法)
- `SHADDX.u64` (1 条，64 位移位加法)
- `ATEST` (1 条，Alpha 测试)
- `BLEND` (1 条，混合)
- `BRANCHZI` (1 条，间接分支)
- `LD_TILE` (1 条，Tile 读取)
