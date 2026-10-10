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
| vendor | `scripts/build-vendor.py` | 🟡 **仅 keymint 已移植** | `out/dist/rom/vendor.img` |
| vendor_dlkm | `scripts/build-vendor_dlkm.py` | ✅ | `out/dist/rom/vendor_dlkm.img` |
| super | `scripts/build-super-lpmake.py`（官方 super 基底 + 原位替换 vendor/vendor_dlkm） | ✅ | `out/dist/rom/super.img` |
| pack | `scripts/pack-roms.py` | ✅ | `out/device/gold-15/15/os3.0.5.0.vnqmixm/{vda.img,data.qcow2}` |

未移植：`build/main.py`、`build/vendor.py`(=`build-vendor.py`)、`build/vendor/build/**`（12 个驱动模块）、
`build/vendor/source/**`（17 个 HAL 子项目，1499 文件）、`build-patch.py`、`build_odm.py`、`build/odm/`。

> ⚠️ `build/vendor/source/**` 目前**只移植了 `android.hardware.keymint`**；
> `build/vendor/build/sepolicy.py` 目前**只移植了 keymint 段**（note13 该文件含约 20 段，
> 含 `pioneer`、audio/radio/wifi/camera 等）。详见 `issue-gold15-keymint-port` 的「遗留」。

前置配置：`build/manifest.json`（schema 2）、`build/baselines/<build-id>.json`（`rom` 段与
`vendor-dlkm.baseline-build-prop` 已实测回填）、`kernel/{build,modules,source}`（source 走 `gold-15` 分支）。

## 证据目录索引

| issue | 主题 | 结论 |
|---|---|---|
| [issue-gold15-kernel-pipeline](evidence/issue-gold15-kernel-pipeline/solution.md) | 内核构建流水线接入（四段跑通） | kernel/boot/vendor_boot/vendor_dlkm 通过 |
| [issue-gold15-keymint-port](evidence/issue-gold15-keymint-port/solution.md) | 移植 Remote KeyMint（原地替换官方 mitee 三件套） | **keymint 端到端可用**（4 实例注册 + keystore2 建钥 + `/data` 元数据加密挂载成功）；引导仍未到 `sys.boot_completed`（已定位：32 位件 ENOEXEC / `/vendor/bin/daemon` 缺失 / `pioneer` 域未移植） |
| [issue-gold15-vendor-gpu](evidence/issue-gold15-vendor-gpu/solution.md) | 让厂商图形栈跑起来：内核侧虚拟 Mali + virtio-gpu 的 MTK KMS 形态对齐 | **M1 + Phase C 第一~四步达成**（run21-32 计数验证）：`/dev/mali0` 身份 `0x09000003/r0p1`、厂商 DDK 兼容性检查四项归零；connector 改 DSI 后 composer 不再构造期崩；49 个 MTK KMS 属性挂上；CREATE_SESSION 分流（补丁 7）、dumb 8bpp 放宽（补丁 8）、fb modifiers 放行（补丁 10）后 **`connected:1`、composer 存活不 abort、AddFB2 全链通**。当前阻塞 = SF RenderEngine `output buffer not gpu writeable`（primeShaderCache 输出 buffer usage 位，gralloc 分配链，§6.10） |

各自目录内 `log-fail.txt`（阻塞现场）/ `log-pass.txt`（通过现场）为配套原始记录。

## 专项方案文档

| 文档 | 主题 |
|---|---|
| [gpu.md](gpu.md) | 让「厂商图形栈」在云机跑起来的完整计划与实施记录（可行性分档 L1/L2/L3、选型对比、Phase A~E、里程碑 M0~M4、Phase A/B 实测结论） |