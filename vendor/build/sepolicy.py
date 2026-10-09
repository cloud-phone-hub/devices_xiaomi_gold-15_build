#!/usr/bin/env python3
"""gold-15 vendor 的 Remote KeyMint sepolicy 追加（容器内以 root 执行）。

gold 官方 vendor `/etc/selinux` 同时含 `vendor_sepolicy.cil` 与 `precompiled_sepolicy`。
实测 gold guest 启动日志：
    init: /system/etc/selinux/plat_sepolicy_and_mapping.sha256 and
          /vendor/etc/selinux/precompiled_sepolicy.plat_sepolicy_and_mapping.sha256 differ
→ init **不采用** precompiled，改由 /system/bin/secilc 现场编译（AOSP
  OpenSplitPolicy），而 `vendor_sepolicy.cil` 是该路径的必需输入。
故与 note13 同法：**向 vendor_sepolicy.cil 追加 CIL 段**即会生效（无需重建
precompiled_sepolicy）。

规则集取自 cuttlefish 上游 `device-cuttlefish/shared/sepolicy/vendor/hal_keymint_*
.te`（远程 keymint 官方策略），按 gold 实况裁剪：官方 vendor_file_contexts 已把
`...keymint@3.0-service.mitee` 标为 `hal_keymint_default_exec`，故远程 keymint 运行在
`hal_keymint_default` 域（基座已定义 `(type hal_keymint_default)`，且已授
`kmsg_device` 与 `vendor_security_patch_level_prop` 读）。

本模块追加（幂等：marker 判定，重复构建不叠加）：

  1) `vendor_sepolicy.cil`
     * 声明 `keymaster_device` 类型（实测官方 plat/vendor 策略均无此类型）+
       roletype + `dev_type` 归属（/dev/hvc3 的 file_contexts 标签目标）；
     * `hal_keymint_default` 对 `device:dir` 的遍历（找 /dev/hvc3 节点）；
     * `hal_keymint_default` 对 /dev/hvc3（chr_file）getattr/ioctl/open/read/write；
     * serialno 属性读（attestation 取序列号；上游 get_prop(serialno_prop)）；
     * `vendor_init` 对 hvc3 getattr（rc 的 `wait /dev/hvc3` 是 stat）。
  2) `vendor_file_contexts`：追加 `/dev/hvc3 u:object_r:keymaster_device:s0`
     （实测官方 file_contexts 无任何 /dev/hvc* 规则，plat 侧也只有 /dev/console）。
  3) `vendor_property_contexts`：追加 `ro.vendor.boot_security_patch` 的 exact 规则，
     类型复用官方已存在且 `hal_keymint_default` 已被授权读的
     `vendor_security_patch_level_prop`（官方 policy 无
     `vendor_boot_security_patch_level_prop`）。该属性值由 props.py 写进
     /vendor/build.prop —— Remote KeyMint 起机时对它 `std::stoi`，缺失即抛异常 abort。
  4) `vendor_sepolicy.cil` 另补 `tmpfs_{vers}` 写（kmsg_debug）：Remote KeyMint 的 LOG
     落在 /dev/kmsg_debug（标签 tmpfs），不补则 HAL 致命日志全丢、只剩 SIGABRT。

对外：
  write_workspace(work_dir)   落盘容器要用的脚本
  script()                    容器脚本片段（由编排层拼装）
"""

import os

from base_fs import C_DST_DIR, C_WORKDIR

SEL = "/etc/selinux"
POLICY_FILE = SEL + "/vendor_sepolicy.cil"     # 镜像内相对路径
VFC = SEL + "/vendor_file_contexts"
VPC = SEL + "/vendor_property_contexts"
VERS_FILE = SEL + "/plat_sepolicy_vers.txt"

KEYMINT_MARKER = ";; cdh gold-15: keymint-hal-v1"
VFC_MARKER = "u:object_r:keymaster_device:s0"
VPC_MARKER = "ro.vendor.boot_security_patch"

