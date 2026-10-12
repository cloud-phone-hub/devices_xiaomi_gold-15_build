#!/usr/bin/env python3
"""gold-15 纯软件 Gatekeeper HAL（android.hardware.gatekeeper）落地。

背景：官方 gold vendor 自带的是 **mitee（TEE）版** gatekeeper：
  * 二进制 /vendor/bin/hw/android.hardware.gatekeeper-service.mitee
  * rc     /vendor/etc/init/android.hardware.gatekeeper-service.mitee.rc
           （service vendor.gatekeeper_mitee，class hal、user system、group system）
  * vintf  /vendor/etc/vintf/manifest/android.hardware.gatekeeper-service.mitee.xml
           （声明 AIDL android.hardware.gatekeeper.IGatekeeper/default）
云机无 TEE，它必然起不来（run46 实测）：
  [MITEE]TEEC_InitializeContext failed ...
  MiteeGateKeeper exit(1) → IGatekeeper/default 永不注册 →
  system_server GateKeeper.java:41 抛 IllegalStateException →
  zygote ~21s 自杀循环 ×8，sys.boot_completed 永不置位。
（第一轮路线 = patch MiteeGateKeeper so 的 OpenSession 失败路径，已否决：
  patch 生效但不够，卡在后续 TEEC 调用，工程上不可穷尽。）

方案（AOSP 纯软件替换，同 keymint 替换 mitee 三件套的「单服务同名替换」模式，
但侵入更小——**只替换二进制一个文件**）：
  1. 编译：委托 cuttlefish/scripts/build-guest-gatekeeper.sh（AIDL 生成 +
     scrypt 预编译 + 单发 clang 链接 + llvm-readelf 校验的单一事实来源），
     产物**以基座同名**落盘 out/dist/vendor/bin/hw/android.hardware.gatekeeper-service.mitee
     （AOSP gatekeeper/aidl/software 纯软件实现，无 TEE、无通道、无 argv 依赖）；
  2. rc：**不动**（官方基座 rc 与软件版完全兼容：无 TEE 节点、无参数、
     class hal 即起；服务名 vendor.gatekeeper_mitee 沿用）；
  3. vintf：**不动**（官方基座 manifest 声明的实例名 IGatekeeper/default
     与软件版注册一致）；
  4. SELinux：二进制沿用基座 file_contexts 路径标签 hal_gatekeeper_default_exec
     （baselines vendor.selinux-labels 已登记），运行域 hal_gatekeeper_default
     与 mitee 相同 —— 该域注册 IGatekeeper/default 的授权官方 sepolicy 已备
     （真机 mitee 版即走此路径）。软件版无 /dev 节点依赖，授权面只会更小。

service.cpp 用本目录 standalone/ 改版（相对 AOSP 原版去掉
ISharedSecret/gatekeeper 的 addService：gold sepolicy 未授权
hal_gatekeeper_default 域注册该跨 HAL 服务名，注册必被拒且 CHECK_EQ abort；
SoftSharedSecret 对象保留构造，SoftGateKeeper 依赖其派生 HMAC key）。

用法：
  python3 build/vendor/source/android.hardware.gatekeeper/build.py
  python3 build/vendor/source/android.hardware.gatekeeper/build.py --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

# 本文件位于 build/vendor/source/android.hardware.gatekeeper/ 下，向上 5 级为设备根
_DEVICE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "scripts"))
sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "vendor", "build"))

from buildenv import (  # noqa: E402
    PROJECT_ROOT, VENDOR_STAGE_DIR, baselines_file,
)
import deps  # noqa: E402   gold 自身官方镜像的编译依赖组装

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
STANDALONE_DIR = os.path.join(MODULE_DIR, "standalone")
# libcxx cfg / ndk-r29-compat.h 与 gold keymint standalone 共用（同设备同工具链
# 约定，避免文件重复；本模块 standalone 只放 gatekeeper 自己的改版源）。
LIBCXX_STANDALONE_DIR = os.path.join(
    _DEVICE_ROOT, "build", "vendor", "source", "android.hardware.keymint",
    "standalone")

# 编译脚本（单一事实来源：AIDL 生成 + scrypt 预编译 + 链接 + readelf 校验）
COMPILE_SCRIPT = os.path.join(PROJECT_ROOT, "cuttlefish", "scripts",
                              "build-guest-gatekeeper.sh")
# 依赖源工作区（缺任一目录则报错退出）
SOURCE_WORKSPACE = os.path.join(PROJECT_ROOT, "cuttlefish", "source")
REQUIRED_SOURCE_DIRS = [
    "hardware-interfaces/gatekeeper/aidl/software",
    "hardware-interfaces/gatekeeper/aidl/aidl_api/android.hardware.gatekeeper/1",
    "hardware-interfaces/security/sharedsecret/aidl/aidl_api/android.hardware.security.sharedsecret/1",
    "hardware-interfaces/security/keymint/aidl/aidl_api/android.hardware.security.keymint/3",
    "hardware-interfaces/security/secureclock/aidl/aidl_api/android.hardware.security.secureclock/1",
    "system-gatekeeper",
    "system-keymaster/km_openssl",
    "external-scrypt/lib/crypto",
    "system-libbase/include",
    "system-logging/liblog/include",
    "frameworks-native/libs/binder/ndk/include_platform",
    "external-boringssl/src/include",
    "hardware-libhardware/include",
]
# 运行库目录：默认由 deps.py 从**官方 gold images**（vendor_a/system_a，EROFS）
# 组装（vendor 4 + system 2），链接期输入；运行时由 guest 自身 /vendor/lib64 +
# /system/lib64 提供。可用 `--runtime-lib-dir` 或 env CDH_RUNTIME 覆盖（仅排查）。
RUNTIME_LIBS = [
    "libc++.so", "libbase.so", "libbinder_ndk.so", "libcrypto.so",
    "libkeymaster_messages.so", "liblog.so",
]

# 落盘（相对 VENDOR_STAGE_DIR 即镜像内 /vendor 路径）：基座同名遮蔽
BIN_NAME = "android.hardware.gatekeeper-service.mitee"
DEST_BIN = os.path.join(VENDOR_STAGE_DIR, "bin", "hw", BIN_NAME)

# 新增/替换文件的 SELinux 标签（二进制沿用基座 file_contexts 路径标签）
LABELS = {
    f"bin/hw/{BIN_NAME}": "u:object_r:hal_gatekeeper_default_exec:s0",
}


def _missing(what, path):
    print(f"[err] {what} 缺失: {path}", file=sys.stderr)
    sys.exit(1)


def resolve_runtime_lib_dir(arg):
    """--runtime-lib-dir 参数 > env CDH_RUNTIME > deps（官方 gold images）。"""
    if arg:
        return arg
    if os.environ.get("CDH_RUNTIME"):
        return os.environ["CDH_RUNTIME"]
    return deps.prepare_runtime_libs()


def check_prereqs(runtime_lib_dir):
    """编译前置校验：脚本、依赖源工作区、运行库。缺失即报错退出。"""
    if not os.path.isfile(COMPILE_SCRIPT):
        _missing("gatekeeper 编译脚本", COMPILE_SCRIPT)
    for sub in REQUIRED_SOURCE_DIRS:
        if not os.path.isdir(os.path.join(SOURCE_WORKSPACE, sub)):
            _missing("依赖源目录", os.path.join(SOURCE_WORKSPACE, sub))
    if not os.path.isfile(os.path.join(STANDALONE_DIR, "service.cpp")):
        _missing("gold 改版 service.cpp",
                 os.path.join(STANDALONE_DIR, "service.cpp"))
    for f in ("libcxx-platform/__config_site", "ndk-r29-compat.h"):
        if not os.path.isfile(os.path.join(LIBCXX_STANDALONE_DIR, f)):
            _missing("keymint standalone 共用件", os.path.join(LIBCXX_STANDALONE_DIR, f))
    if not os.path.isdir(runtime_lib_dir):
        _missing("运行库目录", runtime_lib_dir)
    for lib in RUNTIME_LIBS:
        if not os.path.isfile(os.path.join(runtime_lib_dir, lib)):
            _missing("运行库", os.path.join(runtime_lib_dir, lib))


def compile_software(dry_run=False, runtime_lib_dir=None):
    """委托 build-guest-gatekeeper.sh 编译软件 gatekeeper，产物落成基座同名。"""
    argv = [
        "bash", COMPILE_SCRIPT,
        "--out", DEST_BIN,
        "--runtime-lib-dir", runtime_lib_dir,
        "--standalone-dir", LIBCXX_STANDALONE_DIR,
        "--service-cpp", os.path.join(STANDALONE_DIR, "service.cpp"),
    ]
    if dry_run:
        print("[..] (dry)", " ".join(argv))
        return
    res = subprocess.run(argv)
    if res.returncode != 0:
        print(f"[err] gatekeeper 编译失败（exit={res.returncode}）", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(DEST_BIN):
        _missing("gatekeeper 编译产物", DEST_BIN)


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
    runtime_lib_dir = resolve_runtime_lib_dir(runtime_lib_dir)
    check_prereqs(runtime_lib_dir)
    print(f"==> 编译软件 gatekeeper（{COMPILE_SCRIPT}）"
          + (f" runtime-lib-dir={runtime_lib_dir}" if dry_run else ""))
    compile_software(dry_run=dry_run, runtime_lib_dir=runtime_lib_dir)

    print("==> SELinux 标签登记")
    register_labels(baselines_file(), dry_run=dry_run)

    print("[ok] 软件 Gatekeeper HAL 落盘完成（基座同名遮蔽官方 mitee 二进制；"
          "rc/vintf 保持官方基座）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="gold-15 软件 Gatekeeper HAL 构建")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    ap.add_argument("--runtime-lib-dir", default=None,
                    help="链接期运行库目录（默认 deps.py 组装官方 gold images）")
    args = ap.parse_args()
    sys.exit(build(dry_run=args.dry_run, runtime_lib_dir=args.runtime_lib_dir))


if __name__ == "__main__":
    main()
