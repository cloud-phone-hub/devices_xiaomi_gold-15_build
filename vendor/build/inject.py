#!/usr/bin/env python3
"""vendor 注入树注入（容器内以 root 执行）。

两棵注入树：
  1) build/vendor/patch/            —— 手写注入件（rc / idc / permissions / bin）
  2) out/dist/vendor/（VENDOR_STAGE_DIR）—— source 子构建产物（如 keymint HAL 的
     二进制 / rc / vintf），相对路径即镜像内 /vendor 路径。
两棵树相对路径均即 /vendor 内路径；uid/gid 强制 0:0、mode 0644（`bin/netmgr_setup`
例外 0755，同 note13 的 inject.py）；SELinux 标签按**本设备 baselines** 的
vendor.selinux-labels 回填（键 = 注入树内相对路径）。两树同名路径若冲突报错退出。

标签缺键由编排层在起容器之前即报错退出（vendor_builder 里做），容器内只负责落盘，
故本模块的容器脚本直接 `labels[rel]` 取值。

对外：
  patch_files()                   注入树内所有文件的相对路径（排序）
  missing(files, labels)          缺标签的键
  labels()                        本设备 baselines 的 vendor.selinux-labels
  write_workspace(work_dir, labels)   落盘容器要用的两个文件
  mounts()                        容器挂载表
  script()                        容器脚本片段（由编排层拼装）
"""

import json
import os

from base_fs import C_WORKDIR
from buildenv import VENDOR_PATCH_DIR, VENDOR_STAGE_DIR

# 注入规则（同 note13 inject.py）：常规 0644 / 可执行 0755 / 归属 0:0
# 可执行判定：`bin/**` 一律 0755（Android /vendor/bin 惯例，HAL 二进制必须可执行），
# 另加 PATCH_EXEC 显式名单（兼容历史项）。
PATCH_EXEC = {"bin/netmgr_setup"}
MODE_FILE = 0o644
MODE_EXEC = 0o755

# 两棵注入树：宿主目录 -> 容器内挂载点
TREES = [(VENDOR_PATCH_DIR, "/patch"), (VENDOR_STAGE_DIR, "/stage")]
# 容器内遍历用挂载点（与 TREES 的容器侧一致）
CONTAINER_TREES = [cont for _src, cont in TREES]

LABELS_JSON = "labels.json"
INJECT_SCRIPT = "inject_patch.py"

INJECT_PY = r'''#!/usr/bin/env python3
import json, os

DST = "%(dst)s"
CONTREES = %(contrees)r
PATCH_EXEC = %(exec_set)r
MODE_FILE = %(mode_file)#o
MODE_EXEC = %(mode_exec)#o

labels = json.load(open("%(workdir)s/%(labels)s", encoding="utf-8"))

n = 0
for src_dir in CONTREES:
    if not os.path.isdir(src_dir):
        continue
    for root, _dirs, files in os.walk(src_dir):
        for name in files:
            if name == ".DS_Store":
                continue
            src = os.path.join(root, name)
            rel = os.path.relpath(src, src_dir).replace(os.sep, "/")
            dst = os.path.join(DST, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, "rb") as f:
                data = f.read()
            with open(dst, "wb") as f:
                f.write(data)
            os.chown(dst, 0, 0)
            exec_path = rel in PATCH_EXEC or rel.startswith("bin/")
            os.chmod(dst, MODE_EXEC if exec_path else MODE_FILE)
            os.setxattr(dst, "security.selinux", labels[rel].encode())
            n += 1

print(f"[ok] 注入 patch/stage 树 {n} 个文件（标签按本设备 baselines 回填）")
''' % {"dst": "/mnt/dst", "contrees": CONTAINER_TREES, "exec_set": PATCH_EXEC,
       "mode_file": MODE_FILE, "mode_exec": MODE_EXEC,
       "workdir": C_WORKDIR, "labels": LABELS_JSON}


def patch_files():
    """两棵注入树内所有文件的相对路径（排序，排除 .DS_Store；同名冲突报错）。"""
    out = []
    for src_dir, _cont in TREES:
        if not os.path.isdir(src_dir):
            continue
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                if name == ".DS_Store":
                    continue
                out.append(os.path.relpath(os.path.join(root, name),
                                           src_dir).replace(os.sep, "/"))
    dup = {r for r in out if out.count(r) > 1}
    if dup:
        raise SystemExit(f"[err] 注入树存在同名冲突: {sorted(dup)}")
    return sorted(out)


def labels():
    """本设备 baselines 的 vendor.selinux-labels（键 = 注入树内相对路径）。"""
    from buildenv import load_baselines
    return (load_baselines() or {}).get("vendor", {}).get("selinux-labels", {}) or {}


def missing(files, labels_map):
    return [f for f in files if f not in labels_map]


def write_workspace(work_dir, labels_map):
    """落盘容器内要用的两个文件：注入脚本 + 标签表。"""
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, INJECT_SCRIPT), "w", encoding="utf-8") as f:
        f.write(INJECT_PY)
    with open(os.path.join(work_dir, LABELS_JSON), "w", encoding="utf-8") as f:
        json.dump(labels_map, f, ensure_ascii=False, indent=2)


def mounts(work_dir):
    """容器挂载表：两棵注入树（只读）+ 工作目录（可写）。"""
    out = [(src, cont, True) for src, cont in TREES if os.path.isdir(src)]
    out.append((work_dir, C_WORKDIR, False))
    return out


def script():
    return f"python3 {C_WORKDIR}/{INJECT_SCRIPT}\n"