# ---- pioneer 域（CDH guest 值守域）----
# 移植自 note13-5G-15 build/vendor/build/sepolicy.py 的 pioneer_cil（同项目同域）。
# guest 侧 CDH 服务（/vendor/bin/daemon、netmgr_setup、keep_awake、cph_start_adbd 等）
# 全部 `seclabel u:r:pioneer:s0`；缺 (type pioneer)/(type pioneer_exec) 声明时 init
# setexeccon 直接 EINVAL（实测 gold 引导日志：cannot setexeccon('u:r:pioneer:s0')
# ... Invalid argument；Context u:object_r:pioneer_exec:s0 is not valid）→ CDH 服务
# 全线起不来（含 adb）。
# 内核侧已由 CONFIG_CDH_PIONEER_PERMISSIVE 把 pioneer 置入 policydb->permissive_map，
# 凡以 pioneer 为主体的判定一律放行；故本段只需两类 permissive 覆盖不到的规则：
#   1) 启动链：源域是 init_{vers}（换域 execve 前置的 process transition / file execute）；
#   2) 反向授权：源域是对端域、目标才是 pioneer（netd fwmark / system_server 管道・
#      socketpair / servicemanager 回程 binder）。
# 这里只引用官方 plat 策略必有的标准域（init/shell_exec/netd/system_server/
# servicemanager/hwservicemanager），不含 vendor 自有类型（如 mediacodec），
# 以免 secilc 因类型名不存在而整体失败。
PIONEER_MARKER = ";; cdh gold-15: pioneer"
PIONEER_CIL = """;; cdh gold-15: pioneer: pioneer minimal declaration (kernel permissive).
(type pioneer)
(type pioneer_exec)
(typeattributeset domain (pioneer))
(typeattributeset exec_type (pioneer_exec))
(typeattributeset file_type (pioneer_exec))
(typeattributeset vendor_file_type (pioneer_exec))
(allow init_%(vers)s pioneer (process (noatsecure rlimitinh siginh transition)))
(allow init_%(vers)s pioneer_exec (file (execute getattr ioctl map open read)))
(allow pioneer pioneer_exec (file (entrypoint)))
(allow pioneer shell_exec_%(vers)s (file (entrypoint)))
(allow netd_%(vers)s pioneer (fd (use)))
(allow netd_%(vers)s pioneer (tcp_socket (getattr getopt read setattr setopt write)))
(allow netd_%(vers)s pioneer (udp_socket (getattr getopt read setattr write)))
(allow system_server_%(vers)s pioneer (fifo_file (read write getattr ioctl)))
(allow system_server_%(vers)s pioneer (unix_stream_socket (read write getattr ioctl getopt setopt shutdown)))
(allow servicemanager_%(vers)s pioneer (binder (transfer call)))
(allow servicemanager_%(vers)s pioneer (fd (use)))
(allow hwservicemanager_%(vers)s pioneer (binder (transfer call)))
(allow hwservicemanager_%(vers)s pioneer (fd (use)))
"""

PY_NAME = "apply_sepolicy.py"

