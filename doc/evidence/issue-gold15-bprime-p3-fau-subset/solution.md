# issue-gold15-bprime-p3-fau-subset —— B′ P3：FAU 源建模（uniform 常量 → PushConstant + 特殊值）

> 状态：**验收门通过（2026-10-13）**，承接
> [issue-gold15-bprime-p3-widen-subset](../issue-gold15-bprime-p3-widen-subset/solution.md)（遗留 #1）
> 关联：P3 真机样本 [issue-gold15-bprime-p3-shader-capture](../issue-gold15-bprime-p3-shader-capture/solution.md)

---

## 1. 问题

widen 批遗留 #1：**FAU 源是真机覆盖率最大单点障碍——17 条拦路（含 6 条带
widen 的 FMA）**，占 160 真机字的 1/9。FAU（Fixed Attribute Unit）供给 uniform
常量 u0..u127 与特殊寄存器（warp_id/lane_id/各类指针…），MVP 翻译器此前对
`fau`/`fau_special` 一律 fail-loud（"暂不支持操作数形态"），导致真机 shader 大片
不可直译。本批建模两类 FAU 源，并补回解码丢失的**字选择 wsel**（src 字节 bit0）。

## 2. 根因（语义事实，实证钉死）

1. **FAU 源分两类**（decode_src 已区分）：`Operand("fau", slot, page)` =
   uniform 常量 u0..u127（src 字节 mode=10）；`Operand("fau_special", idx, page)`
   = 特殊值（mode=11，编码同 LUT 32+i 起、bit0 留给 wsel）。
2. **src 字节 bit0 = 字选择 wsel**（0=w0 低字 / 1=w1 高字）——decode_src 原先
   `(byte>>1)&0x1F` 取 slot 时把它丢了；asm.py 的 `w0`/`w1` 语法修饰即编码到该位
   （`if mod == 'w1': encoded_src |= 0x1`）。
3. **`swizzle` 字段（FMA src0@28/src1@26/src2@24）对 f32 指令的任何源（含 FAU）
   = Widen 枚举**（none/h0/h1/reserved）：取所选 32 位字的低/高 16 位按 f16
   解释加宽 f32——widen 批的 `_widen_half()` 扩展到 fau/fau_special 即生效。
4. **uN 条目 = 64 位**：字索引 = N*2+wsel（N = page*32+slot，u0..u127 → 256 字）。
   MVP ABI：映射 **PushConstant 块 u32[256]**（1KB），序 = 字索引；
   特殊值 page0 `warp_id`、page3 `lane_id`/`core_id` → 常量 0（**MVP 单 lane/单
   warp 假设，非真机值**）；**其余特殊值**（thread_local_pointer /
   workgroup_local_pointer / resource_table_pointer / shader_output /
   program_counter / framebuffer_size / atest_datum / sample /
   blend_descriptor_*、以及值为 "reserved" 的项）**一律 fail-loud**。
5. **int 语境**（IADD/ICMP/MOV 等）的 wsel 同样按字选择取（uN.w1 = 高字）；
   `swizzle`（h0/h1）对 int 读仍 fail-loud（既有 src_mod_mask 机制，未动）。
6. **SPIR-V 版本门**（实施中发现）：SPIR-V ≤1.3 禁止 PushConstant 变量进
   `OpEntryPoint` 接口，**1.4 起强制列入全部全局变量**——模块版本升 1.4，
   spirv-val 校验目标同步 vulkan1.1 → vulkan1.2。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：wsel 位丢失（decode_src 遮蔽 bit0）；
  va2spirv 与 asm.py 同进程跑撞 valhall ISA 全局重复解析断言 → **回环必须分进程**；
  真机字形 `FMA(u0.h1, u1.w1, 0)` 双 FAU 槽超 asm.py/硬件非消息指令限额
  （"Too many FAU slots"）→ MOV 源拆解；`fl_cmp_fau` 的 `u2.w1` 实为 uniform
  （mode=10，此前误读作 WARP_ID 特殊值），建模后不再 fail-loud → 改指真正
  未建模的 `framebuffer_size`；PushConstant 进接口撞 SPIR-V 1.3 校验规则。
