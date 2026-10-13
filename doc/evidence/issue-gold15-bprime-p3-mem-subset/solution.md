# issue-gold15-bprime-p3-mem-subset —— B′ P3：全局内存族建模（`LD_PKA` / `LOAD` / `STORE`，33 条真机指令）

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-fau-subset](../issue-gold15-bprime-p3-fau-subset/solution.md)（遗留 #3「内存族 33 条」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

在真机捕获的 160 条指令中，**内存族（`LD_PKA` / `LOAD` / `STORE`）占 33 条**，是剩余 57 条未支持指令中最大的单一类别（占比 57.9%）。
该族涵盖 3 类指令、11 条子形态：
- `LD_PKA.i64`（11 条）：缓冲区描述符载入（FAU 表寻址）；
- `LOAD.i32`（5 条）、`LOAD.i64`（5 条）、`LOAD.i96`（2 条）、`LOAD.i128`（2 条）：多字定长连续内存载入；
- `STORE.i32`（3 条）、`STORE.i64`（5 条）：多字定长连续内存存储。

此前 MVP 翻译器对所有内存指令一律报“不支持指令”，成为阻断 Compute/Fragment 样本直译的最大障碍。本批对全局内存访问通路进行架构级建模。

## 2. 根因（语义事实与架构建模）

1. **指令语义与字段解构（ISA 元数据实证）**：
   - `LD_PKA`：`src0` 为字节偏移，`src1` 为模式描述符，写入 staging 连续寄存器对（`@rBase:r(Base+1)`）；
   - `LOAD`：`src0` 为 64 位基址寄存器对（`[rBase^:r(Base+1)^]`），`offset` 为 16 位有符号立即数偏移，将 `staging_register_count`（1..4）个字载入到 `@rDest..`；
   - `STORE`：`src0` 为 64 位基址寄存器对，`offset` 为 16 位有符号立即数偏移，将 `staging_register_count`（1..4）个字从 `@rSrc..` 写入内存。
2. **SPIR-V 内存映射架构（StorageBuffer SSBO）**：
   - 映射为 Vulkan `StorageBuffer`（DescriptorSet 0, Binding 0）；
   - 内部为 `OpTypeRuntimeArray %int`（ArrayStride 4），通过 `OpAccessChain` 配合字索引寻址；
   - 紧随 SPIR-V 1.4 规范，`var_ssbo` 作为全局接口变量加入 `OpEntryPoint` 接口列表。
3. **地址合成与字对齐**：
   - 基址支持常量折叠（`reg_const`）与动态 SSA（`OpIAdd`）；
   - 有符号立即数偏移符号扩展（`imm_off >= 0x8000` 负值扩展）；
   - 寻址计算：`eff_addr = base_addr + imm_off`，字索引 `word_idx = eff_addr >> 2`；
   - 守卫：未按 4 字节对齐必须 fail-loud 拦截（如 `fl_mem_align` offset=3 -> exit 2）。
4. **MoltenVK 运行期适配（踩坑与突破）**：
   - MoltenVK 默认启用 Metal argument buffers，在缺乏显式反射元数据时会报 `-3`；通过在 `harness.c` 注入 `MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=0` 走离散缓冲区通路；
   - 对未声明描述符集的历史着色器（如既有 39 个正例），MoltenVK 内部 descriptor stage 为 NULL，若无条件调用 `vkCmdBindDescriptorSets` 会触发 SIGSEGV（-11）；通过解析 SPIR-V 字节流动态决定是否调用绑定。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - MoltenVK argument buffer 元数据缺失导致管线创建失败（exit -3）；
  - 无描述符着色器调用 `vkCmdBindDescriptorSets` 导致空指针解引用崩溃（EXC_BAD_ACCESS / SIGSEGV -11）；
  - 内存未对齐访问直接右移导致静默数据错位（修正为 fail-loud 抛出）；
  - `asm.py` 汇编 tuple 语法产出 mode 1（reg_ptr）与 DDK 提交的 mode 0（reg）差异。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **回归矩阵 42/42 PASS**（27 渲染正例含 mem + 3 错误对照 + 12 fail-loud 含 fl_mem_align）；
  - `frag_mem` 离屏渲染逐像素比对（期望 255,128,64,255）**7812 像素失败 0，exit 0**；
  - 负向对照 `mem_neg` 逐像素实证真实值 **255,128,64,255**，exit 1；
  - `spirv-dis` 确认生成完整的 StorageBuffer 块与 `OpAccessChain/OpLoad/OpStore` 流；
  - 真机 33 条内存指令：**11/11 `LD_PKA.i64` 逐位命中**，其余 `LOAD`/`STORE` 指令结构逐位验证全部吻合。

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - SPIR-V 常量声明：`OpTypeRuntimeArray=29`、`SC_STORAGE_BUFFER=12`、`DEC_DESCRIPTORSET=34`、`DEC_BINDING=33`；
   - `SUPPORTED` 集合扩充 `LD_PKA.*`、`LOAD.*`、`STORE.*` 系列指令；
   - `_ssbo_block()`（惰性建）：生成 RuntimeArray + Block Struct + StorageBuffer 变量；
   - `emit_ld_pka()`：将偏移与模式描述符解析到 staging 寄存器对；
   - `emit_load()` / `emit_store()`：完成基址 + 偏移的有符号地址计算与多字连续加载/存储；
   - `build()`：`var_ssbo` 声明进 `OpEntryPoint` 接口列表。
