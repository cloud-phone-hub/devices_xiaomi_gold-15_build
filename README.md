# note13-5G-15 云手机架构

> Redmi Note 13 5G（MTK MT6833，gold OS3.0.9.0.VNQCNXM / Android 15）的云手机（virtio 云主机）构建架构。
> 核心目标：**官方 super.img 字节级零改动**，系统功能（virtio HAL）与真机身份（挂载/内容视角）兼得。

---

## 1. 架构总览

```
┌── 官方 super.img（MTK，零改动，QEMU vda 挂载）───────────────┐
│  /system        MIUI system（官方）                          │
│  /vendor        MTK vendor（erofs，挂载特征=真机）            │
│  /vendor_dlkm   MTK vendor_dlkm（erofs）                     │
└──────────────────────────────────────────────────────────────┘
┌── patch.img（lineage virtio 内容，独立 vdc 块设备）───────────┐
│  镜像根 = /mnt/vendor   lineage virtio vendor 全集（ext4）    │
│  镜像根/vendor_dlkm     lineage virtio vendor_dlkm 全集       │
└──────────────────────────────────────────────────────────────┘
┌── 自编译内核 6.6.127-4k-cdh（getname_flags 路径重定向）────────┐
│  /vendor/etc|bin|lib|lib64  → /mnt/vendor/...                 │
│  /vendor_dlkm/lib           → /mnt/vendor/vendor_dlkm/lib     │
│  /vendor/build.prop         → /mnt/vendor/build.prop          │
│  /vendor/ueventd.rc         → /mnt/vendor/ueventd.rc          │
│  /odm/etc/selinux/odm_sepolicy.cil → 屏蔽（官方 MTK 策略，与  │
│    lineage vendor 策略不兼容；见 2.2.2）                      │
└──────────────────────────────────────────────────────────────┘
```

- **系统进程**访问 `/vendor/...` 被内核重定向到 `/mnt/vendor/...` → 拿到 lineage virtio HAL/库/rc，功能正常。
- **应用/验证**读 `/proc/mounts` 只见官方 `/vendor`（erofs ro，真机样子）+ 一条 `/mnt/vendor` 下的 ext4（真机在 /mnt/vendor 下本来就有 nvdata/nvcfg/protect_[fs] 等 ext4 挂载，形态一致）。
- **官方 super.img 全程不写入**；lineage 内容全部在独立 vdc（patch.img）里。

---

## 2. 三大支柱

### 2.1 内核路径重定向（功能来源）

| 项 | 内容 |
|---|---|
| 内核 | 6.6.127-4k-cdh（GKI android15-6.6 基线 + virtio 配置 + 模块） |
| Hook 点 | `getname_flags()`（fs/namei.c）——所有用户态路径 syscall 的共同上游，单点覆盖 open/exec/stat/access/readlink |
| 开关 | `CONFIG_CDH_PATH_REDIRECT=y`（kernel/build/lineage-kernel.config） |
| 规则 | 白名单匹配后才改写；未命中一律走官方 |

**白名单（当前）**：

| 白名单 | 类型 | 重定向到 |
|---|---|---|
| `/vendor/etc` | 目录（含子树） | `/mnt/vendor/etc/...` |
| `/vendor/bin` | 目录（含子树） | `/mnt/vendor/bin/...` |
| `/vendor/lib` | 目录（含子树） | `/mnt/vendor/lib/...` |
| `/vendor/lib64` | 目录（含子树） | `/mnt/vendor/lib64/...` |
| `/vendor_dlkm/lib` | 目录（含子树） | `/mnt/vendor/vendor_dlkm/lib/...` |
| `/vendor/build.prop` | 文件（精确） | `/mnt/vendor/build.prop` |
| `/vendor/ueventd.rc` | 文件（精确） | `/mnt/vendor/ueventd.rc` |
| `/odm/etc/selinux/odm_sepolicy.cil` | 文件（精确，屏蔽） | `/mnt/vendor/odm/...`（不存在 → ENOENT，见 2.2.2） |
| `/system/bin/update_verifier` | 文件（精确，屏蔽） | `/mnt/vendor/system/bin/...`（不存在 → ENOENT，见 2.2.3） |