# 容器内脚本模板：外层用 @@TOKEN@@ 替换，内层再对 CIL 做 %(vers)s 格式化。
_APPLY_PY = r'''#!/usr/bin/env python3
import sys

DST = "@@DST@@"
POLICY_FILE = DST + "@@POLICY@@"
VFC = DST + "@@VFC@@"
VERS_FILE = DST + "@@VERS_FILE@@"
VPC = DST + "@@VPC@@"
MARKER = "@@MARKER@@"
VFC_MARKER = "@@VFC_MARKER@@"
VPC_MARKER = "@@VPC_MARKER@@"
PIONEER_MARKER = "@@PIONEER_MARKER@@"

CIL = """;; cdh gold-15: keymint-hal-v1: remote keymint hvc3 chardev + serialno
(type keymaster_device)
(roletype object_r keymaster_device)
(typeattributeset dev_type (keymaster_device))
(allow hal_keymint_default device (dir (read getattr open search)))
(allow hal_keymint_default keymaster_device (chr_file (getattr ioctl open read write)))
(allow hal_keymint_default serialno_prop_%(vers)s (file (read getattr map open)))
(allow vendor_init_%(vers)s keymaster_device (chr_file (getattr)))
(typeattributeset tmpfs_%(vers)s (tmpfs))
(allow hal_keymint_default tmpfs_%(vers)s (chr_file (write)))
;; cdh gold-15: vd_device（/dev/block/vdb，data 分区）——vold 建 dm-crypt 与 fsck 格式化
;; 需要读写；官方 MIUI 策略只给 update_engine_common，缺 vold/fsck → 加密失败、
;; /data 只读（实测 avc denied { read } vold -> vd_device blk_file）。
(allow vold vd_device (blk_file (ioctl read write getattr lock append map open watch watch_reads)))
(allow fsck vd_device (blk_file (ioctl read write getattr lock append map open watch watch_reads)))
"""

PIONEER_CIL = """@@PIONEER_CIL@@"""

FC_BLOB = """
# cdh gold-15: remote keymint /dev/hvc3
/dev/hvc3  u:object_r:keymaster_device:s0
"""

# ro.vendor.boot_security_patch 的 exact 规则：Remote KeyMint 起机要读它（见
# remote_keymaster.cpp:87-91，缺失即 std::stoi 抛异常 abort）；值由 props.py 写进
# /vendor/build.prop。类型复用官方已存在且 hal_keymint_default 已被授权读的
# `vendor_security_patch_level_prop`（官方 policy 无 vendor_boot_security_patch_level_prop）。
VPC_BLOB = """
# cdh gold-15: keymint boot security_patch（Remote KeyMint stoi 依赖）
ro.vendor.boot_security_patch u:object_r:vendor_security_patch_level_prop:s0 exact string
"""


def read(path):
    with open(path, "rb") as f:
        return f.read()


def append(path, blob, marker):
    cur = read(path)
    if marker.encode() in cur:
        print("[..] %s 已含标记，跳过" % path)
        return False
    with open(path, "ab") as f:
        f.write(blob.encode("utf-8"))
    print("[ok] %s 追加 %d B" % (path, len(blob)))
    return True


vers = read(VERS_FILE).decode("ascii", "ignore").split()[0]
if not vers:
    print("[err] 读不到 plat_sepolicy_vers.txt", file=sys.stderr)
    sys.exit(1)

append(POLICY_FILE, CIL % {"vers": vers}, MARKER)
append(POLICY_FILE, PIONEER_CIL % {"vers": vers}, PIONEER_MARKER)
append(VFC, FC_BLOB, VFC_MARKER)
append(VPC, VPC_BLOB, VPC_MARKER)
print("[ok] keymint/pioneer sepolicy 追加完成（plat vers %s）" % vers)
'''


def _render():
    """把模板里的 @@TOKEN@@ 换成实际容器内路径（保留内层 %(vers)s 不动）。"""
    return (_APPLY_PY
            .replace("@@DST@@", C_DST_DIR)
            .replace("@@POLICY@@", POLICY_FILE)
            .replace("@@VFC@@", VFC)
            .replace("@@VERS_FILE@@", VERS_FILE)
            .replace("@@VPC@@", VPC)
            .replace("@@MARKER@@", KEYMINT_MARKER)
            .replace("@@VFC_MARKER@@", VFC_MARKER)
            .replace("@@VPC_MARKER@@", VPC_MARKER)
            .replace("@@PIONEER_MARKER@@", PIONEER_MARKER)
            .replace("@@PIONEER_CIL@@", PIONEER_CIL))


def write_workspace(work_dir):
    """把追加脚本落盘到工作目录（由编排层挂进容器 /workdir 后执行）。"""
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, PY_NAME), "w", encoding="utf-8") as f:
        f.write(_render())


def script():
    return f"python3 {C_WORKDIR}/{PY_NAME}\n"