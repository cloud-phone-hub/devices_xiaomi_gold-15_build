#!/usr/bin/env python3
"""gold-15 vendor 重打包的编译依赖准备（deps）。

职责：为 Remote KeyMint HAL 交叉编译准备**链接期**运行库目录。这些 .so 仅作为
编译时 -L/链接输入（决定 DT_NEEDED/SONAME 与链接期符号检查），运行时仍由 gold-15
guest 自身的官方 /vendor/lib64 + /system/lib64 提供。

来源必须是**本设备官方镜像**（不是 lineage，也不是 note13 的提取件），并按分区**分目录**落盘到
`<out>/runtime_lib_dir/` 下：
  * vendor 侧 4 个：官方 vendor_a.img（EROFS）内 /lib64
    （libc++.so libbase.so libcrypto.so libkeymaster_messages.so）
    → <out>/runtime_lib_dir/vendor-compile-deps/lib64/；
  * system 侧 2 个：官方 system_a.img（EROFS）内 /system/lib64
    （liblog.so libbinder_ndk.so）→ <out>/runtime_lib_dir/system-compile-deps/lib64/。
提取优先用**宿主 dump.erofs**（本机 brew 已装即免 docker，可用 env EROFSDUMP 指定）；
宿主没有再退回 builder 容器。保留镜像内 lib64/ 相对路径，幂等：缺哪个取哪个。
prepare_runtime_libs() 返回**运行库目录列表**（上述两个 lib64 目录），
供 audio/keymint/gatekeeper 链接层逐库搜索。

用法：
  python3 build/vendor/build/deps.py            # 准备并打印目录
  python3 build/vendor/build/deps.py --check    # 只校验不准备
"""

import argparse
import os
import subprocess
import sys

# buildenv 位于 build/scripts；独立执行本模块时自行插入（vendor_builder 调用时已存在）
DEVICE_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(DEVICE_ROOT, "build", "scripts"))

from buildenv import OUT_DIR, ROM_ROOT_DIR, LINEAGE_SUPER_DIR, any_tool  # noqa: E402

# 编译链接所需的运行库（顺序无关，仅用于缺失检查）。
# media(c2) store 额外需要 libnativewindow.so（Buffer parcelable 的 native_handle
# 序列化引用其符号），同为 system 侧，随本清单一起研磨。
RUNTIME_LIBS = [
    "libc++.so", "libbase.so", "libbinder_ndk.so", "libcrypto.so",
    "libkeymaster_messages.so", "liblog.so", "libnativewindow.so",
]
# 各库的来源镜像（官方 EROFS）
VENDOR_IMG = os.path.join(ROM_ROOT_DIR, "vendor_a.img")
SYSTEM_IMG = os.path.join(ROM_ROOT_DIR, "system_a.img")
# 按分区分目录：vendor 侧进 vendor-compile-dep，system 侧进 system-compile-dep，
# 各自保留镜像内相对路径（lib64/…）。COMPILE_DEP_DIR 作兼容别名指向 vendor 根
# （新代码应用 prepare_runtime_libs 返回的目录列表）。
# 运行库统一落在 out 根下的 runtime_lib_dir/，其内再按分区分目录
RUNTIME_LIB_ROOT = os.path.join(OUT_DIR, "runtime_lib_dir")
VENDOR_COMPILE_DEP_DIR = os.path.join(RUNTIME_LIB_ROOT, "vendor-compile-deps")
SYSTEM_COMPILE_DEP_DIR = os.path.join(RUNTIME_LIB_ROOT, "system-compile-deps")
COMPILE_DEP_DIR = VENDOR_COMPILE_DEP_DIR  # 兼容别名
# runtime_lib_dir 根下的 media-v412 命名空间：由设备薄层
# .../source/android.hardware.media.v412/build.py 的 prepare_abi 研磨（来源
# gold 自己的 lineage super（非官方镜像），含 swcodec APEX）。
V412_ABI_ROOT = os.path.join(RUNTIME_LIB_ROOT, "media-v412")
V412_ABI_SYS = os.path.join(V412_ABI_ROOT, "system", "lib64")
V412_ABI_APEX = os.path.join(V412_ABI_ROOT, "apex", "media-swcodec", "lib64")
V412_SYS_IMG = os.path.join(LINEAGE_SUPER_DIR, "system_a.img")
V412_APEX_IMG = os.path.join(LINEAGE_SUPER_DIR, "swcodec-work", "apex_payload.img")
# lib → (分区, 镜像内绝对路径)；分区决定根目录与剥前缀后的相对落盘路径
LIB_ORIGIN = {
    "libc++.so":                 ("vendor", "/lib64/libc++.so"),
    "libbase.so":                ("vendor", "/lib64/libbase.so"),
    "libcrypto.so":              ("vendor", "/lib64/libcrypto.so"),
    "libkeymaster_messages.so":  ("vendor", "/lib64/libkeymaster_messages.so"),
    "liblog.so":                 ("system", "/system/lib64/liblog.so"),
    "libbinder_ndk.so":          ("system", "/system/lib64/libbinder_ndk.so"),
    "libnativewindow.so":        ("system", "/system/lib64/libnativewindow.so"),
}
IMAGES = {"vendor": VENDOR_IMG, "system": SYSTEM_IMG}
# 剥掉分区在镜像内的挂载前缀，得到落盘相对路径（lib64/xxx.so）
PART_PREFIX = {"vendor": "/", "system": "/system/"}