- 通过面（[log-pass.txt](log-pass.txt)）：**回归矩阵 39/39**（26 渲染正例含 fau +
  2 对照 + 11 fail-loud）；frag_fau 逐像素 (255,128,26,255) 失败 0；负向对照
  逐像素实证 255,128,26,255；spirv-dis 见 PushConstant 块 + OpAccessChain/OpLoad
  + widen 链（OpUConvert→OpBitcast→OpFConvert）；真机 6 条 FMA（B 源 fau）
  **asm.py 回环 6/6 逐位命中**（含 wsel=1 与 h0/h1 组合）。

## 4. 改动

**`out/tmp/p2/va2spirv.py`（改）**：
1. 常量块：`OpTypeArray=28`、`OpTypeStruct=30`、`OpAccessChain=65`、
   `OpMemberDecorate=72`、`DEC_BLOCK=2`、`DEC_ARRAYSTRIDE=6`、`DEC_OFFSET=35`、
   `SC_PUSH=9`；`SPV_VERSION=0x00010400`（SPIR-V 1.4）；
2. `decode_src()`：fau/fau_special 记 `wsel = byte & 1`；fau_special 经
   `ENUMS['fau_special_page_<page>'].values[idx].value` 记特殊值名（解析失败
   返回 None，用侧惰性再解）；
3. `_fau_block()`（惰性）：`%_arr = OpTypeArray %int %int_256`（ArrayStride 4）
   → `%_struct = OpTypeStruct %_arr`（Block + 成员 Offset 0）→
   `OpTypePointer PushConstant %_struct` → `OpVariable`；
   `_fau_word()`：`OpAccessChain(成员0, 字索引) → OpLoad i32`；`_fau_bits()`：
   字索引 = (page*32+slot)*2+wsel；特殊值 warp_id/lane_id/core_id → 常量 0，
   其余 fail-loud；
4. `val_to_ssa()`/`_widen_half()`/`emit_mov()`：fau/fau_special 取值后走既有
   通道（float 语境 OpBitcast；widen 语境走 f16 通路；int 语境直读）；
5. `build()`：`var_fau` 加入 OpEntryPoint 接口序列（与 var_out/in_vars 并列，
   未用到不创建）。

**`out/tmp/p2/frag_fau.asm`（新建）**：u1.w0 直读（r0=255）、u0.h0 widen
（r1=128）、u0.h1 × u1.w1（r2=26）、常量通道（r3=255）；真机字形双槽 FMA 拆为
`MOV.i32 r5←u1.w1` + `FMA.f32 r2, u0.h1, r5, 0x0`（语义/像素不变，另覆盖整数语境
uN.w1 取高字）。
**`out/tmp/p2/fl_fau_ptr.asm`（新建）**：`thread_local_pointer.w0` → exit 2。
**`out/tmp/p2/fl_cmp_fau.asm`（改）**：B 源 `u2.w1` → `framebuffer_size.w1`
（原用例前提「u2.w1 = WARP_ID」不成立：mode=10 是 uniform；改指未建模特殊值，
保持「比较族 × FAU 特殊值 fail-loud」覆盖）。
**`out/tmp/p2/run_matrix.py`（最小增行 + 一处校验目标）**：EXPECT 加
`"fau": (255,128,26,255)`；FAIL_LOUD 加 `"fl_fau_ptr"`；spirv-val
`--target-env vulkan1.1 → vulkan1.2`（配合 SPIR-V 1.4 版本门）。
**`out/tmp/p2/harness.c`（改）**：pipeline layout 加
`VkPushConstantRange{fragment, 0, 1024}`；draw 前 `vkCmdPushConstants` 填固定
FAU 测试 ABI：`words[0]=0x3C003800(words)=u0.w0、words[1]=0x3F000000=u0.w1、
words[2]=0x3F800000=u1.w0、words[3]=0x3DCCCCCD=u1.w1`，其余 0（字索引=N*2+wsel）。

