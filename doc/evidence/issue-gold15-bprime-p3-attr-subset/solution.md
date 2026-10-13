# issue-gold15-bprime-p3-attr-subset —— B′ P3：顶点属性通路建模（`LD_ATTR_IMM`，10 条真机指令）

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-mem-subset](../issue-gold15-bprime-p3-mem-subset/solution.md)（遗留 #1「LD_ATTR_IMM 10 条」）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

在真机捕获的 160 条指令中，内存族清扫后，**`LD_ATTR_IMM`（10 条）成为剩余 24 条未支持指令中最大的单一类别（占比 41.7%）**。
该指令分布于 P3 样本中的 4 个 Vertex 着色器：
- `bin-007f00023e00`（1 条）：`LD_ATTR_IMM.v4.f16.slot0 @r0:r1, r60^, r61^, index:0x1, table:0x1`；
- `bin-007f00032e80`（1 条）：`LD_ATTR_IMM.v2.f32.slot0 @r0:r1, r60^, r61^, index:0x0, table:0x1`；
- `bin-007f00034180`（4 条）：Location 0、Location 1、Location 4、Location 5 的 `v4.f32` 多属性连续载入；
- `bin-007f00037380`（4 条）：Location 0、Location 1、Location 4、Location 5 的 `v4.f32` 多属性连续载入。

此前 MVP 翻译器将 `LD_ATTR_IMM` 视为不支持指令（exit 2），阻断了顶点属性链路的直译。本批对顶点属性访问通路进行架构级建模。

## 2. 根因（语义事实与架构建模）

1. **指令语义与 ISA 元数据解析**：
   - 操作码为 `0x66`（bit 48..56），opcode2 为 `0x0`（bit 37..38）；
   - 元数据修饰符：
     - `vector_size`（bit 28..29）：0=scalar, 1=v2, 2=v3, 3=v4；
     - `register_format`（bit 24..26）：2=`f32`, 3=`f16`；
     - `staging_register_count`（bit 33..35）：staging 连续寄存器写入数；
     - `slot`（bit 30..32）：依赖槽（slot0/slot1/slot2）；
   - 立即数字段：
     - `index`（bit 20..23）：属性条目索引（直接映射 SPIR-V 阶段输入 `Location = index`）；
     - `table`（bit 16..19）：资源表索引（顶点属性固定为 `table=1`，其余非 1 fail-loud 拦截）；
   - 源操作数：`src0` 为 Vertex ID（固定 r60/r60^），`src1` 为 Instance ID（固定 r61/r61^）。
2. **SPIR-V 阶段输入映射（StorageClass Input + Location）**：
   - 属性条目 `index` 直接映射为 SPIR-V 的 `OpVariable %tptr %vid StorageClass_Input`，并装饰 `OpDecorate %vid Location %loc`；
   - 宽度感知：根据 `comps = vector_size + 1` 建立对应的输入向量类型（v1/v2/v3/v4）；
   - 顶点模板动态镜像：`build_vert_template()` 遍历 `fb.in_vars`，在顶点着色器侧自动为各被引用的 Location 建立对应分量数的输出变量并写入测试阶梯常量（`T_BITS`），确保图形管线在 Vulkan / MoltenVK 上完美链路匹配。
3. **寄存器格式转换（f32 vs f16 打包）**：
   - **`f32` 格式**：`comps` 个 32 位浮点分量依次解开写入 `base .. base + comps - 1` 各寄存器；要求 `staging_register_count == comps`；
   - **`f16` 格式**：真机 1 条 `v4.f16` 分量压缩写入 2 个 32 位寄存器（`@r0:r1`）；通过 GLSL.std.450 扩展指令 `PackHalf2x16`（操作码 58）分别将 `(x, y)` 与 `(z, w)` 打包为 32 位整型字写入寄存器；后续着色器可通过已支持的 `.h0`/`.h1` 半字修饰解出对应浮点数，形成完整闭环。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - 初版 decode_meta() 遗漏 `register_format` 字段导致无法区分 f32 与 f16；
  - `attr_f16` 用例中误读硬件 r63 寄存器触发寄存器文件活跃性未定义拦截（exit 2），改用标准 LUT 常量 `0x0`（LUT0）；
  - `fl_attr_table`（table:0x2）精准拦截并报告 `仅支持 table=1（属性表），当前 table=2`（exit 2）；
  - `attr_neg` 配错期望时 7812 像素精准报错（exit 1），证实逐像素比对高灵敏度。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **回归矩阵 47/47 PASS**（30 渲染正例含 attr/attr_real/attr_f16 + 4 错误对照 + 13 fail-loud 含 fl_attr_table）；
  - `attr` 逐像素比对（期望 51, 102, 153, 255）**7812 像素全部通过，失败 0，exit 0**；
  - `attr_real` 覆盖 P3 真机连续多属性 Location 0 + Location 1，逐像素比对（期望 102, 153, 255, 51）**7812 像素失败 0，exit 0**；
  - `attr_f16` 覆盖真机 `bin-007f00023e00` 打包载入与 widen 读回，逐像素比对（期望 102, 153, 255, 51）**7812 像素失败 0，exit 0**；
  - 真机 10 条指令：**全部 10/10 条逐位命中（100% BIT-MATCH）**。

