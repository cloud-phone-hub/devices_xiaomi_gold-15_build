# issue-gold15-bprime-p3-int64-subset —— B′ P3：64 位整型算术（`IADD.s64` / `SHADDX.u64`）建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-leabuf-subset](../issue-gold15-bprime-p3-leabuf-subset/solution.md)（遗留「候选：64 位整数 2 条」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

在真机捕获的 160 条指令中，缓冲有效地址计算（`LEA_BUF_IMM`）建模后，剩余未支持指令降至 6 条。其中 **64 位整数运算（`IADD.s64` 与 `SHADDX.u64` 各 1 条，共 2 条）为剩余算术类指令的绝对核心**。
该两条指令分布于 P3 样本中的着色器：
- `bin-007f00006c80[02]`: `0x01abc20020004240` —— `IADD.s64 r2, r0^, r2^.w0`
- `bin-007f00026e00[06]`: `0x01a3c000202740ec` —— `SHADDX.u64 r0, sample, r0^.w0, shift:2`

两条指令均用于着色器内部的大地址寻址与基址指针偏移计算：
- `IADD.s64` 将 64 位基址 `r0:r1` 与带符号扩展的低 32 位偏移 `r2^.w0` 相加，结果存入 64 位寄存器对 `@r2:r3`，供后续 `LOAD.i128` 宽内存加载；
- `SHADDX.u64` 将低 32 位元素索引 `r0^.w0` 左移 2 位（字步长缩放 4 字节），加到 64 位采样位置数组基址指针 `sample` 上，结果存入 `@r0:r1`，供下游 `LOAD.i32` 使用。此前翻译器将两者均作为不支持指令拦截（exit 2）。

---

## 2. 根因（语义事实与架构建模）

1. **Valhall 64 位整数指令 ISA 语义**：
   - 操作码：`IADD.s64` 操作码 `0x1AB@48` + `0x0@16`；`SHADDX.u64` 操作码 `0x1A3@48` + `0x7@16`；
   - 目标寄存器：64 位结果写入 `@r<dest>:r<dest+1>` 连续寄存器；
   - 源操作数 A：64 位指针/整数值（支持寄存器对、LUT 常量、FAU uniform、FAU 特殊值如 `sample` 采样位置指针）；
   - 源操作数 B：64 位源，支持字选择修饰 `widen=8`（`.w0`，即取低 32 位字进行符号扩展或零扩展）；
   - 立即数：`SHADDX` 包含 `shift` 移位字段（bits 20..22），语义为 `dest = A + (extend(B) << shift)`。
2. **SPIR-V 64 位整型算术映射**：
   - 声明 SPIR-V 能力 `OpCapability Int64`（编号 11）；
   - 声明 64 位无符号整型 `OpTypeInt 64 0`（`%ulong`）与 64 位有符号整型 `OpTypeInt 64 1`（`%long`）；
   - 寄存器读取：
     - 当带 `.w0`（`widen=8`）时：仅读取低 32 位寄存器，通过 `OpSConvert %long`（有符号）或 `OpUConvert %ulong`（无符号）扩展至 64 位；
     - 当无 widen（`widen=0`）时：分别加载低 32 位与高 32 位寄存器，通过 `OpUConvert` + `OpShiftLeftLogical` (32) + `OpBitwiseOr` 合成 64 位 SSA 值；
     - FAU 特殊值 `sample` 延续 MVP 单采样/单 lane 假设，物化为 64 位常量 0（`%ulong_0`）；
   - 算术执行：
     - `SHADDX` 在非零 shift 时通过 `OpShiftLeftLogical %ulong` 移位；
     - 通过 `OpIAdd %ulong` 计算 64 位和（二进制补码同构，跨 32 位进位自动传递）；
   - 寄存器回写：
     - 截断低 32 位：`OpUConvert %uint` + `OpBitcast %int` 存入 `r<dest>`；
     - 提取高 32 位：`OpShiftRightLogical` (32) + `OpUConvert %uint` + `OpBitcast %int` 存入 `r<dest+1>`。
3. **守卫与 fail-loud 防御**：
   - `saturate` 饱和修饰（bit 30）严格抛出 `ValueError` exit 2；
   - 源 B 使用未建模的 widen（非 0 且非 8）严格 fail-loud exit 2；
   - 源 A 带 widen 字段严格 fail-loud exit 2。

---

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - 修改前真机字 `0x01abc20020004240` 与 `0x01a3c000202740ec` 报不支持指令 exit 2；
  - `fl_iadd64_widen`（`IADD.s64` 带 `.b0`）精准拦截 exit 2；
  - `fl_shaddx_widen`（`SHADDX.u64` 带 `.b0`）精准拦截 exit 2；
  - `int64_neg` 错期望时逐像素精准比对出 7812 像素失败（exit 1）。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - Mesa `asm.py` 汇编编码回环：真机 2 条字 **100% BIT-MATCH**；
  - SPIR-V 验证：`spirv-val --target-env vulkan1.2` 双 PASS；
  - 反汇编验证：`spirv-dis` 见 `OpCapability Int64`、`OpTypeInt 64 0`、`OpUConvert`、`OpSConvert`、`OpIAdd` 正确生成；
  - MoltenVK 离屏渲染：`harness` 7812 像元全像素全为 `(51, 102, 153, 255)`，失败 0，exit 0；
  - 全量回归矩阵：`run_matrix.py` **65/65 PASS（0 FAIL）**。

---

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - 定义 `CAP_INT64 = 11`、`OpSConvert = 114`；
   - `SUPPORTED` 集合：纳入 `"IADD.s64"`, `"IADD.u64"`, `"SHADDX.u64"`, `"SHADDX.s64"`；
   - `src_mod_mask()`：放开 64 位整型指令源 B 的字选择 `widen` 字段；
   - `FragBuilder`：
     - 实现 `_u64_type()`、`_i64_type()`、`_const_u64()`；
     - 实现 `read_64()` 与 `write_64()`；
     - 实现 `emit_iadd64()` 与 `emit_shaddx()`；
     - `build()` 中按需输出 `OpCapability Int64`。
2. **测试与矩阵（增/改）**：
   - 新增 `frag_int64.asm`、`frag_int64_neg.asm`、`fl_iadd64_widen.asm`、`fl_shaddx_widen.asm`；
   - 更新 `run_matrix.py`（从 61 扩充至 65 项全 PASS）。

---

## 5. 覆盖率跃迁量化

对真机 10 个 shader（160 条指令）全量复核：
- **支持的操作码 (Opcode)**：从 154 条跃升至 **156 / 160（97.500%）**；
- **可直译指令 (Translatable)**：从 154 条跃升至 **156 / 160（97.500%）**；
- **未建模指令 (Unsupported)**：从 6 条降至 **4 条**（64 位整数 2 条全清零）；
- **未建模修饰 (Unmodeled Modifiers)**：保持 **0 条（全库未建模修饰保持清零）**。

### 剩余 4 条不支持指令清单（均为固定功能与混合）：
- `ATEST` (1 条，Alpha 测试)
- `BLEND` (1 条，混合)
- `BRANCHZI` (1 条，间接跳转 blend shader)
- `LD_TILE` (1 条，Tile 读取)
