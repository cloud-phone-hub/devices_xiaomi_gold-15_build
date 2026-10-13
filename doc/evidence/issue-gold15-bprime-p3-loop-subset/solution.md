# issue-gold15-bprime-p3-loop-subset —— B′ P3：后向分支/循环放开（`BRANCHZ` → SPIR-V 循环 CFG）+ 寄存器模型升级

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-branch-subset](../issue-gold15-bprime-p3-branch-subset/solution.md)
> （上批放开 `BRANCHZ` 前向 if-then，但**后向分支（循环）fail-loud**，列入遗留 #1）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

上一批（[branch-subset](../issue-gold15-bprime-p3-branch-subset/solution.md)）用 **SSA + OpPhi** 建模了
前向 if-then，但后向 `BRANCHZ`（offset 负 = 回跳）构成**循环**，需 SPIR-V `OpLoopMerge` 循环 CFG，
被主动 fail-loud。本批放开**后向分支/循环**，并做一个关键的**架构决策**（见 §2.1）。

真机 4 条 `BRANCHZ` 中既有前向（fragment 的 FCMP→BRANCHZ 守卫）也有后向（compute 样本 bin-007f00006c80
的 3 条回跳）；`BRANCHZI`（1 条，跳 blend shader）另计（见 §6）。

## 2. 根因（决定性发现）

1. **循环的 SSA 难点 → 改用 Function 变量承载寄存器（本批核心决策）**：
   上批的 SSA+OpPhi 对 if 够用，但**循环回边**需「递归 phi」（phi 前向引用回边值，`%i = phi(init, %i_next)`，
   `%i_next` 又在循环体引用 `%i`），构造与验证都易错。故本批把**寄存器文件从 SSA 切换到
   Function 局部 i32 变量 + `OpBitcast` 解读**：
   - 写：值 →（float 则 `OpBitcast`→i32）→ `OpStore` 到 reg 变量；
   - 读：`OpLoad` i32 →（ty='float' 则 `OpBitcast`→float）。
   值在分支合流/循环回边**自然保留**（变量即内存），**全程无需 phi / SSA 构造**；位型由 `OpBitcast` 保真，
   语义与原 SSA 等价（26 用例回归像素全对证成）。这是支持任意 CFG（含后续 BRANCHZI/内存族）的干净地基。

2. **do-while 循环编码**：`BRANCHZ` 后向 = `opcode 0x1F@48` + `offset`（27 位有符号，负=回跳）。
   语义 `if(src?=0) PC+=8*(offset+1)` → 回跳目标 `t = i+1+offset`（指令字索引，`offset` 须**符号扩展**）。
   循环体 = `[t, i]`（t=header，i=backedge），do-while（先执行 body，末尾条件决定回跳）。

3. **SPIR-V 循环 CFG（do-while）**：
   ```
   header(t):  …body…  <cond>
               OpLoopMerge %merge %cont None
               OpBranchConditional %cond %cont %merge   ; 真→回边、假→退出
   cont:       OpBranch %header
   merge(i+1): …续译…
   ```
   `OpLoopMerge` 在 header 块末；continue 块承载回边。真机 flow=`reconverge`（divergent 前置）。

4. **fall-through 进循环 header 需 `OpBranch`**：顺序 walk 中 entry/前一块 fall-through 到 header，
   前块必须以 `OpBranch header` 终结（否则 spirv-val 报 "A block must end with a branch instruction"）。
   前向分支的 then/merge、循环的 cont/merge 由显式 `OpBranch(Conditional)` 引出，无需额外 fall-through。