## 4. 改动

1. **`out/tmp/p2/va2spirv.py`（改）**：
   - `decode_meta()`：加入 `"register_format"` 字段提取；
   - `SUPPORTED` 集合：纳入 `"LD_ATTR_IMM"`；
   - `FragBuilder`：补充 `t_in1`（标量 float 输入指针）；`input_var()` 扩充支持标量（comps=1）；
   - `emit_ld_attr_imm()`：实现属性 Location 映射、table=1 守卫、sr_cnt 校验、f32 逐分量载入与 f16 `PackHalf2x16` 打包；
   - `build_vert_template()`：补充 `t_out1`，支持标量及多 Location 输出绑定。
2. **`out/tmp/p2/frag_attr.asm` / `frag_attr_real.asm` / `frag_attr_f16.asm` / `frag_attr_neg.asm`（新建）**：
   - 覆盖单属性 f32、多属性真实混合、f16 打包全闭环与负向对照。
3. **`out/tmp/p2/fl_attr_table.asm`（新建）**：
   - 属性表 table 非 1 fail-loud 拦截用例。
4. **`out/tmp/p2/run_matrix.py`（改）**：
   - 接入 `"attr"`、`"attr_real"`、`"attr_f16"`、`"attr_neg"`、`"fl_attr_table"`。

## 5. 验证（2026-10-13，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| 回归矩阵 run_matrix.py | **47 PASS / 0 FAIL**（30 正例 + 4 对照 + 13 fail-loud） |
| frag_attr 逐像素 | 三角形内 7812 px 全 (51, 102, 153, 255)、失败 0、exit 0 |
| frag_attr_real 逐像素 | 三角形内 7812 px 全 (102, 153, 255, 51)、失败 0、exit 0 |
| frag_attr_f16 逐像素 | 三角形内 7812 px 全 (102, 153, 255, 51)、失败 0、exit 0 |
| 负向对照（attr_neg） | exit 1，逐像素失败 7812 px（期望与渲染值不符） |
| fl_attr_table 拦截 | 翻译 exit 2（table 非 1 fail-loud 守卫） |
| 真机 10 条回环 | 10/10 条 100% 逐位命中（100% BIT-MATCH） |

**真机覆盖率重算（160 字全量，同口径）**：

| | 可直译 | 未建模修饰 | 不支持指令 | 源形态不支持 | 覆盖率 |
|---|---|---|---|---|---|
| 基线（内存族建模后） | 107 | 29 | 24 | 0 | 85.0% |
| **本批（LD_ATTR_IMM 建模后）** | **113** | **33** | **14** | **0** | **91.25%** |

- **不支持指令数从 24 骤降至 14（-10，LD_ATTR_IMM 10 条全部清零）**；
- **覆盖率从 85.0% 跃升至 91.25%（+6.25%，突破 90% 大关）**；
- 剩余 14 条不支持指令清单明晰：`LD_VAR_BUF_IMM.f16` (3)、`CLPER.i32` (3)、`LEA_BUF_IMM` (2)、`IADD.s64` (1)、`ATEST` (1)、`BLEND` (1)、`BRANCHZI` (1)、`LD_TILE` (1)、`SHADDX.u64` (1)。

## 6. 遗留

1. **下阶段候选 ③：整数字节/半字选择小修饰与异步 flow 等待修饰收尾**：未建模修饰 33 条中，绝大部分为异步执行标记（如 wait0/wait01、slot 标记）以及整数源 `.b00` 以外的字节选择字段；
2. **剩余 14 条不支持指令**：
   - `LD_VAR_BUF_IMM.f16`（3 条）：flat16 顶点/片元传递；
   - `CLPER.i32`（3 条）：subgroup warp shuffle 算子；
   - `LEA_BUF_IMM`（2 条）：缓冲区有效地址计算；
   - 其余长尾各 1 条：`IADD.s64`、`ATEST`、`BLEND`、`BRANCHZI`、`LD_TILE`、`SHADDX.u64`。
