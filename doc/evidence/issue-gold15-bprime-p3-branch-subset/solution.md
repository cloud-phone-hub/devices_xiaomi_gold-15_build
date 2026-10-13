# issue-gold15-bprime-p3-branch-subset —— B′ P3：控制流放开（`BRANCHZ` → SPIR-V 选择 CFG）

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-cmp-subset](../issue-gold15-bprime-p3-cmp-subset/solution.md)
> （上批放开比较族并落地 **SPIR-V 谓词基础设施**（`%bool` + `OpSelect`），
> 并把**控制流 `BRANCHZ`** 列为遗留 #1 —— 比较族是其直接前置）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

上一批（[cmp-subset](../issue-gold15-bprime-p3-cmp-subset/solution.md)）产出 `%bool` 谓词后，
控制流是自然的下一步 —— 但翻译器此前是**单块线性 SSA**（`main_ops` 平铺、`self.reg` 寄存器态
最后写生效），**无法表达分支**。本批把 `BRANCHZ`（比较归零分支）译为 **SPIR-V 选择 CFG**
（基本块 + `OpSelectionMerge`/`OpBranchConditional` + 合流 `OpPhi`），让翻译器从「直线」跨到「分支」。

P3 真机 160 条中控制流共 5 条：

| 真机指令 | 条数 | 出处 | 说明 |
|---|---|---|---|
| `BRANCHZ` | 4 | bin-007f00006c80[06]/[08]/[11]、bin-007f00037880[03] | 比较归零分支：`if (src ?= 0) PC += 8*(offset+1)`；配比较指令实现控制流 |
| `BRANCHZI` | 1 | bin-007f00023e80[04] | 间接分支（跳 blend shader，绝对/相对地址）—— **不在本批范围**，fail-loud |

**真机用法**：fragment 样本 bin-007f00037880 为 `LD_VAR_BUF_IMM → FCMP_OR.f32 → BRANCHZ`
（比较一 varying 是否为 0，按结果跳过一段计算）—— 即典型 `if (...) { ... }` 守卫/跳过形态；
另一 compute 样本含 3 条 BRANCHZ（LOAD/STORE 区，本批不整段翻译）。

## 2. 根因（决定性发现）

1. **编码定位**（ISA.xml line 804-827 实证）：`BRANCHZ` = `opcode 0x1F@48`（mask `0x1FF`），
   1 源 `combine`（"Value to compare against zero"）+ 1 立即数 `offset`（`start=8 size=27 signed`）
   + `va_mod eq@36`。语义：`PC := PC + 8*(offset+1)` 当 `src ?= 0`，否则 `PC + 8`；
   **默认分支当「非零」，`.eq` 分支当「为零」**；offset 以指令字（8B）为单位、相对**下一**指令
   （正偏移 = 跳过指令数）。`BRANCHZI`（line 842-855）= `opcode 0x2F@48`，2 源（值 + 分支目标）
   + `va_mod eq@36`/`absolute@40`，用于跳 blend shader。

2. **flow 字段 = `reconverge`**（ISA.xml Flow 枚举 line 50-80，`flow=10`）：真机 BRANCHZ 均带
   `.reconverge`（ISA 明示"分支/divergent 改变活跃线程掩码前一指令须设 reconverge"）。
   `asm.py` 回环须写 `BRANCHZ.reconverge r4^, offset:15` 才逐位命中真机字
   （flow 不影响翻译，仅作调度元数据；本翻译器忽略 flow）。

3. **条件映射**：BRANCHZ 源与**整数零**比较 → `%bool`：默认（`eq=0`）`OpINotEqual(src, 0)`、
   `.eq` `OpIEqual(src, 0)`。源须为**整数比较结果**（上批 ICMP/FCMP 的 `m1`/`i1`，即 `int` SSA）
   或常量（位型按 int 解释）；浮点源 → fail-loud（"比较到零"是整数域语义）。

4. **CFG 建模（本批核心）**：MVP 仅支持**前向、非嵌套**的结构化 **if-then**：
   ```
   header(算条件 %cond) ─真→ merge(跳过区)；假→ then(区 [i+1,t)) ─→ merge
   ```
   SPIR-V 用**选择构造**表达（Vulkan 要求结构化 CFG）：`OpSelectionMerge %merge None` +
   `OpBranchConditional %cond %merge %then`；then 块 `OpBranch %merge`；
   merge 块首以 **`OpPhi`** 合流。目标 `t = i+1+offset`（指令字索引），区 `[i+1, t)`。

5. **SSA 合流（OpPhi）**：合流处寄存器态 = 两前驱（跳过边 `S0`、then 边 `St`）合并：
   - 相同值 → 直取（无需 phi）；
   - 差异且**活跃**（后向活性：`prog[t:]` 读前未重写 ∪ 末尾输出 r0..r3）→ `OpPhi`；
     类型取自 SSA 侧（const 位型无类型，须有 SSA 锚定，否则 fail-loud）；
   - 差异不活跃（死值）→ 任取。
   保留既有 SSA 寄存器模型与全部 emit_*（直线语义零改动），仅在分支合流点插入 phi。

