# Issue: gold-15 内核构建流水线接入（kernel / boot / vendor_boot / vendor_dlkm 四段跑通，vendor 起阻塞）—— 解决方案

- 设备: Xiaomi Redmi Note 13 5G（gold）**全局版 ROM** `OS3.0.5.0.VNQMIXM`，云机 `devices/xiaomi/gold-15`
- 模板: `devices/xiaomi/note13-5G-15`（同硬件中国版，CDH 全套已跑通）
- 状态: **四段跑通**（2026-10-09）；`vendor / super / pack` 阻塞于**未移植的 vendor 阶段**
- 依据: AGENTS.md 约束 2（先固证再推进）

---

## 1. 问题

`devices/xiaomi/gold-15` 是一个新建的空骨架：`build/manifest.json`、`build/.gitignore` 是空文件，
`build/scripts`、`build/baselines` 为空目录。内核侧已按 note13-5G-15 移植
（`kernel/{build,modules,source}`，source 走 `gold-15` 分支）。本次要求**执行编译**。

按 note13 的流水线顺序逐段执行（`unpack → kernel → boot → vendor_boot → vendor → vendor_dlkm → super → pack`），
验证哪些能在 gold-15 上跑通、卡点在哪。

## 2. 根因

跑通过程中暴露两类"未移植依赖"，都不是脚本 bug：

| # | 卡点 | 根因 |
|---|---|---|
| 1 | `boot` 阶段 `FileNotFoundError: build/vendor/patch/etc/fstab.mt6833` | `build-boot.py` 的 first_stage fstab **单一来源**就是 vendor patch 树那份（与二阶段 `/vendor/etc/fstab.mt6833` 同源），gold-15 尚未搬入该树 |
| 2 | `vendor_dlkm` 阶段缺 `vendor-dlkm.baseline-build-prop` | baselines 中该键原先为 `null`（哈希/提取件类数据按约定不臆造，需 unpack 后实测） |
| 3 | `vendor / super / pack` 仍阻塞 | `build-vendor.py` + `build/vendor/{build,source}`（1499 文件 / 17 个 HAL 子项目）**未移植**；`build-super.py` 需 `out/dist/rom/vendor.img`，`pack-roms.py` 需 `super.img` |

## 3. 证据

原始报错与完整产物清单见同目录 `log-fail.txt`（阻塞现场）与 `log-pass.txt`（四段产物 + 关键日志）。

关键事实：

- `unpack` 已完成（另见 baselines 的 `rom.images`，9 个镜像 size/sha256 实测）。
- 内核编译 recipe 与 note13 完全一致：`lineage-22.1-gki-virtio-wifi-cdh-modules-v28`，
  `kernelrelease = 6.6.127-4k-cdh`；builder 镜像复用本地 `cloud-phone-hub-ubuntu-builder:latest`。
- 官方 fstab 内容属**板级 CDH 配置**（`/dev/block/vdc` + `mi_ext` + `vdb` data），board 同为 `mt6833`，
  故位置级复用成立。

## 4. 改动

均为**新增**，未修改 note13-5G-15 任何文件：

1. `build/vendor/patch/`（16 文件）自 note13 拷入：`etc/fstab.mt6833` + `etc/init/*.rc` +
   `etc/permissions/*.xml` + `usr/idc/*.idc` + `bin/{logcat-dump,netmgr_setup,setup_display.sh}`。
   boot 阶段只消费 `etc/fstab.mt6833`，其余待 vendor 阶段使用。
2. `build/baselines/<build-id>.json` 的 `vendor-dlkm.baseline-build-prop` 由 `null` 回填为实测值：
   `/etc/build.prop`，`length 865`，`sha256 0ee4e98c092a83f77c9e144544c5cd298ebdf7e17c2ba23b4468b8ec2b5a49df`，
   `label u:object_r:vendor_configs_file:s0`。
3. （前序）`build-super.py` 新增：以官方 super 为基底，原位替换 `vendor_a` / `vendor_dlkm_a`。

## 5. 验证

