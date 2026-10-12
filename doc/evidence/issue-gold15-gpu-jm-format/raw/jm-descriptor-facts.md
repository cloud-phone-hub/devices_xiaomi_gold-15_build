# gold-15 JM（Job Manager）作业格式取证原始数据（2026-10-12）

> 目的：为「GPU 硬件仿真」路线 A/B 的核心未知量 **R4（job chain / job descriptor 格式）** 定性定量。
> 方法：① 读 kbase r49p0 源码认定"谁写、谁解析"的边界；② 汇总项目已有实测（wire-kbase 补丁 16d + run71-83 日志）。
> 所有路径以 `devices/xiaomi/gold-15/kernel/modules/mali_vgpu/kbase/` 为根（记为 `KB/`）。

---

## 1. 边界：kbase 只传指针，不解析描述符

| 事实 | 证据 |
|---|---|
| job chain 指针 `jc` 由**用户态**放在 atom 里传进来 | `KB/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h:633`（`struct base_jd_atom_v2`，含 `jc`）；项目实测 72B atom 的 `jc` 在 `+0x08`（见 `../issue-gold15-vendor-gpu/solution.md:809-830` §12.4） |
| kbase 提交时只把 `jc` 写进 `JS_HEAD_NEXT` | `KB/backend/gpu/mali_kbase_jm_hw.c:116-124`（`select_job_chain()` 直接返回 `katom->jc`）、`kbase_job_hw_submit()`（同文件 `:195+`） |
| kbase 树内**没有**描述符布局定义 | `grep -rn "exception_status\|job_type\|jd_job\|job_descriptor" KB/drivers KB/include` → **0 命中** |
| 描述符是**硬件格式**（由 GPU 消费），因此与驱动无关；Mesa panfrost 的 genxml 提供同一格式的公开参考 | 项目 patch 16d 注释（`wire-kbase.sh` §16d，约 920-1170 行）明确"布局依据 Mesa panfrost genxml v9.xml + run71 chain dump 字节对照" |

## 2. 作业类型编号 = `JS<n>_FEATURES` 位号（kbase 自己的枚举）

| 类型 | 位 | 依据 |
|---|---|---|
| NULL | bit 1 | `KB/backend/gpu/mali_kbase_gpuprops.c:183`；宏 `KB/hw_access/regmap/mali_kbase_regmap_jm_macros.h:229` `JS_FEATURE_NULL_JOB (1u<<1)` |
| SET_VALUE（write value） | bit 2 | `gpuprops.c:184`；宏 `:230` |
| CACHE_FLUSH | bit 3 | `gpuprops.c:185`；宏 `:237` |
| COMPUTE | bit 4 | `gpuprops.c:186`；宏 `:232` |
| VERTEX | bit 5 | 宏 `:233`（gpuprops 未取） |
| GEOMETRY | bit 6 | 宏 `:234`（gpuprops 未取） |
| TILER | bit 7 | `gpuprops.c:187`；宏 `:235` |
| FUSED | bit 8 | 宏 `:236`（gpuprops 未取） |
| **FRAGMENT** | **bit 9** | `gpuprops.c:188`；宏 `:237` |

**自洽性校验（关键）**：项目 patch 16d 从实测字节解出的 Fragment Job `Type` 字段 = **9**，
与 `JS<n>_FEATURES` 的 **FRAGMENT = bit 9** 完全一致 → 说明"描述符 Type 字段"与"kbase 的 job 类型位号"同一套编号，
可直接用 kbase 头文件当作**作业类型的权威枚举**。

## 3. 已实测解出的 v9 布局（项目已落地为 B2 v1 渲染桥）

来源：`wire-kbase.sh` 补丁 **16d**（注释即布局规格）+ 实测日志。

### 3.1 Fragment Job（64 B）

| 偏移/位域 | 字段 | 说明 |
|---|---|---|
| `0x10` bit1..7（`u7 @ (0x10:1)`） | **Type** | 实测 **9 = Fragment** |
| `0x18`（u64） | **Next** | 下一条 job 的 GPU VA（链式） |
| `0x20` | Payload | 后续 job 触发所需数据指针（此实现未用） |
| `0x28`（u64） | **FBD 指针** | 低 6 位打包：bit0=type、bit1=ZS CRC 扩展存在、bits2-5=**RT 数 − 1** |

### 3.2 FBD（Frame Builder Descriptor）

```
+0x00  参数块 64B：Width  u16@0x20（-1 编码）、Height u16@0x22（-1 编码）
+0x40  填充块 64B
+0x80  [若 ZS CRC 扩展存在：额外 64B]
+0x80(+64)  RT 数组：每项 64B
```

RGB RT 项内：

| 偏移/位域 | 字段 |
|---|---|
| `0x20`（u64） | **Base**（RT 的 GPU VA） |
| `0x28`（u32） | **Row Stride** |
| `0x2c`（u32） | **Surface Stride** |
| word1 `0x04` bit0 | **Write Enable** |
| word1 bits 3..7（`&0x1f`） | **Format** |
| word1 bits 8..11（`&0xf`） | **Block Format**（0=No Write / 1=Tiled / 2=Linear / 12=AFBC / 13=AFBC Tiled） |

