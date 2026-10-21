# Issue: 蓝牙 HCI HAL 设备集成（android.hardware.bluetooth / IBluetoothHci）—— 解决方案

设备：xiaomi gold-15（Redmi Note 13 5G, android15）
卡点：在 `hal/android15` 引擎已能自研出 `android.hardware.bluetooth-service`（IBluetoothHci/default
over /dev/hvc5）之后，把它**接入设备构建**——落 /vendor、开机自启、VINTF 声明、SELinux 授权，
使 framework Fluoride 蓝牙栈能取得 HCI 传输，端到端支撑扫描/连接。

---

## 1. 问题（修复前）

- gold 底包 vendor 侧只带了 **bluetooth.audio**（A2DP 音频 HAL，VINTF `android.hardware.bluetooth.audio.xml`），
  **没有 HCI 传输 HAL**：vendor VINTF 无 `android.hardware.bluetooth` / `IBluetoothHci`，
  `vendor_service_contexts` 无 `IBluetoothHci` 表项，`/dev/hvc5`（device.json `host-hvc-bluetooth: "hvc5"`）
  仅被基座正则 `/dev/hvc[0-9]+ -> console_device` 粗标。
- 后果：即使引擎产出了 `android.hardware.bluetooth-service`，无人把它装进镜像、无人拉起、
  VINTF 不认、SELinux 会拒绝注册与设备访问 → 蓝牙始终不可用。

## 2. 根因 / 设计判断

- **实现路线**：与 vibrator/wifi 同属"薄集成层委托 hal 引擎"范式——集成层只 `install_binaries`
  + 落 rc/vintf + `register_labels`，codegen/编译/链接全交给 `hal/android15/android.hardware.bluetooth/build.py`。
- **不需外置 cfg**：蓝牙 HCI 是纯传输层，设备节点走引擎内置默认 `/dev/hvc5`（属性 `vendor.ser.bt-uart`
  可覆盖），无机型能力清单要运行时读，故比 vibrator/wifi 少一个 `etc/cfg/*.cfg` 环节。
- **SELinux 走 wifi 式"仅授权、不自声明域"**：底包**已具备**完整 `hal_bluetooth_default` 域
  （见 §3 证据），自声明域反而可能与底包冲突；只需补 `IBluetoothHci` 的 service_contexts 映射、
  兜底 add/find、以及 `/dev/hvc5` 的**独立设备标签**（避免把全部 hvc 放开给蓝牙，与 wifi 的
  `cdh_wifi_channel` 同理）。
- **末次写入者约束**：`vendor_sepolicy.cil`/`vendor_service_contexts`/`vendor_file_contexts` 都靠
  debugfs 顺序回写、只有最后一次 write 生效。故 `bluetooth_hal_patch_commands` 必须排在 wifi 之后，
  并通过共享聚合器（`hal_cil_chain`/`hal_vsc_missing`/`vfc_missing_hal_lines`）把前序各段一并写出。

## 3. 证据（决定"复用域"而非"自声明域"的盘上核验）

在 `out/tmp/patch/stage/root/etc/selinux/` 提取件上离线核验：

| # | 事实（grep 命中） | 结论 |
|---|---|---|
| 1 | `vendor_sepolicy.cil`: `(type hal_bluetooth_default_exec)` + `(typeattributeset exec_type (... hal_bluetooth_default_exec ...))` | exec 类型底包已有 |
| 2 | `(allow init_202404 hal_bluetooth_default_exec (file (read getattr map execute open)))` + `(allow hal_bluetooth_default hal_bluetooth_default_exec (file (... entrypoint)))` + `(typetransition init_202404 hal_bluetooth_default_exec process hal_bluetooth_default)` | init→hal_bluetooth_default 域转换底包已就绪 |
| 3 | `(typeattributeset hal_bluetooth_server (hal_bluetooth_btlinux hal_bluetooth_default))` | 服务注册 add/find 由 plat 宏覆盖（本段仍兜底显式补，无害） |
| 4 | `plat_pub_versioned.cil`: `hal_bluetooth_service_202404`、`bluetooth_202404` 版本化影子存在 | allow 可安全引用 `_{vers}` |
| 5 | `vendor_file_contexts`: 仅 `/dev/hvc3`(keymint)/`hvc9`/`hvc10`(wifi) 有字面行，`hvc5` 落 `console_device` 正则 | 需为 hvc5 建独立标签 `cdh_bt_channel` |
| 6 | `baselines/*.json` 只有 `android.hardware.bluetooth.audio.xml`，无 IBluetoothHci | VINTF 落片段是补缺，不冲突 |

## 4. 改动

### 4.1 新增设备集成层 `build/vendor/source/android.hardware.bluetooth/`
- `build.py`：委托引擎 `build(out, runtime, tmp_dir, dry_run)` → `install_binaries`（`bin/hw/android.hardware.bluetooth-service`
  进 VENDOR_STAGE）→ 落 rc/vintf → `register_labels`（binary→`hal_bluetooth_default_exec`、rc/vintf→`vendor_configs_file`）。
  迭代 `hal_bluetooth.SERVICES`（dict 键=二进制名）；无 cfg。
