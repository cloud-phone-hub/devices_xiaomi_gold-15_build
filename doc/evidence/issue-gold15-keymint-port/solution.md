# Issue: gold-15 移植 Remote KeyMint（原地替换官方 mitee 三件套）—— 解决方案

- 设备: Xiaomi Redmi Note 13 5G（gold）**全局版 ROM** `OS3.0.5.0.VNQMIXM`，云机 `devices/xiaomi/gold-15`
- 模板: `devices/xiaomi/note13-5G-15`（同硬件中国版，CDH 全套已跑通）
- 状态: **Remote KeyMint 端到端可用**（4 实例注册 + keystore2 建钥 + /data 元数据加密挂载成功）
- 依据: AGENTS.md 约束 2（先固证再推进）

---

## 1. 问题

gold 官方 vendor 的 keymint 是 **mitee（TEE 直连）**实现，云机无 TEE，故需把
keymint HAL 换成 CDH 的 **Remote KeyMint**（host 侧 `secure_env` + QEMU `virtconsole` 通道
`cdh-hvc3-keymint` = guest `/dev/hvc3`）。

需求：在**不改动 note13 任何文件**的前提下，于 gold-15 建出可运行的 Remote KeyMint，
并让它像 note13 一样被 keystore2 正常使用（进而撑起 `/data` 的元数据加密）。

## 2. 根因

gold 与 note13 的差异，逐项定位：

| # | 卡点 | 根因 |
|---|---|---|
| 1 | HAL 无法编译 | gold 官方 vendor 内 `libkeymaster_messages.so` **未导出** `keymaster::TAG_MODULE_HASH`（note13 有）→ 需强定义补齐 |
| 2 | HAL 起不来（status 127） | 注入层把 `bin/**` 强制 0644 → `init: cannot execv(...): Permission denied` |
| 3 | `/dev/hvc3` open 被拒 | 官方 ueventd 无 `/dev/hvc*` 规则 → 默认 `0600 root:root`，keymint（user `system`）`open()` EACCES → HAL abort |
| 4 | 看不到 HAL 致命日志 | LOG 落 `/dev/kmsg_debug`（tmpfs）被 SELinux 拒，只剩 SIGABRT 无因 |
| 5 | HAL 启动即 abort | `remote_keymaster.cpp:87-91` 对 `ro.vendor.boot_security_patch` `std::stoi`，gold 官方无此属性 → `invalid_argument` |
| 6 | init 加载策略机制不同 | gold `plat_sepolicy_and_mapping.sha256` 与 `precompiled_sepolicy.plat_policy_and_mapping.sha256` 不一致 → init 走 `OpenSplitPolicy()` 现场编译 `/vendor/etc/selinux/vendor_sepolicy.cil`（故**追加 vendor CIL 即生效**，无需重建 precompiled） |
| 7 | `/data` 加密失败、只读 | 官方 MIUI 策略只给 `update_engine_common` 访问 `/dev/block/vdb`（`vd_device`），缺 `vold`/`fsck` → `avc denied { read } vold -> vd_device blk_file` → `create_crypto_blk_dev failed` → `/data` 只读 |

## 3. 证据

- 阻塞现场与最终通过现场见同目录 `log-fail.txt` / `log-pass.txt`。
- 关键事实（实测）：
  - gold init 选择 CIL 现场编译：`precompiled_sepolicy.plat_sepolicy_and_mapping.sha256 differ`。
  - Remote KeyMint 注册 4 个实例（`log-pass.txt`）：
    `android.hardware.security.keymint.IKeyMintDevice/default`、
    `android.hardware.security.secureclock.ISecureClock/default`、
    `android.hardware.security.sharedsecret.ISharedSecret/default`、
    `android.hardware.security.keymint.IRemotelyProvisionedComponent/default`。
  - keystore2 起 pid，`servicemanager: Found android.system.keystore2.IKeystoreService/default`。
  - `vdc keymaster earlyBootEnded` 退出码 0。
  - vold 经 keystore2 建密钥后：`vold: Successfully encrypted f2fs filesystem on /dev/block/vdb`（元数据加密）→
    `__mount(source=/dev/block/mapper/userdata,target=/data,type=f2fs)=0: Success`。
  - host 侧 `runtime/secure-env/keymint-wire.log` 有约 10 KB 二进制报文（attestation 证书链等），
    证明 hvc3 通道**真正跑通业务**，非仅注册。
  - keymint / hvc3 / keystore 相关 `avc denied` = 0。

## 4. 改动

均只在 `devices/xiaomi/gold-15/` 内新增/修改，未触碰 note13：

1. `build/vendor/build/deps.py`（新增）：从官方 gold 镜像（EROFS）用容器 `dump.erofs --cat`
   提取 6 个链接期运行库到 `dist/vendor-compile-dep/`。
2. `build/vendor/build/source_build.py`（新增）：遍历 `build/vendor/source/*/build.py`。
3. `build/vendor/source/android.hardware.keymint/build.py`（新增）：编译 CDH 远程 keymint 并
   **原地替换官方 mitee 三件套**（二进制 / rc / vintf），并注释掉 `tee-supplicant.rc` 里的
   `enable vendor.keymint-mitee` 提前拉起钩子；rc 追加 ` /dev/hvc3` 通道参数 +
   `file /dev/kmsg w` + `stdio_to_kmsg`，前置 `on post-fs` 门控（`wait /dev/hvc3 30` + `start`）。