def _lib_target(lib):
    """返回 (落盘绝对路径, 镜像内路径, 来源镜像)。"""
    part, path_in_img = LIB_ORIGIN[lib]
    root = VENDOR_COMPILE_DEP_DIR if part == "vendor" else SYSTEM_COMPILE_DEP_DIR
    rel = path_in_img[len(PART_PREFIX[part]):]
    return os.path.join(root, rel), path_in_img, IMAGES[part]


def _runtime_dirs():
    """已研磨运行库所在的目录列表（去重、稳定顺序）。"""
    dirs = []
    for lib in RUNTIME_LIBS:
        d = os.path.dirname(_lib_target(lib)[0])
        if d not in dirs:
            dirs.append(d)
    return dirs

# 容器：Linux root + erofs-utils（本仓库 builder 镜像已含）
CONTAINER_IMAGE = os.environ.get("CDH_VENDOR_BUILDER",
                                 "cloud-phone-hub-ubuntu-builder:latest")


def _erofs_dump():
    """宿主 dump.erofs 绝对路径（优先 env EROFSDUMP），没有则 None。"""
    cand = os.environ.get("EROFSDUMP")
    if cand and os.path.isfile(cand):
        return cand
    return any_tool(["dump.erofs"])


def _extract(img, path_in_img, dst):
    """把 EROFS 镜像内文件提取到 dst（保镜像内 lib64/ 结构）。

    优先用宿主 dump.erofs（本机已装即免 docker）；没有再退回 builder 容器
    （把 out 挂到 /out，容器内 dump.erofs 按相对路径写出）。幂等由调用方保证。
    """
    if not os.path.isfile(img):
        print(f"[err] 依赖研磨: 缺官方镜像：{img}", file=sys.stderr)
        sys.exit(1)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    rel = os.path.relpath(dst, OUT_DIR)
    host = _erofs_dump()
    if host:
        with open(dst, "wb") as fh:
            res = subprocess.run([host, "--cat", f"--path={path_in_img}", img],
                                 stdout=fh, stderr=subprocess.PIPE)
        if res.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            print(f"[err] 依赖研磨: 宿主 dump.erofs 提取 {path_in_img} 失败",
                  file=sys.stderr)
            print(res.stderr.decode("utf8", "replace")[-400:], file=sys.stderr)
            sys.exit(1)
        print(f"[ok] 依赖研磨(宿主): {rel} <- {os.path.basename(img)}:{path_in_img}")
        return
    # 容器兜底：宿主无 dump.erofs 时，用 builder 镜像里的 erofs-utils
    rom_dir = os.path.dirname(img)
    res = subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{rom_dir}:/roms:ro",
         "-v", f"{OUT_DIR}:/out",
         CONTAINER_IMAGE, "bash", "-c",
         f'mkdir -p "$(dirname /out/{rel})" && '
         f"dump.erofs --cat --path={path_in_img} /roms/{os.path.basename(img)} "
         f"> /out/{rel} && chmod 0644 /out/{rel}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        print(f"[err] 依赖研磨: 容器提取 {path_in_img} 失败（且宿主无 dump.erofs）",
              file=sys.stderr)
        print(res.stderr.decode("utf8", "replace")[-400:], file=sys.stderr)
        sys.exit(1)
    print(f"[ok] 依赖研磨(容器): {rel} <- {os.path.basename(img)}:{path_in_img}")


