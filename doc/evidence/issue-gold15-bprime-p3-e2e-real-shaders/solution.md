# issue-gold15-bprime-p3-e2e-real-shaders —— B′ P3：真机 shader 端到端复跑（入口预置寄存器 ABI 建模 + E2E 闭环验收）

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-blend-subset](../issue-gold15-bprime-p3-blend-subset/solution.md)（160/160 逐字满贯）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)、
> 翻译器 [issue-gold15-bprime-p2-va2spirv](../issue-gold15-bprime-p2-va2spirv/solution.md)

---

## 1. 问题

P3 以「真机 160 条指令逐字统计」为覆盖率口径，blend-subset 收官 160/160 全满贯、run_matrix 69/69 全绿。
但**逐字可译 ≠ 整 shader 可译**：把 10 个真机 shader 的完整二进制（`out/tmp/p3/bin-007f*.bin`，
每个 128B/16 字）端到端喂给 `va2spirv.py`（解码 → 翻译 → SPIR-V → `spirv-val`）时，只有 **4/10** 通过：
- **缺口 A**：5/10 样本报 `r57/r59/r61 未定义即被读取`——真机 shader 把 **r56–r63 当入口预置寄存器**
  （Valhall 顶区寄存器在 shader 入口已被硬件/驱动装入环境指针的半字）：读 r57/r59/r61（指针半字），
  同时别的样本又把 r56/r59/r63 当普通 dest 写（用后覆盖，合法）→ 语义 = **入口预置、可覆盖**；
- **缺口 B**：`bin-007f00037880` 报 `BRANCHZ 目标越界（index=19, end=15）`——**捕获探针每个 shader
  只 dump 了前 128B**，真机 shader 更长，指令流被截断（分支跳出 16 字窗口即为实证）。

**端到端闭环才是真验收**：只有整 shader 能翻译 + SPIR-V 校验通过，指令级覆盖率才有实际意义。

---

## 2. 根因（语义事实与架构建模）

1. **缺口 A：入口预置寄存器 ABI 缺失**。
   - 真机事实：`bin-007f00006c80` 读 `r57`（IMUL.i32）、`bin-007f00023e00`/`032e80` 读 `r59`（LEA_BUF_IMM
     结构索引源）、`bin-007f00026e00` 读 `r61`（RSHIFT_AND.i32）、`bin-007f00037380` 读 `r59`（MOV.i32）；
     而 `frag_clper`（真机字回环）把 `r63` 当 discard dest、`frag_blend` 把 `r60` 当 staging dest——
     **顶区 r56..r63 = 入口预置、用后可覆盖**；
   - 翻译器原状：`get_reg()` fail-loud「先写后读」对 **r0..r55 全域**生效，把入口预置区也拦了；
   - MVP 语义（文档注明占位）：r56–r63 入口各预置**常量 0**（指针对统一视为 SSBO 基址 0；
     真实指针值由后续 B2 渲染桥提供），之后照常可写可读。
2. **缺口 B：捕获截断 128B**。
   - `wire-kbase.sh` patch 19 的 shader 捕获探针每个 shader 只 dump 前 128B（16 字）；
   - 真机 shader 更长，指令流在 16 字窗口处被截断 → `BRANCHZ` 的合法跳转目标落到窗口外，
     `bin-007f00037880` 报 `目标越界（index=19, end=15）` + `未见 flow=end 终止指令`——
     这是**正确的 fail-loud**（防错译），不是翻译器缺陷。
3. **字节序缝隙**：`p3/bin-*.bin` 是 dmesg `print_hex_dump` 的大端转写（须 `>Q` 读，见 shader-capture
   硬教训），`asm.py` 产物是 LE；`out/tmp/p2/e2e/bin-*.le.bin` 已是转换后的 LE 版（本批直接用）。

---

## 3. 证据

1. **基线 E2E（改动前，逐样本实测）**：4/10 OK（`01000`/`0c900`/`23e80`/`34180`）+ 5 个 r 未定义
   （`06c80` r57 / `23e00` r59 / `26e00` r61 / `32e80` r59 / `37380` r59）+ 1 个截断越界（`37880`），
   详见同目录 `log-fail.txt`；
2. **`frag_preload.asm` 解码回显**：`FADD.f32 r0, r59, 1.0` / `IADD.u32 r6, r57, r5` / `FADD.f32 r2, r61, 0.1`
   / `MOV.i32 r61, 1.0` 后回读——三寄存器预置读 + 一寄存器覆盖写全覆盖；
3. **MoltenVK 逐像素**：`preload` = **(255, 128, 26, 255)**、失败 0；负向区分度（注释内逐条）：
   未预置 → 翻译 fail-loud；预置值非 0 → r0/r1/r2 三通道同时偏离；预置误作不可覆盖 → r3=0；
   整数域误按 f32 解释 → r1=0；
4. **E2E 复跑（改动后）**：`06c80` 从「r57 未定义」翻到 `BRANCHZ 目标越界（index=25, end=15）`——
   **新暴露的截断实证**（第 2 个被截断样本，此前被缺口 A 掩盖）；`26e00`/`37380` 指令流可译、
   卡在输出组装 `片元输出 rN 未写入`（非 fragment 样本，见遗留③）。详见 `log-pass.txt`。

