# issue-gold15-bprime-b2-bridge-min —— B2 渲染桥最小闭环：四段管道贯通 + 两卡点定案

> 状态：**管道贯通 / 回执段待收口（2026-10-14）**，承接
> [issue-gold15-bprime-p3-preload-abi](../issue-gold15-bprime-p3-preload-abi/solution.md)（优先级 #3「B2 渲染桥」开工）
> 关联：[issue-gold15-bprime-p1-gpuecho](../issue-gold15-bprime-p1-gpuecho/solution.md)（hvc11 通道）、kraid/`(19)` 探针（shader 捕获）

---

## 1. 问题

B2 渲染桥验收门 = 「guest job chain → host 翻译渲染 → 像素回显」。本批开工最小
闭环：FS atom 提交时抽出 fragment shader + RT 元数据，经 hvc11 送 host，用
`va2spirv` + MoltenVK 渲染后把像素写回 guest RT（替代 (16d) 纯色兜底），SF 合成
上屏可见。

## 2. 根因（语义事实与架构建模）

1. **fragment shader 的真实位置**：(19) 注释定案 + run100/106 双实证——shader
   落在 **Tiler Job 的 DCD**（job 缓冲 `+0xe8` 直指 Shader Program，stage=2），
   FS atom 的 chain 是**单 type=9 job**（`next=0x0`），FS 时点链上追不到
   → 建模：**(28j) Tiler atom 提交时捕获 stage-2 shader 进缓存**（同 tgid、2s）
   + **(28h-c) payload@+0x20 / FBD 两种子 BFS 直指追逐**兜底；run106 两路均有
   命中日志（`cache-hit len=48` / `seed=0x… off=+0xe8 len=64`）。
2. **mailbox 通道**：内核 misc 设备的 `.mode` 被 Android ueventd 重置 0600
   （run102 实证 Permission denied）→ procfs 权限直达（/proc/ged 0666 先例）
   → `/proc/cdh_gpumbox` 0666。
3. **回执段语义**：helper 读记录（原子出队）→ GR01 帧经 hvc11 → host 渲染 →
   RP01 帧 → helper 写回 mailbox → 内核写回 RT（blk=2 线性逐行 / blk=12/13
   AFBC solid 头）→ 放行 job 完成；status=1（翻译失败）不写像素、走 (16d) 兜底。

## 3. 证据

- **shader 捕获双路命中**（[log-pass.txt](log-pass.txt) §1）：`cache-hit` +
  `seed=+0xe8` 两路实证，与 (19) chase 逐条吻合；
- **线格式三端对称定案**：内核 `put_be32` 序列化 ↔ helper `get_be32` ↔ host
  `struct(">4sIQIIIIIII")`，host 解析的 rt/w/h/blk 与内核 q 行逐字段一致；
- **host 真实收帧**（gpurender log）：seq=9/10/11 三条真机请求处理；
- **离线渲染自测**：frag_flat → RP01 status=0 + 256x256 RGBA，三角形内
  `ff0000ff`/外透明（[log-pass.txt](log-pass.txt) §2）；
- **失败面**（[log-fail.txt](log-fail.txt)）：0 done/30 timeout、helper 卡死与
  ueventd 权限陷阱、构建缓存复犯、TEX 语义缺口，全部留档。

## 4. 改动

- **`kernel/modules/mali_vgpu/bridge/cdh_gpumbox.c/.h`**（新）：procfs mailbox
  （read=阻塞原子出队 GR01，write=RP01 校验/写回/放行）、500ms→3s 兜底超时、
  stage-2 shader 捕获缓存（`cdh_bridge_cache_put/get`）、AFBC solid 头/线性
  最近邻缩放写回（get_user_pages_remote + kmap，保存 mm）；
- **`wire-kbase.sh`**：(28a) include/extern、(28b)(28c) hold/认领/门控/放行
  （seq→slot 表，(27m) 机制对接）、(28h) FS atom 桥接（env 候选+job 缓冲直指
  +payload/FBD BFS+缓存兜底四层）、(28j) Tiler atom 捕获、(28k) 公共提取辅助
  `cdh_b2_extract_at()`、(28i) 桥文件拷入内核树；
- **`out/tmp/p2/gpurender_host.py`**（新）：host 渲染服务（GR01→va2spirv→
  harness `--dump`→RP01），`--oneshot` 离线自测模式；
- **`out/tmp/p2/gpumbox_bridge.c`**（新）：guest 桥 helper（NDK 静态 aarch64），
  响应 poll 2.5s 超时 + 错误分支 continue 抗卡死；
- **`harness.c`**：`--dump` 像素导出模式（回归零破坏 95/95）。

## 5. 验证

1. 内核构建 v77→v80 四轮（recipe bump 教训复犯一次后定案流程）；
2. 实例 100–106 六轮实测：`/proc/cdh_gpumbox` 0666 就位、shader 双路捕获、
   30 条 q 入队判据、host 真实收帧解析全对；
3. run_matrix **95/95 全绿**（harness 改动零破坏）；va2spirv 本批零改动；
4. 回执段（done/像素写回/screencap）**未收口**——见「遗留」。

## 6. 遗留

1. **回执段闭环**（下批第一优先）：helper 保活（guest 重启循环杀死进程；
   debuggerd ptrace 冻结嫌疑）+ 写回 -EINVAL 各分支 dmesg 诊断行 +
   done 判据实证；
2. **纹理族语义缺口**（像素回显前置）：真机 UI shader = `TEX_SINGLE`/
   `VAR_TEX_BUF_SINGLE`（纹理采样）不在 160 条子集 → TEX/VAR_TEX + sampler +
   纹理描述符 ABI 最小建模（fail-loud 语义维持）；
3. 首渲染管线延迟 vs 3s 兜底窗的匹配（va2spirv 50-90ms/次，harness/MoltenVK
   冷启动待实测）；像素回显 screencap 验证（adb 窗口约束）；
4. 多 shader 精确关联（缓存单条目 MVP）归 B2 后续。
