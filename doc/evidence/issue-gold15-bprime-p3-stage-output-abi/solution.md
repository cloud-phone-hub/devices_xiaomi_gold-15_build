# issue-gold15-bprime-p3-stage-output-abi —— B′ P3：非 fragment 输出组装（stage 感知输出 ABI：compute/vertex）建模

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-cmp-residue-subset](../issue-gold15-bprime-p3-cmp-residue-subset/solution.md)（下一步优先级 1「非 fragment 输出组装」）
> 关联：[issue-gold15-bprime-p3-e2e-real-shaders](../issue-gold15-bprime-p3-e2e-real-shaders/solution.md)（记账口径③始作俑者）、[issue-gold15-bprime-p3-shader-recapture](../issue-gold15-bprime-p3-shader-recapture/solution.md)（3C/3F/4V 阶段归属）

---

## 1. 问题

`va2spirv` 的 `build()` 硬编码 **fragment 输出 ABI**（r0..r3 → vec4 RGBA，P2 约定），而 E2E 语料 10 个真机 shader 中 **7 个是非 fragment**（3 Compute / 4 Vertex，recapture log-pass [3] 阶段归属）——它们被硬塞 fragment ABI 能过 val 只是侥幸（staging 指令恰好定义了 r0..r3），**模块语义错误**（B2 渲染桥拿去渲染即错）。记账口径③（「片元输出 rN 未写入」→ 记账）即此缺口的挡板。

本批按阶段组装输出 ABI：**compute → GLCompute 模块**（输出=STORE→SSBO，无输出接口）、**vertex → Vertex 模块**（真机输出经 STORE 写输出缓冲 + 占位 Position 满足模块契约）、fragment 现状不变。

---

## 2. 根因（语义事实与架构建模）

1. **输出形态取证**（10 样本尾部指令流）：**Compute 尾 = `STORE.*`（SSBO 写出）**；**Vertex 尾 = `STORE.i128/i64`（flow=end，写位置/varying 输出缓冲）**；**Fragment 尾 = `BLEND`+`ATEST`**（颜色写回 r0..r3，blend 批已建模）→ **Compute/Vertex 的输出都是内存写（指令流内 STORE 已建模），不是接口输出变量**。
2. **阶段 ABI**：
   - compute：`OpExecutionModel GLCompute`(5) + `OpExecutionMode LocalSize 1 1 1`(17)；接口 = SSBO/FAU（已建模）；**不要求 r0..r3**（输出组装跳过）；
   - vertex：`OpExecutionModel Vertex`(0) + **占位 Position 输出**（`vec4(0,0,0,1)`，SPIR-V 模块契约要求 BuiltIn Position）——真机位置经 STORE 输出缓冲，buffer→Position 数据面映射待 B2（沿袭 r56-63 预置占位先例）；
   - fragment：r0..r3→RGBA + OriginUpperLeft（现状）。
3. **阶段归属**（recapture log-pass [3]）：Compute=01000/c900/06c80；Vertex=32e80/23e00/37380/34180；Fragment=26e00/23e80/37880。

---

## 3. 证据

- **输出形态取证**（[log-pass.txt](log-pass.txt) §1）：三阶段样本尾部指令制表（STORE/BLEND 形态）；
- 失败面（[log-fail.txt](log-fail.txt)）：①build() 重构 bug（body 拷贝早于 get_reg 发射 → `ID has not been defined` 53 假 FAIL，自发现自修复，教训留档）；②编辑与验证同批并行的执行竞态假象（92/3 间歇，串行复跑 2×95/95 稳定）；③占位 Position 语义待 B2 列明；
- 通过面：真机 compute（01000）/vertex（23e00）按阶段生成 + spirv-val PASS（GLCompute+LocalSize / Vertex+占位 Position）；`compute_store`（**无 r0..r3**，验证输出组装跳过）与 `vtx_store`（LD_ATTR_IMM+LEA_BUF_IMM+STORE，贴真机 vertex 尾形态）VAL_ONLY 用例 PASS；**回归矩阵 95/95**（93 零破坏 + 2 VAL_ONLY，连跑 2 次稳定）；**E2E 10/10 stage 驱动全绿**（3 compute/4 vertex/3 fragment 各走语义正确 ABI）。

---

## 4. 改动清单

1. **翻译器（`out/tmp/p2/va2spirv.py`）**：`EXEC_GLCOMPUTE=5`/`EXEC_MODE_LOCALSIZE=17` 常量；`build(stage)` 三分支输出组装（fragment 现状 / compute 无输出接口+LocalSize / vertex 占位 Position）；`main()` 第 4 可选参数 `stage`（缺省 fragment，矩阵 93 用例零破坏）；修复 body 拷贝时机（先组装后拷）。
2. **E2E**：`run_e2e.sh` 阶段驱动（`stage_of` 表 = recapture 阶段归属），产物 `<name>.<stage>.spv`，验证 = 翻译产物 + 顶点模板双 val；记账口径③⑤保留为 fragment 样本防御。
3. **测试件**：`compute_store.asm`（compute 正例，无 r0..r3）+ `vtx_store.asm`（vertex 正例，属性/地址/STORE 链）；`run_matrix.py` 增 `VAL_ONLY` 通道（翻译 + spirv-val 即绿，harness 渲染契约不适用）。

---

## 5. 验证

| 验证项 | 命令 | 结果 |
|---|---|---|
| 输出形态取证 | 10 样本尾部 dump | Compute/Vertex=STORE、Fragment=BLEND |
| 阶段 ABI | 真机 01000(compute)/23e00(vertex) + val | 双 PASS |
| VAL_ONLY 用例 | `run_matrix.py compute_store vtx_store` | 2/2 PASS |
| 回归（×2 稳定性） | `python3 run_matrix.py` | **95/95** |
| E2E stage 驱动 | `bash run_e2e.sh` | **10/10**（3C/4V/3F 各走 ABI） |

---

## 6. 遗留

1. **vertex 占位 Position**：真机位置经 STORE 写输出缓冲（POS/VARYING），buffer→`gl_Position`/varying 接口的数据面映射待 **B2 渲染桥**（占位 `vec4(0,0,0,1)` 沿袭 r56-63 预置占位先例）；
2. **vertex varying 输出接口**：真机 varying 写输出缓冲同待 B2 映射（`build_vert_template` 的镜像 varying 仍为 harness 契约模板）；
3. **build() 教训固化**：get_reg/type_const_bits 等发射型 API 的产物必须在 body 拷贝前完成（log-fail §1 留档）；
4. 真实预置寄存器指针 ABI、探针清理、B2 渲染桥 → P3 验收门沿袭不变。