> `genxml` 的 `start` 是 **word:bit**（word=4 B），以上为换算后的字节偏移。

### 3.3 实测佐证（判据而非推断）

| 判据 | 现场 |
|---|---|
| 布局可用（RT 被找到并填色） | `B2 fill rt=0x5efce00000 rows=9216 surf=176128 w=1080 h=2400 ... filled=163200`（`../issue-gold15-vendor-gpu/log-pass.txt`，run81-83） |
| RT 是 AFBC | run74 观察"填色 filled 与理论值吻合（**含 AFBC solid 头**）"（`../issue-gold15-vendor-gpu/solution.md` §13.2） |
| 真 `jc` 的来源与形态 | 72B atom `+0x08`：`0x5effb5d780`/`0x5effd82480`/`0x5effb1c780`（§12.4） |

## 4. 设备模型必须自建的两件事（格式已知，工作量确定）

### 4.1 GPU VA → guest PA 的页表遍历（kbase 自有编码）

| 事实 | 依据 |
|---|---|
| 4 级页表，级号 0..3 | `KB/mali_kbase_defs.h:98-102`（`MIDGARD_MMU_LEVEL(x)=x`、TOP=0、BOTTOM=3） |
| 每表 `PAGE_SIZE/8` 项、每项 8 B | `KB/mali_kbase_defs.h:328`（`levels[BOTTOMLEVEL][PAGE_SIZE/sizeof(u64)]`） |
| 有效标志 | L3 有效 ATE = `3`；L0-L2 有效 ATE = `1`；无效 = `0`；PTE（指向下级表）= `3`（仅 level<3） → `KB/mmu/mali_kbase_mmu_mode_aarch64.c:29-40, 89-105` |
| 物理地址提取 | `entry & ~0xFFF`（`as_phys_addr_t`） |
| 权限/属性位 | RW/RO = bits 6-7、`ENTRY_ACCESS_BIT` = bit10、`ENTRY_NX_BIT` = bit54、未用位 55 起 → 同文件 `:42-45, 129-150` |
| AS 基址寄存器 | `AS<n>_TRANSTAB`（`KB/mmu/mali_kbase_mmu_hw_direct.c:346-353` 写序：TRANSCFG→TRANSTAB→MEMATTR→`AS_COMMAND=UPDATE`） |

### 4.2 job chain 遍历与 renderpass 语义

- job chain = `Next` 指针链（见 §3.1）直到 0。
- `END_RENDERPASS` 的 atom 携带 **4 条** fragment job chain（norm/forced read × norm/forced write），
  由 kbase 依据 tiler 是否 OOM 选择其一：`KB/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h:475-523`（`struct base_jd_fragment` 四个字段）
  + `KB/backend/gpu/mali_kbase_jm_hw.c:116-168`（`KBASE_JD_RP_OOM/START/RETRY_*` 状态机）。

## 5. 仍然未知 / 研究级（否定性验证的结论部分）

| # | 未知项 | 影响档位 | 公开参考 |
|---|---|---|---|
| 1 | **TILER(7) / VERTEX(5) / GEOMETRY(6) / COMPUTE(4) 描述符布局**（项目只解出 FRAGMENT(9)） | T3（真渲染）；T2 不需要（只用到 FS 的 RT） | Mesa panfrost genxml（同硬件格式） |
| 2 | **图形状态**（blend / depth-stencil / viewport / scissor / MSAA / shader 绑定）在 FBD 与其它描述符中的位置 | T3 | 同上 |
| 3 | **shader ISA 执行**（Valhall v9 二进制 → 宿主 GPU） | T3 的硬门槛 | 只有 disassembler；**无反编译器** |
| 4 | **AFBC 解 tile / 压缩语义**（RT 常为 AFBC） | T2/T3 的显示正确性 | panfrost + 硬件文档 |
| 5 | **tiler heap / polygon list 语义**（tiler→fragment 的中间产物） | T3 | panfrost |

## 6. 结论（供 §3 路线 A/B 的门判定）

| 档 | 判定 | 依据 |
|---|---|---|
| **T1 控制面（保活）** | **可行（GO）** | 寄存器语义有 C 参考（`model_dummy.c`）+ 设备模型接口面已清点（见 `../issue-gold15-gpu-hw-emu/`）；不需要描述符解析 |
| **T2 搬运/填充（部分出画）** | **可行（GO），且风险远低于先前估计** | 描述符路径**已被实测打通**（§3.1-3.3，B2 v1 在跑）；设备模型只需补"页表遍历（§4.1）+ chain 遍历（§4.2）"两项确定性工作 |
| **T3 真渲染** | **Apple Silicon 宿主上 NO-GO** | 未知项 1-5；其中 #3 无公开反编译器 → 需路线 C（宿主换同族 Mali 板）才转为工程问题 |
