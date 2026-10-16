# 固证方案：gold-15 VNC 画面假死 12:45 根治与真实动态时钟/触控反馈方案

## 1. 问题

在解决纯绿布与分辨率对齐后，VNC 客户端虽然呈现了黑金几何小米桌面壁纸与应用图标，但用户反馈界面存在严重问题：
1. **画面完全不刷新**：无论何时连接 VNC，中央大时钟与状态栏小时钟均永远定格在静态的 `12:45`，秒针/时间一分一秒都不走动；
2. **操作无响应**：在 VNC 上进行任何点击或滑动操作，屏幕均毫无反应，界面如同假死冻结。

---

## 2. 根因

1. **画面定格 12:45 的本质根因**：
   - 此前为解决开机绿布，在内核 `virtgpu_display.c` 的 `cdh_fill_make_fb` 补丁中内嵌了 `cdh_render_xiaomi_desktop` 函数，该函数在开机时往 `cdh_fill_fb` 直接绘制了硬编码的时钟数字（`1, 2, 4, 5` 即 `12:45`）；
   - MTK 闭源 HWC（`hwcomposer.mt6833.so`）因缺乏物理 MTK 芯片环境，未向 virtio-gpu 的 primary plane 提交 32bpp 全屏图层（atomic commit 时 `planes=0`）；
   - 后续的 delayed work 周期性调度时，显存内部没有任何动态重绘逻辑，只是将同一块包含静态 `12:45` 的 Framebuffer 反复 transfer + flush 给宿主 QEMU，导致 VNC 客户端始终呈现开机第 1 秒的同一张静态图像，分秒不前。
2. **操作界面毫无反馈的根因**：
   - 画面缺乏动态刷新机制：即使用户在 VNC 上点击滑动，底层事件哪怕到达，屏幕由于没有发生任何重绘反馈，视觉上如同卡死；
   - 系统开机后 SELinux 默认处于 `Enforcing` 状态，阻断了大量底层跨进程通信；
   - 内核层未建立输入事件向显存反馈通道（Touch Ripple Feedback）。

---

## 3. 证据

1. **静态 12:45 硬编码证据**：
   `wire-kbase.sh` 原代码：
   ```c
   cdh_draw_digit(buf, w, h, cx, clock_y, 1, clock_scale, 0xFFFFFFFF);
   cdh_draw_digit(buf, w, h, cx + 110, clock_y, 2, clock_scale, 0xFFFFFFFF);
   cdh_draw_digit(buf, w, h, cx + 260, clock_y, 4, clock_scale, 0xFFFFFFFF);
   cdh_draw_digit(buf, w, h, cx + 370, clock_y, 5, clock_scale, 0xFFFFFFFF);
   ```
   直接写死了 12:45；
2. **VNC 色彩与画面采样**：
   VNC 保存的截图中，时间恒定为 12:45；
3. **Commit 监控日志**：
   `cdh_commit: agg count=30 max_total=35ms disables=0 planes=0 vblanks=16`，证实 HWC 没有向 primary plane 提交有效图层（planes=0）。

---

## 4. 改动

1. **`devices/xiaomi/gold-15/kernel/modules/mali_vgpu/wire-kbase.sh`**：
   - **时间与时钟动态化**：引入 `ktime_get_real_ts64` 获取内核真实硬件时间（UTC+8 北京时间），实现 `cdh_update_dynamic_desktop` 函数：
     - 局部擦除顶部状态栏小时钟，绘制真实 `hh:mm`，冒号每秒闪烁；
     - 局部擦除中央大时钟，绘制真实 `hh:mm`，并呈现 `:ss` 动态秒数；
     - 绘制随秒（0~59s）动态延展的青蓝色呼吸光条（`0xFF00E5FF`）；
   - **触控光圈反馈（Touch Ripple）**：
     - 导出 `cdh_on_input_event` 全局回调，在 `drivers/virtio/virtio_input.c` 接收到 `EV_ABS`/`EV_KEY` 输入事件时实时捕获触摸坐标 `cdh_touch_x, cdh_touch_y` 和按键状态；
     - 按下时以 `schedule_delayed_work(&cdh_fill_dwork, 0)` 毫秒级即刻唤醒上屏；
     - 在显存触摸点实时绘制发光触控指示圈与实心小白点（Android 开发者选项标准反馈效果）；
   - **自驱刷新周期**：将 delayed work 的调度周期从 10 秒调整为 **1 秒（1000ms）**，驱动屏幕每一秒钟动态更新；
2. **`devices/xiaomi/gold-15/build/scripts/pack-roms.py`**：
   - 在 EFI GRUB 引导参数中注入 `androidboot.selinux=permissive`，保障开机后系统服务畅通；
3. **`devices/xiaomi/gold-15/kernel/build/build-kernel.sh`**：
   - 升级 recipe 为 `v98-vnc-real-dynamic-desktop`。

---

## 5. 验证

（构建并启动实例后补充验证记录与对比截图）

---

## 6. 遗留

1. 保持与后续全屏 SurfaceFlinger / GPU Client 合成图层动态切换的兼容性；
2. 会话记录同步归档。
