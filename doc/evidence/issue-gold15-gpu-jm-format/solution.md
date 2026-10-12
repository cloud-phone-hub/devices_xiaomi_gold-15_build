# issue-gold15-gpu-jm-format —— 路线 A/B 的否定性验证：JM 作业格式边界定案

> 状态：**取证完成（2026-10-12，round 2）**；本轮**只读 + 文档**，零代码改动（用户指令）。
> 关联：方案主文档 [../../gpu-hw-emu.md](../../gpu-hw-emu.md) §2.9/§4/§6；前置取证 [../issue-gold15-gpu-hw-emu/solution.md](../issue-gold15-gpu-hw-emu/solution.md)
> 原始数据：[raw/jm-descriptor-facts.md](raw/jm-descriptor-facts.md)；判据现场：[log-pass.txt](log-pass.txt)

---

## 1. 问题

上轮（round 1）把"GPU 硬件仿真"路线 A/B 的剩余风险归到 **R4：job chain / job descriptor 内存格式未知**
（kbase 内核不解析它，公开文档缺失）。外部调研又建议：**先做否定性验证**——客观估算"设备模型是否值得做"，
不可行就尽早否定，而不是先写 MMIO 骨架。

因此本轮要回答三个可判定问题：

1. **谁写、谁读** job chain？（决定我们能否从"已有源码/实测"拿到格式，而不是靠猜）
2. 设备模型要真正执行图形作业，**还缺哪些格式知识**？各自影响哪一档（T1/T2/T3）？
3. 结论是 **GO / NO-GO**？

## 2. 根因（结论）

1. **边界清楚**：`jc`（job chain 的 GPU VA）由**用户态 DDK 放在 atom 里传进来**
   （`base_jd_atom_v2.jc`，uapi `jm/mali_base_jm_kernel.h:633`；kbase 侧 `select_job_chain()` 直接返回 `katom->jc`，
   `backend/gpu/mali_kbase_jm_hw.c:116-124`）。kbase **只把指针写进 `JS_HEAD_NEXT`，完全不解析描述符**——
   `grep -rn "exception_status|job_type|jd_job|job_descriptor" KB/drivers KB/include` **0 命中**。
   → 描述符是**硬件格式**（由 GPU 消费），**不随驱动变化**，因此 Mesa panfrost 的 genxml 是同格式的公开参考。
2. **格式其实已经解出来了（本轮最重要的正面结论）**：项目上一阶段的 B2 v1 渲染桥（`wire-kbase.sh` 补丁 **16d**）
   已经从"panfrost v9.xml + run71 chain dump 字节"定案并落地了 **v9 Fragment Job(64B) → FBD → RT 数组** 的完整偏移，
   并据此对真实 RT 填色成功（run81-83：`B2 fill rt=0x5efce00000 rows=9216 surf=176128 w=1080 h=2400 … filled=163200`，
   含 AFBC solid 头）。**R4 不再是"未知量"，而是"未移植量"。**
3. **作业类型有权威枚举**：descriptor 的 Type 字段编号 = kbase 的 `JS<n>_FEATURES` 位号
   （NULL=1、SET_VALUE=2、CACHE_FLUSH=3、COMPUTE=4、VERTEX=5、GEOMETRY=6、TILER=7、FUSED=8、**FRAGMENT=9**；
   `gpuprops.c:181-189` + `regmap_jm_macros.h:229-237`）。
   **自洽校验**：实测解出的 Fragment Type = **9** ✅ ——可直接用 kbase 头文件当作业类型权威表。
4. **剩余未知是"研究级"但范围收窄**：TILER/VERTEX/GEOMETRY/COMPUTE 描述符、图形状态（blend/depth/viewport…）、
   **shader ISA 执行**、AFBC 解 tile、tiler heap/polygon list。其中 **shader ISA 无公开反编译器**是 T3 的死结。
5. → **判定**：**T1 GO、T2 GO（且风险低于先前估计）、T3 在 Apple Silicon 宿主 NO-GO**（要 T3 → 路线 C 换宿主）。

## 3. 证据

### 3.1 源码级（file:line）