- `vendor/etc/init/android.hardware.bluetooth-service.rc`：`service vendor.bluetooth-default` / `class hal` /
  `user bluetooth` `group bluetooth` / `seclabel u:r:hal_bluetooth_default:s0` / `interface aidl
  android.hardware.bluetooth.IBluetoothHci/default` / `disabled` + `on post-fs-data start`。
- `vendor/etc/vintf/manifest/android.hardware.bluetooth-service.xml`：声明 `android.hardware.bluetooth` v1 `IBluetoothHci/default`。

### 4.2 `build/vendor/build/sepolicy.py`（6 处）
- 常量：`BLUETOOTH_MARKER`、`BLUETOOTH_VSC_LINES`（`IBluetoothHci → hal_bluetooth_service`，接口名匹配）、
  `BT_CHANNEL_TYPE = "cdh_bt_channel"`、`BT_FC_LINES`（`/dev/hvc5 → cdh_bt_channel`）。
- `bluetooth_hal_cil(vers)`：`hal_bluetooth_default → hal_bluetooth_service_{vers}` add/find、
  `bluetooth_{vers} → hal_bluetooth_service_{vers}` find、`cdh_bt_channel` 类型 + `dev_type` +
  `hal_bluetooth_default` 对其 chr_file getattr/ioctl/open/read/write。
- 三聚合器：`hal_cil_chain` 追加 `bluetooth_hal_cil`（wifi 之后）；`hal_vsc_missing` 并入 `BLUETOOTH_VSC_LINES`；
  `vfc_missing_hal_lines` 并入 `BT_FC_LINES`。
- `bluetooth_hal_patch_commands`（镜像 wifi）：调 `hal_policy_patch_commands` + 写 vendor_file_contexts（聚合全部设备标签），
  作为三处文件的末次写入者。

### 4.3 `build/vendor/build/vendor_builder.py`
- 在 `wifi_hal_patch_commands` 之后加 `cmds += sepolicy.bluetooth_hal_patch_commands(work_img, WORK_DIR)`。

> 编排无需注册：`source_build.py` 遍历 `vendor/source/*/build.py` 自动拉起本模块（字母序在 audio 后、board 前）。

## 5. 验证结果（构建层，见同目录 log-pass.txt）

| # | 验证点 | 结果 |
|---|---|---|
| 1 | sepolicy.py / vendor_builder.py / 集成层 build.py 语法 | `py_compile` 全通过 |
| 2 | 委托引擎 codegen+编译+链接 | `AIDL 代码生成 (v1, hash f0eb2eb6d3b9)` → 编译 3+1 → 写 `bin/hw/android.hardware.bluetooth-service` → `[ok]` |
| 3 | 产物形态 | ELF 64-bit LSB **pie** ARM aarch64, interpreter `/system/bin/linker64`, 178592 B |
| 4 | 依赖闭包 | `llvm-readelf -d` NEEDED 仅 libc++/libbinder_ndk/liblog/libdl/libm/libc（无 libbase/cutils/hidlbase/utils 夹带） |
| 5 | CIL 合法性 | `validate_cil(bluetooth_hal_cil("202404"), 底包+plat 名字全集)` **通过**（引用名全部存在，真实重打不会 secilc 失败） |
| 6 | SELinux 标签登记 | `register_labels` 在副本上登记 3 条（binary→hal_bluetooth_default_exec；rc/vintf→vendor_configs_file） |
| 7 | 编排发现 | `source_build.py --dry-run` 列出 `android.hardware.bluetooth/build.py` |

## 6. 遗留 / 下一步

> **2026-10-21 运行期已打通**（实例 gold-15/8，global 3.0.5.0）：
> `vendor.bluetooth-default` 启动（pid 482）、HAL 持有 `/dev/hvc5`、
> `cmd bluetooth_manager enable` 后 `state: ON`，netsimd 收到完整 HCI 初始化与
> LE 扫描命令。过程中暴露并修复了两个运行期卡点，详见：
> - [issue-bt-hci-vintf-duplicate-fqname](../issue-bt-hci-vintf-duplicate-fqname/solution.md)
> - [issue-bt-hci-rc-duplicate-service](../issue-bt-hci-rc-duplicate-service/solution.md)

- **实际扫描/配对尚未验证**：netsimd 侧未启用仿真信标（`--no-test-beacons`），
  当前只到“HCI 帧往返 + framework state ON”。
- **MTK 私有 HCI 命令**：netsimd 报 `Unknown command 0xfd5e/0xfd5f`，暂不影响
  enable/扫描；若配对/连接异常再评估 HAL 侧兜底。
- **属性覆盖路径**：若某机型 hvc 号非 5，需在 `vendor/build.prop` 或 vendor 属性上下文设
  `vendor.ser.bt-uart`（届时 `hal_bluetooth_default` 需该属性类型的 file read 授权，本段暂未加，因走内置默认）。
- **域转换兜底**：本段依赖底包既有 init→hal_bluetooth_default 域转换；若某底包缺失，需照 media/chipinfo
  先例补自声明域 + entrypoint + typetransition。
