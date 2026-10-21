# Issue: c2-mediatek / dolby-c2 服务 SIGABRT 循环（setexeccon hal_codec2_default 失败）—— 解决方案

- 设备: Xiaomi Redmi Note 13 5G (note13-5G-15)
- 宿主: macOS (darwin, QEMU + hvf)
- 状态: **已打通**（2026-10-20，实例 note13-15/3）：三个 c2 store 服务 running、
  IComponentStore default/default1/default9/software 全注册、`sys.boot_completed=1`。
- 触发背景: vendor/source 参考 gold-15 改造为薄集成层后，media 模块的 rc 资产从
  gold 照搬，其中 `seclabel` 是**设备策略相关项**，未与 note13 基座对齐。

---

## 1. 问题现象

guest 卡 boot_completed 不置位；两个 codec2 服务 restarting 循环：

```
[init.svc.android-hardware-media-c2-hal]: [restarting]
[init.svc.vendor-dolby-media-c2-hal]: [restarting]
（对照组：android-hardware-media-c2-v4l2-hal-1-2 = running，rc 用的 mediacodec）
```

## 2. 根因

note13 guest 基座 sepolicy（MTK 官方 MIUI 派生）**没有 `hal_codec2_default` 域**；
gold 基座（官方策略完整版）才有。本项目 note13 的设计（`build/vendor/build/sepolicy.py`
注释明确）是 codec2 store 以既有 `mediacodec` 域运行，经 `halserverdomain` 覆盖。
从 gold 搬来的两个 rc 写了 `seclabel u:r:hal_codec2_default:s0`，init `setexeccon`
对不存在域返回 EINVAL → 服务子进程 SIGABRT 循环。

## 3. 证据

`log-fail.txt`（qemu.log 硬证据）：

```
init: cannot setexeccon('u:r:hal_codec2_default:s0') for android-hardware-media-c2-hal: Invalid argument
init: cannot setexeccon('u:r:hal_codec2_default:s0') for vendor-dolby-media-c2-hal: Invalid argument
```

辅证：v4l2 rc 同基座用 `seclabel u:r:mediacodec:s0` 一直正常存活。

## 4. 改动

| 文件 | 改动 |
|---|---|
| `build/vendor/source/android.hardware.media/vendor/etc/init/android.hardware.media.c2-mediatek.rc` | `seclabel u:r:hal_codec2_default:s0` → `u:r:mediacodec:s0` |
| `build/vendor/source/android.hardware.media/vendor/etc/init/vendor.dolby.media.c2-default-service-dax.rc` | 同上 |

随后 `python3 build/main.py --from vendor` 重打 vendor→patch→super→pack。

## 5. 验证

`log-pass.txt` 摘要（实例 3）：

```
[init.svc.android-hardware-media-c2-hal]: [running]
[init.svc.android-hardware-media-c2-v4l2-hal-1-2]: [running]
[init.svc.vendor-dolby-media-c2-hal]: [running]
service list: IComponentStore default / default1 / default9 / software 均在
```

## 6. 遗留

- **薄集成层搬运资产清单**：rc `seclabel`、vintf 声明面等属设备侧策略/契约项，
  gold→note13 复制时必须逐项对齐（本次与 issue-vendor-vintf-audio-effect 皆源于此）。
- gold-15 若换基座策略，同样需要复核这两个 rc。