2. **`out/tmp/p2/harness.c`（改）**：
   - 注入 `MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS=0`；
   - 配置 StorageBuffer (DescriptorSet 0, Binding 0, 4KB) 与测试初始化内存；
   - 动态扫描着色器是否包含 DescriptorSet，安全按需绑定描述符。
3. **`out/tmp/p2/frag_mem.asm` / `frag_mem_neg.asm`（新建）**：
   - 覆盖 `LD_PKA` -> `LOAD.i64` -> `LOAD.i32` -> `STORE.i32` -> `LOAD.i32` 内存闭环。
4. **`out/tmp/p2/fl_mem_align.asm`（新建）**：
   - 非 4 字节对齐访问 fail-loud 测试（offset=3 -> exit 2）。
5. **`out/tmp/p2/run_matrix.py`（改）**：
   - 接入 `"mem"`、`"mem_neg"`、`"fl_mem_align"`。

## 5. 验证（2026-10-13，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| 回归矩阵 run_matrix.py | **42 PASS / 0 FAIL**（27 正例 + 3 对照 + 12 fail-loud） |
| frag_mem 逐像素 | 三角形内 7812 px 全 (255,128,64,255)、失败 0、exit 0 |
| 负向对照（错期望 255,0,0,255） | exit 1，逐像素打印真实值 **255,128,64,255** |
| fl_mem_align 拦截 | 翻译 exit 2（偏移未 4 字节对齐 fail-loud） |
| 真机 33 条回环 | 11/11 `LD_PKA.i64` 100% 逐位命中，其余各 LOAD/STORE 语法语义完全吻合 |

**真机覆盖率重算（160 字全量，同口径）**：

| | 可直译 | 未建模修饰 | 不支持指令 | 源形态不支持 | 覆盖率 |
|---|---|---|---|---|---|
| 基线（FAU 建模后） | 88 | 15 | 57 | 0 | 64.4% |
| **本批（内存族建模后）** | **107** | **29** | **24** | **0** | **85.0%** |

- **不支持指令从 57 骤降到 24（-33）**；
- 覆盖率从 64.4% 跃升到 **85.0%**；
- 剩余 24 条不支持指令清单明晰：`LD_ATTR_IMM` (10)、`LD_VAR_BUF_IMM.f16` (3)、`CLPER.i32` (3)、`LEA_BUF_IMM` (2)、`IADD.s64` (1)、`ATEST` (1)、`BLEND` (1)、`BRANCHZI` (1)、`LD_TILE` (1)、`SHADDX.u64` (1)。

## 6. 遗留

1. **剩余不支持指令的大头转向 ② `LD_ATTR_IMM`（10 条）**：顶点属性载入通路，单项占剩余 24 条的 41.7%；
2. **`LD_VAR_BUF_IMM.f16`（3 条）与 `CLPER.i32`（3 条）**：需 16 位片元属性打包与 lane shuffle 建模；
3. **未建模修饰 29 条**：主要为内存族异步 flow wait 标志（wait0/wait01 等在单线程模型下语义透明，待需时放开）与整数字节选择修饰。
