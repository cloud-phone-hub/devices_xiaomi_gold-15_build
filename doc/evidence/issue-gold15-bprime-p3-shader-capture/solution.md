# issue-gold15-bprime-p3-shader-capture —— B′ 主线 P3 前置：真机 Valhall shader 捕获探针（全阶段机器码闭环）

> 状态：**探针闭环（2026-10-12，run94-96）**，方案文档 = [../../gpu.md](../../gpu.md) §8（B′ 主线）、§8.5（P3 定义）
> 关联：[../issue-gold15-bprime-p2-va2spirv/solution.md](../issue-gold15-bprime-p2-va2spirv/solution.md)（P2 翻译器）、
> [../issue-gold15-vendor-gpu/solution.md](../issue-gold15-vendor-gpu/solution.md)（P2 遗留 #1「真机输入闭环」）

---

## 1. 问题

P2 验收门（[issue-gold15-bprime-p2-va2spirv](../issue-gold15-bprime-p2-va2spirv/solution.md)）通过后，
其**遗留 #1** = 「真机输入闭环」：P2 的翻译器输入是用 Mesa `asm.py` **本地合成**的
flat-color 机器码（6 指令/48B，`MOV.i32`/`FADD.f32`/`NOP` 三类）。P3（UI 负载端到端，
gpu.md §8.5）要求拿下 SF/HWUI 的**真实** shader 全量 → 必须先有办法把真机提交的
Valhall 机器码**抓出来**，且要覆盖三阶段（Compute/Fragment/Vertex），否则指令子集扩充
无的放矢。

卡点有三层：
1. **定位**：从 GPU 提交的 job 链，找到 `Shader Environment → Shader Program → .Binary`
   三级指针（v9.xml 里有静态布局，但真机指针是否落在预期偏移要实测）；
2. **取码**：`.Binary` 指向 **GPU-exec 区**（`0x7f00_xxxx_xxxx`，CPU 侧 `PROT_NONE`），
   内核态 `copy_from_user` **必然失败**；
3. **译码**：dmesg 里 `print_hex_dump(..., 32, 8, ...)` 的 8 字节组**字节序**不明，
   按小端解会产出大量假指令，无法判断解码器对不对。

## 2. 根因（决定性发现）

1. **工具链一致性（本轮最主要纠正）**：正确构建入口是
   `build/scripts/build-kernel.py`，它把 `CDH_KERNEL_BUILDER` 设为
   `cloud-phone-hub-ubuntu-builder:latest`（**Ubuntu clang 21.1.8**，与运行内核 banner
   同源）后再调 `kernel/build/build-kernel.sh`。若**直接**调 `build-kernel.sh`，
   `builder_image` 回落到 `ghcr.io/feicong/droid-ddk:android15-6.6`（**Android clang 17.0.0**）。
   本内核 `CONFIG_CFI_CLANG=y` + `CONFIG_SHADOW_CALL_STACK=y`，编译器混用属高风险。
   → **必须走 `build-kernel.py`**。
2. **取码必须用 `kbase_vmap`**：`.Binary` 位于 GPU-exec 区（CPU 侧 `PROT_NONE`），
   只有经 kbase 页表映射才可读。探针挂点选在 `kbase_jd_submit()` 的**进程上下文**
   （`mali_kbase_jd.c`，此处**未持** kbase 锁、**可睡眠**），因此 `kbase_vmap`/
   `kbase_vunmap` 安全可用；`copy_from_user` 仅作兜底探测。
3. **chase 判据 = Descriptor Type Shader = 8**：有界指针追踪（扫 job 描述符里 8B 对齐字）
   时，命中的 32B 描述符 `word0` 低 4 位须为 **8**（Descriptor Type Shader），
   bits4-7 = Stage（**1=Compute 2=Fragment 3=Vertex**）；`.Binary` 在 `+0x08`。
   上一版误用 `3` 导致漏报。
4. **字节序（译码关键）**：Linux `print_hex_dump(..., 32, 8, ...)` 对 8 字节组按
   **`%016llx` 格式化 u64** 输出，故打印出的 16 位十六进制串即该 u64 的**大端表示**
   （= `int(hexstr, 16)`）。→ 对提取出的字节流必须用 **`struct.unpack(">Q")`** 解析；
   用 `<Q` 会把字节序颠倒，产出 `MMUL.v4s8`/`CUBEFACE1` 等**假指令**且 UNKNOWN 高达 12/16。

## 3. 证据

