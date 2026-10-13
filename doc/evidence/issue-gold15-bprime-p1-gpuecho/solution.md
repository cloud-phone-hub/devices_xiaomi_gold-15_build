# issue-gold15-bprime-p1-gpuecho —— B′ 主线 P1：gpu 通道打通（job chain 转发 echo）+ 延迟/带宽实测

> 状态：**验收门通过（2026-10-12，run93）**，方案文档 = [../../gpu.md](../../gpu.md) §8（B′ 主线）、结果 §8.10
> 关联：[../issue-gold15-vendor-gpu/solution.md](../issue-gold15-vendor-gpu/solution.md)（§7 原形态）、[../issue-gold15-gpu-hw-emu/solution.md](../issue-gold15-gpu-hw-emu/solution.md)（GPU 仿真取证）

---

## 1. 问题

B′ 主线（[gpu.md](../../gpu.md) §8）P1 阶段要求「job chain 转发 + 回传 echo（假渲染）：
guest 提交 → host 收到完整 job chain → 内存回写可见，链路跑通，延迟/带宽实测」。
落地前有三个未决问题：

1. **通道选型**：guest⇄host 的二进制帧通道用什么器件、帧协议如何、host 侧怎么接；
2. **前置 3 项确认**（§8.9② 遗留）：darwin dist 的 vhost-user-gpu 后端二进制、
   virtio-gpu blob/零拷贝支持面、cocoa 与 GL 渲染的组合可用性；
3. **量化**：通道 RTT 与吞吐实测——决定 job chain 描述结构走此通道是否够用。

## 2. 根因（决定性发现）

1. **通道落点**：复用 dense v3 virtconsole 布局末位追加 **hvc11**（`cdh-gpuecho`）即可，
   不动既有编号。hvc6 原预留此用途，但已被宿主振动（cdh-haptic）占用 → 迁至末位。
   帧格式沿用 hvc7 事件通道的 `[4B BE 长度][payload]`。
2. **`/dev/hvcN` 是 tty 不是裸字符设备**（run89 wire log 实锤）：默认 `ICANON|ECHO`
   把二进制帧当输入行处理——NUL 被回显成 `^@`、0x17(WERASE) 触发退格序列，
   host 侧表现为 `bad frame len 1581276736`（= 0x5E405E40 `^@^@`）。
   **guest 侧 open 后必须 `cfmakeraw`**，否则二进制帧必坏。
3. **adb 窗口**：gold-15 处于 nativehang/watchdog 重启循环，adb 窗口短且对已注册
   offline 端点反复 `adb connect` 无效，须先 `adb disconnect` 再 connect 复位。
4. **前置确认实测**：`VIRTIO_GPU_F_RESOURCE_BLOB` 在 QEMU 特性串中（零拷贝可用）；
   darwin dist `libexec/` 无 `vhost-user-gpu`（linux dist 有）但 `virgl_render_server`
   在 → 渲染走 virtio-gpu-gl + render server（venus→MoltenVK→Metal），
   不依赖 vhost-user-gpu、不依赖 cocoa GL。

## 3. 证据

- **失败面**（[log-fail.txt](log-fail.txt)）：tty 行规约破坏二进制帧（`bad frame len
  1581276736`=0x5E405E40）；`adb connect` 无 disconnect 复位时窗口不开；
  run_p1.sh zsh 分词坑（`$ADB` 含空格整串当命令名）。
- **通过面**（[log-pass.txt](log-pass.txt)）：run93 QEMU cmdline 含
  `socket,id=cdh-gpuecho,…gpu.sock` 与 `virtconsole,chardev=cdh-gpuecho,id=cdh-hvc11-gpuecho`；
  RTT 三档（64/1024/4096B）+ TX 吞吐实测输出；echo memcmp 校验 exit 0。
- 原始产物：`out/tmp/p1/`（gpuchan_test.c/gpuchan_test/gpuecho_host.py/run_p1.sh、
  rtt_result.txt、tx_result.txt、host_rtt.log、host_sink.log）。

## 4. 改动

1. **`core/host/daemon/qemu/qemu.go`**：denseVirtconArgs 末尾追加 hvc11 gpuecho
   通道——`-chardev socket,id=cdh-gpuecho,path=<instance>/channels/gpu.sock,
   server=on,wait=off,logfile=…log` + `-device virtconsole,chardev=cdh-gpuecho,
   id=cdh-hvc11-gpuecho`（server=on,wait=off：QEMU 常驻，host 侧按需 connect）。
2. **`out/tmp/p1/gpuchan_test.c`**：guest 测量工具（NDK 静态 aarch64）——open 后
   `cfmakeraw`；RTT 3 档帧长（预热 50 次 + 2000 iters，memcmp 回显校验）；
   吞吐 64KB 帧只写（sink 模式配合）。
3. **`out/tmp/p1/gpuecho_host.py`**：host echo/sink 两模式帧解析器
   （`[4B BE len][payload]`，>64MB 帧长判坏帧）。
4. **`out/tmp/p1/run_p1.sh`**：抢 adb 窗口编排（disconnect+connect 复位、2s 轮询、
   push+run）。注：zsh 下含空格变量须展开为独立参数（不自动分词）。

## 5. 验证（run93，2026-10-12）

| 指标 | 数值 | 说明 |
|---|---|---|
| RTT 64B | avg **0.297ms** | 2000 iters，含 guest tty 写+读与 host python echo |
| RTT 1024B | avg **0.201ms** | 同上 |
| RTT 4096B | avg **0.378ms** | 同上 |
| TX 吞吐 | **25.2 MB/s** | 64MB / 2540.8ms，64KB 帧（host sink） |
| echo 校验 | **全过** | memcmp 逐档比对，gpuchan_test exit 0 |

- 帧协议完整性：2000×3 档回显逐字节一致 → 帧边界/长度头/二进制透明传输全部正确；
- **P1 验收门（链路跑通 + 延迟/带宽实测）通过**；
- 25.2MB/s 是 virtconsole tty 调试/控制通道上限，**非最终数据面约束**——
  job chain 本体走共享内存/blob 零拷贝（`VIRTIO_GPU_F_RESOURCE_BLOB` 已确认在）。

## 6. 遗留

1. **P2 前端 MVP**（§8.5）：Valhall fragment 子集 → SPIR-V → MoltenVK，
   单三角形/纹理逐像素比对——B′ 的真正风险面（研究级翻译器）尚未触碰；
2. tty 通道吞吐 25.2MB/s：若 P2 起 job chain 回执走 tty 够用（KB 级），
   但大块数据面必须走 blob/共享内存，届时另测零拷贝通路带宽；
3. gold-15 nativehang/watchdog 重启循环（guest 侧老问题）仍在，不阻塞通道测量；
4. qemu.go hvc11 改动随本条证据一并提交 core/host/daemon 仓库。
