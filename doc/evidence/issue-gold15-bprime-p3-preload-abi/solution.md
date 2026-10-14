# issue-gold15-bprime-p3-preload-abi —— B′ P3：真实预置寄存器指针 ABI（r55–r63 按阶段 BuiltIn 组装）建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-stage-output-abi](../issue-gold15-bprime-p3-stage-output-abi/solution.md)（优先级 #1「真实预置寄存器指针 ABI」）
> 关联：[issue-gold15-bprime-p3-e2e-real-shaders](../issue-gold15-bprime-p3-e2e-real-shaders/solution.md)（r56–r63 入口预置语义定性）、kraid `ir.rs`/`model.rs`（`PreloadReg`/`preload_reg()` 权威映射）

---

## 1. 问题

E2E-real-shaders 批把 **r56–r63 入口预置寄存器**（读=环境值、可覆盖写）MVP 置为**常量 0**，描述为「环境指针半字，真实值待 B2」。这使所有依赖预置值的 shader 翻译出**语义错误的常量 0 模块**（compute 的 WorkgroupId 寻址恒 0、fragment 的坐标/coverage 恒 0），B2 渲染桥数据面无法收敛。本批把预置寄存器从占位常量升级为**按阶段 BuiltIn 真实组装**（SPIR-V 入口接口变量 + 打包进对应 r 寄存器）。

---

## 2. 根因（语义事实与架构建模）

1. **kraid `preload_reg()` 权威映射**（`ir.rs` `PreloadReg` 枚举 + `model.rs` 分配表，逐寄存器核对）：
   - **compute r55..62** = `LocalId01`（0..16=local_id_0、16..32=local_id_1 双半字）/`LocalId2`/`WorkgroupId0..2`/`GlobalId0..2`；
   - **vertex r59..63** = `InternalId`/`VertexId`/`InstanceId`/`DrawId`/`ViewId`；
   - **fragment r57..62** = `PrimitiveId`/`PrimitiveFlags`/`PositionXY`（0..16=position_x、16..32=position_y **双半字**）/`CumulativeCoverage`/`RasterizerCoverage`|`SampleCentroidId`/`FrameArg`。
2. **真机语境实证**（`/tmp/preload_probe.py` 对 160 条解码流检索）：26e00 `U16_TO_F32 r59·lane1` = PositionXY.y 双半字读、`ATEST r60` = coverage 固定源、vertex `LD_ATTR r60/r61` = VertexId/InstanceId 参数、compute `IMUL r57` = WorkgroupId 参与寻址——**与 kraid 表逐项吻合**。旧「环境指针半字」描述 = PositionXY 双半字的近似误读，真相为**按阶段 BuiltIn 输入打包**。
3. **组装模型**：预置值 = SPIR-V BuiltIn 输入变量（`OpVariable` + `OpDecorate BuiltIn`）→ `OpLoad`/`OpCompositeExtract` → 位拼接（`OpShiftLeftLogical`+`OpBitwiseOr`）→ 写入对应 staging r 寄存器；语义=**入口预置、可覆盖**（后续指令 dest 写覆盖即为普通寄存器，与 E2E-real-shaders 定性一致）。

---

## 3. 证据

- **kraid 映射表 + 真机语境吻合**（[log-pass.txt](log-pass.txt) §1）：两源交叉制表。
- **Builtin 枚举 spirv-dis 逐值实证**（[log-pass.txt](log-pass.txt) §2）：本机低位区偏移 +1（14=PatchVertices/17=FrontFacing/19=SamplePosition → FragCoord=**15**/SampleId=**18**/SampleMask=**20**），compute/vertex 区（26/27/28/42/43）无偏移——全部 spirv-dis 名字反查实证；SampleId/SampleMask 整数 Input 须 **Flat** 装饰 + `SampleRateShading` 能力（VUID-StandaloneSpirv-Flat-04744）。
- **三阶段真机样本 BuiltIn 组装 + spirv-val**（[log-pass.txt](log-pass.txt) §3）：26e00/01000/23e00 各自 VAL PASS。
- **frag_preload 逐像素**（[log-pass.txt](log-pass.txt) §4）：256x256，三角形内 7812 px、失败 0——预置真实值非零 + 可覆盖双向锁定。
- **回归 ×2 + E2E**（[log-pass.txt](log-pass.txt) §5）：95/95 ×2 稳定、E2E 10/10（0 记账）。

---

## 4. 改动

- **`va2spirv.py`**：
  - Builtin 常量（spirv-dis 实证值）：`BUILTIN_FRAGCOORD=15`/`SAMPLEMASK=20`/`SAMPLEID=18`/`WORKGROUPID=26`/`LOCALINVOCATIONID=27`/`GLOBALINVOCATIONID=28`/`VERTEXINDEX=42`/`INSTANCEINDEX=43`；
  - `_preload_builtins(stage)`（mk 函数：先 `OpTypePointer(Input, tval)` 再 `OpVariable`，`flat=True` 走 DEC_FLAT + `need_sample_rate`）、`_bi_extract_u32`/`_bi_load_i32`（分量提取/加载）、`_emit_preloads(stage)`：
    - **compute**：r55=LocalId01 双半字打包（local_id_0 | local_id_1<<16）、r56=LocalId2、r57..r59=WorkgroupId0..2、r60..r62=GlobalId0..2；
    - **vertex**：r60=VertexIndex、r61=InstanceIndex（r59/r62/r63 无对应 Vulkan BuiltIn → 占位 0）；
    - **fragment**：r59=PositionXY（`FragCoord.x|y` 取 floor 转 u16 双半字打包）、r60=SampleMaskIn[0]、r61=SampleId<<16|coverage；
  - `translate(prog, stage)` 接线：`_emit_preloads(stage)` 替代占位常量 0；`preload_vars` 进 `OpEntryPoint` 接口。
- **测试件**：`frag_preload.asm` 重写为真实 ABI 期望 **(255,128,255,255)**（r0/r2=预置非零探测、r1=r57 占位 0 路径、r3=可覆盖验证）；`run_matrix.py` EXPECT 同步。

---

## 5. 验证

1. **编码/组装回环**：预置位拼接（半字打包/移位或）位型与 kraid 注释逐字段核对。
2. **spirv-val**：三阶段真机样本全 PASS（含 Flat/SampleRateShading 修正后）。
3. **MoltenVK 逐像素**：frag_preload 三角形内 7812 px、失败 0（harness rc=0）。
4. **全量回归矩阵 ×2**：**95 PASS / 0 FAIL** 两次一致（94 既有零破坏）。
5. **E2E**：10/10 可译 + val PASS，记账 0。
6. **负向对照**（[log-fail.txt](log-fail.txt)）：Builtin 编号初版偏移假 FAIL、OpVariable 指针类型假 FAIL、Flat 缺失假 FAIL——均为实现期拦截面，修正后全绿。

---

## 6. 遗留

1. **占位项存留（待 B2）**：fragment r57=PrimitiveId（Vulkan 需 GS）、r58=PrimitiveFlags、r62:63=FrameArg；vertex r59=InternalId（IDVS buf idx，无 Vulkan BuiltIn）、r62=DrawId（DrawParameters 扩展）、r63=ViewId（ViewIndex 扩展）——占位 0。
2. **PositionXY u16 坐标编码**（floor 截断语义）待 B2 真机数据面复核。
3. MoltenVK `OpFOrdNotEqual` NaN 行为偏差、dest 宽度=0 精确语义（沿袭前批）待 B2。
