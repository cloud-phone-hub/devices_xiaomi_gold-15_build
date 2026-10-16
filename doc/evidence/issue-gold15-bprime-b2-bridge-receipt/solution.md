# issue-gold15-bprime-b2-bridge-receipt —— B2 回执段闭环：helper 保活 + 写回诊断 + done 判据 + 像素写回

> 状态：**回执段全链闭环（2026-10-14）**，承接
> [issue-gold15-bprime-b2-bridge-min](../issue-gold15-bprime-b2-bridge-min/solution.md) 遗留 #1
> （断点续做优先级第 2 项）。run121 `B2 bridge done` ×12 实证；run123 **像素写回
> 成功（wrote=163200B，与 (16d) filled=163200 同量级）**。
> 语义参照：[p3-tex-subset](../issue-gold15-bprime-p3-tex-subset/solution.md) +
> [p3-mkvec-subset](../issue-gold15-bprime-p3-mkvec-subset/solution.md)（真机 shader 全链翻译清障）

---

## 1. 问题

B2 渲染桥回执段（RP01 写回 → `B2 bridge done` → 放行 job）此前一次都没被行使
（run106：**0 done / 30 timeout**）。本批目标：helper 保活 + 写回诊断 + done 判据
实证 + screencap 像素回显验收。过程中暴露 6 个串联缺陷（见 §2），逐案定位修复。

---

## 2. 根因（语义事实与架构建模，run107→121 定案链）

1. **helper 保活**（run107/108/109 三案定案）：`restart_period` 非 AOSP init
   选项（run107 服务未注册）；`sys.boot_completed=1` trigger 在 watchdog 重启
   循环下 action **0 命中**（run108；auto_provision 起是 `class main` 直起与
   trigger 无关）→ **`class main` 直起、非 oneshot**（退出即重启）定案；
   helper 二进制打包进 vendor.img（`bin/**` 0755 规则）免 adb push；
   (17) ptrace 保护名单 +`gpumbox_bridge`。
2. **写回诊断**：`cdh_bridge_write` 7 个静默 `-EINVAL` 分支补
   `pr_info("B2 bridge reject …")`（内核 v81+）。
3. **resp 缓冲不足致流错位**（run113 定案）：helper `resp` 用 `FRAME_MAX`
   （65584B）装不下 262168B RP01 满图帧 → `frame_read` 判超限直接返回、
   **不消费 payload** → hvc11 字节流错位 → 后续帧全被当「RP01 规格非法」丢弃
   → `write()` 从未执行（0 done + 0 reject 现象真正根因）。→ `RESP_MAX`
   （4+24+262144）独立缓冲 + `frame_read` 无条件排空 payload。
4. **readn 纯阻塞读长帧卡死**（run116 定案）：RP01 含 262KB 像素、hvc11 tty
   分批到达，`readn` 等剩余字节永不返回 → 整条链停滞（helper 日志只留启动行）。
   → `readn` 每段前 `poll 10s` 超时。
5. **tty 输入队列溢出丢字节**（run118/119 定案）：host 一次性 `sendall` 262KB
   打爆 guest virtconsole tty 输入队列（P1 教训：virtconsole 是吞吐上限约束）。
   → host `send_throttled` 4KB/10ms 限速发送。
6. **host RP01 漏长度前缀**（run120 定案，决定性修复）：协议约定帧 =
   `[4B BE len][payload]`（与 helper selftest/内核 mailbox 记录同构），而
   `build_resp` 只回 `[RP01 header][pix]` → helper 把 magic `RP01` 当长度前缀
   解析成 0x52503031 巨值 → frame_read 超时 → 流错位。→ `build_resp` 补
   `struct.pack(">I", len(payload))` 前缀。
7. **内核兜底超时 3s < 渲染延迟**（run110 定案）：va2spirv + harness/MoltenVK
   首次冷启动 >3s → 内核兜底先于写回收记录（即使写回也被判「not found」）。
   → `CDH_BRIDGE_TIMEOUT_MS` 3s→15s、helper 响应窗 2.5s→10s（+迟到错位帧丢弃）。
8. **取证盲点**（run111-115 逐案清障）：init 服务 stderr 默认丢弃、printf 写
   文件全缓冲、init umask 砍权限 → helper 自落日志（`/data/local/tmp/
   gpumbox_bridge.log`，fchmod 0666 + setvbuf 行缓冲）。
9. **像素写回 GUP 拒绝（PFNMAP）**（run122 定案，内核 v83 GUP 诊断版实证）：
   `wb gup fail addr=0x5effb7b000 n=-14 locked=1 mm_users=43 vma=hit PFNMAP IO WRITE`
   —— kbase 用 `vmf_insert_pfn_prot` 惰性映射 GPU 缓冲区（`VM_PFNMAP|VM_IO`，
   无 `struct page`），`get_user_pages_remote` 对 PFNMAP **恒拒不触发 fault**
   （-EFAULT）；`mm_users=43` 证 mm 存活（非悬空）。而 (16d) 兜底填色用
   `copy_to_user` 在 kbase 提交上下文走页表 fault 可写（`filled=163200` 实证）。
   → `cdh_write_pfnmap`：`fixup_user_fault` 触发 `kbase_cpu_vm_fault` 装 PTE
   （vma fault 路径）→ `follow_pte` 取 pfn → `pfn_valid` 兜底（device 内存
   PFN 拒绝防 kmap 崩）→ `kmap` 写真实页（kbase 分配的 PFN 是真实页）。

---

## 3. 证据