| 结论 | 依据 |
|---|---|
| `jc` 由用户态传入、kbase 不解析 | `KB/include/uapi/gpu/arm/midgard/jm/mali_base_jm_kernel.h:633`、`KB/backend/gpu/mali_kbase_jm_hw.c:116-124` |
| kbase 树内无描述符布局 | `grep -rn "exception_status\|job_type\|jd_job\|job_descriptor" KB/drivers KB/include` → 0 |
| 作业类型枚举（位号 = Type 值） | `KB/mali_kbase_gpuprops.c:181-189`、`KB/hw_access/regmap/mali_kbase_regmap_jm_macros.h:228-237` |
| `END_RENDERPASS` 四条 fragment chain 语义 | `mali_base_jm_kernel.h:475-523`（`struct base_jd_fragment`）+ `mali_kbase_jm_hw.c:116-168`（OOM 状态机） |
| 页表遍历规则（4 级、8B 项、ATE/PTE 标志、物理位） | `KB/mali_kbase_defs.h:98-102,328`、`KB/mmu/mali_kbase_mmu_mode_aarch64.c:29-45,89-105,129-150` |
| AS 配置写序 | `KB/mmu/mali_kbase_mmu_hw_direct.c:346-353`（TRANSCFG→TRANSTAB→MEMATTR→UPDATE） |

### 3.2 实测级（项目既有）

| 结论 | 依据 |
|---|---|
| Fragment Job / FBD / RT 布局定案 | `wire-kbase.sh` 补丁 **16d** 注释（约 920-1170 行）；`../issue-gold15-vendor-gpu/solution.md` §12.4（`:809-830`）、§13（`:853+`） |
| 布局可用（RT 找到并填色） | `../issue-gold15-vendor-gpu/log-pass.txt`（run81-83 `B2 fill … filled=163200`） |
| RT 常为 AFBC | `../issue-gold15-vendor-gpu/solution.md` §13.2（run74："含 AFBC solid 头"） |
| 真 `jc` 形态 | §12.4（`0x5effb5d780`/`0x5effd82480`/`0x5effb1c780`） |

### 3.3 完整事实表

见 [raw/jm-descriptor-facts.md](raw/jm-descriptor-facts.md)（作业类型表、Fragment Job 64B、FBD/RT 布局、
页表编码、仍需自建的两项、仍未知的五项）。

## 4. 改动

**零代码改动**（用户指令：只出方案文档）。新增文档/证据：

```
build/doc/evidence/issue-gold15-gpu-jm-format/
├── solution.md                 # 本文件（六段式）
├── log-pass.txt                # 判据现场（round 2 的 grep/读码现场）
├── raw/jm-descriptor-facts.md  # 作业格式事实汇总（含 T1/T2/T3 判定依据）
├── raw/ddk-job-mix.md          # round 4：DDK 实测作业类型分布 + tiler heap 归属
└── raw/external-jm-structures.md # round 7：外部权威原文摘录（job 类型枚举/链头/payload/scoreboard/24 位 PC）
```

主文档同步更新：`gpu-hw-emu.md` 新增 **§2.9**，并修订 §4（G3/G4）、§6（R4）、§8（round 2/4 完成）、§0.1；
round 4 另产出 **T3 可行性矩阵** [../../gpu-hw-emu-t3.md](../../gpu-hw-emu-t3.md)（引用本目录的两份 raw）。

## 5. 验证

| 判据 | 结果 |
|---|---|
| "谁写 job chain"可确证 | ✅ uapi `base_jd_atom_v2.jc` + `select_job_chain()`：**用户态写、kbase 只转发** |
| "kbase 不含描述符布局"可确证 | ✅ 全树 grep 0 命中 |
| 作业类型编号可确证 | ✅ kbase 宏 + gpuprops 解析表；且与项目实测 Type=9(Fragment) **自洽** |
| "描述符格式已被项目解出"可确证 | ✅ `wire-kbase.sh` 16d 规格 + B2 v1 填色判据（`filled=163200`、AFBC solid 头） |
| 设备模型缺口可枚举 | ✅ 只剩 2 项确定性工作（页表遍历、chain 遍历）+ 5 项未知（详见 raw §4/§5） |
| 分档判定有据 | ✅ T1 GO / T2 GO / T3 NO-GO（Apple Silicon 宿主），依据见 raw §6 |

## 6. 遗留

1. **T3 的前置**：补 TILER/VERTEX/GEOMETRY/COMPUTE 描述符与图形状态的取证（panfrost genxml 对照 + 一次 chain dump），
   以及 shader ISA 问题的唯一出路评估（**路线 C**：宿主换同族 Mali 板 → ISA 免翻译/重编码）。
2. **T2 落地时的两处工程点**：AFBC 解 tile（RT 常为 AFBC）；页表遍历需处理 kbase 的 ATE/PTE 语义（raw §4.1）。
3. **一次可选的实测补证**（需用户授权才动代码）：现有 chain dump 探针在 16c 修正后使用 `cdh_real_jc`，
   但既有日志中未见 chain hexdump 落盘——若要"字节级"证据留档，可用一次**只加打印**的运行补齐（不改逻辑）。
4. 外部调研建议的"通读 `mali_kbase_ioctl.h`"（用户态 ABI 全量）尚未做；与 T1/T2 关系不大（kbase 已承担），
   仅对 T3 的状态还原有意义，排在描述符取证之后。