实现要点：
- 逐条"替换前缀"（规则表自带 target），不是统一插入：patch.img 的镜像根就是 vendor 树，而 `/vendor_dlkm` 是镜像根下的子目录，统一插入会算出挂载点之外的 `/mnt/vendor_dlkm`。长度护栏防嵌入式缓冲区溢出；裸 `/vendor`、`/mnt`、`/mnt/vendor` 天然排除（不劫持 first-stage 挂载与 /mnt 的 tmpfs 挂载）。
- 调试：`pr_info_ratelimited("cdh_redirect: %s\n")` → `dmesg | grep cdh_redirect` 可看命中路径。注意该限速（10 条/5s）会吞掉后续打印，日志里没有 `cdh_redirect` 不等于没重定向。
- 改动已提交进内核仓（fs/namei.c + fs/Kconfig）。

#### 2.1.1 内核强制 pioneer 域 permissive（guest root 语义）

| 项 | 内容 |
|---|---|
| 内核 | 同上（`security/selinux/ss/services.c` 的 `cdh_mark_pioneer_permissive()`） |
| 开关 | `CONFIG_CDH_PIONEER_PERMISSIVE=y`（kernel/build/lineage-kernel.config） |
| 落点 | `security_load_policy()` 内 `policydb_load_isids()` 之后、`selinux_initialized()` 分支之前（首载与重载两条返回路径都覆盖；此刻 `newpolicy->policydb` 尚未 RCU 发布，无并发） |
| 手段 | `symtab_search(&p->p_types, "pioneer")` 取 type id → `ebitmap_set_bit(&p->permissive_map, id, 1)` |
| 等价于 | 策略文件里的 `(permissive pioneer)`，但免疫 vendor_sepolicy.cil 追加段互相覆盖 |

**为什么放内核**：pioneer 域（guest 值守 daemon + 其自研 adbd 的 shell/sync 子进程，`user/group root` 且不降权）要求完全 root 语义——凡以它为主体的 AVC 判定一律放行。逐条 allow 枚举既写不全也易碎；而本盘 init 内嵌 secilc **不支持 CIL `permissive` 语句**（Unknown keyword → init fatal），策略侧无法自声明，故 permissive 只能由内核置位。

裁判链本身不变：`security_compute_av()` 见 `permissive_map` 置位后给 `avd->flags |= AVD_FLAGS_PERMISSIVE`，`avc_denied()` 据此放行（**审计日志保留**，denial 仍进 avc log，只是不再返回 `-EACCES`）。

**覆盖面**：permissive 只看 AVC 判定的**源上下文 type**。故策略段仍需保留：
- 启动链（源域是 `init_{vers}`：换域 execve 前置的 `process transition` / `pioneer_exec:file execute`）；
- 反向授权（源域是对端域、目标才是 pioneer：`netd` fwmark fd / `system_server_{vers}` 的 stdio 管道与 socketpair / 媒体域 binder 回程）；
- 类型与属性声明本身（否则 init 的 `seclabel u:r:pioneer:s0` 解析失败 → `Invalid argument`，见 `doc/evidence/issue-pioneer-selinux-overwrite/`）。

详见 `build/vendor/build/sepolicy.py`（`pioneer_cil()`，枚举上述保留项并注明理由）与 `.trae/documents/pioneer-domain-kernel-permissive.md`。

### 2.2 patch.img（lineage 内容载体）

| 项 | 内容 |
|---|---|
| 文件系统 | ext4 |
| 结构 | **镜像根 = vendor 树**（= lineage vendor_a 内容），根下另有 `vendor_dlkm/` 子树与自供目录（`EFI`、`persist`、`shared` 三个 mount_all 挂载点 + `mi_ext` 分区挂载点） |
| 标签 | 自源镜像逐文件提取 `security.selinux`（ea_list）+ mode/uid/gid（stat），写入 patch.img（ea_set/sif） |
| 加载 | QEMU vdc 块设备 → `mount -t ext4 -o ro /dev/block/vdc /mnt/vendor` |
| fstab | `/dev/block/vdc /mnt/vendor ext4 ro first_stage_mount`（boot ramdisk） |