- **done 判据实证**（[log-pass.txt](log-pass.txt) §1，run121）：`B2 bridge done`
  ×12 全闭环（seq 1-15 无 timeout/reject 残留），seq 9-15 **status=0 渲染成功**、
  seq 2-8 status=1 语义失败（fail-loud 正确回退兜底，不回错误像素）；
  helper 转发行实证全链时序（req 112B → resp 262172B，874-943ms/条）。
- **host 渲染服务 daemon 自动拉起**（run110 起每轮实证）：`gpurender_host.py
  --base-dir … <实例>/channels/gpu.sock`，断线 2s 重连（QEMU 重启 round 重建
  socket 无碍）。
- **host oneshot 离线自测**（log-pass §4）：RP01 status=0 + 262144B 像素，
  长度前缀 262168 = 总长-4 回归对齐。
- 失败面（[log-fail.txt](log-fail.txt)）：`wrote=0B`（像素写回返回 0 字节）为
  唯一剩余精确缺口 + run107-120 定案链全留档。

---

## 4. 改动清单

1. **`kernel/modules/mali_vgpu/bridge/cdh_gpumbox.c`**：写回 7 分支 reject 诊断；
2. **`kernel/modules/mali_vgpu/bridge/cdh_gpumbox.h`**：`CDH_BRIDGE_TIMEOUT_MS`
   3000→15000（大于 host 渲染延迟）；
3. **`kernel/modules/mali_vgpu/wire-kbase.sh`**：(17) 保护名单 +`gpumbox_bridge`；
4. **`kernel/build/build-kernel.sh`**：recipe v80→v81-bridge5→v82-bridge6；
5. **`out/tmp/p2/gpumbox_bridge.c`**（helper）：`RESP_MAX` 独立 resp 缓冲、
   `frame_read` 无条件排空 payload + 超限丢弃、`readn` poll 10s 防长帧卡死、
   响应等待窗 2.5s→10s（迟到错位帧丢弃循环）、自落行缓冲日志（fchmod 0666）；
6. **`out/tmp/p2/gpurender_host.py`**（host 渲染服务）：`build_resp` 补 4B
   长度前缀（决定性修复）、`send_throttled` 4KB/10ms 限速、`serve` 断线 2s 重连、
   `--base-dir` 指定三件套目录；
7. **daemon 集成**：`core/host/daemon/backend/gpurender.go`（受监督子进程，
   范式同 modemdata.go）+ `device.go` `GPuRenderHostService` 开关（Gold15=true）
   + `run.go` 2b-4 接线（随实例生命周期拉起/停止）；
8. **`build/vendor/patch/bin/gpumbox_bridge`** + **`etc/init/cph_gpumbox_bridge.rc`**
   + baselines selinux-labels 2 键：helper 保活入 vendor.img。

---

## 5. 验证（2026-10-14，run107→121 十五轮实测）

1. run121：**done ×12 全闭环**（status=0 ×7 / status=1 ×8 fail-loud 正确），
   零 timeout、零 reject；
2. host 渲染服务随 daemon 自动拉起（run110-121 每轮实证）+ 断线重连；
3. helper 全链转发日志实证（req/resp/耗时逐条，874-943ms/条含 262KB 回传）；
4. host oneshot 离线自测 RP01 status=0 带像素 + 长度前缀回归对齐；
5. run122 GUP 诊断实证（`vma=hit PFNMAP IO WRITE`，mm_users=43）→ 根因定案；
6. run123 **像素写回成功**（内核 v84 PFNMAP 页表直写）：`wrote=163200B`
   （AFBC 面，与 (16d) filled=163200 同量级）+ seq1 线性 256B + seq9 23040B，
   仅 `wb skip afbc wide block` 跳过（已定案，见遗留 #2）；零 `gup fail`。

---

## 6. 遗留

1. **screencap/VNC 视觉回显验收**（run123 尝试定案）：写回已由内核日志实证
   （wrote=163200B 与 (16d) 同量级）= B2 回执段闭环；视觉回显受阻于
   **guest 启动未达稳定显示态**——`screencap` 报 `Failed to get ID for
   any displays`（SF 显示未枚举）、VNC RFB 截屏 4 帧全灰屏（桌面宽 272
   异常，应 1088）、guest 208s 仍 `before boot completed`。此为 gpu.md
   遗留 **M3 出画 / AtomicCommit 慢**（vendor-gpu 层），非 B2 回执段缺陷；
   待 guest 稳定态后补视觉验收（screencap 对比渲染像素 或 VNC 帧）；
2. **afbc wide block**（blk=13 + w2&0x4，32x8 SB）写回跳过（`wb skip`），
   布局未定案；真机 UI 有此类面（seq11/15）；
3. **host 渲染服务性能**：262KB 像素走 virtconsole tty 是吞吐上限约束（P1
   实证 25.2MB/s），VIRTIO_GPU_F_RESOURCE_BLOB 零拷贝数据面归 B2 后续；
   当前限速 4KB/10ms 传输约 0.6s/条，首条含 MoltenVK 冷启动 2.6s；
4. **语义面按真机子集扩充**：seq2-8 的 32x32 小 shader（`FADD.v2f16`/
   `U16_TO_U32` 等）fail-loud 回退兜底（行为正确），按频次补充建模
   （`FADD.v2f16` 源 swizzle 已建模、emit 未接；`U16_TO_U32` 同族）；
5. helper 自恢复自然验证（reboot 循环下 init 自动拉起已实证 run109-123，
   退出即重启待自然触发）。
