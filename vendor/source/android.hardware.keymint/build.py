#!/usr/bin/env python3
"""gold-15 Remote KeyMint HAL（android.hardware.security.keymint）落地。

背景：官方 gold vendor 自带的是 **mitee（TEE）版** keymint：
  * 二进制 /vendor/bin/hw/android.hardware.security.keymint@3.0-service.mitee
  * rc     /vendor/etc/init/android.hardware.security.keymint.mitee@3.0-service.rc
           （service vendor.keymint-mitee，class early_hal、disabled）
  * vintf  /vendor/etc/vintf/manifest/android.hardware.security.keymint-service.mitee.xml
它在云机上必然起不来（无 TEE）——实测：
  [MITEE]TEEC_InitializeContext failed with code 0xffff0008
  [MITEE]Could not initialize Mitee KeyMint,err:-65528
于是 keystore2 拿不到 IKeyMintDevice/default，vold 每秒等待、引导卡死。

方案（原地替换，与 note13「单服务同名替换」同思路，避免双 keymint 争抢）：
  1. 编译：委托 cuttlefish/scripts/build-guest-keymint.sh（AIDL 生成 + 单发 clang
     链接 + llvm-readelf 校验的单一事实来源），产物**以基座同名**落盘
     out/dist/vendor/bin/hw/android.hardware.security.keymint@3.0-service.mitee；
  2. rc：以基座 rc 为底 —— 去掉 class early_hal、追加通道参数 ` /dev/hvc3` 与
     `    file /dev/kmsg w`，并前置 `on late-fs { wait /dev/hvc3 30; start ... }`
     （官方 rc 已是 disabled，改由 late-fs 门控启动；virtio_console 是模块，
     /dev/hvc3 晚出现，早起会 open 失败）；
  3. tee-supplicant 去钩子：官方 /vendor/etc/init/tee-supplicant.rc 用
     `enable vendor.keymint-mitee` 提前拉起 mitee keymint —— 注释掉该行，避免
     在 /dev/hvc3 就绪前启动我们的远程 keymint；
  4. vintf：以基座 manifest 为底，换注释头并删除 RKP 组件块（RKP 注册前不声明）。

标签沿用基座路径标签：二进制 /vendor/bin/hw/android.hardware.security.keymint@3.0-
service.mitee 在官方 vendor_file_contexts 中已是 hal_keymint_default_exec，故远程
keymint 运行在 hal_keymint_default 域（与 note13 一致）；rc/vintf 标签登记进
baselines vendor.selinux-labels，由 inject 回填。

SELinux 追加（/dev/hvc3 标签 + hal_keymint_default 授权）由
build/vendor/build/sepolicy.py 随 vendor 重打包写入
vendor_sepolicy.cil + vendor_file_contexts + vendor_property_contexts。

host 侧依赖：设备规格 FactoryTEE=true 时 daemon 拉起 secure_env 并把 socketpair
guest 端挂到 QEMU hvc3 chardev（core/host/daemon/qemu/qemu.go，dense 布局）。

用法：
  python3 build/vendor/source/android.hardware.keymint/build.py
  python3 build/vendor/source/android.hardware.keymint/build.py --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

# 本文件位于 build/vendor/source/android.hardware.keymint/ 下，向上 5 级为设备根
_DEVICE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "scripts"))
sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "vendor", "build"))

from buildenv import (  # noqa: E402
    PROJECT_ROOT, ROM_ROOT_DIR, VENDOR_STAGE_DIR, baselines_file,
)
import deps  # noqa: E402   gold 自身官方镜像的编译依赖组装

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
STANDALONE_DIR = os.path.join(MODULE_DIR, "standalone")
# gold 运行库兼容垫片：官方 vendor /lib64/libkeymaster_messages.so 未导出
# keymaster::TAG_MODULE_HASH（实测 llvm-nm 无该符号），而 AIDL keymint 服务源
# 静态初始化会引用它 -> 链接失败。用强定义补齐（详见该文件头注释）。
COMPAT_SOURCES = (
    os.path.join(STANDALONE_DIR, "keymaster_tags_module_hash.cpp"),
)

# 编译脚本（单一事实来源：AIDL 生成 + 链接 + readelf 校验）
COMPILE_SCRIPT = os.path.join(PROJECT_ROOT, "cuttlefish", "scripts",
                              "build-guest-keymint.sh")
# 依赖源工作区（缺任一目录则报错退出）
SOURCE_WORKSPACE = os.path.join(PROJECT_ROOT, "cuttlefish", "source")
REQUIRED_SOURCE_DIRS = [
    "system-libbase/include",
    "system-logging/liblog/include",
    "frameworks-native/libs/binder/ndk/include_platform",
    "external-boringssl/src/include",
]
# 运行库目录：默认由 deps.py 从**官方 gold images**（vendor_a/system_a，EROFS）
# 组装（vendor 4 + system 2），链接期输入；运行时由 guest 自身 /vendor/lib64 +
# /system/lib64 提供。可用 `--runtime-lib-dir` 或 env CDH_RUNTIME 覆盖（仅排查）。
RUNTIME_LIBS = [
    "libc++.so", "libbase.so", "libbinder_ndk.so", "libcrypto.so",
    "libkeymaster_messages.so", "liblog.so",
]

# 官方 vendor 镜像（基座 rc/vintf/tee-supplicant.rc 的来源，EROFS）
VENDOR_IMG = os.path.join(ROM_ROOT_DIR, "vendor_a.img")
CONTAINER_IMAGE = os.environ.get("CDH_VENDOR_BUILDER",
                                 "cloud-phone-hub-ubuntu-builder:latest")

# 落盘（相对 VENDOR_STAGE_DIR 即镜像内 /vendor 路径）
BIN_NAME = "android.hardware.security.keymint@3.0-service.mitee"
RC_NAME = "android.hardware.security.keymint.mitee@3.0-service.rc"
VINTF_NAME = "android.hardware.security.keymint-service.mitee.xml"
DEST_BIN = os.path.join(VENDOR_STAGE_DIR, "bin", "hw", BIN_NAME)
DEST_RC = os.path.join(VENDOR_STAGE_DIR, "etc", "init", RC_NAME)
DEST_TEE_RC = os.path.join(VENDOR_STAGE_DIR, "etc", "init", "tee-supplicant.rc")
DEST_VINTF = os.path.join(VENDOR_STAGE_DIR, "etc", "vintf", "manifest", VINTF_NAME)

# 基座同名文件（官方 vendor 内路径；以镜像内绝对路径表示）
BASE_RC = f"/etc/init/{RC_NAME}"
BASE_TEE_RC = "/etc/init/tee-supplicant.rc"
BASE_VINTF = f"/etc/vintf/manifest/{VINTF_NAME}"

# 新增/替换文件的 SELinux 标签（二进制沿用基座 file_contexts 路径标签）
LABELS = {
    f"bin/hw/{BIN_NAME}": "u:object_r:hal_keymint_default_exec:s0",
    f"etc/init/{RC_NAME}": "u:object_r:vendor_file:s0",
    "etc/init/tee-supplicant.rc": "u:object_r:vendor_file:s0",
    f"etc/vintf/manifest/{VINTF_NAME}": "u:object_r:vendor_configs_file:s0",
}

# rc 改写：基座 service 声明行 + 通道参数（/dev/hvc3，Cuttlefish 基线编号）
RC_SERVICE = b"service vendor.keymint-mitee " \
             b"/vendor/bin/hw/android.hardware.security.keymint@3.0-service.mitee"
KEYMINT_CHANNEL_ARG = b" /dev/hvc3"
KMSG_LINE = b"    file /dev/kmsg w"
# 把服务 stdout/stderr 重定向到 kmsg（诊断 HAL 致命日志；默认丢弃看不到原因）
STDIO_KMSG_LINE = b"    stdio_to_kmsg"
# 官方 rc 的服务类：early_hal（boot 早期启动）。去掉它，配合 disabled + late-fs
# wait /dev/hvc3 门控（virtio_console 是模块，/dev/hvc3 晚出现）。
EARLY_HAL_CLASS = b"early_hal"

# tee-supplicant 去钩子：官方用 enable 提前拉起 mitee keymint
TEE_ENABLE_LINE = b"enable vendor.keymint-mitee"
# 注释文案（含中文，用 str -> utf-8 编码，避免 bytes 字面量只能 ASCII）
TEE_COMMENT_LINE = (
    "# cdh gold-15: 由 keymint rc late-fs 门控启动，见 "
    "android.hardware.security.keymint.mitee@3.0-service.rc").encode("utf-8")

# vintf 改写：换注释头 + 删除 RKP 组件块（与 note13 同款）
VINTF_HEADER = "<!-- gold-15 Remote KeyMint 实际能力清单：RKP 注册前不声明。 -->"
RKP_FQ = b"IRemotelyProvisionedComponent/default"


def _missing(what, path):
    print(f"[err] {what} 缺失: {path}", file=sys.stderr)
    sys.exit(1)


def resolve_runtime_lib_dir(arg):
    """--runtime-lib-dir 参数 > env CDH_RUNTIME > deps（官方 gold images）。

    返回**目录列表**（vendor-/system-compile-dep 各一）；env 可用 os.pathsep 传多目录。
    """
    if arg:
        return [arg]
    env = os.environ.get("CDH_RUNTIME")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    return deps.prepare_runtime_libs()


def extract_base(path_in_img):
    """用容器 dump.erofs --cat 取官方 vendor 镜像内文件字节（EROFS）。"""
    if not os.path.isfile(VENDOR_IMG):
        _missing("官方 vendor 镜像", VENDOR_IMG)
    with tempfile.TemporaryDirectory() as td:
        res = subprocess.run(
            ["docker", "run", "--rm",
             "-v", f"{os.path.dirname(VENDOR_IMG)}:/roms:ro",
             "-v", f"{td}:/out",
             CONTAINER_IMAGE, "bash", "-c",
             f"dump.erofs --cat --path={path_in_img} "
             f"/roms/{os.path.basename(VENDOR_IMG)} > /out/base"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        data = b""
        f = os.path.join(td, "base")
        if res.returncode == 0 and os.path.isfile(f):
            with open(f, "rb") as fh:
                data = fh.read()
    if not data:
        print(f"[err] 基座文件提取失败: {path_in_img}", file=sys.stderr)
        print(res.stderr.decode("utf8", "replace")[-400:], file=sys.stderr)
        sys.exit(1)
    return data


def check_prereqs(runtime_lib_dirs):
    """编译前置校验：脚本、依赖源工作区、运行库（多目录逐库搜索）。缺失即报错退出。"""
    if not os.path.isfile(COMPILE_SCRIPT):
        _missing("keymint 编译脚本", COMPILE_SCRIPT)
    for sub in REQUIRED_SOURCE_DIRS:
        if not os.path.isdir(os.path.join(SOURCE_WORKSPACE, sub)):
            _missing("依赖源目录", os.path.join(SOURCE_WORKSPACE, sub))
    for d in runtime_lib_dirs:
        if not os.path.isdir(d):
            _missing("运行库目录", d)
    for lib in RUNTIME_LIBS:
        if not any(os.path.isfile(os.path.join(d, lib)) for d in runtime_lib_dirs):
            _missing("运行库", f"{lib}（搜索: {', '.join(runtime_lib_dirs)}）")


def compile_remote(dry_run=False, runtime_lib_dirs=None):
    """委托 build-guest-keymint.sh 编译远程 keymint，产物落成基座同名。"""
    argv = ["bash", COMPILE_SCRIPT, "--out", DEST_BIN]
    for d in runtime_lib_dirs:
        argv += ["--runtime-lib-dir", d]
    argv += ["--standalone-dir", STANDALONE_DIR]
    argv += [arg for src in COMPAT_SOURCES for arg in ("--extra-source", src)]
    if dry_run:
        print("[..] (dry)", " ".join(argv))
        return
    res = subprocess.run(argv)
    if res.returncode != 0:
        print(f"[err] keymint 编译失败（exit={res.returncode}）", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(DEST_BIN):
        _missing("keymint 编译产物", DEST_BIN)


def patch_rc(base: bytes) -> bytes:
    """基座 mitee rc -> late-fs 等 /dev/hvc3 + disabled 门控。

    基座：`service vendor.keymint-mitee ...`（class early_hal、disabled）。
    改动：
      * 去 `class early_hal`（不再早期启动）；
      * service 行尾追加通道参数 ` /dev/hvc3` 与 `    file /dev/kmsg w`（去重）；
      * 前置 on 块：`on late-fs { wait /dev/hvc3 30; start vendor.keymint-mitee }`
        —— virtio_console 是模块（CONFIG_VIRTIO_CONSOLE=m），/dev/hvc3 由
        ueventd 在模块加载后创建；wait 保证就绪后再启动，避免 open 失败即退。
    """
    head = (b"on post-fs\n"
            b"    write /dev/kmsg \"CDH-GOLD-KEYMINT-RC-V3-POSTFS\\n\"\n"
            b"    wait /dev/hvc3 30\n"
            b"    write /dev/kmsg \"CDH-GOLD-KEYMINT-RC-V3-POSTFS-STARTING\\n\"\n"
            b"    start vendor.keymint-mitee\n\n"
            b"on late-fs\n"
            b"    write /dev/kmsg \"CDH-GOLD-KEYMINT-RC-V3-LATEFS\\n\"\n"
            b"    wait /dev/hvc3 30\n"
            b"    write /dev/kmsg \"CDH-GOLD-KEYMINT-RC-V3-LATEFS-STARTING\\n\"\n"
            b"    start vendor.keymint-mitee\n\n")
    lines = base.split(b"\n")
    body = []
    inserted = 0
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and fields[0] == b"file" and \
                fields[1] == b"/dev/kmsg" and fields[2] == b"w":
            continue  # 基座若已含 kmsg 行则去重
        if fields and fields[0] == b"class" and EARLY_HAL_CLASS in fields:
            continue  # 去掉 early_hal 类（见 docstring 竞态说明）
        body.append(line)
        if line.startswith(RC_SERVICE):
            body.pop()
            body.append(line + KEYMINT_CHANNEL_ARG)
            body.append(KMSG_LINE)
            body.append(STDIO_KMSG_LINE)
            inserted += 1
    if inserted != 1:
        print(f"[err] keymint rc service 声明命中 {inserted} 处（期望 1）",
              file=sys.stderr)
        sys.exit(1)
    return head + b"\n".join(body) + b"\n"


def patch_tee_rc(base: bytes) -> bytes:
    """tee-supplicant.rc：注释掉 `enable vendor.keymint-mitee`（提前拉起钩子）。"""
    out = []
    hit = 0
    for line in base.split(b"\n"):
        if line.strip() == TEE_ENABLE_LINE:
            out.append(TEE_COMMENT_LINE)
            out.append(b"#" + line)
            hit += 1
        else:
            out.append(line)
    if hit != 1:
        print(f"[err] tee-supplicant.rc 的 enable 行命中 {hit} 处（期望 1）",
              file=sys.stderr)
        sys.exit(1)
    return b"\n".join(out)


def patch_vintf(base: bytes) -> bytes:
    """基座 vintf：去 4 行注释头 -> 换基线头 -> 删 RKP 组件块。"""
    lines = base.split(b"\n")
    if len(lines) >= 4 and lines[0] == b"<!--" and lines[3] == b"-->":
        lines = lines[4:]
    lines = [VINTF_HEADER.encode("utf-8")] + lines
    idx = next((i for i, l in enumerate(lines) if RKP_FQ in l), -1)
    if idx >= 0:
        start = idx
        while b"<hal" not in lines[start]:
            start -= 1
        end = idx
        while b"</hal>" not in lines[end]:
            end += 1
        lines = lines[:start] + lines[end + 1:]
    return b"\n".join(lines)


def _write(dest, data, mode):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    os.chmod(dest, mode)
    print(f"    写 {os.path.relpath(dest, VENDOR_STAGE_DIR)} ({len(data)} B)")


def write_rc_vintf(dry_run=False):
    """读基座 rc / tee-supplicant.rc / vintf，改写后落盘（遮蔽基座同名件）。"""
    if dry_run:
        print("[..] (dry) 将改写并落盘: "
              f"etc/init/{RC_NAME} / etc/init/tee-supplicant.rc / "
              f"etc/vintf/manifest/{VINTF_NAME}")
        return
    rc = patch_rc(extract_base(BASE_RC))
    tee = patch_tee_rc(extract_base(BASE_TEE_RC))
    vintf = patch_vintf(extract_base(BASE_VINTF))
    _write(DEST_RC, rc, 0o644)
    _write(DEST_TEE_RC, tee, 0o644)
    _write(DEST_VINTF, vintf, 0o644)


def register_labels(baselines_path, dry_run=False):
    """登记新增文件的 SELinux 标签进 baselines vendor.selinux-labels（幂等）。"""
    if not baselines_path or not os.path.isfile(baselines_path):
        _missing("基线清单", baselines_path)
    with open(baselines_path, encoding="utf-8") as f:
        doc = json.load(f)
    labels = doc.setdefault("vendor", {}).setdefault("selinux-labels", {})
    missing = {k: v for k, v in LABELS.items() if labels.get(k) != v}
    if not missing:
        print("[..] baselines 标签已齐，跳过")
        return
    labels.update(missing)
    if dry_run:
        print(f"[..] (dry) baselines 将登记标签 {len(missing)} 条")
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(baselines_path),
                               prefix=".baselines.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, baselines_path)
    except BaseException:
        os.unlink(tmp)
        raise
    print(f"[ok] baselines 登记标签 {len(missing)} 条")


def build(dry_run=False, runtime_lib_dir=None):
    runtime_lib_dirs = resolve_runtime_lib_dir(runtime_lib_dir)
    check_prereqs(runtime_lib_dirs)
    print(f"==> 编译远程 keymint（{COMPILE_SCRIPT}）"
          + (f" runtime-lib-dirs={runtime_lib_dirs}" if dry_run else ""))
    compile_remote(dry_run=dry_run, runtime_lib_dirs=runtime_lib_dirs)

    print("==> rc / tee-supplicant / vintf（基座改写）")
    write_rc_vintf(dry_run=dry_run)

    print("==> SELinux 标签登记")
    register_labels(baselines_file(), dry_run=dry_run)

    print("[ok] Remote KeyMint HAL 落盘完成（原地替换官方 mitee 三件套）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="gold-15 Remote KeyMint HAL 构建")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    ap.add_argument("--runtime-lib-dir", default=None,
                    help="链接期运行库目录（默认 deps.py 组装官方 gold images）")
    args = ap.parse_args()
    sys.exit(build(dry_run=args.dry_run, runtime_lib_dir=args.runtime_lib_dir))


if __name__ == "__main__":
    main()