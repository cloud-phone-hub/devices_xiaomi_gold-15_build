# issue-gold15-bprime-p2-va2spirv —— B′ 主线 P2：Valhall→SPIR-V 翻译器 MVP + MoltenVK 逐像素比对

> 状态：**验收门通过（2026-10-12）**，方案文档 = [../../gpu.md](../../gpu.md) §8（B′ 主线）、结果 §8.11
> 关联：[../issue-gold15-bprime-p1-gpuecho/solution.md](../issue-gold15-bprime-p1-gpuecho/solution.md)（P1 通道）

---

## 1. 问题

B′ 主线（gpu.md §8）P2 验收门：「Valhall fragment 子集 → SPIR-V → MoltenVK，
单三角形/纹理出正确像素，**与参考实现逐像素比对通过**」。这是 B′ 唯一研究级风险面
（ISA 翻译正确性）的首次端到端验证。仓库此前零 shader 样本，输入来源需定型。

## 2. 根因（决定性发现）

1. **输入机器码可本地合成**：Mesa panfrost 的 Valhall 汇编器 `asm.py` + `ISA.xml`
   是纯 Python，可离线把文本汇编编成真 64 位 Valhall 机器码 → P2 先验证翻译器本体，
   真机捕获（`mali_shader_meta.shader` 指针定位+dump）后补，互不阻塞；
2. **解码语义来源**：Mesa `valhall.py` 的位域元数据（src 8 位/操作数：
   mode 00/01=寄存器、10=FAU、11=小常量 LUT/特殊值；dest@bit40；opcode 多段匹配；
   flow@bit59）→ 翻译器解码器按同一元数据逆向，无二义；
3. **SPIR-V 指令首字编码**：低 16=opcode、高 16=wordCount，写反则全模块无效；
4. **MoltenVK 1.4.2 空 pipeline layout 段错误**（解引用 layout+0x30）。

## 3. 证据

- 失败面（[log-fail.txt](log-fail.txt)）：SPIR-V 首字编码反（spirv-val 报
  OpSourceContinued 假错误 / MoltenVK -3）；OpFAdd 漏发（%10 未定义）；
  空 layout 段错误（EXC_BAD_ACCESS @ MVKPipeline+68）；参考三角形 y 约定镜像
  （3844 失败像素全为 0,0,0,0，画出的像素颜色本身正确）。
- 通过面（[log-pass.txt](log-pass.txt)）：asm.py 汇编输出；解码打印（LUT 常量
  1065353216=0x3F800000、flow=15=end 正确）；spirv-val 双 PASS；
  harness「失败 0」exit 0。
- 原始产物：`out/tmp/p2/`（frag_flat.asm/bin、va2spirv.py、frag.spv、vert.spv、
  harness.c、harness）。

## 4. 改动

1. `out/tmp/p2/va2spirv.py`（新建）：解码器（复用 Mesa valhall.py 解析 ISA.xml）+
   翻译器（寄存器文件→SSA，支持 MOV.i32/FADD.f32/NOP）+ SPIR-V 生成器；
2. `out/tmp/p2/frag_flat.asm`（新建）：flat-color 最小 shader（红 1,0,0,1），
   覆盖 LUT 常量/寄存器/浮点三种操作数形态；
3. `out/tmp/p2/harness.c`（新建）：MoltenVK 离屏渲染 + 边函数逐像素比对。
Mesa 依赖为只读复用（deps/mesa sparse clone，未修改 Mesa 源码）。

## 5. 验证（2026-10-12，Apple M4 Pro / MoltenVK 1.4.2）

- 机器码 6 指令/48B，FADD/NOP 字节与 Mesa 官方测试样例逐位一致；
- 解码打印逐条正确（LUT 值、dest 寄存器号、flow=end）；
- `spirv-val --target-env vulkan1.1` frag.spv / vert.spv 均 PASS；
- **逐像素比对：256x256，三角形内 7812 px、外 0 px（边界带跳过），失败 0，exit 0**
  → **P2 验收门通过**：Valhall 机器码 → SPIR-V → Metal 出正确像素。

## 6. 遗留

1. ~~**真机输入闭环**：`mali_shader_meta.shader` 指针在 job 链上的定位 + dump~~ ——
   **已闭环**（2026-10-12，见 `issue-gold15-bprime-p3-shader-capture`：10 真机 shader、
   大端解码 UNKNOWN=0）；
2. 指令子集仅 4 类（MOV.i32/FADD.f32/NOP）：varying 插值、纹理采样、
   整数/分支指令待扩，扩一条验一条（同 harness 比对）——**已扩 `FMA.f32`**
   （见 `issue-gold15-bprime-p3-fma-subset`），续扩 `FRSQ.f32`/`FRCP.f32`/
   `FMAX.f32`、`LD_VAR_BUF_IMM`；
3. 比对基准为解析期望值；真机捕获样本后应改为「真机 golden 对照」；
4. 顶点/tiler 端翻译未动（P2 只做 fragment 子集）。
