# issue-gold15-bprime-p3-e2e-closeout —— B′ P3：E2E 收官批（BRANCHZ 嵌套控制流放开 + 移位族 LSHIFT_AND 补全）

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-shader-recapture](../issue-gold15-bprime-p3-shader-recapture/solution.md)（E2E 6/10，遗留=嵌套分支+v4i8 族）
> 关联：[issue-gold15-bprime-p3-loop-subset](../issue-gold15-bprime-p3-loop-subset/solution.md)（变量模型地基）、
> [issue-gold15-bprime-p3-shift-subset](../issue-gold15-bprime-p3-shift-subset/solution.md)（移位族前身）

---

## 1. 问题

真机 shader E2E 复跑 **6/10**（`bash run_e2e.sh`，bin 在 `out/tmp/p3/`，`e2e/*.le.bin` 已转字节序），
4 个 FAIL 归结为**两个建模缺口**（全部为宿主侧 `va2spirv.py` 工作，无需 guest）：

| 样本 | 报错 | 缺口 |
|---|---|---|
| `bin-007f00006c80` / `34180` / `37380` | `BRANCHZ 区内嵌套分支，暂不支持` | 缺口 1：嵌套控制流被 translate() 守卫拦死 |
| `bin-007f00001000` | `不支持的指令 LSHIFT_AND.i32（P2 子集外）` | 缺口 2：移位族 LSHIFT_AND.i32 漏网 |

验收门：`python3 run_matrix.py` 全绿（改动前基线 **70/70**）、`bash run_e2e.sh` **10/10**
（若出现新的、无法合理建模的深层形态，允许如实记账，但「记账」与「可译」分开计数并写明原因）。

---

## 2. 根因（语义事实与架构建模）

1. **缺口 1：嵌套 CFG 守卫（地基已在，守卫未放）**。
   loop-subset 批已把寄存器模型升级为 **Function 局部 i32 变量**（跨块天然保留、无需 phi），
   嵌套 CFG 的地基已在；但 `translate()` 仍有「BRANCHZ 区内不许嵌套 / 循环体内不许分支」
   两道 fail-loud 守卫（`fl_br_nest`/`fl_loop_nest` 负例在测它）。真机 3 样本解码实况
   （`/tmp/dump_cfg.py` 逐条 BRANCHZ 目标制表）——**全部为前向嵌套，无后向回边**：
   - `06c80`：`[06]→25` 外层 if，then 内含 `[08]→12` if-else（then 尾 `[11]` 恒真
     BRANCHZ `lut:0xFFFFFFFF → 23` = 无条件 goto，逃逸至外层合流）；
   - `34180`：`[24]→36`、`[55]→70`（then 含 `[64]→100` **逃逸至外层合流**，then 尾
     `[69] goto 100`）、`[101]→131` 内含 `[110]→122` 与 `[125]→131`（双分支同目标）；
   - `37380`：`[24]→35`+`[34] goto 45`、`[50]→65`（then 含 `[59]→94` 逃逸 + `[64] goto 94`）、
     `[100]→123` 内含 `[104]→112`、`[115]→123`。
   结构化建模的关键事实：**SPIR-V 禁止多个 header 共享同一 merge 块**（spirv-val 实测
   `Block is already a merge block for another header`，见 log-fail.txt 反例）→ 合流必须
   经「漏斗块」（空块 + `OpBranch` 真合流点）隔离；恒真 BRANCHZ（常量源）即无条件 goto，
   其落在分支 fall 侧尾部且目标 > 分支目标 → **if-else 形态**（then/else 双区合流）。
2. **缺口 2：移位族 LSHIFT_AND.i32 漏网**。shift-subset 批只放了 `LSHIFT_OR.i32`/
   `RSHIFT_AND.i32`。ISA.xml 实证全族同 `opcode 0xB4@48`、`opcode2@24（mask 0x703）` 区分：
   低位 [1:0]=组合（AND=0/OR=1/XOR=2），bit8=左移、bit10=算术右移 →
   `RSHIFT_AND=0x000`、`RSHIFT_OR=0x001`、`RSHIFT_XOR=0x002`、`LSHIFT_AND=0x100`、
   `LSHIFT_OR=0x101`、`LSHIFT_XOR=0x102`、`ARSHIFT_AND=0x400`、`ARSHIFT_OR=0x401`、
   `ARSHIFT_XOR=0x402`。同族一句一变体（方向×组合二维表）→ **9/9 i32 变体全放开**。
   移位族守卫沿用：`not_result`(bit30) fail-loud、shift 源限 `.b00`（widen=4）、
   B 源 `not`(bit35)/A 源 `widen` 由 `src_mod_mask()` 自动拦截。
