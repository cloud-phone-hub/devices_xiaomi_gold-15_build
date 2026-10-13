# issue-gold15-bprime-p3-varying-subset —— B′ P3：片元 varying 输入 ABI + LD_VAR_BUF_IMM.f32 最小切片

> 状态：**验收门通过（2026-10-12）**，承接
> [issue-gold15-bprime-p3-float-mod-subset](../issue-gold15-bprime-p3-float-mod-subset/solution.md)（#3/#4 遗留）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

此前四批（FMA / SFU 三连 / 源修饰 neg·abs）都是**自包含 ALU 指令**：源与目的全在寄存器/
小常量文件内，无内存、无输入 ABI。而 P3 真机样本的下一高频族是**属性/插值通路**：
`LD_VAR_BUF_IMM`（片元 varying 插值载入）。

这与前几批有本质差异——**不是加一条指令，而是扩框架本身**：
- `LD_VAR_BUF_IMM` **没有普通 dest**：结果写进 **staging 寄存器区**（基址落在 bits40-45，
  语义为「从基址开始连续 N 个寄存器」），现有 `reg[]`（单值映射）需能表达连续区间；
- 需要一个**片元输入 ABI**：SPIR-V `OpVariable(Input, Location k)` + `OpLoad`；
- 顶点侧模板需**输出对应 varying**，才能让插值器把数据送进片元。

本批即：把这条通路打通并逐像素验证。按 `LD_ATTR_IMM`（顶点属性加载，属顶点路径）不同，
**本批只做片元侧 `LD_VAR_BUF_IMM.f32`**；`LD_ATTR_IMM` 单独立项。

## 2. 根因（决定性发现）

1. **编码（Mesa ISA.xml 权威）**：`LD_VAR_BUF_IMM.f32` opcode = `0x5C`（bits48-56）。
   各字段（全部由 `valhall.py` 元数据定位，**非硬编码**）：

   | 字段 | 位域 | 含义 |
   |---|---|---|
   | staging 基址 | bits40-45（flags `0x80`@47） | 写入起始寄存器，`.sr write=true` → start=40 |
   | `staging_register_count` | bits33-35（3b） | 连续寄存器数（v4→4），由 staging 操作数推导 |
   | `vector_size` | bits28-29（2b） | `none/v2/v3/v4` |
   | `source_format` | bits24-27（4b） | `src_flat32/src_flat16/src_f32/src_f16` |
   | `sample_mode` | bits38-39（2b） | `center/centroid/sample/explicit` |
   | `update_mode` | bits36-37（2b） | `store/retrieve/reserved/clobber` |
   | `slot` | bits30-32（3b） | 依赖槽 |
   | `index`（立即数） | bits16-23（8b） | varying 字节偏移 |

2. **汇编语法（Mesa 权威用例 `test/assembler-cases.txt:183-184`）**：
   `LD_VAR_BUF_IMM.f32.slotN.v4.src_f32.<sample>.<update> @rN:rN+1:rN+2:rN+3, <src>, index:0xM`。
   注意 **staging 操作数在最前**，且**连续寄存器须 rN 起、基数偶对齐**（`asm.py:214`）。

3. **varying → Location 映射**：`index` 为 varying 字节偏移，f32 vec4 占 16B，
   故 **Location = index / 16**（index=0 → Location 0）。`src_f32` 即**透视校正插值**
   （SPIR-V 默认平滑语义，无需修饰）；`sample_mode=center` 亦无对应 SPIR-V 修饰
   （只有 centroid/sample 才需 `Centroid`/`Sample`）。

4. **staging 区间语义**：`@r0:r1:r2:r3` = 从 r0 起连续 4 个寄存器接收插值后的 vec4
   → 逐分量 `OpCompositeExtract` 写回 `reg[base+i]`。基址落在 **dest 域**，
   但 `ins.dests` 为空（`assert(len(dests)==0 or not staging)`）→ 必须单独提取 staging。

5. **两阶段验证（降风险）**：先做**静态验证**（解码 + spirv-val + spirv-dis），
   不碰 harness；确认 staging→SSA 映射无误后，再扩 harness 顶点侧 varying 做**逐像素比对**。
   否则若映射有误，会被 harness 改动掩盖。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：`ins.dests` 为空导致 dest 提取拿不到 staging 基址；
  非 v4/src_f32 变体若直接 raise 会以 traceback/exit 1 呈现（非干净 fail-loud）。
