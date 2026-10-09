#!/usr/bin/env python3
"""vendor patch 树注入（容器内以 root 执行）。

注入 build/vendor/patch/：相对路径即 /vendor 内路径；uid/gid 强制 0:0、mode 0644
（`bin/netmgr_setup` 例外 0755，同 note13 的 inject.py）；SELinux 标签按**本设备
baselines** 的 vendor.selinux-labels 回填（键 = 注入树内相对路径）。

标签缺键由编排层在起容器之前即报错退出（vendor_builder 里做），容器内只负责落盘，
故本模块的容器脚本直接 `labels[rel]` 取值。

对外：
  patch_files(patch_dir)          注入树内所有文件的相对路径（排序）
  missing(files, labels)          缺标签的键
  labels()                        本设备 baselines 的 vendor.selinux-labels
  write_workspace(work_dir, labels)   落盘容器要用的两个文件
  mounts(patch_dir, work_dir)     容器挂载表
  script()                        容器脚本片段（由编排层拼装）
"""

import json
import os

# 注入规则（同 note13 inject.py）：常规 0644 / 该脚本需可执行 0755 / 归属 0:0
PATCH_EXEC = {"bin/netmgr_setup"}
MODE_FILE = 0o644
MODE_EXEC = 0o755

# 容器内路径
C_PATCH_DIR = "/patch"
C_WORKDIR = "/workdir"
C_DST_DIR = "/mnt/dst"
LABELS_JSON = "labels.json"
INJECT_SCRIPT = "inject_patch.py"

INJECT_PY = r'''#!/usr/bin/env python3
import json, os

DST, PATCH = "%(dst)s", "%(patch)s"
PATCH_EXEC = %(exec_set)r
MODE_FILE = %(mode_file)#o
MODE_EXEC = %(mode_exec)#o

labels = json.load(open("%(workdir)s/%(labels)s", encoding="utf-8"))

n = 0
for root, _dirs, files in os.walk(PATCH):
    for name in files:
        if name == ".DS_Store":
            continue
        src = os.path.join(root, name)
        rel = os.path.relpath(src, PATCH).replace(os.sep, "/")
        dst = os.path.join(DST, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as f:
            data = f.read()
        with open(dst, "wb") as f:
            f.write(data)
        os.chown(dst, 0, 0)
        os.chmod(dst, MODE_EXEC if rel in PATCH_EXEC else MODE_FILE)
        os.setxattr(dst, "security.selinux", labels[rel].encode())
        n += 1

print(f"[ok] 注入 patch 树 {n} 个文件（标签按本设备 baselines 回填）")
''' % {"dst": C_DST_DIR, "patch": C_PATCH_DIR, "exec_set": PATCH_EXEC,
       "mode_file": MODE_FILE, "mode_exec": MODE_EXEC,
       "workdir": C_WORKDIR, "labels": LABELS_JSON}


def patch_files(patch_dir):
    """注入树内所有文件的相对路径（排序，排除 .DS_Store）。"""
    out = []
    for root, _dirs, files in os.walk(patch_dir):
        for name in files:
            if name == ".DS_Store":
                continue
            out.append(os.path.relpath(os.path.join(root, name),
                                       patch_dir).replace(os.sep, "/"))
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


def mounts(patch_dir, work_dir):
    return [(patch_dir, C_PATCH_DIR, True), (work_dir, C_WORKDIR, False)]


def script():
    return f"python3 {C_WORKDIR}/{INJECT_SCRIPT}\n"