5. **fail-loud 守卫**：循环体内嵌套分支/循环、目标越界、`BRANCHZI`（blend 间接跳转）、源 `combine` 非
   none、浮点源 → 拒绝。注意：**后向分支本身不再 fail-loud**（已支持为循环），原 `fl_br_back` 用例删除。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：
  - **循环负向对照**：正确 `frag_loop`（真实 (102,0,0,255)，4 次迭代）配错期望 (51,0,0,255) →
    逐像素**失败 7812 / exit 1**（证循环计数确为 4，非固定输出）；
  - **fail-loud 五例**（均 **exit 2**）：循环体内嵌套 / 前向区内嵌套 / 源 combine 非 none /
    浮点源 / `BRANCHZI`。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - **循环 CFG**（`spirv-dis`）：`OpBranch %25`（entry→header fall-through）→ `%25 OpLabel`（body）→
    `OpINotEqual`（cond）→ `OpLoopMerge %34 %35` + `OpBranchConditional`（真→cont 回边、假→merge）→
    `%35 OpBranch %25`（回边）→ `%34` merge，do-while 结构正确；
  - loop/loop_neg ×frag/vert 共 **4 模块 `spirv-val` 全 PASS**（Vulkan 结构化循环校验过）；
  - **MoltenVK 逐像素**：`frag_loop`=(102,0,0,255)（4 次迭代 acc=4）、`frag_loop_neg`=(51,0,0,255)
    （2 次迭代 acc=2），**失败 0 / exit 0** —— 循环按计数正确迭代；
  - **全量回归 26 用例**（既有 24 + loop/loop_neg）重生成后 `spirv-val` 全 PASS、逐像素失败 0 /
    exit 0（证 SSA→Function 变量重构对直线/分支全部 opcode **语义等价、零破坏**）。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改，46401B / 1014 行）**：
1. 常量区增 `OpBitcast=124`、`OpLoopMerge=246`、`SC_FUNCTION=7`；
2. **寄存器模型变量化**：`__init__` 增 `reg_vars`/`reg_defined`/`reg_const`/`reg_type` + `t_i32_fn`
   （Function i32 指针）；新增 `set_reg_const`/`set_reg`/`get_reg`/`_reg_var`（OpStore/OpLoad/OpBitcast）；
   `val_to_ssa` 的 reg 分支改走 `get_reg`；**全部 emit_* 写站点**（mov/fadd/fma/rsq/fmax/frcp/
   iadd_imm/ialu/lshift/rshift/cmp/cvt/varying）由 `self.reg[dest]=...` 改 `set_reg`/`set_reg_const`；
   `reg_const`/`reg_type` 维护供 C 源常量判定与 BRANCHZ 源浮点 fail-loud；
3. **`translate` 加循环**：预扫描后向 BRANCHZ（`offset` 符号扩展）→ `{header: backedge}`；walk 时
   header 处 fall-through `OpBranch` + 开块、backedge 处 `_emit_loop_backedge`（`OpLoopMerge` +
   `OpBranchConditional` + cont 回边 + merge）；前向 if-then 保留（合流靠变量，**删 OpPhi**）；
4. 删除 `_live_at`/`_operand`/`_make_phi`/`_merge_state`（SSA 合流不再需要）；
5. `build`：r0..r3 读改 `get_reg(_, 'float')`；寄存器 `OpVariable`(Function) 声明插入 entry 块首
   （entry `OpLabel` 之后，body[2:2]）；
6. `branch_cond` 改用 `reg_type` 判浮点源（保留 fail-loud）；模块 docstring 更新。

**新增用例（`out/tmp/p2/`）**：
- `frag_loop.asm`：do-while 循环（cnt=4 → body acc+=1/cnt-=1 → BRANCHZ 回跳 → acc=4）→ 期望 (102,0,0,255)；
- `frag_loop_neg.asm`：负向对照（cnt=2 → acc=2）→ 期望 (51,0,0,255)；
- `fl_loop_nest.asm`：fail-loud（循环体内嵌套分支）。
- 删除 `fl_br_back.asm/.bin`（后向现被支持为循环，不再 fail）。

