# issue-gold15-bprime-p3-shader-recapture —— B′ P3：扩 shader 捕获探针 dump 长度 + guest 重捕完整 shader + E2E 复跑

> 状态：**重捕闭环（2026-10-13，instance 99）**；E2E 6/10（口径升级，详见 §5）
> 关联：[../issue-gold15-bprime-p3-e2e-real-shaders/solution.md](../issue-gold15-bprime-p3-e2e-real-shaders/solution.md)
> （缺口 B「捕获截断 128B」的提出与实证）、
> [../issue-gold15-bprime-p3-shader-capture/solution.md](../issue-gold15-bprime-p3-shader-capture/solution.md)
> （探针机制与工具链教训）

---

## 1. 问题

e2e-real-shaders 的**遗留 #1（缺口 B）**：`wire-kbase.sh` patch (19) 捕获探针每个 shader
只 dump 前 128B（16 字），真机 shader 更长，指令流在 16 字窗口处被截断——
`bin-007f00006c80`/`bin-007f00037880` 的 `BRANCHZ 目标越界（index=25/19 > end=15）`
即截断实证。本批目标：**dump 完整 shader 二进制**（按实际长度，上限防呆）→
guest 重捕 → 端到端重跑，消除截断面。

## 2. 根因（决定性发现）

1. **Shader Program 无长度字段**：Mesa panfrost genxml v9.xml 的 `Shader Program`
   （32B）仅 `Type(4)/Stage(4)/…/Preload(16)@1:0/.Binary(64)@2:0`，**没有 size/count**。
   → 按预案「加大上限到 4KB 并打印实际遍历到的长度」：dump 上限 128B→**4KB（防呆）**，
   `kbase_vmap` 整段越出 region 时**折半重试**（4096→…→128），并打印
   `len=<dump 字节数> end=<首个 flow=end 处字节数>（实际遍历长度）`。
2. **截断面比已实证的更大**：旧 10 个样本全部恰为 128B；重捕后同址真实长度
   **3–150 字**（8/10 >16 字）——旧样本里 8 个实际被截（不只 BRANCHZ 越界的 2 个），
   旧 E2E 的 4 个"OK"中 4 个其实是残缺流的"假 OK"。
3. **flow=end 可被 BLEND 超载**（ISA.xml BLEND desc：fixed-function 路径下
   flow 指定终止则结束、否则为相对分支；blend-shader 路径忽略 flow 继续执行）。
   截尾口径 = **首个 flow=end（Flow 枚举 15=Return from shader，bits59-62，含该字）**，
   与 `va2spirv.main()` 的 break 语义严格一致；另记 `nz`（非零跨度）揭示 blend 续体
   （如 23e80 end=3字 nz=5字）。
4. **4KB dump 打爆 dmesg ring**：P3 输出 ~8000+ 行，adb 抓 dmesg 时启动早期的
   P3 块已被滚掉（[log-fail.txt](log-fail.txt) #2）→ 全量取证面改为实例
   `runtime/<inst>/qemu.log`（daemon 从 UEFI t=0 聚合 serial console 流）；
   又因 console 流里 hexdump 与其它线程日志**交错**，提取器改为
   「P3 标记行开/关块 + hexdump 按 offset 落位」。

## 3. 证据

- 内核产物指纹（[log-pass.txt](log-pass.txt) [1]）：`Image` **32295424 B**、
  **Ubuntu clang 21.1.8 (6ubuntu1)**（与上批一致）；`mali_kbase.ko` 1274656 B
  vermagic `6.6.127-4k-cdh` 同源；ko 内新探针串 `len=%zu end=%zu` 在、旧 128B 版 0 处。
- 探针全链路（[log-pass.txt](log-pass.txt) [2]，instance 99）：`chain` 16 /
  `code vmap` 40 / `chase` 24 / `chase-code` 24 / `unreadable` **0**；
  折半防呆实证 `0x7f0000c900 len=1024 end=536`（4KB 越出 region 折半成功）。
- 重捕样本（[log-pass.txt](log-pass.txt) [3]）：**10 个唯一 shader**（3C/3F/4V，
  与旧批同址同分布），字数 47/67/39/17/3/38/3/146/150/24，**>16 字 8/10**；
  2 个 3 字为**真实短程序**（23e00/23e80：带 flow=end 哨兵，trim md5
  `7e428652…`/`c60d2a28…` 与旧 128B 样本独立复现一致 = 跨实例稳定性实证）。
- 解码（[log-pass.txt](log-pass.txt) [4]）：534 条指令 **UNKNOWN=0**，
  新暴露指令类（CSEL/FMIN/FCMP_AND/FMA.v2f16/LSHIFT_AND.v4i8…）为子集扩充供料。
- E2E 前后对比（[log-pass.txt](log-pass.txt) [5]）：6/10（0 记账），
  26e00/37880 由截断 fail/记账翻**真 OK**；失败面从「截断/未定义读取」全部转为
  深层结构缺口（3×BRANCHZ 嵌套 + 1×v4i8 移位族）。

## 4. 改动