---

## 4. 改动

1. `out/tmp/p2/va2spirv.py`：`translate()` 入口块建立后对 **r56..r63** 逐个 `set_reg_const(regno, 0)`
   （8 个 `OpStore 0`，注释「入口预置寄存器 MVP：环境指针半字占位 0，真实值待 B2 桥」）；
   **不改 fail-loud 机制**（r0..r55 仍须先写后读）；docstring 同步 ABI 说明；
   小修：`fb.build()` 纳入同一 fail-loud 出口（exit 2），输出组装校验（如「片元输出 rN 未写入」）
   不再裸抛 traceback exit 1；
2. `out/tmp/p2/frag_preload.asm`：预置读参与计算出可区分像素（r0 浮点域读 r59、r1 整数域读 r57、
   r2 读 r61、r3 覆盖 r61 后回读）；
3. `out/tmp/p2/run_matrix.py`：EXPECT 增一行 `"preload": (255, 128, 26, 255)`（只增行，不改既有内容）；
4. `out/tmp/p2/run_e2e.sh`（新增，E2E 流程固化）：对 `e2e/bin-*.le.bin` 逐个 `va2spirv` →
   `spirv-val --target-env vulkan1.2`，输出每样本 translate/val 结果与总分；「指令流可译 +
   输出组装跳过」按**记账**单列（遗留③判定口径：非 fragment 样本不计 OK 分也不掩盖）。
   踩坑：bash 中 `$rc：`（全角冒号紧跟变量）会被并入变量名 → 须 `${rc}`。

---

## 5. 验证（2026-10-13，宿主 Apple Silicon + MoltenVK）

1. **回归矩阵 70/70 全绿**（37 渲染正例含 `preload` + 10 对照 + 23 fail-loud）：
   既有 69 项零破坏（含 dest=r56/r59/r63 的真机字回环样例 `clper`/`blend`/`imul_b0`/`lea_buf`）+
   `preload` 逐像素 (255,128,26,255) 失败 0；
2. **真机 E2E 前后对比**（`./run_e2e.sh`，10 个真机 shader）：

| 样本 | 基线（改前） | 复跑（改后） |
|---|---|---|
| bin-007f00001000 | OK | OK |
| bin-007f00006c80 | FAIL（r57 未定义） | FAIL（**新暴露**：BRANCHZ 越界 index=25>end=15 = 截断） |
| bin-007f0000c900 | OK | OK |
| bin-007f00023e00 | FAIL（r59 未定义） | **OK** |
| bin-007f00023e80 | OK | OK |
| bin-007f00026e00 | FAIL（r61 未定义） | 记账（指令流可译，输出组装跳过：片元输出 r3 未写入） |
| bin-007f00032e80 | FAIL（r59 未定义） | **OK** |
| bin-007f00034180 | OK | OK |
| bin-007f00037380 | FAIL（r59 未定义） | 记账（指令流可译，输出组装跳过：片元输出 r2 未写入） |
| bin-007f00037880 | FAIL（BRANCHZ 越界 index=19 = 截断） | FAIL（同，正确 fail-loud） |

   **总分 4/10 → 6/10 OK**（另 2 个指令流可译=记账、2 个截断 fail-loud）。
   5 个 r 未定义样本：**2 个全过、2 个推进到输出组装、1 个翻出截断真相**——缺口 A 已解，
   缺口 B 由 1 个实证变 2 个实证（06c80/37880），全如「不掩盖」预期暴露；
3. fail-loud 契约未伤：23 个 fl_* 负例仍 exit 2，`BRANCHZ` 越界仍正确拒绝（防把截断流错译）。

---

## 6. 遗留

1. **扩捕获探针 dump 长度 + guest 重捕完整 shader**（缺口 B 根治）：`wire-kbase.sh` patch 19 的
   128B（16 字）上限须放大（按 shader 实际长度或放宽上限），重跑 guest 捕获拿完整指令流；
   06c80/37880 两个样本的分支目标越界即截断实证；
2. **预置寄存器真实指针 ABI 待 B2 渲染桥**：本批 r56–r63 占位 0，「指针对统一视为 SSBO 基址 0」
   是 MVP 假设；真实环境指针半字的值与配对语义（4×64 位指针？指针→缓冲绑定）须由 B2 渲染桥
   按真机数据面复核（与 FAU 64 位条目布局假设同批待验）；
3. **Compute/Vertex 样本的输出组装**：`va2spirv` 目前只组 fragment ABI（r0..r3 → vec4 输出），
   非 fragment 样本（捕获 10 个 = 3 Compute + 3 Fragment + 4 Vertex）以「**指令流可译 +
   输出组装跳过/记账**」判定（26e00/37380 即此类）；后续按 stage 分别组装输出；
4. **e2e 字节序转换宜固化进工具链**：dmesg 大端转写（`>Q`）→ LE 现为手工一次性转换
   （`e2e/bin-*.le.bin`），应并入捕获/翻译流水线自动完成，防止后续复现时字节序踩坑复发。