## 5. 验证（2026-10-13，Apple M4 Pro / MoltenVK 1.4.2）

| 环节 | 结果 |
|---|---|
| 回归矩阵 run_matrix.py | **39 PASS / 0 FAIL**（26 渲染正例含 fau + 2 错误对照 + 11 fail-loud 含 fl_fau_ptr） |
| frag_fau 逐像素 | 三角形内 7812 px 全 (255,128,26,255)、失败 0、exit 0 |
| 负向对照（错期望 255,0,0,255） | exit 1，逐像素打印真实值 **255,128,26,255** |
| spirv-dis | `%_arr_int_int_256 = OpTypeArray …`（ArrayStride 4）+ `OpDecorate %_struct Block` + `OpMemberDecorate … Offset 0` + `OpVariable … PushConstant`（进 OpEntryPoint 接口）+ `OpAccessChain %_ptr_PushConstant_int %21 %int_0 %int_N → OpLoad` + widen 链 `OpUConvert %ushort → OpBitcast %half → OpFConvert %float` |
| fl_fau_ptr / fl_cmp_fau | 翻译 exit 2（未建模特殊值 fail-loud） |
| 真机编码回环（分进程） | 6 条 B 源为 fau 的真机 FMA（0x00b2c20404c08a00 / 0x00b2c20008428901 / 0x00b2c50408c0813f / 0x00b2c50004458103 / 0x00b2ff0408c0807f / 0x00b2c300047f8043）asm.py 试装 **6/6 逐位命中**（`u5.h0`、`u4.w1.h1`、`u0.w1.h1/h0` 等 wsel+widen 组合全覆盖） |

**真机覆盖率（160 字全量重算，p3-decode-be.txt，同口径：decode_word + SUPPORTED
+ src_mod_mask + 源形态检查）**：

| | 可直译 | 未建模修饰 | 不支持指令 | 源形态不支持 |
|---|---|---|---|---|
| 基线（FAU 未建模） | 71 | 15 | 57 | 17 |
| 本批（FAU 建模） | **88** | 15 | 57 | **0** |

- 17 条 FAU 拦路构成：FMA×15（6 条带 widen + 9 条纯字读）、RSHIFT_AND×1、
  MOV×1——FAU 取值接进 `val_to_ssa`/`_widen_half`/`emit_mov` 通用通道后**全部
  转正**（可直译 71→88，+17），不止任务预期的 6 条 widen-FMA（预期 77/11 系
  只计 6 条转正的保守口径，实测以通用通道为准）。
- 未建模修饰 15 不变：src_mod_mask 命中 1（IMUL src0 整数字节选择 .b00）、
  flow≠none 10（wait/reconverge/discard）、比较族 condition/result_type 非默认 4。

## 6. 遗留

1. **FAU 特殊值大部分未建模**：framebuffer_size / atest_datum / sample /
   blend_descriptor_* / shader_output / program_counter / 各类指针待按需建模
   （本批一律 fail-loud，防错译）；
2. **FAU 64 位条目布局（字索引 = N*2+wsel、u32[256] PushConstant ABI）系 MVP
   假设**，待 B2 桥真机数据面复核（含 warp_id/lane_id/core_id 恒 0 的单 lane
   假设）；
3. 承接前批：`LD_VAR_BUF_IMM.f16`（src_flat16）、update_mode retrieve/clobber、
   slot 语义、非对齐 index、内存族（LOAD/STORE，33 条）、`LD_ATTR_IMM`；
4. 源侧整数字节/半字选择（IADD/ICMP 等 `widen` 字段 .b00 系）与 flow 等待/
   合流修饰仍未建模（未建模修饰 15 条）；SPIR-V 版本已升 1.4，后续如接
   MoltenVK 之外的 1.3 限值环境需回退接口策略。
