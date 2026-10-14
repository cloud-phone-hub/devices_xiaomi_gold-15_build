# issue-gold15-bprime-p3-tex-subset —— B′ P3：纹理族指令最小建模（`TEX_SINGLE` / `VAR_TEX_BUF_SINGLE`）与真机 UI shader 像素回显前置语义

> 状态：**验收门通过（2026-10-14）**，承接
> [issue-gold15-bprime-b2-bridge-min](../issue-gold15-bprime-b2-bridge-min/solution.md)（B2 渲染桥 GR01→RP01 像素回显）
> 语义参照：kraid（`deps/mesa/src/panfrost/compiler/kraid/`，只读）+ mesa `valhall/ISA.xml` 双源核对

---

## 1. 问题

真机 UI shader（B2 渲染桥 run106 捕获的 48B/64B fragment shader）翻译被
fail-loud 拦截：`错误：不支持的指令 TEX_SINGLE（P2 子集外）` /
`VAR_TEX_BUF_SINGLE`——纹理采样是「像素回显」的前置语义缺口，不建模则
真机 UI 纹理绘制无法出像素。要求 MVP 最小建模（超出面 fail-loud）：
TEX_SINGLE 采样写 texel、VAR_TEX_BUF_SINGLE 融合（LD_VAR v2 + TEX）、
harness 2x2 测试纹理 + 四象限逐像素比对、负向对照与 fail-loud 用例矩阵。

---

## 2. 根因（语义事实与架构建模）

1. **编码布局（mesa ISA.xml:3700/3812 + kraid isa-v9-v14 字段表双源核对）**：
   - `TEX_SINGLE`（`opcode 0x128@48/mask 0x1FF`，unit=T）：`<sr write flags=false>`
     写基址 **bits16-21**（texel 目标）、`<sr read flags=false>` 读基址
     **bits40-45**（坐标）——kraid `staging_register_write_index@16` /
     `staging_register_index@40` 逐位吻合；src0=64 位句柄@0；write_mask@22/4、
     dimension@28/2、slot@30/3、sr_count@33/3、sr_write_count@36/3、skip@39、
     register_width@46/1、lod_mode@13/3、shadow@12、array_enable@10、
     texel_offset@11、wide_indices@8。
   - `VAR_TEX_BUF_SINGLE`（`0x130@48`，unit=VT）：sr 写基址 **bits40-45**、
     src0=句柄@0、**src2=varying offset@16**（64 位源占两槽位，后继源起点顺延
     ——kraid `src2@16` 实证；既有 decode 用 `i*8` 会错位，已改按 ISA 元数据
     `Source.start` 解码）、sample_and_update@33/3、vartex_register_width@24/2。
2. **坐标与结果形态**：坐标恒 staging 读 v2f32（sr_count==2，2D）；texel
   regfmt 由 register_width/vartex_register_width 定（32=f32 一分量一寄存器；
   16=4×f16 打包两分量一寄存器）——写寄存器数 = ceil(comps/宽度)，与 kraid
   `sr_write_count=(countones(write_mask)-1)>>(width^1)` 表达式一致（真机
   4×f16→2 regs 实测吻合）。
3. **双源冲突取舍**：mesa `register_type@26/2` 与 kraid `bits26-27=ignore`
   冲突；真机字实测该域=3（mesa 枚举语义为 s，明显失真）→ 取 kraid（语义
   权威）按 ignore 处理，regfmt 判定只看宽度位。
4. **MVP 语义**：单一 combined image sampler（`OpTypeSampledImage` = 2D
   `OpTypeImage` + `OpTypeSampler`，UniformConstant，DescriptorSet 0
   **Binding 3**，harness 约定 0=SSBO/3=纹理）+ `OpImageSampleImplicitLod`
   真采样（**禁止折叠常量**）；句柄源值域不参与寻址（诚实近似，真纹理数据面
   归 B2）。`sample_and_update` store 系（center/centroid/sample_store）
   复用 LD_VAR 底座 sample_mode 0/1/2，explicit/clobber/retrieve fail-loud。