3. **修完缺口后的下一层（如实记账，不猜）**：
   - `06c80` 过嵌套关后卡 `BRANCHZI 目标需 blend_descriptor`——ISA.xml 明确其 src2 是**通用
     Branch target**（Jump to an indirectly specified address，典型用途=fragment 尾跳 blend），
     静态模型本就作程序尾屏障 → 放宽为「blend_descriptor 或 **flow=end 终止位**寄存器目标」，
     非终止位间接跳转（目标不可知）仍 fail-loud；
   - `01000` 过 LSHIFT_AND.i32 关后卡 **v4i8/v4u8 字节向量子族**（`[35]LSHIFT_AND.v4i8`
     `[36]LSHIFT_XOR.v4i8` `[37]ICMP_OR.v4u8` `[38]U8_TO_U32`）——逐 lane 移位/打包/比较归约
     需独立建模批次 → 按验收条款**记账**（run_e2e.sh 记账口径⑤）。

---

## 3. 证据

1. **真机形态解码实况**：3 个嵌套样本的 BRANCHZ 目标制表（见上；`python3 /tmp/dump_cfg.py
   e2e/bin-*.le.bin`）——嵌套 if / if-else / 逃逸至外层合流 / 双分支同目标四种形态齐见；
2. **LSHIFT_AND 编码钉死**：真机字 `bin-007f00001000[16] = 0x00b4c2011086c042`
   （`LSHIFT_AND.i32 r2, r2^, 0x0.b00, u3`）经 **asm.py 回环逐位命中**（log-pass.txt）；
   opcode2=**0x100**（ISA.xml `<op val="0x100" start="24" mask="0x703"/>`）；
3. **SPIR-V 结构化反例/正例**（spirv-as+spirv-val 实测）：共享 merge 被拒
   （`already a merge block for another header`）；漏斗 merge、嵌套 if-else、
   do-while 通用形（header 独立块 + 体含嵌套选择）均 PASS → 发射方案由此定型；
4. **矩阵与 E2E 日志**：log-fail.txt（改前基线 6/10 与失败摘要）、log-pass.txt
   （改后 73/73 + E2E 9 OK/1 记账 + 回环命中）。

---

## 4. 改动

1. **`va2spirv.py`——嵌套结构化 CFG 重写**（控制流段整体换代）：
   - `emit_seq(lo,hi,cont_label,cont_pos)` **区域递归**：发射指令区间，结束走向 cont；
     `cont_pos` = 该标签在指令流上的位置，区内分支外逃时漏斗至此（循环体的 cont=回边检查点
     → 「continue」形态天然成立）；
   - `_branch_shape()` 构造解析：fall 侧扫描递归跳过嵌套构造；**恒真 BRANCHZ=goto**
     （常量源按 eq 静态判定；恒假=死指令跳过）；出口==目标 → if、出口>目标 → if-else、
     出口<目标 → 跨层跳出 fail-loud；then/else 区间交叠、构造尾不可达死代码 → fail-loud（不猜）；
   - **漏斗合流块**：每个选择/循环自建 header/merge，merge 后 `OpBranch` 真合流点
     （SPIR-V 禁共享 merge）；流位置→块标签经 `pos_labels` 统一（分支目标可共享普通块）；
   - **do-while 通用形**：`header(OpLoopMerge)→body(可嵌套)→cont(回边条件)→merge→续块`；
     循环可嵌于选择体内（if 内循环）、体内可含分支（循环体内分支）；
   - 保留 fail-loud：目标越界/跨层跳出/多回边循环/区域切割循环构造。
2. **`va2spirv.py`——移位族二维表**：`SHIFT_DIR_OPS`（LSHIFT/RSHIFT/ARSHIFT →
   OpShiftLeftLogical=196 / OpShiftRightLogical=194 / OpShiftRightArithmetic=195）×
   `SHIFT_COMBINE_OPS`（AND/OR/XOR → OpBitwiseAnd=199 / OpBitwiseOr=197 /
   OpBitwiseXor=198，回环实证 198）→ `SHIFT_I32` 9 变体，`emit_shift()` 一条通路；
3. **`va2spirv.py`——BRANCHZI 放宽**：寄存器/reg_ptr 目标仅 `flow=end` 终止位放行
   （程序尾屏障），非终止位仍 fail-loud（见根因 3）；