要点：标签与源 lineage 分区一致 → 与当前"lineage vendor 直接作 /vendor"运行态等价，功能不回归。

#### 2.2.1 挂载接线（vdc）—— 要修改的文件

| # | 文件 / 参数 | 改动 |
|---|---|---|
| 1 | `build/scripts/build-boot.py`（`FIRSTSTAGE_FSTAB` 常量） | 加一行 `/dev/block/vdc /mnt/vendor ext4 ro first_stage_mount` → 重建 boot.img（自编译内核 + 重定向 + fstab）→ 写进 vda 的 boot 分区 |
| 2 | daemon 启动参数（**非源码**） | `daemon run ... -gsi-image out/dist/rom/patch.img` → vdc 就位。daemon 已实现 vdc（`-drive file=...,id=vdc,format=raw,readonly=on` + `virtio-blk-pci,drive=vdc,bootindex=2`），单盘模型默认不挂、显式传即挂 |
| 3 | `patch.img`（构建期） | ext4，镜像根即 vendor 树 + `vendor_dlkm/` 子树 + 自供挂载点目录，逐文件自源镜像保留 `security.selinux` 标签 + mode/uid/gid |
| 4 | SELinux（按需） | 挂载点必须是 first_stage 已建好的目录（见 2.2.2）；若 init 挂载被拒（mounton），在注入的 vendor_sepolicy.cil 补 allow |

时序：vdc 必须 `first_stage_mount`（boot ramdisk fstab），早于 second-stage 读 `/vendor/etc/init`——重定向目标先就位。

#### 2.2.2 挂载点选型：为什么是 /mnt/vendor（而非 /patch、/debug_ramdisk、/mi_ext、/mnt）

init 首阶段只挂 `/metadata`、`/system` 后 `SwitchRoot` 到 `/system`，**其余 first_stage 条目在 switch root 之后挂载**，因此挂载点必须已存在（首阶段 ramdisk 或 system_a 里，不能现场新建）。实测结论：

| 候选 | 结果 |
|---|---|
| `/patch` | 官方 system_a 无此目录 → `failed to realpath(/patch)` → InitFatalReboot |
| `/debug_ramdisk` | 目录存在且第一阶段可用（file contexts 能读出），但 init 第二阶段执行 `mount("tmpfs","/debug_ramdisk",...)`（system/core/init/setup_mounts.cpp）**遮蔽**该挂载点 → 第二阶段所有重定向读 ENOENT（`Failed to read /vendor/etc/selinux/plat_sepolicy_vers.txt` → InitFatalReboot） |
| `/mi_ext` | 能挂、能跑，但**反指纹上是可证伪的强信号**：真机 `/mi_ext` = mi_ext_a（erofs，仅 156 KB，内容只有 etc/product/system/system_ext），App 的 `/proc/self/maps` 里出现 `/mi_ext/vendor/lib64/*.so` 与该分区内容自相矛盾（实测 device-checker `proc.selfidentity` 128 行）。已弃用 |
| `/mnt` | 能挂（first_stage_init 在 DoFirstStageMount **之前**挂 /mnt 的 tmpfs，SwitchRoot 又 MS_MOVE 进新根，不会被遮蔽），但**只读分区会打死运行时**：first_stage 只 mkdir 了 `/mnt/vendor`、`/mnt/product`，其余骨架全是二阶段 init.rc `on init` 的 mkdir，zygote 还要 `fs_prepare_dir("/mnt/user/0")`、vold 要建 pass_through/runtime 视图、netmgr 要在 `/mnt/run` 建 adb_net 软链 —— 实测 system_server 直接 `JNI FatalError: fs_prepare_dir failed on /mnt/user/0: Read-only file` 反复重启。把整棵骨架按实测值烘进镜像也救不回 chmod/chown 类调用 |
| `/mnt/vendor` ✅ | first_stage_init 在 /mnt 的 tmpfs 上建好该目录（注释即 "so that first stage mount can mount subdirectories of /mnt/{vendor,product}/"），switch root 后仍在；/mnt 本身保持可写 tmpfs，骨架一行都不用复刻。真机 `/mnt/vendor/...` 本就存在（mi_ext/persist/nvdata/nvcfg/protect_f/protect_s，其中多个是 ext4），/proc/mounts 与 maps 的形态都不可证伪 |