四段产物（`devices/xiaomi/gold-15/out/`，sha256 见 `log-pass.txt`）：

| 阶段 | 产物 | 大小 | 判定依据 |
|---|---|---|---|
| kernel | `out/kernel/Image` | 32295424 | `kernelrelease.txt = 6.6.127-4k-cdh`；`build-recipe.txt` 与 note13 一致；builder 内 11 个 `.ko` 全部 `OK` 并过 SHA256SUMS 自校验 |
| kernel | `out/dist/kernel/modules/*.ko` | 11 个 | 归集目录计数 11 |
| boot | `out/dist/rom/boot.img` | 83886080 | first_stage fstab 注入 9 项；kernel_size=32295424（= 自编译 Image）；os_version=0x1e00018b 保留 |
| vendor_boot | `out/dist/rom/vendor_boot.img` | 20961556 | fragment0 删 fstab 3 项；cmdline `verifiedbootstate=green`、`androidboot.hardware=mt6833` |
| vendor_dlkm | `out/dist/rom/vendor_dlkm.img` | 13209600 | 11 个 .ko 写入 + 回读校验通过 + `e2fsck -fy` 通过；`/etc/build.prop` 回读 `Size: 865`（与基线一致） |

两处修复的独立复核：
- `vendor-dlkm.baseline-build-prop`：`dump.erofs --path=/etc/build.prop --cat` 与
  `fsck.erofs --extract=… --path=/etc/build.prop` 两条路径取出的字节**完全一致**（len 865 / 同 sha256）。
- 官方 super 的逻辑分区与容量（供 super 阶段判断）：16 个（含 `_b`），
  `vendor_a` 分配 1372213248 B、`vendor_dlkm_a` 13983744 B，**余量 0**。

## 6. 遗留 / 下一步

1. **vendor 阶段未移植**（当前唯一阻塞）：`build-vendor.py` + `build/vendor/build/`（12 个驱动模块）
   + `build/vendor/source/`（17 个 HAL 子项目：audio 276 / radio 264 / camera 214 / media.v412 181 /
   wifi 151 / common 137 / media 75 / gnss 54 / sensors 29 / thermal 20 / health 19 / vibrator 19 /
   light 14 / keymint 8 / gpu 5 / board 3 / daemon 1＝1499 文件，9.5 MB）。移植时需做设备名改适
   （HAL 可执行名 `cdh-note13-*` → `cdh-gold-*`、`*.cdh-note13` 等）。
2. **baselines 待 dump**：`vendor.selinux-labels`（note13 有 173 条，自官方 gold images dump）与
   `vendor.build-prop-sets`（700 条）——vendor 阶段消费；当前为空。
3. **super 容量硬约束**：官方 super 无冗余（余量 0），替换用的 `vendor.img` / `vendor_dlkm.img`
   必须 ≤ `vendor_a` 1372213248 B / `vendor_dlkm_a` 13983744 B，超出时 `build-super.py` 直接拒绝。
4. **命名/接线差异**：note13 的 super 阶段接的是 `pack_super.py`（官方 super 零改动直拷，
   因其走 `patch.img`(vdc) 路线）；"替换 vendor/vendor_dlkm" 的逻辑在未接线的 `pack_super_v2.py`。
   gold-15 按需求做成了 `build-super.py`，将来移植 `main.py` 时 `STAGES` 的 `"super"` 要指向它。
5. **`kernel/modules/device_info/uts/uts.c` 仍是 note13 的值**（`6.6.89-android15-8-gbe8d201b0d27-ab13762941-4k` /
   `Thu Jul 10 06:35:14 UTC 2025`）。gold-15 ROM 实测内核 banner 为
   `6.6.118-android15-8-ge56cf6b09cca-ab15511674-4k #1 SMP PREEMPT Thu May 28 07:48:46 UTC 2026`；
   是否替换需确认该字段口径（note13 的取值与其 ROM 库存内核 6.6.66 并不相同，疑为真机实测值）。
6. **尚未端到端引导验证**：`vda.img` 需 `super` + `pack` 两段，当前无法产出，故本次只到"产物齐"为止。