1. `kernel/modules/mali_vgpu/wire-kbase.sh` patch (19)（改，探针其余逻辑不动）：
   dump 上限 128B→4KB + `kbase_vmap` 折半重试（下限 128B）+ 标记行打印
   `len=/end=`（end=首个 flow=end 处字节数，Flow 枚举 15）；`copy_from_user`
   兜底与限次（16 提交/24 追踪）不变。内核构建仍走
   `build/scripts/build-kernel.py`（CDH_REFRESH_KERNEL=1，ubuntu clang21 builder）。
2. `out/tmp/p3_extract.py` / `out/tmp/p3_decode.py`（重建，中间脚本）：
   提取器按 hexdump offset 落位抗 console 交错、同址取首块、flow=end 截尾 + nz 统计、
   产物 `bin-<addr>.bin`（大端转写）+ `bin-<addr>.le.bin`（LE 供 va2spirv）；
   解码器独立进程复用 va2spirv 解码器（与 asm.py 不同进程，规避 ISA 二次解析断言）。
   旧 p3-dmesg3.txt 回归 10/10 md5 对齐后上线。
3. `out/tmp/p2/va2spirv.py`（按重捕样本扩子集，6 条指令 + 3 处语义修正）：
   - 新指令：`FMIN.f32`（GLSL.std.450 FMin=37）、`CSEL.f32`/`CSEL.u32`
     （`dest=(A<cond>B)?C:D`，OpSelect）、`FCMP_AND.f32`（比较族 AND 组合）、
     `V2F32_TO_V2F16`（双 f16 打包进 32 位位型）、`FMA.v2f16`（h0/h1 逐 lane
     f16 FMA 后打包）；
   - `clamp` 修饰建模（ISA Clamp 枚举：1=clamp_0_inf/2=clamp_m1_1/3=clamp_0_1，
     FMin/FMax 等价式）；
   - 比较族 C 源放开**谓词寄存器**（`pred_regs` 判据：仅比较族写过的寄存器，
     i1/m1 位与/位或语义成立）；普通寄存器 C 仍 fail-loud（fl_cmp_csrc 契约保住）；
   - f16 打包的 `OpUConvert` 结果类型须无符号（spirv-val 硬约束）→ u32 域移位/或
     后 OpBitcast 回 i32。

## 5. 验证（2026-10-13，instance 99 / 宿主 Apple Silicon + MoltenVK）

1. **内核重建 + guest 重捕成功**：10 个唯一 shader、字数 3–150（>16 字 8/10，
   2 个 3 字为真短程序非截断）、`end=` 全部打印实际遍历长度、解码 **UNKNOWN=0**；
2. **截断面消除**：旧 BRANCHZ 越界两样本目标落界内（06c80 index25<39、37880 index19<24）；
3. **E2E 复跑 6/10**（0 记账）：26e00/37880 翻真 OK；4 个 FAIL 全部为深层结构缺口
   （3×BRANCHZ 区内嵌套 + 1×v4i8 复合移位族）——无一例截断类失败；
   「较 6/10 提升」数量目标未达成（如实记录），但口径已从"截断残流假 OK"升级为
   "完整流真 OK"，且旧分数中 4 个假 OK 已被揭穿重判；
4. **回归矩阵 70/70 全绿**（既有 70 项零破坏；fl_cmp_csrc 经 pred_regs 判据保持
   正确 fail-loud——本批曾因放开 C 源回归 69/1，见 [log-fail.txt](log-fail.txt) 6f）。

## 6. 遗留

1. **BRANCHZ 区内嵌套分支**（06c80/34180/37380 三个样本卡此）：translate 仅支持
   非嵌套 if-then 与直线循环体，嵌套 CFG 需区间递归/结构化合流改造（动 CFG 核心，
   本批刻意不做防砸 70/70）；
2. **v4i8 打包整数族**（01000）：`LSHIFT_AND/LSHIFT_XOR.i32`（复合移位+位运算）、
   `.v4i8` 变体、`ICMP_OR.v4u8`、`U8_TO_U32`——4×i8 打包 lane 语义待建模；
3. **本批新扩 6 条指令缺 harness 逐像素对照**：语义按 ISA.xml/Valhall 描述建模、
   E2E spirv-val 通过，但未按「扩一条验一条」补 run_matrix 正例/负例（含 CSEL 比较
   选择语义、FMA.v2f16 逐 lane f16 舍入语义的可区分像素验证），下批补用例；
4. **预置寄存器真实指针 ABI 待 B2 渲染桥**（同 e2e-real-shaders 遗留 #2，r56–r63 占位 0）；
5. **非 fragment 样本输出组装**（同遗留 #3）：本批 26e00 已过 fragment ABI，
   Compute/Vertex 仍按「指令流可译 + 组装跳过」口径；
6. **探针临时件清理**：patch (19) 仍为限次只读探针，定案后删（或降级为可开关）；
7. **e2e 字节序转换固化进工具链**（同遗留 #4）；dmesg ring 滚动教训一并固化：
   大 dump 取证走 qemu.log 而非 adb dmesg。
