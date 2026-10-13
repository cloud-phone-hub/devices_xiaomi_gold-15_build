# issue-gold15-bprime-p3-leabuf-subset —— B′ P3：`LEA_BUF_IMM`（缓冲有效地址生成）建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-clper-subset](../issue-gold15-bprime-p3-clper-subset/solution.md)（遗留「候选：`LEA_BUF_IMM` 2 条」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

在真机捕获的 160 条指令中，跨车道置换（`CLPER.i32`）建模并彻底清零未建模修饰后，剩余未支持指令降至 8 条。其中 **`LEA_BUF_IMM`（2 条）为剩余指令中数量最多的单一核心算子**。
该指令分布于 P3 样本中的 Vertex 着色器 `bin-007f00023e00` 与 `bin-007f00032e80`:
- `bin-007f00023e00[01]`: `0x085e820400000d7b` —— `LEA_BUF_IMM.slot0.wait0 @r2:r3, r59^, table:13, index:0`
- `bin-007f00032e80[01]`: `0x085e840440000d7b` —— `LEA_BUF_IMM.slot1.wait0 @r4:r5, r59^, table:13, index:0`

该两条指令负责在着色器入口处依据结构索引/基础偏移量，将缓冲资源的 64 位有效基址加载至 staging 双寄存器（`@r2:r3` 或 `@r4:r5`），以供下游的全局内存指令（`LOAD.i64`/`LOAD.i32`/`STORE.i64` 等）作为 64 位基址指针使用。此前翻译器将该指令拦截在 P2 子集外（exit 2）。

---

## 2. 根因（语义事实与架构建模）

1. **Valhall `LEA_BUF_IMM` ISA 语义**：
   - 操作码为 `0x5E`（bits 48..56）；
   - Mesa ISA 语义：`Load effective address of buffer into staging registers`；
   - 目标 staging 寄存器：bits 40..45 为 staging 基址，`staging_register_count=2`（连续 2 个 32 位寄存器 `@r<base>:r<base+1>`，承载 64 位指针）；
   - 源操作数 `src_struct_idx`（bits 0..7）：支持常规寄存器、指针寄存器（如 `r59^`，高位 0x40 标记）、LUT 常量及 FAU；
   - 修饰字段：`table`（bits 8..15，真机均为 `13`，代表缓冲描述符表 Buffer Table）、`index`（bits 16..23，真机均为 `0`，代表缓冲索引 0）。
2. **SSBO 全局内存架构同构映射**：
   - 在已支持的全局内存模型中，内存块映射为 Vulkan `StorageBuffer`（SSBO，DescriptorSet 0, Binding 0）及 `OpTypeRuntimeArray %int`；
   - 下游内存指令（如 `LOAD.i64 @r0:r1, [r2^:r3^]`）读取 64 位指针的低字寄存器作为基址字节偏移（除以 4 得到数组字索引）；
   - 因此，`LEA_BUF_IMM` 针对 `table: 13, index: 0`，将源 `src_struct_idx` 的 32 位整数值写入 staging 低字寄存器（`r<base>`），高字寄存器（`r<base+1>`）写入常量 0；
   - 这一映射使 `LEA_BUF_IMM` 与既有的 `LOAD`/`STORE` 全局内存通路完全同构闭环。
3. **守卫与 fail-loud 防御**：
   - `table != 13`（非缓冲表）严格抛出 `ValueError` exit 2；
   - `index != 0`（非 0 号缓冲描述符）严格 fail-loud exit 2；
   - `staging_register_count != 2`（非 64 位双寄存器）严格 fail-loud exit 2。

---

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - 修改前 `0x085e820400000d7b` 报不支持指令 exit 2；
  - `fl_lea_table`（`table=1`）精准拦截 exit 2；
  - `fl_lea_index`（`index=1`）精准拦截 exit 2；
  - `lea_buf_neg` 错期望时逐像素精准比对出 7812 像素失败（exit 1）。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - Mesa `asm.py` 汇编编码回环：真机 2 条字 **100% BIT-MATCH**；
  - SPIR-V 验证：`spirv-val --target-env vulkan1.2` 双 PASS；
  - MoltenVK 离屏渲染：`harness` 7812 像元全像素全为 `(255, 128, 64, 255)`，失败 0，exit 0；
  - 全量回归矩阵：`run_matrix.py` **61/61 PASS（0 FAIL）**。

---

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - `SUPPORTED` 集合：纳入 `"LEA_BUF_IMM"`；
   - `FragBuilder.emit_lea_buf_imm(meta, src_struct_idx)`：
     - 校验 `table == 13`、`index == 0`、`sr_count == 2`；
     - 触发 `_ssbo_block()` 确保 SSBO 描述符声明就绪；
     - 结构索引写入 staging 低字寄存器 `r<base>`，高字寄存器 `r<base+1>` 存入 0；
   - `emit_instr()`：分发 `LEA_BUF_IMM`。
2. **测试与矩阵（增/改）**：
   - 新增 `frag_lea_buf.asm`、`frag_lea_buf_neg.asm`、`fl_lea_table.asm`、`fl_lea_index.asm`；
   - 更新 `run_matrix.py`（从 57 扩充至 61 项全 PASS）。

---

## 5. 覆盖率跃迁量化

对真机 10 个 shader（160 条指令）全量复核：
- **支持的操作码 (Opcode)**：从 152 条跃升至 **154 / 160（96.250%）**；
- **可直译指令 (Translatable)**：从 152 条跃升至 **154 / 160（96.250%）**；
- **未建模指令 (Unsupported)**：从 8 条降至 **6 条**（`LEA_BUF_IMM` 2 条全清零）；
- **未建模修饰 (Unmodeled Modifiers)**：保持 **0 条（全库未建模修饰保持清零）**。

### 剩余 6 条不支持指令清单：
- `IADD.s64` (1 条，64 位加法)
- `SHADDX.u64` (1 条，64 位移位加法)
- `ATEST` (1 条，Alpha 测试)
- `BLEND` (1 条，混合)
- `BRANCHZI` (1 条，间接分支)
- `LD_TILE` (1 条，Tile 读取)