5. **UV 坐标来源**：顶点模板按片元 TEX 坐标的 varying 来源位点（LD_VAR 直载
   登记 `varying_regs`，覆盖写即失效；VAR_TEX 取 offset 位点）输出 UV varying
   `uv=(pos.xy+1)*0.5`；真机 UI shader 坐标另有形态（r59=PositionXY 拆半字 +
   FAU 常量线性变换，见 tex_real 字段实测），不依赖 varying，预置寄存器 ABI
   已建模可直译。
6. **译码细节全部 ISA 元数据驱动**（valhall.py start/mask），不硬编码猜测；
   kraid 补证的 mesa 未列位域（bit9 projection、bit11 source_format、
   bit47/bits46-47 staging 控制）同样纳入 fail-loud 校验。

---

## 3. 证据

- **真机字命中**（此前误判"找不到"，实为扫描口径问题）：qemu.log P3 code
  dump 代码区确实 0 命中（数据区 16896 个巧合字非指令），但
  `runtime/gold-15/106/channels/gpu.sock.log` 存有 **run106 B2 桥 GR01 帧原始
  字节**：seq=9/10（64B）字 [06] `0x492800941fc02086` = **TEX_SINGLE**、
  seq=11（48B）字 [00] `0x093082941cc02082` = **VAR_TEX_BUF_SINGLE**（BLEND
  字 `0x787f4004333c00f0` 与既有 blend 取证 100% 逐位一致，字节序互证）。
  已存 `out/tmp/p2/tex_real.bin`（64B）/ `vartex_real.bin`（48B）+ 来源注释
  `tex_real.asm`。
- **字段实测**：TEX——dimension=2d、lod=computed、write_mask=rgba、
  sr_count=2、register_width=16（4×f16→2 regs）、bits26-27=3(ignore 位)；
  VAR_TEX——sample_and_update=sample_store、vartex_register_width=16、
  varying offset=LUT 0、写基址 r2。
- **SPIR-V 编号 spirv-as 逐值实证**（禁记忆表）：OpTypeImage=25 /
  OpTypeSampler=26 / OpTypeSampledImage=27 / OpImageSampleImplicitLod=87 /
  UniformConstant=0 / Dim2D=1 / ImageFormat Unknown=0；
  DescriptorSet=34/Binding=33 与既有常量一致。
- 失败面（[log-fail.txt](log-fail.txt)）：10 个 fl_* 用例 exit 2 精准拦截；
  负向对照 tex_neg 四象限全部取错邻色（7812 px 全差异，exit 1）。
- 通过面（[log-pass.txt](log-pass.txt)）：tex/vartex 四象限 7812 px 失败 0；
  tex_real 翻译 exit 0 + spirv-val PASS；GR01 oneshot RP01 status=0 带
  262144B 像素（48B/64B 双形态）；run_matrix **109/109**、run_e2e **10/10**。

---

## 4. 改动清单

1. **`out/tmp/p2/va2spirv.py`**：
   - `decode_word` 源解码按 `Source.start`（64 位源占双槽位，修 VAR_TEX/BLEND
     src 错位）；`decode_meta` 改按 `ins.modifiers` 全量解析 + `staging_bases`；
   - 纹理族：`_tex_block`（combined image sampler DS0/B3）/ `_sample_tex` /
     `_tex_common_checks` / `_tex_regfmt` / `_write_texel`（f32 逐分量、f16
     两两打包）/ `emit_tex_single` / `emit_vartex_buf_single`；
   - `_load_varying_vec` 抽出（LD_VAR_BUF_IMM 与 VAR_TEX 共用底座）；
   - `IADD_IMM.i32` LUT 源常量折叠（VAR_TEX 常量 varying offset 经寄存器
     传递的编译期判定）；
   - TEX 坐标来源追踪（`varying_regs`/`uv_locs`）+ `build_vert_template`
     UV varying 输出（uv=(pos.xy+1)*0.5，仅坐标来源位点，其余常量镜像零变化）。