产物指纹（md5）：
| 文件 | 大小 | md5 |
|---|---|---|
| frag_loop.asm | — | 293fb45401cd53185d16945b5d8bf9c5 |
| frag_loop.bin | 96B | 71d0d9d5bcf1c93cf363a4f3f2e0736a |
| frag_loop_neg.asm | — | a7f3068e00a43ceb3b8a624aea24bba3 |
| frag_loop_neg.bin | 96B | b61007fde3e178586dd91a88327051f3 |
| fl_loop_nest.asm | — | de7d348ec663da54e1c8dccc323c531e |
| fl_loop_nest.bin | — | 0adb9a86674c2a386c91c151cfb88b94 |
| out_frag_loop.frag.spv | 1368B | 6b0567a16e899d74ba211c6bcfaccc58 |
| out_frag_loop.vert.spv | 504B | f0228fea070b6ef15ae9629a5ae52a9f |
| out_frag_loop_neg.frag.spv | — | 850e74ebb50005206fc701255829e1bc |
| va2spirv.py | 46401B | 613d134f6440478d7a5e5c4d5635ae61 |

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 用例 | 形态 | spirv-val (frag/vert) | 逐像素期望 | harness |
|---|---|---|---|---|
| `loop` | do-while 循环，cnt=4（4 次迭代 acc=4） | PASS/PASS | (102,0,0,255) | 失败 0 / exit 0 |
| `loop_neg` | 负向对照，cnt=2（2 次迭代 acc=2） | PASS/PASS | (51,0,0,255) | 失败 0 / exit 0 |
| 回归 26 用例 | 既有 24（flat/sfu/fma/mod/vary×4/ialu×3/cvt×3/shift×3/cmp×4/branch×3）+ loop×2 | PASS/PASS | 各自期望 | 失败 0 / exit 0 |

- **循环 CFG**：`spirv-dis` 见 `OpBranch`+`OpLoopMerge`+`OpBranchConditional`+回边 `OpBranch %25`，结构正确；
- **循环迭代正确**：loop(4 次→102) ≠ loop_neg(2 次→51)，负向对照（loop 配 51）→ 失败 7812/exit 1；
- **重构零破坏**：26 用例全绿，证 SSA→Function 变量对直线/分支 opcode 语义等价；
- **fail-loud**：循环体嵌套 / 区内嵌套 / combine / 浮点源 / BRANCHZI 五例均 **exit 2**。

结论：**后向分支/循环（do-while）端到端正确**，SPIR-V 循环 CFG 可逐像素可证；**寄存器模型升级为
Function 变量**，为任意 CFG（BRANCHZI 间接跳转、内存族）铺平地基。

覆盖统计：本批不新增 opcode（`BRANCHZ` 已在 branch-subset 计入 **19 种 / 103 条 64.4%**），
但把 `BRANCHZ` 从「仅前向 if」扩展到「前向 if + 后向 do-while 循环」，可直译面更广。

## 6. 遗留

1. **嵌套控制流**：循环体/分支区内嵌套分支或嵌套循环 → fail-loud，可递归扩展 `translate()` 支持
   嵌套（需区间/支配分析 + 递归区域）。
2. **`BRANCHZI`（真机 1 条）**：间接分支（跳 blend shader，绝对/相对地址 + 目标寄存器）——
   实为 **shader 间跳转**（fragment 末尾跳 blend shader），超出单 SPIR-V 模块翻译范围，
   需 blend shader 管线/ABI 建模，单独立项；当前 fail-loud。
3. **内存族（`LD_PKA.i64`/`LOAD.*`/`STORE.*`/`LEA_BUF_IMM`，约 33 条，最大块）**：需地址计算 +
   加载/存储 + 地址空间（buffer/指针）建模，架构级，单独立项。
4. **`while`/多重回边、`continue`/`break`**：本批仅 do-while 单回边；更复杂循环形态待扩。
5. 源 `combine` lane-select、浮点源比零（承接 branch-subset 遗留）。
6. **其余**（承接上批）：`LD_ATTR_IMM`（顶点属性）、`LD_VAR_BUF_IMM.f16`（flat16）、64 位整数、
   `CLPER.i32`（subgroup）、`ICMP_AND`/`FCMP_AND`、`condition` 的 `gtlt/total`、`result_type=u1`。