def _scan_rel_libs(root):
    """递归列出 root 下所有 .so（相对 root 的路径），供 README 实况枚举。"""
    if not os.path.isdir(root):
        return []
    libs = []
    for dirpath, _dirs, files in os.walk(root):
        libs.extend(os.path.relpath(os.path.join(dirpath, fn), root)
                    for fn in files if fn.endswith(".so"))
    return sorted(libs)


def render_runtime_lib_readme():
    """生成 out/runtime_lib_dir/README.md 文本：逐子目录说明来源镜像与研磨脚本。

    来源为静态知识（镜像路径/脚本固定），库清单按调用时目录实况扫描，故 deps.py 与
    v412 任一先跑都会得到完整正确的说明（幂等）。
    """
    vendor_libs = _scan_rel_libs(VENDOR_COMPILE_DEP_DIR)
    system_libs = _scan_rel_libs(SYSTEM_COMPILE_DEP_DIR)
    v412_sys = _scan_rel_libs(V412_ABI_SYS)
    v412_apex = _scan_rel_libs(V412_ABI_APEX)

    def section(title, image, inpath, tool, script, libs, note):
        lines = [f"## {title}", ""]
        lines.append(f"- **来源镜像**：`{image}`")
        lines.append(f"- **镜像内路径**：`{inpath}`")
        lines.append(f"- **提取工具**：{tool}")
        lines.append(f"- **研磨脚本**：`{script}`")
        lines.append(f"- **说明**：{note}")
        if libs:
            lines.append(f"- **现有库（{len(libs)}）**：")
            lines.extend(f"  - `{l}`" for l in libs)
        else:
            lines.append("- **现有库**：（尚未研磨）")
        lines.append("")
        return "\n".join(lines)

    head = [
        "# runtime_lib_dir —— 链接期运行库（ABI 研磨）索引",
        "",
        "本目录是各 HAL 交叉编译的**链接期 `-L`/链接输入**（决定 DT_NEEDED/SONAME 与",
        "链接期符号检查），**不打包进镜像**；运行时仍由 gold guest 自身的官方 /system、",
        "/vendor 提供。下面逐子目录说明各自的**来源镜像**与研磨脚本。",
        "",
        "| 子目录 | 来源 | 研磨方 |",
        "|---|---|---|",
        "| `vendor-compile-deps/` | 官方 gold `vendor_a.img` | 本 `deps.py` |",
        "| `system-compile-deps/` | 官方 gold `system_a.img` | 本 `deps.py` |",
        "| `media-v412/` | lineage super `system_a.img` + swcodec `apex_payload.img` | `android.hardware.media.v412/build.py` |",
        "",
        "---",
        "",
    ]
    body = [
        section("vendor-compile-deps/lib64", VENDOR_IMG, "/lib64/*.so",
                "dump.erofs（宿主优先，容器兜底）",
                "build/vendor/build/deps.py → prepare_runtime_libs()",
                vendor_libs,
                "audio/keymint/gatekeeper 等链接所需的 vendor 侧运行库（固定清单 RUNTIME_LIBS）。"),
        section("system-compile-deps/lib64", SYSTEM_IMG, "/system/lib64/*.so",
                "dump.erofs（宿主优先，容器兜底）",
                "build/vendor/build/deps.py → prepare_runtime_libs()",
                system_libs,
                "media(c2)/camera 等需要的 system 侧运行库（liblog/libbinder_ndk/libnativewindow）。"),
        section("media-v412/system/lib64", V412_SYS_IMG, "/system/lib64/*.so",
                "debugfs dump（ext4）",
                "build/vendor/source/android.hardware.media.v412/build.py → prepare_abi()",
                v412_sys,
                "v4l2_codec2 引擎链接的 system 侧 ABI（~47 个 c2/bufferpool/graphics/codec2 库）。"),
        section("media-v412/apex/media-swcodec/lib64", V412_APEX_IMG, "/lib64/*.so",
                "debugfs dump（ext4）",
                "build/vendor/source/android.hardware.media.v412/build.py → prepare_abi()",
                v412_apex,
                "media-swcodec APEX 私有库（libcodec2_vndk/libcodec2_aidl 等 9 个）。"),
    ]
    return "\n".join(head) + "\n".join(body)