- 通过面（[log-pass.txt](log-pass.txt)）：
  - 解码 `meta={'staging_base':0, 'vector_size':3(v4), 'source_format':2(src_f32),
    'sample_mode':0(center), 'update_mode':0(store), 'staging_register_count':4,
    'slot':0, 'index':0}`；
  - `spirv-val --target-env vulkan1.1` frag/vert **双 PASS**；
  - `spirv-dis` frag：`OpVariable %Input v4float`（Location 0）+ `OpLoad` + 4×
    `OpCompositeExtract` + 组装存储；vert：额外 `OpVariable %Output v4float`
    （Location 0）存常量 (0.2,0.4,0.6,1.0)；
  - **逐像素比对：256×256，三角形内 7812 px 全为 (51,102,153,255)，失败 0，exit 0**；
  - **负向对照**（错期望）→ 内部像素逐一报 `51,102,153,255`；
  - **回归**：`frag_mod`(23,255,81,255)/`frag_sfu`(180,5,177,255)/`frag_fma`(28)/`frag_flat`(255)
    均失败 0（**新顶点模板向后兼容**）；
  - **fail-loud**：v2 变体 → `错误：LD_VAR_BUF_IMM.f32 翻译失败：仅支持 v4`，**exit 2**。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. `SUPPORTED` 增 `LD_VAR_BUF_IMM.f32`；
2. 新增 `decode_meta(word, ins)`：按 ISA 元数据提取 **staging 基址 + 连续数 + 修饰字域 + index**，
   `decode_word` 返回值扩为 5 元组（增 `meta`）；
3. `FragBuilder`：新增 `t_in`（Input 指针类型）、`in_vars`（location→变量 id）、
   `input_var(loc)`（按需建 `OpVariable(Input)` + `Location` 修饰）、
   `emit_varying(meta)`（`OpLoad` → 逐分量 `OpCompositeExtract` → 写 `reg[base+i]`）；
   `build()` 的 `OpEntryPoint` 追加输入变量列表；
4. `build_vert_template()`：新增 **Location 0 常量 varying 输出**（vec4 = 0.2,0.4,0.6,1.0）；
5. `main()`：分派 `LD_VAR_BUF_IMM.f32`，并把指令翻译包进 `try/except ValueError` →
   未建模变体干净 `exit 2`（与原 fail-loud 一致）。

**`out/tmp/p2/frag_vary.asm`（新建，2 指令/16B）**：
`LD_VAR_BUF_IMM.f32.slot0.v4.src_f32.center.store @r0:r1:r2:r3, r61, index:0x0` + `NOP.end`。
因 P2 约定 r0..r3 = 片元输出，故输出即该 varying。

产物指纹：`frag_vary.bin` 16B md5 `64b88d53ba10a85631639d00419c74f3`；
`frag_vary.spv` 436B md5 `74f3e94b9ae79f6391cab5c0d6418ae4`；
`vert_vary.spv` 516B md5 `174f595386ace6dc63c67a32364595c0`。

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| Mesa `asm.py` 汇编 | `frag_vary.bin` 16B（指令字 `0x005c80083200003d`） |
| `va2spirv.py` 解码 | `meta={staging_base:0, vector_size:3, source_format:2, sample_mode:0, update_mode:0, staging_register_count:4, index:0}` |
| `spirv-val` vulkan1.1 | frag **PASS** / vert **PASS** |
| `spirv-dis`（frag） | `OpVariable %Input v4float`（Location 0）→ `OpLoad` → 4×`OpCompositeExtract` → `OpStore %Output` |
| `spirv-dis`（vert） | 额外 `%Output v4float`（Location 0）存 `(0.2,0.4,0.6,1.0)` |
| **MoltenVK 逐像素比对** | 三角形内 **7812 px 全为 (51,102,153,255)**、外 0 px、**失败 0 / exit 0** |
| 负向对照（错期望 255/0/0/255） | 内部像素逐一报 `51,102,153,255` → 证明输出确为该 varying |
| 回归 `frag_mod`/`sfu`/`fma`/`flat` | 均失败 0 → 新顶点模板向后兼容 |
| fail-loud（v2 变体） | `错误：仅支持 v4` **exit 2** |

结论：**片元 varying 输入通路（vertex varying 输出 → 插值器 → LD_VAR_BUF_IMM 载入 → 片元输出）
端到端语义正确**，在 Metal 上逐像素可证。

## 6. 遗留

1. **仅覆盖 v4 + src_f32 + center + store**：其它 `vector_size`（v2/v3）、
   `source_format`（src_flat32/f16）、`sample_mode`（centroid/sample）、
   `update_mode`（retrieve/clobber）均 fail-loud，待逐一放开。
2. **多 Location / 非对齐 index**：本批要求 index 按 Location（16B）对齐；
   非对齐（同一 Location 内的分量偏移）与多重 varying 待扩。
3. **`LD_ATTR_IMM`（顶点属性加载）单独立项**：属顶点路径（源为 Vertex/Instance ID + 属性表），
   ABI 与 varying 不同，混做会放大复杂度。
4. **真机样本对齐**：需核对真机 `LD_VAR_BUF_IMM` 的 sample/update/slot 组合分布，
   决定优先放开顺序。
5. **swizzle/widen 源修饰**（承接上批遗留）与 **FAU 源**仍待建模。