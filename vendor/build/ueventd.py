#!/usr/bin/env python3
"""gold-15 vendor 的 ueventd 节点授权追加（容器内以 root 执行）。

为什么需要：Remote KeyMint（/vendor/bin/hw/android.hardware.security.keymint@3.0-
service.mitee）由 init 以 `user system` 启动，需 open("/dev/hvc3")（与 host
secure_env 收发 keymaster 消息的 virtconsole 通道）。而该设备节点由 ueventd 按
/vendor/etc/ueventd.rc 授权——官方 gold 的 ueventd.rc（224 行）**没有 hvc 规则**，
/dev/hvc* 落到默认 0600 root:root，open 直接 EACCES（先于 SELinux，故无 avc）。
实测：
    android.hardware.security.keymint-service.remote: Could not connect to
        keymaster: Permission denied  → SIGABRT(signal 6)

故与 note13 的 /dev/video* 授权同法：向 /vendor/etc/ueventd.rc **追加**（不覆盖，
保留官方 224 行）`/dev/hvc*  0666  system  system`，幂等（marker 判定）。

对外：
  write_workspace(work_dir)   落盘容器要用的脚本
  script()                    容器脚本片段（由编排层拼装）
"""

import os

from base_fs import C_DST_DIR, C_WORKDIR

UEVENTD = "/etc/ueventd.rc"          # 镜像内相对路径
MARKER = "# cdh gold-15: remote keymint virtconsole channel /dev/hvc*"
PY_NAME = "apply_ueventd.py"

_APPLY_PY = r'''#!/usr/bin/env python3
import sys

DST = "@@DST@@"
UEVENTD = DST + "@@UEVENTD@@"
MARKER = "@@MARKER@@"

RULE = """
@@MARKER@@
# Remote KeyMint 以 user system 运行，需 open /dev/hvc3（QEMU virtconsole 通道）；
# 官方 ueventd.rc 无 hvc 规则，默认 0600 root:root 会导致 open EACCES（DAC，先于 SELinux）。
/dev/hvc*          0666    system  system
"""


def read(path):
    with open(path, "rb") as f:
        return f.read()


try:
    cur = read(UEVENTD)
except OSError as e:
    print("[warn] 镜像内缺 %s，跳过 hvc 授权（%s）" % (UEVENTD, e), file=sys.stderr)
    sys.exit(0)

if MARKER.encode() in cur:
    print("[..] %s 已含 hvc 规则，跳过" % UEVENTD)
    sys.exit(0)

with open(UEVENTD, "ab") as f:
    f.write(RULE.encode("utf-8"))
print("[ok] %s 追加 /dev/hvc* 规则（0666 system:system，幂等）" % UEVENTD)
'''


def _render():
    return (_APPLY_PY
            .replace("@@DST@@", C_DST_DIR)
            .replace("@@UEVENTD@@", UEVENTD)
            .replace("@@MARKER@@", MARKER))


def write_workspace(work_dir):
    """把追加脚本落盘到工作目录（由编排层挂进容器 /workdir 后执行）。"""
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, PY_NAME), "w", encoding="utf-8") as f:
        f.write(_render())


def script():
    return f"python3 {C_WORKDIR}/{PY_NAME}\n"