- 工具链修正（[log-fail.txt](log-fail.txt) 第 [1] 段）：
  - 误用：`out/kernel/Image` 32623104 B、`Android clang version 17.0.0`（两轮 v76 均如此）；
  - 修正：`Image` **32295424 B**、`Ubuntu clang version 21.1.8 (6ubuntu1)`（与 v75 运行内核一致）；
  - 客机 `dmesg` banner：`Linux version 6.6.127-4k-cdh (root@a3d93e056c8b)
    (Ubuntu clang version 21.1.8 (6ubuntu1), Ubuntu LLD 21.1.8) #25`；
  - `mali_kbase.ko` vermagic = `6.6.127-4k-cdh SMP preempt mod_unload modversions aarch64`
    （与 Image 同源）。
- 探针计数（[log-pass.txt](log-pass.txt) 第 [3] 段，`out/tmp/p3-dmesg3.txt`）：
  `P3 code vmap`=40、`P3 chase`=24、`P3 chase-code`=24、`P3 frag fb`=8、
  `P3 code unreadable`=**0**、`P3 code user`=0 → **三级指针链路全命中、零失败**。
- 捕获产物（[log-pass.txt](log-pass.txt) 第 [4] 段）：
  `out/tmp/p3/bin-*.bin` **10 个唯一 shader**（**3 Compute / 3 Fragment / 4 Vertex**），
  每个 128B，md5 见日志。
- 译码（[log-pass.txt](log-pass.txt) 第 [5] 段，`out/tmp/p3-decode-be.txt`）：
  大端解析 160 条指令、**UNKNOWN = 0**；全局 opcode 分布合理
  （`FMA.f32`×36、`NOP`×25、`LD_PKA.i64`×11、`LD_ATTR_IMM`×10、`FADD.f32`×7 …）。
- 失败面（[log-fail.txt](log-fail.txt)）：clang17 误用、chase 判据 3、copy_from_user 取
  GPU-exec 区必失败、chase 无 dump 致 Fragment 缺码、提取器混入 chase hexdump、LE 译码假指令。

## 4. 改动

1. **`kernel/modules/mali_vgpu/wire-kbase.sh`（patch `(19)`，改）**：在
   `mali_kbase_jd.c` 的 `kbase_jd_submit()` 内新增只读探针（`do{}while(0)`/幂等标记，
   限 16 次提交、24 次追踪），三类动作：
   - 遍历 job 链（≤8 个），按 `Header>>1 & 0x7f` 判类型定长 dump 描述符
     （4=Compute 128B / 7=Tiler 256B / 9=Fragment 64B / 11=MallocVertex 384B）；
   - 按 v9.xml 静态布局取 Shader Environment（Compute env@+0x40、Tiler DCD@128+0x40、
     MallocVertex Position@256/Varying@320，各 `+0x28` 为 `.Shader`）→ `P3 shader-desc`，
     读 32B Shader Program 后取 `.Binary@+0x08` → **`kbase_vmap` dump 128B 机器码**
     （`P3 code vmap`；`copy_from_user` 作 `P3 code user` 兜底，失败 `P3 code unreadable`）；
   - 有界指针追踪：扫描述符内 8B 对齐字，命中的描述符 `word0&0xf==8` 才打印
     `P3 chase`，并对其 `.Binary` 同样 `kbase_vmap` dump（`P3 chase-code`）——补回
     Fragment（stage=2，其 shader 落在 Tiler DCD 里）的机器码；
   - Fragment Job 另打印 `P3 frag fb=... rts=.. zs=.. fbd=..`（FBD@+0x28 低 6 位打包）。
2. **`build/scripts/build-kernel.py`（用）**：以 `CDH_REFRESH_KERNEL=1` 全量重建，
   强制注入 ubuntu clang21 builder；`sync_kernel_dist()` 归集模块 → `out/dist/kernel/modules/`。
3. **`out/tmp/p3_extract.py`（新建，中间脚本）**：从 dmesg 提取真机机器码——识别
   `P3 code vmap`/`P3 chase-code` 块头，拼接其后 `cdh-mali: OFFSET: HEX` 行，
   同址只取首块、遇其它 `P3` 标记终止当前块，按地址落盘 `out/tmp/p3/bin-<addr>.bin`。
4. **`out/tmp/p3_decode.py`（新建，中间脚本）**：复用 `out/tmp/p2/va2spirv.py` 解码器，
   按 **大端 `>Q`** 逐字解码 10 个 shader，打印逐指令名与全局/分阶段 opcode 分布。