init 自己会挂 tmpfs 的目录（必须避开）：`/mnt`、`/second_stage_resources`、`/apex`、`/bootstrap-apex`、`/dev`、`/linkerconfig`、`/debug_ramdisk`。

挂到 `/mnt/vendor` 的连带要求（build-patch.py 已处理）：
- fstab 里 `/mnt/vendor/{EFI,persist,shared}` 由二阶段 mount_all 挂载，挂载点必须已存在 → 镜像根自带这三个空目录（标签按 file_contexts 最具体匹配：`mnt_vendor_file` / `persist_file`）。
- `/mnt/vendor/mi_ext` 是真 mi_ext 分区（官方 super 内 `mi_ext_a`，erofs）的挂载点：真机 MTK fstab 同样 first_stage 挂这里再 bind 到 `/mi_ext`，init 随之**原生加载真分区**里的 `etc/build.prop` 并解析 `etc/init/init.miui.mi_ext.rc`（`ro.mi.os.*` / `ro.ai.os.*` 等属性全部来自真机文件，无复刻件、无 pioneer 兜底服务）。云机侧唯一差异是挂载点必须预置在 patch.img 里（只读 ext4 上 fs_mgr 无法 mkdir），挂载条目见 `build/scripts/build-boot.py` 的 `FIRSTSTAGE_FSTAB`（`mi_ext` 两行，排在 vdc 之后以保证不被遮蔽）。
- vendor 树自带的 `odm -> /odm` 是**绝对符号链接**，摊平到镜像根后会让 `/mnt/vendor/odm/...` 在 path_walk 内解析回官方 `/odm`（符号链接目标不经过 getname_flags 重定向），使 `odm_sepolicy.cil` 的屏蔽反噬（secilc 失败 → InitFatalReboot）。构建期把它替换为真实空目录，并用 `MUST_ABSENT` 断言两个屏蔽落点真的不存在。

同机其它问题：官方 `/odm/etc` 是指向 `/vendor/odm/etc` 的符号链接，官方 vendor 的 `odm_sepolicy.cil`（624B）引用 MTK 专属类型（`thermal_data_file`、`teei_data_file`、`hal_dumpstate_impl`、`servicemanager_202404`），与本项目实际加载的 lineage vendor 策略不兼容 → secilc 解析失败 `Failed to resolve allow statement at /odm/etc/selinux/odm_sepolicy.cil:1` → init 重启。处理：内核白名单加一条把该文件指到不存在的 `/mnt/vendor/odm/...`（ENOENT → init 跳过 odm 策略，等价 lineage 无 odm 策略的情形），其余 odm 内容保持官方原样。

**patch.img 根目录必须带标签**（`build-patch.py` 直接取 vendor.img 根 inode 的 meta）：挂载后 `/mnt/vendor` 的根 inode 若无 `security.selinux`，mount 根为 `unlabeled`，各域 traverse `/mnt/vendor/...` 被 SELinux 拒（`avc denied { search } tclass=dir`）。现象很容易误判为"重定向没生效"：`linkerconfig` 读不到 vendor 配置、linker 拿不到命名空间（`Warning: failed to find generated linker configuration`）、随后所有进程出现 `CANNOT LINK EXECUTABLE ... library "xxx.so" not found`、VINTF 报 `/vendor/etc/vintf/manifest.xml: Permission denied`。根目录沿用 `u:object_r:vendor_file:s0`（与官方 `/vendor` 挂载根一致）后全部消失。

