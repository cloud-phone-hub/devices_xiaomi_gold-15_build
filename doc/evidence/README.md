# Redmi Note 13 5G（gold-15）问题与证据链索引

本文档汇总 `gold-15`（MTK MT6833）在云手机虚拟化框架适配中解决的问题与六段式证据。
每个目录包含 `solution.md`（问题 → 根因 → 证据 → 改动 → 验证 → 遗留）+
`log-fail.txt` / `log-pass.txt`。

## 方案目录

| # | 问题标识 | 说明 | 状态 |
| :--- | :--- | :--- | :--- |
| 1 | [issue-bt-hci-vintf-duplicate-fqname](issue-bt-hci-vintf-duplicate-fqname/solution.md) | 蓝牙 HAL VINTF 重复声明（`Conflicting FqInstance: IBluetoothHci/default`）→ 整份 device manifest 解析失败；改为与底座同名覆盖 `bluetooth-service-default.xml` | **已完成**（2026-10-21，实例 8） |
| 2 | [issue-bt-hci-rc-duplicate-service](issue-bt-hci-rc-duplicate-service/solution.md) | 蓝牙 init service 重名（`ignored duplicate definition of service 'vendor.bluetooth-default'`）；rc 改为同名覆盖 `bluetooth-service-default.rc`，并验证 `state: ON` + HCI 帧往返 | **已完成**（2026-10-21，实例 8） |
| 3 | [issue-bluetooth-hal-integration](issue-bluetooth-hal-integration/solution.md) | 蓝牙 HCI HAL 设备集成（`android.hardware.bluetooth` / `IBluetoothHci`）：复用 `hal_bluetooth_default` 域、新增 `cdh_bt_channel` 设备标签、委托 HAL 引擎编译 | 构建层完成；运行期见 #1/#2 |
| 4 | [issue-gold15-align-note13-lineage-route](issue-gold15-align-note13-lineage-route/solution.md) | gold-15 整树对齐 note13 构建路线并首次启动成功 | 已完成 |
| 5 | [issue-vendor-vintf-audio-effect](issue-vendor-vintf-audio-effect/solution.md) | audioserver SIGSEGV 循环（`AudioFlinger::onFirstRef+704` 空指针）：vendor VINTF 缺 `audio.effect/IFactory` 声明 | 已完成 |
| 6 | [issue-vendor-apex-audio](issue-vendor-apex-audio/solution.md) | audioserver SIGSEGV 循环 → system_server Watchdog 重启（audio HAL 缺失） | 已完成 |
| 7 | [issue-vendor-apex-health](issue-vendor-apex-health/solution.md) | `/vendor/apex` 未重定向 → BatteryService 因缺 health HAL 崩溃 | 已完成 |
| 8 | [issue-media-c2-seclabel](issue-media-c2-seclabel/solution.md) | c2-mediatek / dolby-c2 服务 SIGABRT 循环（`setexeccon hal_codec2_default` 失败） | 已完成 |
| 9 | [issue-odm-load](issue-odm-load/solution.md) | 官方 odm 加载冲突（`odm_sepolicy.cil` 引用 MTK 专属类型导致 init 崩溃） | 已完成 |
| 10 | [issue-pioneer-selinux-overwrite](issue-pioneer-selinux-overwrite/solution.md) | `vendor_sepolicy.cil` 追加段互相覆盖（pioneer 域丢失） | 已完成 |
| 11 | [issue-vendor-property-area-align](issue-vendor-property-area-align/solution.md) | `/dev/__properties__` 属性区命名空间对齐真机（vendor 属性上下文 + 策略类型） | 已完成 |
| 12 | [issue-vendor-buildprop-props](issue-vendor-buildprop-props/solution.md) | 官方 vendor build.prop 属性启用（必排除属性 + 必锁值属性） | 已完成 |
| 13 | [issue-vendor-dlkm-modules](issue-vendor-dlkm-modules/solution.md) | `/vendor/lib/modules` 误读厂商 ko（Exec format error） | 已完成 |
| 14 | [issue-vendor-so-identity](issue-vendor-so-identity/solution.md) | 云机 App 观测到的 vendor `.so` 加载来源判定（zygote 继承 vs App 自加载） | 已完成 |
| 15 | [issue-remote-keymint-hal](issue-remote-keymint-hal/solution.md) | Remote KeyMint HAL 集成（`android.hardware.security.keymint`） | 已完成 |
| 16 | [issue-cellular-data-no-setupdatacall](issue-cellular-data-no-setupdatacall/solution.md) | 蜂窝数据框架不发起 SetupDataCall（数据开关正常、PS 域已注册，数据却不上来） | 已完成 |
| 17 | [issue-mtk-iccid-indication](issue-mtk-iccid-indication/solution.md) | MTK radioex SIM 指示（iccidChanged）oneway 语义不匹配 | 已完成 |
| 18 | [issue-wifi-connection-unstable](issue-wifi-connection-unstable/solution.md) | v8 模拟 Wi-Fi 连接不稳定（BEACON-LOSS + 30s 规律性断开） | 已完成 |
| 19 | [issue-scrcpy-audio-vdac](issue-scrcpy-audio-vdac/solution.md) | scrcpy 无音频（Cannot create AudioRecord）—— virtual DAC 真实音频通路 | 已完成 |
| 20 | [issue-scrcpy-input-inject-drop](issue-scrcpy-input-inject-drop/solution.md) | scrcpy 触摸/滑动无效（dropping inconsistent event）——Virtio 输入设备被识别为 STYLUS | 已完成 |
| 21 | [issue-zygote-secondary-missing](issue-zygote-secondary-missing/solution.md) | system_server 周期自杀重启（MIUI bootCompleted 硬连 zygote_secondary） | 已完成 |
| 22 | [issue-adb-shell-netns](issue-adb-shell-netns/solution.md) | 云机 adb shell / app 看不到 ccmni2（ADB 被关在 adb_net 私有 netns） | 已完成 |
| 23 | [issue-serial-logcat-capture](issue-serial-logcat-capture/solution.md) | adb 不可达 → logcat 串口取证通道（guest 侧 Java 崩溃证据） | 已完成 |
| 24 | [issue-video-fast-playback](issue-video-fast-playback/solution.md) | 在线视频倍速播放（5~8 倍速）+ 绿块花屏 | 已完成 |

## 备注

- 2026-10-21 新增 #1/#2：均由「自研蓝牙 HCI HAL」运行期暴露，修复方式遵循同一原则
  ——**lineage/gold 底座已有的同名文件必须“同名覆盖”，不要另起文件名追加**
  （VINTF 片段、init rc 都一样）。
- #3 为构建层集成记录；其运行期结论（HCI frames 往返、`bluetooth_manager` state ON）
  见 #1/#2 的 `log-pass.txt`。
- 本目录尚未覆盖的问题（如 MTK 私有 HCI 命令 `0xfd5e/0xfd5f` 未被 netsimd 实现）
  记录在 #2 的「遗留」中。
