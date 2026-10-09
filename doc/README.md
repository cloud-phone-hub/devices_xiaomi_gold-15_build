# gold-15 构建文档索引

> 设备：Xiaomi Redmi Note 13 5G（codename `gold`，board `mt6833`）**全局版 ROM** `OS3.0.5.0.VNQMIXM`
> 模板：`devices/xiaomi/note13-5G-15`（同硬件中国版）

## 构建流水线状态（2026-10-09）

`main.py` 尚未移植，各阶段按流程顺序直接调用。

| 阶段 | 脚本 | 状态 | 产物 |
|---|---|---|---|
| unpack | `scripts/unpack-roms.py` | ✅ | `roms/gold_global_images_OS3.0.5.0.VNQMIXM_15.0/`（super.img 展开 + 8 个逻辑分区） |
| kernel | `scripts/build-kernel.py` → `kernel/build/build-kernel.sh` | ✅ | `out/kernel/Image`（`6.6.127-4k-cdh`）+ `out/dist/kernel/modules/*.ko`（11） |
| boot | `scripts/build-boot.py` | ✅ | `out/dist/rom/boot.img` |
| vendor_boot | `scripts/build-vendor-boot.py` | ✅ | `out/dist/rom/vendor_boot.img` |
| vendor | `scripts/build-vendor.py`（**未移植**） | ⛔ | `out/dist/rom/vendor.img` |
| vendor_dlkm | `scripts/build-vendor_dlkm.py` | ✅ | `out/dist/rom/vendor_dlkm.img` |
| super | `scripts/build-super.py`（官方 super 基底 + 原位替换 vendor/vendor_dlkm） | ⛔ 依赖 vendor | `out/dist/rom/super.img` |
| pack | `scripts/pack-roms.py` | ⛔ 依赖 super | `out/device/gold-15/15/os3.0.5.0.vnqmixm/{vda.img,data.qcow2}` |

未移植：`build/main.py`、`build/vendor.py`(=`build-vendor.py`)、`build/vendor/build/**`（12 个驱动模块）、
`build/vendor/source/**`（17 个 HAL 子项目，1499 文件）、`build-patch.py`、`build_odm.py`、`build/odm/`。

前置配置：`build/manifest.json`（schema 2）、`build/baselines/<build-id>.json`（`rom` 段与
`vendor-dlkm.baseline-build-prop` 已实测回填）、`kernel/{build,modules,source}`（source 走 `gold-15` 分支）。

## 证据目录索引

| issue | 主题 | 结论 |
|---|---|---|
| [issue-gold15-kernel-pipeline](evidence/issue-gold15-kernel-pipeline/solution.md) | 内核构建流水线接入（四段跑通） | kernel/boot/vendor_boot/vendor_dlkm 通过；vendor/super/pack 阻塞于未移植的 vendor 阶段 |

同目录 `log-fail.txt`（阻塞现场）/ `log-pass.txt`（四段产物与关键日志）为配套原始记录。