#### 2.2.3 update_verifier 屏蔽（A/B bootctrl HAL 缺失）

MIUI `init.rc` 在 `on zygote-start` 里 `exec_start update_verifier`——**SVC_EXEC 会阻塞动作队列**，而该服务要取 A/B bootctrl HAL（`android.hardware.boot`）来标记当前槽位；我们移植的是 lineage virtio vendor，不含该 HAL，于是：

```
update_verifier: AIDL IBootControl not available, falling back to HIDL.
update_verifier: Error getting bootctrl v1.0 module.
update_verifier: Error getting bootctrl module.
init: Received sys.powerctl='reboot' from pid: 402 (/system/bin/update_verifier)
```

→ 约 7.5s 一次主动 reboot 的**引导死循环**，且 zygote/SurfaceFlinger 永远没机会启动（动作队列被 SVC_EXEC 卡住）。云机既无 dm-verity 也不靠 AVB 槽位状态（槽位由 GRUB 的 `persist/grubenv_abootctrl` 维护），故在内核白名单里把 `/system/bin/update_verifier` 指到不存在的 `/mnt/vendor/system/bin/...`（exec ENOENT → init 跳过该服务）。

### 2.3 boot.img（引导链）

| 项 | 内容 |
|---|---|
| 基底 | lineage-22.0 `boot_a.img`（内嵌 ramdisk，EDK II 可 EFI-stub 直启；官方 gold 无可用 boot.img） |
| 内核 | 替换为自编译 6.6.127-4k-cdh Image |
| ramdisk | 注入 first_stage virtio fstab（各名落根/system/etc/first_stage_ramdisk 四处）+ vdc `/mnt/vendor` 条目 |
| os_version | 保留基底值 0x1e00018b（当前能引导 = EDK II 接受值） |
| 产物 | out/dist/rom/boot.img |

### 2.4 vendor_boot.img（fstab 副本清理）

GRUB 的 `eval initrd ${boot_partition}/ramdisk.img ...` 会把 boot.img 的 ramdisk 排在前面、vendor_boot 的 ramdisk fragment 排在后面；initramfs 解包时**后者覆盖前者**。lineage vendor_boot 的 platform fragment 里带一份 lineage 原版 `first_stage_ramdisk/system/etc/fstab.virtio`（含 `odm` 挂载、无 vdc `/mnt/vendor`），会盖掉 boot.img 注入的 fstab，导致 init 去挂官方 super 中不存在的 odm 分区。

| 项 | 内容 |
|---|---|
| 基底 | lineage-22.0 `vendor_boot_a.img`（v4 header、page 2048） |
| fragment | `frag0` platform（lz4+newc cpio，含 fstab 副本）、`frag1` recovery（保留不动） |
| 处理 | `build/scripts/build-vendor-boot.py`：解出 frag0 → newc cpio 删除 3 个 fstab（`fstab.virtio`、`fstab.virtio.gsi.sda`、`fstab.virtio.gsi.vdc`）→ 重压 → 同步更新 header `vendor_ramdisk_size` 与 fragment table 的 size/offset |
| 效果 | boot.img 的 fstab 成为**唯一来源** |
| 产物 | out/dist/rom/vendor_boot.img（由 pack-roms.py 写入 vda 的 `vendor_boot_a/b`） |

> 改 vendor_boot 时**必须**同步更新 fragment table（`header + vrs` 之后、`vendor_ramdisk_table` 每个 entry 的 ramdisk_size/ramdisk_offset）与 header 的 `vendor_ramdisk_size`；只改 size 不更新 table 会让 GRUB 加载 fragment 失败（initrd 只剩 boot ramdisk，init 报 `ReadDefaultFstab(): failed to find device default fstab`）。

---

## 3. 决策记录（为什么不用其它方案）