5. **部署链（未改脚本，按既有语义执行）**：`build-kernel.py` → `build-vendor_dlkm.py`
   （lineage 基座 `vendor_dlkm_a.img` 原地 debugfs 注入 → `out/dist/rom/vendor_dlkm.img`
   13209600 B）→ `pack-roms.py --patch vendor_dlkm=out/dist/rom/vendor_dlkm.img`
   （直写 `vda.img` 逻辑分区）→ 停旧 daemon → `daemon run 2`（实例 94→96）。

## 5. 验证（2026-10-12，instance 96 / gold-15 全局版）

- **工具链**：`Image` 32295424 B = Ubuntu clang 21.1.8；`mali_kbase.ko`（1273936 B）
  vermagic 与 Image 一致；客机 banner 同源（#25）。
- **探针全链路**：`P3 code vmap` 40 / `chase` 24 / `chase-code` 24 / `frag fb` 8 /
  `unreadable` 0 → `ShaderEnv → ShaderProgram → .Binary` 三级指针**全部命中**，
  GPU-exec 区经 `kbase_vmap` **可读**。
- **样本覆盖**：10 个唯一 shader = 3 Compute + 3 Fragment + 4 Vertex（每个 128B）。
  典型形态：
  - Fragment `bin-007f00026e00`：`LD_TILE` + `LD_VAR_BUF_IMM.f16` +
    `U16_TO_F32`/`S16_TO_F32` + `SHADDX.u64` + `FMA.f32`×4 + `FADD.f32`×2
    （纹理/插值类）；
  - Fragment `bin-007f00037880`：`LD_VAR_BUF_IMM.f32/f16` + `FCMP_OR.f32` + `BRANCHZ`
    + `CLPER.i32`×3 + `FMA.f32`×6 + `FADD.f32`×3（比较/钳位类）；
  - Fragment `bin-007f00023e80`：`LD_VAR_BUF_IMM.f16` + `ATEST` + `BLEND` +
    `IADD_IMM.i32` + `BRANCHZI`（合成/Blit 类，带固定功能混合/Alpha 测试）；
  - Vertex `bin-007f00032e80`：`LD_ATTR_IMM` + `LEA_BUF_IMM` + `FMA.f32`×10 +
    `FMAX.f32` + `FRCP.f32`（变换类）；
  - Vertex `bin-007f00034180`：`LD_ATTR_IMM`×4 + `FMA.f32` + `FRSQ.f32`（规格化类）；
  - Vertex `bin-007f00023e00`：`LD_ATTR_IMM` + `LEA_BUF_IMM` + `STORE.i64` + NOP×13
    （直通写位置类）。
- **译码**：大端 160 指令、**UNKNOWN=0**，结果落 `out/tmp/p3-decode-be.txt`。

## 6. 遗留

1. **抓的是 SF RenderEngine / HWUI 的混合样本**：10 个 shader 是启动期 SF 合成链的
   真实提交（含 Compute 子链，疑为 RenderEngine/GPU 计算路径），尚未按「系统 UI 全量」
   定标；P3 指令子集扩充应以本样本为起点、扩一条验一条（复用 P2 harness 逐像素比对）。
2. **探针为临时件**：`wire-kbase.sh` patch `(19)` 限 16 次提交/24 次追踪、仅打印不改行为，
   定案后应删（或降级为可开关）。
3. **指令子集仍窄**：P2 仅 `MOV.i32`/`FADD.f32`/`NOP`；本样本已暴露需补的类：
   `FMA.f32`/`FRSQ.f32`/`FRCP.f32`/`FMAX.f32`/`FMUL`、`LD_ATTR_IMM`/`LD_VAR_BUF_IMM`/
   `LD_TILE`/`LD_PKA`、`U16_TO_F32`/`S16_TO_F32`/`SHADDX`、
   `ATEST`/`BLEND`/`CLPER`/`BRANCHZ`/`BRANCHZI` 等——按 P3 实际渲染路径优先级排序。
4. **P3 验收门未动**：screencap 正确 UI / 60fps / guest CPU<5% 仍待 B2 渲染桥落地后评测
   （见 [issue-gold15-vendor-gpu](../issue-gold15-vendor-gpu/solution.md)：B2 渲染桥是
   watchdog 循环的根因层）。
5. **字节序教训需固化**：dmesg 8 字节组按大端呈现，后续任何 `print_hex_dump(..., 8, ...)`
   的译码都须走 `>Q`。