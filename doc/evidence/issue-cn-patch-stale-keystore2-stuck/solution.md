# Issue: CN 3.0.10.0 镜像 patch.img 未重新发布 → VINTF manifest 整份解析失败 → keystore2 卡死、开机停在 /data

- 设备: Xiaomi Redmi Note 13 5G（gold-15，MTK MT6833）
- 宿主: macOS（darwin, QEMU + hvf）
- ROM: `gold_images_OS3.0.10.0.VNQCNXM_20260715.0000.00_15.0_cn`（国行 3.0.10.0）
- 状态: **已修复**（2026-10-21，实例 gold_cn_OS3.0.10.0.VNQCNXM/1）

---

## 1. 问题现象

实例启动后 keymint 侧完全正常，但 **keystore2 起来后从不注册**，vold 每秒等待
`android.system.keystore2.IKeystoreService/default`，开机卡在 /data 挂载前：

```
[    5.986281][  T192] servicemanager: NULL VINTF MANIFEST!: device
[    6.091681][    T1] init: starting service 'keystore2'...
[    6.093640][    T1] init: ... started service 'keystore2' has pid 306
[    6.136219][  T192] servicemanager: Caller(pid=205,uid=0,sid=u:r:vold:s0) Since
'android.system.keystore2.IKeystoreService/default' could not be found trying to start it as
a lazy AIDL service.
[    7.138134][  T205] ServiceManagerCppClient: Waited one second for
android.system.keystore2.IKeystoreService/default ...
```

同期日志里 keymint 是健康的（易误判为 keymint 故障）：

- `CDH-KM-V5-HVC3READY`，`vendor.keymint-default` 启动并注册 IKeyMintDevice /
  ISecureClock / ISharedSecret / IRemotelyProvisionedComponent 四实例；
- `secure-env/keymint-wire.log` 每次启动只增 222 B（boot info），
  `secure-env.log` 无错误（`Could not receive message` 只在实例被 SIGTERM、
  socket 关闭时出现）。

## 2. 根因

两层叠加：

1. **VINTF 重复声明（已知问题，issue-bt-hci-vintf-duplicate-fqname）**：patch.img 内
   `bluetooth-service-default.xml`（282 B，底座 AOSP 参考声明）与
   `android.hardware.bluetooth-service.xml`（551 B，自研旧文件名片段）都声明
   `IBluetoothHci/default@1` → VINTF 唯一性校验失败 → **整份 device manifest 不可用**
   （`NULL VINTF MANIFEST: device`）。
2. **修复未发布到 CN 镜像**：源码层已于 20:40（asset）/21:28（build.py 同名覆盖
   `VINTF_NAMES`）修复，且 21:04 的 global 构建已把修复产物发布到
   `out/device/gold-15/15/os3.0.5.0.vnqmixm/patch.img`；但 **CN 目录
   `out/device/gold-15/15/os3.0.10.0.vnqcnxm/patch.img` 仍是 20:21 的旧构建**
   （pack-roms 未重跑），实例加载后照旧冲突。

keystore2 启动时要查 device VINTF manifest 里的 keymint HAL 声明，manifest 解析整份
失败后它卡住不注册；vold 依赖 `IKeystoreService` 才能挂 /data → 无限重试，开机停在
/data 前。keymint 服务本身不受影响（guest 侧已注册）。

## 3. 证据

- `log-fail.txt`：22:25 启动段落（CN，旧 patch.img）的 VINTF 冲突、`NULL VINTF
  MANIFEST`、keystore2 启动、vold 每秒重试原行；以及旧 patch.img 内两份声明的清单。
- 排除 data 盘格式化死锁：把实例 data 换成预格式化 ext4（8 GiB）后 `/data` 直接挂载
  （无 `Format it` / `vdc cryptfs encryptFstab`），keystore2 仍卡 → 与 data 无关。
- 定点验证（同一实例、只改 patch.img 一个文件）：把 `bluetooth-service-default.xml`
  替换为合法空 manifest（40 B，保留 0644 与 `u:object_r:vendor_configs_file:s0`）后
  重启：`VINTF parse error` 0 条、vold 重试 0 次、keystore2 6.3 s 注册成功、
  `sys.boot_completed=1`、`keymint-wire.log` 增至 ~14 KB（见 `log-pass.txt`）。
- 时间线（2026-10-21）：20:13 CN baseline 更新 → 20:21 CN patch.img 打包（旧命名）→
  20:40 自研 manifest 资产改同名 → 20:58~21:04 global 重建并发布 → 21:28 build.py
  注释/逻辑定稿 → 22:25 CN 实例复现 → 22:46 空 manifest 定点验证通过 → 22:51 起
  用 CN build-id 重建 vendor/vendor_dlkm/patch 并重新发布。

## 4. 改动

| 文件 / 动作 | 改动 |
|---|---|
| `build/baselines/gold_images_OS3.0.10.0...json` | 删除旧键 `etc/vintf/manifest/android.hardware.bluetooth-service.xml`、`etc/init/android.hardware.bluetooth-service.rc`（`register_labels()` 只增不删，旧键会导致同名覆盖退化为"旧名残留 + 新名追加"） |
| 重跑 CN 构建 `--build-id gold_images_OS3.0.10.0...` | `--only vendor` → `--only vendor_dlkm` → `--only patch`，产出 CN 的 `out/dist/rom/patch.img`（仅含一份 885 B `bluetooth-service-default.xml`） |
| `out/device/gold-15/15/os3.0.10.0.vnqcnxm/patch.img` | 用新 CN 产物替换（等同 pack-roms 的 patch 发布段；vda.img 不动） |

## 5. 验证结果

CN build-id（`gold_images_OS3.0.10.0...`）重建 vendor/vendor_dlkm/patch 并发布后复验：

| # | 验证点 | 结果 |
|---|---|---|
| 1 | 新 patch.img | 402653184 B（22:56），`e2fsck -fn` 干净，sha256 `a0ed50f4…cec364` 与 device 目录发布件一致 |
| 2 | patch.img 内 vintf 目录 | 仅 `bluetooth-service-default.xml`（885 B，22:56）；旧名文件不再存在 |
| 3 | build.prop fingerprint | `Redmi/gold/gold:15/AP3A.240905.015.A2/OS3.0.10.0.VNQCNXM`（确认是 CN 底包构建，未误用 global 产物） |
| 4 | 运行期 `VINTF parse error` / `NULL VINTF` | **0 / 0** |
| 5 | vold 等 `IKeystoreService` 重试次数 | **0** |
| 6 | keystore2 | 6.038 s `Successfully registered Keystore 2.0 service.` |
| 7 | keymint | `CDH-KM-V5-HVC3READY`，`keymint-wire.log` 增至 19764 B（通道被实际使用） |
| 8 | 系统 | `adb … getprop sys.boot_completed` = **1** |
| 9 | HAL 服务 | `service check`：`IKeyMintDevice/default`、`IBluetoothHci/default` 均 **found** |

## 6. 遗留 / 下一步

- **发布环节必须显式重跑**：只跑 `--only patch` 不会更新 `out/device/<...>/patch.img`，
  必须跑 pack（或手动拷贝 patch.img）。建议在 pack-roms 之后校验
  `out/dist/rom/patch.img` 与 device 目录 patch.img 的 sha256 一致。
- 换 ROM / build-id 时先核对三处：`out/dist/vendor` staging 残留、baseline 标签键、
  `out/device/<...>/patch.img` 的 mtime。
- 同名覆盖原则（VINTF 片段、init rc）不变：底座已有的同名文件一律同名覆盖，不追加。
