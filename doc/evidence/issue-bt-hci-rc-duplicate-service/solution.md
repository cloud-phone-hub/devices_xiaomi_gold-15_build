# Issue: init service 重名（ignored duplicate definition of service 'vendor.bluetooth-default'）—— 与底座 rc 争抢同名服务

- 设备: Xiaomi Redmi Note 13 5G（gold-15，MTK MT6833）
- 宿主: macOS（darwin, QEMU + hvf）
- ROM: `gold_global_images_OS3.0.5.0.VNQMIXM_20260811.0000.00_15_global`（海外版 3.0.5.0）
- 状态: **已修复**（2026-10-21，实例 gold-15/8）

---

## 1. 问题现象

修好 VINTF 重复声明后，服务能起来，但 init 解析 rc 时报警：

```
init: /vendor/etc/init/bluetooth-service-default.rc: 1:
      ignored duplicate definition of service 'vendor.bluetooth-default'
```

同一实例里我们的 rc 也已解析：

```
init: Parsing file /vendor/etc/init/android.hardware.bluetooth-service.rc...
init: processing action (post-fs-data) from (/vendor/etc/init/android.hardware.bluetooth-service.rc:20)
init: starting service 'vendor.bluetooth-default'...
init: ... started service 'vendor.bluetooth-default' has pid 474
```

即：**两份 rc 定义了同名 service**，谁生效取决于 rc 解析顺序（字母序
`android...` 在我们之前，暂时我们胜出），属于随时会翻车的隐患。

## 2. 根因

lineage/gold 底座自带 AOSP 参考实现与 rc：

| 底座文件 | 内容 |
|---|---|
| `/vendor/etc/init/bluetooth-service-default.rc` | `service vendor.bluetooth-default /vendor/bin/hw/android.hardware.bluetooth-service.default`（class hal、capabilities BLOCK_SUSPEND NET_ADMIN SYS_NICE、无 seclabel、无 interface、无 start 触发） |
| `/vendor/bin/hw/android.hardware.bluetooth-service.default` | AOSP 参考二进制（70512 B） |

我们的集成层把 rc 写成 `android.hardware.bluetooth-service.rc`，同样定义
`service vendor.bluetooth-default`（指向我们的二进制 + seclabel + interface +
`on post-fs-data start`）。两个文件名不同 → init 不会覆盖，直接丢弃后者（先解析者胜）。

## 3. 证据

- `log-fail.txt`：实例 gold-15/7 的 qemu.log 原行（duplicate 警告 + 两份 rc 定义内容）。
- `log-pass.txt`：实例 gold-15/8 只剩一个蓝牙 rc 被解析、无 duplicate；
  HAL 进程 `/proc/482/fd/5 -> /dev/hvc5`；`cmd bluetooth_manager enable` 后
  `state: ON`，netsimd 收到 HCI 初始化与 LE 扫描命令。

## 4. 改动

| 文件 | 改动 |
|---|---|
| `build/vendor/source/android.hardware.bluetooth/vendor/etc/init/bluetooth-service-default.rc` | 新建（与底座同名覆盖），保留我们的 service 定义 |
| 同目录 `android.hardware.bluetooth-service.rc` | 删除 |
| `build/vendor/source/android.hardware.bluetooth/build.py` | `RC_NAME = "bluetooth-service-default.rc"`，注释记录重名根因 |
| `build/baselines/<build-id>.json` | `vendor.selinux-labels` 键改为 `etc/init/bluetooth-service-default.rc` |
| `out/dist/vendor/etc/init/android.hardware.bluetooth-service.rc` | 删除上一次构建残留（否则基线校验报缺标签） |

与 VINTF 修复同一思路：**底座已有的同名文件一律“同名覆盖”，不另起文件名追加**。

## 5. 验证结果

| # | 验证点 | 结果 |
|---|---|---|
| 1 | patch.img 内 `/etc/init` | 仅 `bluetooth-service-default.rc`（1625 B，21-Oct 21:04） |
| 2 | 运行期蓝牙 duplicate | **0**（仅剩底座既有的 `setup_wifi` 重名，与本模块无关） |
| 3 | 服务启动 | `started service 'vendor.bluetooth-default' has pid 482` |
| 4 | HAL 持有的设备节点 | `ls -l /proc/482/fd` → `5 -> /dev/hvc5`；cmdline = `/vendor/bin/hw/android.hardware.bluetooth-service` |
| 5 | HCI 下行 | netsimd 收到 `Reset / Set Event Mask / Read Local Version / Read Buffer Size / Read BD_ADDR / LE Set Host Feature …` |
| 6 | 真实开启蓝牙 | `cmd bluetooth_manager enable` → `enabled: true, state: ON`，`time since enabled` 持续增长 |
| 7 | 开启后扫描 | netsimd 收到 `LE Set Extended Scan Parameters` / `LE Set Extended Scan Enable enable=true` |
| 8 | 进程 | `com.android.bluetooth`(10053)、`com.xiaomi.bluetooth`(10127)、HAL(482) 同时存活 |

补充观察：**冷启动时** `com.android.bluetooth` 曾在 post-fs-data 后 ~6 s 退出一次，
但 `dumpsys bluetooth_manager` 显示 `Bluetooth crashed 0 times`，且当时
`state: OFF / Bluetooth never enabled`——属于 MIUI 开机探测后正常退出，不是崩溃；
手动 `enable` 后蓝牙栈稳定运行。

## 6. 遗留 / 下一步

- **MTK 私有 HCI 命令**：netsimd 反复报 `Unknown command, opcode: 0xfd5e / 0xfd5f
  (OGF 0x3f)`。root-canal 不实现 MTK 厂商命令，目前不影响 enable/扫描；若后续
  发现配对/连接异常，需评估在 HAL 侧对未知厂商命令回 Command Complete 的兜底。
- **无害 avc**：`hal_bluetooth_default` 启动时对 `/data/local/tmp`、`/data/local/tests`
  的 `search` 拒绝（permissive=0），是二进制扫描路径所致，未影响功能；如要消除可
  在 vendor sepolicy 里补 `allow hal_bluetooth_default shell_test_data_file:dir search;`
  （不建议仅为降噪放宽）。
- 蓝牙实际可用性（扫描到真实设备/配对）还依赖 netsimd 侧仿真信标，本轮以
  frames 往返 + framework state ON 为准。