2. **`out/tmp/p2/harness.c`**：2x2 RGBA 测试纹理（左上红/右上绿/左下蓝/右下白，
   Nearest+CLAMP_TO_EDGE，staging 上传 + 布局转换）、descriptor binding 3
   combined image sampler、可选 `--quad` 16 值四象限期望模式（既有 3 种调用
   形态零变化，`--dump` 保持）。
3. **`out/tmp/p2/run_matrix.py`**：QUAD_COLORS/QUAD_POS/QUAD_NEG 四象限
   dispatch、FAIL_LOUD +9、REAL_BIN 真机字用例（tex_real 进 VAL_ONLY）。
4. **测试件**：`frag_tex.asm`（TEX_SINGLE 四象限正例）/`frag_vartex.asm`
   （融合正例）/`frag_tex_neg.asm`（u'=1-u 翻转负向对照）/ fl_tex_dim、
   fl_tex_shadow、fl_tex_lod、fl_tex_arr、fl_tex_off、fl_tex_wide、
   fl_tex_v4coord、fl_vartex_su、fl_vartex_explicit、fl_vartex_off；
   `tex_real.bin`/`vartex_real.bin` + `tex_real.asm` 来源注释。
5. **`fl_cmp_csrc.asm`**：IADD_IMM 常量折叠后原"非常量 C 源"前提失效（折叠后
   确为常量寄存器），改为真正非常量 C 源（FAU u0），fail-loud 覆盖意图不变。

---

## 5. 验证（2026-10-14 全跑）

1. `python3 run_matrix.py` → **109 PASS / 0 FAIL**（基线 95/95 零破坏 +
   新增 14：tex/vartex/tex_neg/tex_real + 10 fl_*）；
2. `bash run_e2e.sh` → **10/10** 可译 + val PASS（零回退）；
3. 四象限正例逐像素失败 **0**（各 7812 px），负向对照 tex_neg **7812 px
   全差异**（exit 1，按预期失败）；
4. 真机字 `python3 va2spirv.py tex_real.bin …` **exit 0** + spirv-val
   --target-env vulkan1.2 **PASS**（run_matrix VAL_ONLY 常驻回归）；
5. `python3 gpurender_host.py --oneshot` 复现 run106 GR01（seq=10 64B /
   seq=11 48B）→ **RP01 status=0 + 262144B 像素**，dump 抽样四象限四色全对；
6. 纹理族全部新产物（rg_tex*/rg_vartex*/rg_tex_neg*/rg_tex_real*/out_tex_real*
   frag+vert 共 10 个 spv）spirv-val vulkan1.2 **全 PASS**。

---

## 6. 遗留

1. **vartex_real.bin 全链未通**：VAR_TEX_BUF_SINGLE 本身已可直译，卡在后段
   `MKVEC.v2i16`（向量构造族未建模）——像素回显下一缺口候选；
2. **真纹理数据面**：harness 2x2 测试纹理为诚实近似占位，真机 UI 纹理内容/
   句柄寻址（FAU/描述符表）归 B2 后续；句柄源在单一 sampler 模型下不参与寻址；
3. **regfmt 双源冲突**：mesa `register_type@26/2` 按 kraid ignore 处理（真机
   实测=3），若后续权威资料给出语义再收紧；
4. **双格式宽度 fail-loud 面未入矩阵**：vartex_register_width=16.32/32.32
   拦截在 `_tex_regfmt`，但枚举名含点号无法经 asm.py 修饰符语法构造（不入
   run_matrix，代码面已拦）；
5. **UV 近似**：tex 程序坐标来源位点输出 UV varying；混合 shader（颜色
   varying + 纹理坐标并存）的逐位点区分靠 `varying_regs` 追踪，坐标经算术
   改写但同一坐标对内至少一寄存器直载时可定位，跨指令链间接形态待复核；
6. **sample_and_update 放宽说明**：任务面原按"非默认 fail-loud"，但真机字为
   sample_store 且验证门要求真机字 exit 0 → 按 store 系（0/1/2，复用 LD_VAR
   sample_mode）建模，explicit/clobber/retrieve 仍 fail-loud。