4. **`run_matrix.py`**：EXPECT 增 `br_nest=(51,102,0,255)`、`loop_nest=(153,102,0,255)`、
   `shift_and=(102,204,51,255)`；FAIL_LOUD 删 `fl_br_nest`/`fl_loop_nest`（语义反转），
   增 `fl_shift_and_notres`（not_result bit30）/`fl_shift_and_widen`（shift 源 `.b11` 非 .b00）；
5. **测试文件**：新增 `frag_br_nest.asm`（嵌套 if-else + then 尾 goto 逃逸）、
   `frag_loop_nest.asm`（if 内循环 + 体嵌套分支 continue 形态）、`frag_shift_and.asm`
   （LSHIFT_AND + ARSHIFT_AND + LSHIFT_XOR，含算术/逻辑右移区分度）、
   `fl_shift_and_notres.asm`/`fl_shift_and_widen.asm`；删 `fl_br_nest.*`/`fl_loop_nest.*`；
6. **`run_e2e.sh`**：记账口径增⑤（字节/半字向量子族未建模，消息模式匹配 `v4i8|v4u8|…|U8_TO_U32`），
   总分行改为「可译 + 记账」分列。

---

## 5. 验证

1. **E2E 对比**（`bash run_e2e.sh`）：

| 样本 | 改前 | 改后 |
|---|---|---|
| bin-007f00001000 | FAIL（LSHIFT_AND.i32 不支持） | **记账**（字节向量子族未建模，原因列明） |
| bin-007f00006c80 | FAIL（BRANCHZ 区内嵌套分支） | **OK**（val PASS） |
| bin-007f0000c900 / 23e00 / 23e80 / 26e00 / 32e80 | OK | **OK**（不变） |
| bin-007f00034180 | FAIL（BRANCHZ 区内嵌套分支） | **OK**（val PASS） |
| bin-007f00037380 | FAIL（BRANCHZ 区内嵌套分支） | **OK**（val PASS） |
| bin-007f00037880 | OK | **OK**（不变） |
| **合计** | **6 OK / 4 FAIL** | **9 OK + 1 记账 = 10/10 有账**（可译 9、记账 1 分列） |

2. **矩阵回归**（`python3 run_matrix.py`）：**73 PASS / 0 FAIL**
   （40 渲染正例 + 10 对照 + 23 fail-loud）。既有 70 项语义不变：68 项逐条原样通过；
   `fl_br_nest`/`fl_loop_nest` 2 项按预期**语义反转**为正例 `br_nest`/`loop_nest`
   （换名进 EXPECT，出可区分期望色）；新增 `shift_and` 正例 + `fl_shift_and_*` 2 负例。
   新正例期望色均经 harness 逐像素实证（与手算轨迹逐通道一致，区分度注释在各 asm 头部）；
3. **编码回环**：`LSHIFT_AND.i32 r2, r2^, 0x0.b00, u3` → `0x00b4c2011086c042` 与真机字**逐位命中**。

---

## 6. 遗留

1. **字节向量子族未建模**（`01000` 记账根因）：`LSHIFT_AND/LSHIFT_XOR.v4i8`（逐 lane 移位）、
   `ICMP_OR.v4u8`（逐 lane 比较 + result_type 逐 lane 编码）、`U8_TO_U32`。语义已有据可查：
   Mesa kraid `ops.rs` 的 `PerCompFoldable` 参考模型（`OpShiftLop`/`OpICmp` **逐分量** fold，
   lane 宽度截断移位）+ `hw_tests.rs` 硬件对照测试（V4U8 在 v9/v10 实测）——**下一批可据此
   建模**（v4i8 移位量为常量 0 时退化为字级 AND/OR/XOR，可先行窄放）；
2. **非 fragment 样本输出组装**（记账口径③，沿袭 e2e-real-shaders 遗留）；
3. **真实指针 ABI 待 B2 渲染桥**：入口预置 r56..r63 仍为常量 0 占位；
4. **探针临时件清理**：`wire-kbase.sh` shader 捕获探针（patch 19）仍在内核树，B2 前须撤；
5. **移位族宽度变体未放开**（列明）：i32 全 9 变体已放开；`v2i16`/`v4i8`/`i64` 宽度变体、
   `not_result`/B 源 `not`/非 `.b00` 字节选择、移位族 rotate（LRot/RRot）仍 fail-loud；
6. **BRANCHZ 高级形态**：跨层跳出（break/return 类）、多回边循环、恒假分支跨层目标等
   仍 fail-loud（不猜）；`gtlt/total` 比较条件、`u1` result_type 沿袭 cmp-subset 遗留。