6. **fail-loud 守卫**（防错译，本批 5 例）：后向分支（`offset` 位 26 = 1 → 回跳/循环，需
   `OpLoopMerge`）、区内嵌套 BRANCHZ、源 `combine` 非 none（lane select，`src_mod_mask` 自动拦）、
   浮点源、`BRANCHZI`（blend 间接跳转，不在 SUPPORTED）。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - **负向对照**：正确 shader `frag_branch`（真实 (51,0,0,255)）配错期望 (102,0,0,255) →
    逐像素**失败 7812 / exit 1**（证像素确为分支真实结果，非走向被忽略）；
  - **fail-loud 五例**（均 **exit 2**）：后向分支 / 嵌套 / 源 combine 非 none / 浮点源 / `BRANCHZI`
    → 分别被相应守卫拒绝。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **编码回环**：`BRANCHZ.reconverge r4^, offset:15` 组装出 `0x501fc00000000f44`，
    与真机字（bin-007f00037880[03]，FCMP→BRANCHZ）**逐位一致** —— `.reconverge`/`r4^`/`offset`
    asm 语法实证钉死；
  - `spirv-dis` 显示正确选择 CFG：`OpINotEqual %bool`（分支条件）→
    `OpSelectionMerge %31 None` + `OpBranchConditional %cond %31 %30`（真→merge/跳过、假→then）→
    `OpBranch %31` → `OpPhi %float %17 %15 %32 %30`（合流 base/then 两值）；
  - 三用例 ×frag/vert 共 **6 模块 `spirv-val` 全 PASS**（Vulkan 结构化 CFG 校验过）；
  - **MoltenVK 逐像素**：`branch`=(51,0,0,255)（取→跳过 then）、`branch_neg`=(102,0,0,255)
    （不取→执行 then）、`branch_eq`=(102,0,0,255)（`.eq` 翻转走向），**失败 0 / exit 0**；
  - **回归**：既有 **21 用例** + 本批 3 = **24 用例**重生成后 `spirv-val` 全 PASS、
    逐像素失败 0 / exit 0（证 SSA 寄存器模型与 emit 重构零破坏）。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改，45051B / 1000 行）**：
1. 常量区新增控制流 opcode（`OpPhi=245`、`OpSelectionMerge=247`、`OpBranch=249`、
   `OpBranchConditional=250`，编号查头文件）+ `SEL_NONE=0`；
2. `decode_meta()` 固定名列表增 `"eq"`/`"absolute"`（va_mod 随 ISA 元数据自动解码）；
3. `SUPPORTED` 增 `"BRANCHZ"`（**不加 `BRANCHZI`** → 自动 fail-loud）；
4. `FragBuilder` 增基本块追踪（`cur_label`）与控制流方法：`new_block()`（OpLabel 标记）、
   `branch_cond()`（源→整数比零→%bool）、`_live_at()`（线性后向活性）、`_operand()`/`_make_phi()`/
   `_merge_state()`（合流 OpPhi）、`translate()`（前向 if-then CFG 遍历 + 各类 fail-loud 守卫）；
5. **`emit_instr()`**：把原 `main()` 的单条分派抽成方法，保留全部指令级 fail-loud
   （FMAX clamp / IADD saturate / LSHIFT not_result + shift 字节选择）；
6. `main()` 改两段式：第一遍解码+校验（SUPPORTED/`src_mod_mask`）建 `prog`（含 NOP 占位保索引对齐），
   第二遍 `translate(prog)` + `build()`；语义 fail-loud 归 `translate`（ValueError → exit 2）；
7. `build()` 改多块装配：`main_ops` 已含各块 OpLabel（平铺线性布局），末块续接输出 + `OpReturn`；
8. 模块 docstring 更新覆盖范围与 CFG 约束。

**新增用例（`out/tmp/p2/`）**：
- `frag_branch.asm`：BRANCHZ 前向 if-then（base r0=0.2 → 条件 5>3 真 → 跳过 then → 保持 0.2）
  → 期望 (51,0,0,255)；
- `frag_branch_neg.asm`：负向对照（ICMP 操作数互换 3>5 假 → then 执行 → r0 翻倍 0.4）
  → 期望 (102,0,0,255)；
- `frag_branch_eq.asm`：`.eq` 修饰（5>3 真、分支判「为零」→ 不跳 → then 执行）→ 期望 (102,0,0,255)；
- `fl_br_back` / `fl_br_nest` / `fl_br_combine` / `fl_br_fsrc` / `fl_br_i`（+`.bin`）：五条 fail-loud
  （后向 / 嵌套 / combine 非 none / 浮点源 / BRANCHZI）。

