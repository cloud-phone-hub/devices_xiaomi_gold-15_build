# Issue: 蓝牙 HAL 的 VINTF 声明重复（Conflicting FqInstance: IBluetoothHci/default）—— vendormanifest 整份解析失败

- 设备: Xiaomi Redmi Note 13 5G（gold-15，MTK MT6833）
- 宿主: macOS（darwin, QEMU + hvf）
- ROM: `gold_global_images_OS3.0.5.0.VNQMIXM_20260811.0000.00_15.0_global`（海外版 3.0.5.0）
- 状态: **已修复**（2026-10-21，实例 gold-15/8）

---

## 1. 问题现象

自研蓝牙 HCI HAL（`android.hardware.bluetooth-service`）打进 `patch.img` 后首次启动，
servicemanager 反复报 VINTF 解析失败，且**整份 device manifest 不可用**：

```
servicemanager: getDeviceHalManifest: -2147483648 VINTF parse error:
Cannot add manifest fragment /vendor/etc/vintf/manifest/bluetooth-service-default.xml:
HAL "android.hardware.bluetooth" has a conflict:
Conflicting FqInstance: IBluetoothHci/default (@1)
  (from /vendor/etc/vintf/manifest/android.hardware.bluetooth-service.xml)
vs. IBluetoothHci/default (@1)
  (from /vendor/etc/vintf/manifest/bluetooth-service-default.xml).
```

后果不止蓝牙：`getDeviceHalManifest` 整体返回 -2147483648，framework 侧所有
“先查 VINTF 声明再取服务”的路径都可能受影响（此前 audio effect 缺声明就是同类问题）。

## 2. 根因

lineage/gold 底座**自带**一份 AOSP 参考声明
`/vendor/etc/vintf/manifest/bluetooth-service-default.xml`（282 B，指向底座里
AOSP 参考实现 `android.hardware.bluetooth-service.default`），已声明
`android.hardware.bluetooth` / `IBluetoothHci/default` v1。

我们的集成层原本把片段写成**另一个文件名**
`android.hardware.bluetooth-service.xml`（551 B），内容同样是
`IBluetoothHci/default` v1 → 同一 fqname 在 device manifest 中出现两条 →
VINTF 唯一性校验失败，整份 manifest 解析中止。

## 3. 证据

- `log-fail.txt`：实例 gold-15/6（修复前）的 qemu.log 原行；以及修复前 patch.img 内
  `/etc/vintf/manifest/` 同时存在两份声明的清单。
- `log-pass.txt`：实例 gold-15/8（修复后）`Conflicting FqInstance` 计数 **0**，
  servicemanager 正常 `Found android.hardware.bluetooth.IBluetoothHci/default in
  device VINTF manifest`。

## 4. 改动

| 文件 | 改动 |
|---|---|
| `build/vendor/source/android.hardware.bluetooth/build.py` | `VINTF_NAMES = ["bluetooth-service-default.xml"]`（与底座同名覆盖），注释记录冲突根因 |
| `build/vendor/source/android.hardware.bluetooth/vendor/etc/vintf/manifest/bluetooth-service-default.xml` | 新建（覆盖底座同名文件），只保留一条 `IBluetoothHci/default` v1 声明 |
| 同目录 `.../manifest/android.hardware.bluetooth-service.xml` | 删除（旧文件名，会与底座叠加） |
| `build/baselines/<build-id>.json` | `vendor.selinux-labels` 的键改名为 `etc/vintf/manifest/bluetooth-service-default.xml` |

构建脚本 `register_labels()` 只“增/改”不删，故换了文件名后要同步清理基线里
旧键、以及 `out/dist/vendor` 里上一次构建的残留文件，否则会报
`[err] 基线清单缺少 SELinux 标签`。

## 5. 验证结果

| # | 验证点 | 结果 |
|---|---|---|
| 1 | 构建 `--from vendor --force-pack` | 全流程完成，patch.img 402653184 B |
| 2 | patch.img 内 vintf 目录 | 仅 `bluetooth-service-default.xml`（885 B，21-Oct 21:04） |
| 3 | 运行期 `Conflicting FqInstance` | **0** 条 |
| 4 | servicemanager 解析 | `Found android.hardware.bluetooth.IBluetoothHci/default in device VINTF manifest` |
| 5 | 服务启动 | `starting service 'vendor.bluetooth-default'` → pid 482 |

## 6. 遗留 / 下一步

- 换 ROM（另一 build-id）时，基线 JSON 与 `out/dist/vendor` 残留要一起确认，
  否则同名覆盖会退化成“旧名残留 + 新名追加”。
- 若后续底座移除该 AOSP 参考声明，可改回独立文件名；但**永远只能有一份**
  `IBluetoothHci/default` 声明。