def write_runtime_lib_readme(dry_run=False):
    """把来源说明写入 out/runtime_lib_dir/README.md（幂等；dry_run 不落盘）。"""
    doc = render_runtime_lib_readme()
    path = os.path.join(RUNTIME_LIB_ROOT, "README.md")
    if dry_run:
        print(f"[..] (dry) 将写来源说明: {path}")
        return path
    os.makedirs(RUNTIME_LIB_ROOT, exist_ok=True)
    old = None
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old == doc:
        return path
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[ok] 来源说明: {path}")
    return path


def prepare_runtime_libs(dry_run=False):
    """按分区准备链接期运行库目录，返回**目录列表**（vendor + system 的 lib64）。幂等。"""
    for root in (VENDOR_COMPILE_DEP_DIR, SYSTEM_COMPILE_DEP_DIR):
        os.makedirs(root, exist_ok=True)
    missing = [lib for lib in RUNTIME_LIBS if not os.path.isfile(_lib_target(lib)[0])]
    dirs = _runtime_dirs()
    if not missing:
        write_runtime_lib_readme(dry_run=dry_run)
        return dirs
    if dry_run:
        print(f"[..] (dry) 将补齐运行库: {', '.join(missing)} -> {', '.join(dirs)}")
        write_runtime_lib_readme(dry_run=True)
        return dirs
    print(f"[..] 依赖研磨: 补齐 {len(missing)} 个链接期运行库"
          f"（来源: 官方 gold images，EROFS，按 vendor/system 分目录）")
    for lib in RUNTIME_LIBS:
        dst, path_in_img, img = _lib_target(lib)
        if os.path.isfile(dst):
            continue
        _extract(img, path_in_img, dst)
    write_runtime_lib_readme()
    return _runtime_dirs()


def main():
    ap = argparse.ArgumentParser(description="gold-15 vendor 编译依赖准备")
    ap.add_argument("--check", action="store_true",
                    help="只校验不准备（缺即报错退出）")
    args = ap.parse_args()

    if args.check:
        missing = [lib for lib in RUNTIME_LIBS
                   if not os.path.isfile(_lib_target(lib)[0])]
        if missing:
            print(f"[err] 运行库缺失: {', '.join(missing)}（先跑 deps.py）",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[ok] 运行库齐全: {', '.join(_runtime_dirs())}")
        return 0

    dirs = prepare_runtime_libs(dry_run=False)
    print(f"[ok] 运行库目录: {', '.join(dirs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())