产物指纹（md5）：
| 文件 | 大小 | md5 |
|---|---|---|
| frag_branch.asm | 1616B | ef8dc1b6ce37606dd3a02a19bb6922da |
| frag_branch.bin | 80B | fff2d8ee6af6e08a9bd91ba00846aebe |
| frag_branch_neg.asm | 601B | 12de8cf6b9117f9ce6d5c8249cfe9f77 |
| frag_branch_neg.bin | 80B | 426bc46f8f0611b736c593521b0cb128 |
| frag_branch_eq.asm | 631B | 6592034760eeca36712668b5e2b96540 |
| frag_branch_eq.bin | 80B | d1214597a1cbfcbd88a07e0d0d1b7c29 |
| fl_br_back.bin | — | 0e2f4203f91f11308ccc030924a69a42 |
| fl_br_nest.bin | — | 82b294d53fa573dfbe9998996e8f0f84 |
| fl_br_combine.bin | — | 0e4a4aa35a11c75b196542f3f8ec9cae |
| fl_br_fsrc.bin | — | a76995da60ba25d9800c8522887480af |
| fl_br_i.bin | — | 8ba923cd12522cd1cab2154cfe1306f7 |
| out_frag_branch.frag.spv | 752B | e1c1d50c4758c259a5b5f2bdf30d3063 |
| out_frag_branch.vert.spv | 504B | — |
| out_frag_branch_neg.frag.spv | 752B | 2fd82de02898ac27eb2c9a931c66dd64 |
| out_frag_branch_eq.frag.spv | 752B | ef3241a4e2de42c59f9a579f2b3c1e79 |
| va2spirv.py | 45051B | ac562e4af8f32821e56ae791f3a503d6 |

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 用例 | 形态 | spirv-val (frag/vert) | 逐像素期望 | harness |
|---|---|---|---|---|
| `branch` | BRANCHZ 取→跳过 then（条件 5>3 真） | PASS/PASS | (51,0,0,255) | 失败 0 / exit 0 |
| `branch_neg` | BRANCHZ 不取→执行 then（3>5 假） | PASS/PASS | (102,0,0,255) | 失败 0 / exit 0 |
| `branch_eq` | `.eq` 翻转走向（5>3 真但判为零→不跳） | PASS/PASS | (102,0,0,255) | 失败 0 / exit 0 |
| 回归 24 用例 | 既有 21（flat/sfu/fma/mod/vary×4/ialu×3/cvt×3/shift×3/cmp×4）+ 本批 3 | PASS/PASS | 各自期望 | 失败 0 / exit 0 |

- **编码回环**：真机字 `0x501fc00000000f44` 逐位命中（`BRANCHZ.reconverge r4^, offset:15`）；
- **CFG 结构**：`spirv-dis` 见 `OpSelectionMerge`+`OpBranchConditional`+`OpBranch`+`OpPhi`，逻辑正确；
- **负向对照**：正确 shader + 错期望 → 失败 7812 / exit 1；`branch`(51) 与 `branch_neg`/`eq`(102)
  逐通道不同（证分支走向随比较结果/eq 位变化）；
- **fail-loud**：后向/嵌套/combine/浮点源/BRANCHZI 五例均 **exit 2**。

结论：**控制流 `BRANCHZ`（前向 if-then）端到端语义正确**，SPIR-V 选择 CFG + OpPhi 合流可在
Metal 上逐像素可证；翻译器从「单块直线」跨到「结构化分支」，为循环/复杂控制流铺路。

覆盖统计：真机 160 条中已覆盖 opcode **18→19 种**，条数 **99→103 条（64.4%）**。
（注：4 条真机 BRANCHZ 嵌在含内存/CLPER 等未建模指令的完整 shader 中，本批不整段翻译真机
shader —— 与既往批次一致，用合成用例验证语义 + 真机字回环钉编码；BRANCHZ 对**结构合法**的
前向 if-then 可直译，越界/嵌套/后向目标 fail-loud。）

## 6. 遗留

1. **后向分支 / 循环（`OpLoopMerge`）**：本批仅前向 if-then；回跳（`offset` 负）需循环 CFG 建模，
   当前 fail-loud。
2. **嵌套分支 / if-then-else**：区内嵌套 BRANCHZ、双分支 else 形态暂不支持（fail-loud），
   可递归扩展 `translate()`。
3. **`BRANCHZI`（真机 1 条）**：间接分支（跳 blend shader，绝对/相对地址 + 目标寄存器），
   需 blend shader ABI + 间接目标解析 → 单独立项。
4. **源 `combine` lane-select（`none`/`h0`/`h1`/`and`/`lowbits`）**：本批仅 `none`（整值比零），
   其余 fail-loud；`h0/h1` 取半字、`and`/`lowbits` 需按 ISA Combine 枚举建模。
5. **浮点源比零**：BRANCHZ 源限整数比较结果（m1/i1）；浮点源（如 `f1` 结果）比零语义需另行定义。
6. **`ICMP_AND`/`FCMP_AND`、`condition` 的 `gtlt/total`、`result_type=u1`、有符号 `ICMP_OR.s32`**
   （承接 cmp-subset 遗留）—— 配合分支可表达更复杂条件。
7. **其余大块**（承接上批遗留）：内存族（`LD_PKA.i64`/`LOAD.*`/`STORE.*`/`LEA_BUF_IMM`，约 33 条）、
   `LD_ATTR_IMM`（10 条，顶点属性 ABI）、`LD_VAR_BUF_IMM.f16`（3 条，flat16 打包）、
   64 位整数（`IADD.s64`/`SHADDX.u64`）、`CLPER.i32`（3 条，subgroup cross-lane）。