| 方案 | 问题 |
|---|---|
| bind-mount / overlayfs 挂 /vendor | `/proc/mounts` 暴露 `bind`/`overlay` 类型与 lower/upper 路径 → 挂载特征检测失败；overlay 需 P0 伪装 patch 才可藏 |
| EROFS 重建（官方 vendor 底座） | EROFS **无原地编辑工具**（无空闲块/inode 位图），加文件必然重排全部 inode；mtime 可保但 inode 不可保 |
| 往官方 super 追加分区 | 改 super 元数据 → 违背"零改动" |
| vendor_patch + 重定向 `/vendor/etc/init` | 需逐目录 bind（暴露）；且 init/linker/hwservicemanager 硬编码 /vendor/lib64 等路径，重定向 init 不够 |
| uid 分流（应用走官方、系统走 patch） | 可行（`current_fsuid() >= 10000` 不重定向），但 property 层（build.prop→getprop）不受文件级分流管，需另配基线 |

**正解**：内核 `getname_flags` 路径重定向 + 独立 vdc(patch.img) + 官方 super 零改动——内容与挂载特征分离，互不干扰。

---

## 4. 构建流水线

main.py STAGES：`unpack → kernel → boot → vendor_boot → vendor → vendor_dlkm → super → pack`

| 阶段 | 脚本 | 职责 | 产物 |
|---|---|---|---|
| unpack | unpack-roms.py | 解官方 tgz / lineage 底包 | roms/*, lineage-22.0/* |
| kernel | build-kernel.py → kernel/build/build-kernel.sh | 编译内核/模块（docker；带 CDH_PATH_REDIRECT、CDH_PIONEER_PERMISSIVE 开关，见 2.1/2.1.1；改动 recipe 版本号后缓存失效才重编） | out/kernel/Image、out/dist/kernel/modules/*.ko |
| boot | build-boot.py | 打包 boot.img（内核+ramdisk+fstab） | out/dist/rom/boot.img |
| vendor_boot | build-vendor-boot.py | 删 vendor_boot fragment 内 fstab 副本 | out/dist/rom/vendor_boot.img |
| vendor_dlkm | build-vendor_dlkm.py | 注入自编译模块到 vendor_dlkm | out/dist/rom/vendor_dlkm.img |
| patch | build-patch.py | 修补后 vendor/vendor_dlkm → ext4+标签 | out/dist/rom/patch.img |
| super | pack_super.py（官方 super 直拷，零改动） | 官方 super.img 字节级复制 | out/dist/rom/super.img |
| pack | pack-roms.py | vda/vdb/patch 组装 + 写 boot_a/b、vendor_boot_a/b | out/device/note13-5g/15/os3.0.9.0.vnqcnxm/{vda.img,data.qcow2,patch.img} |

**新链路（官方 super 零改动路线，已落地）**：
```
patch.img 构建（build-patch.py，vdc 挂 /mnt/vendor）
   + build-boot.py fstab 加 vdc /mnt/vendor（boot.img，内核含重定向）
   + build-vendor-boot.py 删 vendor_boot fragment 内 fstab 副本（否则覆盖 boot fstab）
   + pack_super.py 官方 super 直拷（零改动）
   + pack-roms.py 写入 vda：super / boot_a+b / vendor_boot_a+b + 带出 patch.img
   + daemon applyDeviceImage 检测 patch.img -> -gsi-image（vdc）
   + 官方 super.img 原样进 vda
```

---

## 5. 验证

| 验证点 | 方法 |
|---|---|
| 内核为我们所编 | `qemu.log` 里 `Linux version 6.6.127-4k-cdh`（不是官方 6.6.89） |
| 引导链槽位 | `BOOT_IMAGE=(hd0,gpt9)/kernel` + `androidboot.slot_suffix=_a`（persist 里 `abootctrl_global_active_slot=a`；overlay 缓存旧 persist 会让 GRUB 走 _b → 挂 system_b 失败） |
| 重定向命中 | `adb shell dmesg \| grep cdh_redirect`（应为 `/mnt/vendor/...`） |
| vdc 挂载 | `qemu.log` 里 `__mount(source=/dev/block/vdc,target=/mnt/vendor,type=ext4)=0: Success` |
| 挂载特征 | `adb shell cat /proc/mounts \| grep vendor`（官方 erofs ro + `/mnt/vendor` 一条 ext4 ro，与真机 /mnt/vendor 下的 nvdata/nvcfg/protect_[fs] 同形态） |
| maps 反指纹 | device-checker `proc.selfidentity`：`mi_ext_count` 应为 0（App 进程内严格读 /proc/self/maps）；vendor 行前缀应为 `/mnt/vendor/` |
| 功能 | 系统正常启动、图形/WiFi/音频等 virtio HAL 生效 |
| patch.img | debugfs 抽查内容/mode/uid/gid/security.selinux 与源镜像一致 |
| pioneer permissive | `adb shell dmesg \| grep "cdh: pioneer"` 应为 `pioneer (type N) forced permissive`；`qemu.log` 无 `Context u:object_r:pioneer_exec:s0 is not valid` |
| guest root | `adb shell id` = `uid=0(root) … context=u:r:pioneer:s0`；`getenforce` 仍 `Enforcing`（放行只针对 pioneer 域） |

> 排查提示：**adb 设备要确认是 guest 还是真机**——真机 USB（`usb:` 序列号）连上时很容易误判（官方内核 6.6.89、uptime 很大都不是 guest）；guest 的 adb 走 QEMU virtio-serial `hvc8`（daemon 持有）。

---

## 6. BatteryService / health HAL 修复方案（当前卡点）

### 6.1 现状证据

32 位问题解决后，引导推进到 system_server：`BatteryService` 起不来 → FATAL 崩溃循环
（实测每 ~64s 一轮，`grep 'Entered the Android system server'` 已 36 次，永不到
boot_completed）：

```
java.lang.RuntimeException: Failed to start service com.android.server.BatteryService
Caused by: java.util.NoSuchElementException: IHealth service instance default isn't available
```

即框架要 `android.hardware.health.IHealth/default`（HIDL health HAL），而当前 guest
的 `/vendor/apex` 是**官方的**（只有 cas/widevine，无 health），health HAL 缺失。

### 6.2 根因：APEX 加载机制

apdex（apexd）**自动扫描固定目录**发现并激活 APEX，无配置文件登记：
`/system/apex、/system_ext/apex、/vendor/apex、/product/apex、/odm/apex、/data/apex`。
目录下的 `.apex` 文件被发现后依次：解析内部 manifest → 签名校验 → 挂载到
`/apex/<name>@<version>` 并注册 binder 服务。`/apex/apex-info-list.xml` 是激活**结果**
（运行期生成），不是输入配置。

lineage virtio vendor 的 `/vendor/apex` 有 7 个 **cuttlefish 专用 HAL APEX**：
`com.google.cf.health`、`com.android.hardware.{audio,boot.virt,drm.clearkey,memtrack,power,sensors}.apex`。
官方 vendor 的 `/vendor/apex` 只有 `cas` + `widevine`。

### 6.3 方案 A（推荐）：重定向 /vendor/apex

内核白名单加一条目录规则 `"/vendor/apex" -> "/mnt/vendor/apex"`，apexd 扫
`/vendor/apex` 时经重定向读到 lineage 的 7 个 HAL APEX → 自动激活 →
`com.google.cf.health.apex` 注册 IHealth → BatteryService 通过，且 power/sensors/
audio/boot 一并就位（消除后续可能出现的同类缺 HAL 问题）。

- 改动：`kernel/source/fs/namei.c` 一行 `{ "/vendor/apex", ... , true }` + 重编内核
  （bump recipe）+ 重打 boot/patch + 重启。
- **代价（身份视角）**：`/proc/mounts` 会出现 `/apex/com.google.cf.health`、
  `/apex/com.android.hardware.power|sensors|audio|boot.virt|memtrack|drm.clearkey`
  等挂载（真机没有）。普通 app 无法连接 IHealth binder（那是 system_server 的），
  能观测到的只有这些挂载条目名。
- **缓解**：后续用 P0-1 挂载隐藏（`fs/namespace.c` 的 show_mountinfo/show_vfsmnt
  按路径前缀 `SEQ_SKIP`）把 `/apex/com.google.cf.*`、`/apex/com.android.hardware.*`
  藏掉——只影响 /proc 输出，不影响真实挂载与 binder，做完 app 视角回到真机一致。

### 6.4 方案 B：保持官方 /vendor/apex + 移植官方 health HAL

真机（gold OS3.0.9.0）vendor 的 `/vendor/apex` 确实只有 cas/widevine；health 不在
APEX 里，而是 `/vendor/bin/hw/android.hardware.health-service.example`（AOSP generic
health 服务），依赖 `libpng / libbinder_ndk / android.hardware.health-V3-ndk`（官方
vendor lib64 有、patch.img 无）+ 官方 rc `android.hardware.health-service.example.rc`。

- 改动：从官方 vendor 把这些文件移植进 patch.img（走 inject），health 以独立服务
  起来，官方 /vendor/apex 保持 → 挂载视角与真机零差异。
- 风险：该实现是 AOSP generic（读 `/sys/class/power_supply`，virtio 云机无电池）；
  cuttlefish 的 cf.health 才是专门处理虚拟电池的实现。移植后电量状态可能异常/服务
  起不来，且依赖闭包（示例二进制 +3 库 + rc + manifest 声明 + selinux label）工作量大。

### 6.5 结论与建议

功能优先 + 事后隐藏：**先走方案 A**（一行白名单让系统全链路跑通，同时暴露后续缺 HAL
底牌），验证整机功能；把"隐藏 /apex 挂载"并进 P0-1 挂载隐藏进一步收敛 app 视角。
方案 B 作为"零 APEX 暴露"的长期选项，待方案 A 验证 battery/功能细节后再评估。

---

## 7. 已知缺口 / 下一步

- [x] **白名单扩展**：`/vendor/ueventd.rc` + `/odm/etc/selinux/odm_sepolicy.cil`（屏蔽）
- [x] **daemon 挂 vdc**：走 `-gsi-image patch.img`（daemon 现成实现），单盘模型显式传即挂
- [x] **patch.img 构建脚本**：`build/scripts/build-patch.py`——ext4 + 自 out/dist/rom/vendor.img/vendor_dlkm.img（修补后产物）逐文件回填 mode/uid/gid/security.selinux，已验证与源一致
- [x] **build-boot.py fstab 加 vdc /mnt/vendor** + 重建 boot.img
- [x] **vendor_boot fragment fstab 副本清理**（build-vendor-boot.py，否则 odm 挂载失败）
- [x] **挂载点选型**：`/patch` 不存在、`/debug_ramdisk` 被 init 二阶段 tmpfs 遮蔽 → `/mi_ext`（可跑但 maps 可证伪）→ `/mnt`（只读分区打死 zygote/vold）→ 定稿 `/mnt/vendor`（见 2.2.2）
- [x] **patch.img 根目录标签**（`u:object_r:vendor_file:s0`）：否则 mount 根 unlabeled，重定向读全被 SELinux 拒（表现为 linker 找不到库/VINTF Permission denied）
- [x] **屏蔽 `/system/bin/update_verifier`**：缺 A/B bootctrl HAL → 主动 reboot + 阻塞 zygote-start → 引导死循环
- [x] **mi_ext 真分区走 init 原生加载**：`mi_ext`（官方 super 内 `mi_ext_a`）由 boot ramdisk 一阶段 fstab 挂到 `/mnt/vendor/mi_ext`（排在 vdc 之后），init 从真分区加载 `etc/build.prop` 与 `etc/init/*.rc`；无复刻件、无 pioneer 兜底服务
- [ ] **P0-1 挂载隐藏**（可选）：/proc/mounts 隐藏 vdc `/mnt/vendor` 挂载
- [ ] **property 身份**：ro.* 指纹经 property_service 读 `/mnt/vendor/build.prop`，如需真机指纹另配基线
- [ ] **vendor_dlkm 自编译模块**：8 个 .ko（vermagic 6.6.127-4k-cdh）注入链路已就绪