4. `build/vendor/source/android.hardware.keymint/standalone/`（新增 6 件）：`posix_channel.cpp`、
   `ndk-r29-compat.h`、`keymaster_tags_module_hash.cpp`（强定义补 `TAG_MODULE_HASH`）、
   `libcxx-platform/__config_site`、`common/libs/fs/shared_fd.h`、
   `common/libs/security/keymaster_channel_sharedfd.h`。
5. `build/vendor/build/sepolicy.py`（新增）：向 `vendor_sepolicy.cil` 追加
   `keymaster_device` 类型 + hvc3 chr_file 授权 + serialno 读 + `tmpfs` 写（kmsg_debug），
   以及 `vold`/`fsck` 对 `vd_device` 的 blk_file 读写；`vendor_file_contexts` 追加
   `/dev/hvc3 u:object_r:keymaster_device:s0`；`vendor_property_contexts` 追加
   `ro.vendor.boot_security_patch` 的 exact 规则（类型复用 `vendor_security_patch_level_prop`）。
6. `build/vendor/build/ueventd.py`（新增）：追加 `/dev/hvc*  0666  system  system`。
7. `build/vendor/build/props.py`（修改）：向 `/vendor/build.prop` 派生补
   `ro.vendor.boot_security_patch`（取自 `ro.vendor.build.security_patch`）。
8. `build/vendor/build/inject.py`（修改）：修注入树遍历（容器挂载点）、`bin/**` 可执行位 0755。
9. `build/vendor/build/vendor_builder.py`（修改）：接线 deps / source_build / sepolicy / ueventd。
10. `build/baselines/<build-id>.json`（修改）：`vendor.selinux-labels` 由 10 条增至 14 条（keymint 4 条）。

## 5. 验证

- 产物链：`build-vendor.py && build-super-lpmake.py && pack-roms.py` 全部成功（官方 super 余量为 0，
  故用 lpmake 重建 super，GPT 原地替换 `super`/`boot_a`/`vendor_boot_a`）。
- 引导实例 `out/core/daemon/runtime/gold-15/13` 实测（见 `log-pass.txt`）：
  - `/dev/hvc3` 在 post-fs 已存在（`wait for '/dev/hvc3' took 0ms`）；
  - Remote KeyMint 4 实例注册；
  - `/data` 元数据加密成功并挂载；
  - keymint 相关 SELinux 拒绝为 0。
- **判定：keymint 移植目标达成。**（keymint 链路的可用性由「/data 能加密」这一强依赖项背书。）

## 6. 遗留 / 下一步（**引导仍未到 `sys.boot_completed`，但与本 keymint 移植无关**）

gold-15 官方 ROM 与本环境存在**系统性差异**，导致系统起不到 boot_completed；这些是
独立于 keymint 的移植缺口，已定位但未处理：

1. **官方 ROM 是 64_32，本环境跑不了 32 位**（决定性阻塞）：
   gold 官方 ROM 为 `ro.zygote=zygote64_32`，含 `zygote_secondary`/`app_process32`/`drmserver32`/
   `aee_aed(v)`/`v3avpud`/`media.omx`/`media.c2-mediatek`/`cas@1.2`/`fuelgauged` 等 32 位件；
   实测这些件一律 `init: ... Exec format error`（`zygote_secondary` 退出 127），
   随后 init 把主 `zygote` 一起 SIGKILL 并重启——**zygote 重启 123 次，`system_server` 从未启动**。
   模板 note13 是**纯 64 位 ROM**（`service zygote_secondary not found`），故从未暴露此问题。
   （CDH 的 `cph_daemon.rc` 里已预置 `getprop ro.zygote` 探针，注释即「debug zygote64 注入是否生效」，
   说明团队已预期需要把 gold 强制为 `ro.zygote=zygote64`。）
2. **`/vendor/bin/daemon` 缺失**：`init: Could not start service 'daemon': Cannot find '/vendor/bin/daemon'`
   → CDH guest daemon 未构建/未注入（`build/vendor/source` 下 daemon 子项目未移植），
   adbkey 握手与自研 adbd 启动随之缺失（这也是 `adb: device offline` 的原因）。
3. **`pioneer` 域未移植**：gold 的 `sepolicy.py` 目前**只移植了 keymint 段**（153 行），
   而 note13 的该文件有约 20 段（1453 行：pioneer / audio / radio / sensors / thermal /
   vibrator / lights / gnss / chipinfo / wifi / media / camera / coredomain-vendor-prop…）。
   缺 `pioneer`/`pioneer_exec` 类型 → `init: cannot setexeccon('u:r:pioneer:s0') ... Invalid argument`、
   `Context u:object_r:pioneer_exec:s0 is not valid`，CDH 的 `daemon`/`netmgr_setup`/`keep_awake`/
   adbkey 等 **`seclabel u:r:pioneer:s0` 服务全线起不来**。
   （kernel 侧 `CONFIG_CDH_PIONEER_PERMISSIVE` 与 `cdh_mark_pioneer_permissive` 补丁 gold 已有。）
4. **gold-15 端到端引导验证能力**：需先补齐第 1～3 项；本 keymint 移植的最终判定已由
   「注册 + keystore2 建钥 + /data 加密」给出，不依赖 boot_completed。