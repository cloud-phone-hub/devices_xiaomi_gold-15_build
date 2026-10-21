#!/usr/bin/env python3
"""xiaomi note13（Redmi Note 13 5G, android15）—— AIDL health HAL 设备集成层。

本模块**不自行编译/链接驱动**：codegen + 编译 + 链接整套流水线全部委托给
`hal/android15/android.hardware.health/build.py` 的
`build(out_dir, runtime_lib_dirs, dry_run)`。note13 只做设备集成层独有的两件事：
  1. 把引擎产出的服务二进制从 `<out>/vendor/bin/hw` 拷进 /vendor staging
     （`out/dist/vendor/bin/hw`）；
  2. 落 **note13 独有配置**（本目录 vendor/：rc、vintf）并登记 SELinux。

职责划分：
  * 驱动**源码 / AIDL 快照 / platform 头 / 工具链 / codegen+编译+链接**
    全在 hal/android15 —— 本模块不再复制该流水线（无 src/aidl/deps.py 自带实现）；
  * 本目录只放 **note13 设备独有配置** vendor/；
  * 运行库 ABI（libc++/libbinder_ndk）→ note13 官方镜像研磨
    （build/vendor/build/deps.py prepare_runtime_libs），也可经 CDH_RUNTIME 借用他机。

产物（仅服务，framework 依赖一律不得链入）：
  out/dist/vendor/bin/hw/android.hardware.health-service.example

用法（在设备根 devices/xiaomi/note13-5G-15 执行，前置：hal deps 就绪）：
  python3 build/vendor/source/android.hardware.health/build.py
  python3 build/vendor/source/android.hardware.health/build.py --dry-run
"""

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile

# 本文件位于 devices/xiaomi/note13-5G-15/build/vendor/source/<module>/ 下
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
# 上跳 4 级 <module> -> source -> vendor -> build -> 设备根
_DEVICE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(MODULE_DIR))))
# 仓库根（note13-5G-15 -> xiaomi -> devices -> 仓库根）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DEVICE_ROOT)))

# hal/android15 线：驱动 codegen+编译+链接的规范引擎
_HAL_ROOT = os.path.join(_REPO_ROOT, "hal", "android15")
_HAL_ENGINE = os.path.join(_HAL_ROOT, "android.hardware.health", "build.py")

# 设备 buildenv（唯一名，正常 import）
sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "scripts"))
sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "vendor", "build"))
from buildenv import OUT_DIR, VENDOR_STAGE_DIR, baselines_file  # noqa: E402


def _load_by_path(name, path):
    """按绝对路径加载模块，规避 hal/deps.py 与设备/deps.py 同名 deps 的冲突。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# note13 设备共享依赖（ABI 运行库研磨）
device_deps = _load_by_path(
    "cdh_device_deps", os.path.join(_DEVICE_ROOT, "build", "vendor", "build", "deps.py"))
# hal 线 health 构建引擎：build(out_dir, runtime_lib_dirs, dry_run)
hal_health = _load_by_path("cdh_hal_health_engine", _HAL_ENGINE)

# 引擎产物根：bin/hw 落 <out>/vendor（= buildenv VENDOR_OUT），
# tmp 工作区经 tmp_dir 落 out/tmp，**不进 vendor staging**（out/dist/vendor 是打包镜像面）
ENGINE_OUT_DIR = os.path.join(OUT_DIR, "vendor")

# ---- note13 独有配置 / SELinux（引擎不关心，设备层职责）----
RC_NAME = "android.hardware.health-service.rc"
# 不落 vintf 片段：官方 vendor 的 android.hardware.health-service.example.xml 已声明
# IHealth/default，重复会让 VINTF 整体解析失败→ early boot 卡死（实测 2026-10-20）。
VINTF_NAMES = ['android.hardware.health-service.xml']
LABEL_BIN = "u:object_r:hal_health_default_exec:s0"
LABEL_CONFIG = "u:object_r:vendor_configs_file:s0"


def prepare_abi(dry_run=False):
    """运行库**目录列表**：CDH_RUNTIME 显式指定（可用 os.pathsep 传多目录，
    单目录也兼容），否则研磨 note13 lineage super（单目录 vendor-compile-dep）。"""
    env = os.environ.get("CDH_RUNTIME")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    d = device_deps.prepare_runtime_libs(dry_run)
    return [d] if isinstance(d, str) else d


def install_binaries(dry_run=False):
    """把引擎链接好的服务二进制从 <out>/vendor/bin/hw 拷进 /vendor staging。"""
    bin_subdir = hal_health.BIN_SUBDIR  # bin/hw
    for service in hal_health.SERVICES:
        src = os.path.join(ENGINE_OUT_DIR, bin_subdir, service)
        dst = os.path.join(VENDOR_STAGE_DIR, bin_subdir, service)
        if dry_run:
            print(f"[..] (dry) cp {src} -> {dst}")
            continue
        if not os.path.isfile(src):
            print(f"[err] 引擎未产出二进制: {src}", file=sys.stderr)
            sys.exit(1)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        print(f"    写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")


def write_rc(dst):
    """拷贝 init rc 到 staging（源在 vendor/etc/init/，设备无关的 /vendor/bin/hw 路径）。"""
    src = os.path.join(MODULE_DIR, "vendor", "etc", "init", RC_NAME)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, encoding="utf-8") as f:
        content = f.read()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"    写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")


def write_vintf(dst):
    """拷贝 VINTF manifest 到 staging（源在 vendor/etc/vintf/manifest/）。"""
    src = os.path.join(MODULE_DIR, "vendor", "etc", "vintf", "manifest",
                       os.path.basename(dst))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, encoding="utf-8") as f:
        content = f.read()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"    写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")


def register_labels(baselines_path, dry_run=False):
    """向设备 baselines 清单登记本模块安装文件的 SELinux 上下文。"""
    if not baselines_path or not os.path.isfile(baselines_path):
        print(f"[err] 基线清单缺失: {baselines_path or 'None'}", file=sys.stderr)
        sys.exit(1)
    with open(baselines_path, encoding="utf-8") as f:
        doc = json.load(f)
    labels = doc.setdefault("vendor", {}).setdefault("selinux-labels", {})
    entries = {f"bin/hw/{name}": LABEL_BIN for name in hal_health.SERVICES}
    entries[f"etc/init/{RC_NAME}"] = LABEL_CONFIG
    for v in VINTF_NAMES:
        entries[f"etc/vintf/manifest/{v}"] = LABEL_CONFIG
    missing = {k: v for k, v in entries.items() if labels.get(k) != v}
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


def build(dry_run=False):
    runtime = prepare_abi(dry_run)

    # 1) 整套 codegen + 编译 + 链接委托给 hal 线规范引擎（产物落 ENGINE_OUT_DIR）
    print(f"==> 委托 hal 引擎：codegen + 编译 + 链接"
          f"（out={ENGINE_OUT_DIR}, runtime={runtime}）")
    hal_health.build(ENGINE_OUT_DIR, runtime, tmp_dir=OUT_DIR, dry_run=dry_run)

    # 2) 引擎产物 -> /vendor staging（tmp 工作区不随镜像走）
    print("==> 安装服务二进制到 /vendor staging")
    install_binaries(dry_run=dry_run)

    # 3) note13 独有配置 + SELinux（设备集成层职责）
    print("==> rc / vintf")
    write_rc(os.path.join(VENDOR_STAGE_DIR, "etc", "init", RC_NAME))
    for v in VINTF_NAMES:
        write_vintf(os.path.join(VENDOR_STAGE_DIR, "etc", "vintf", "manifest", v))

    print("==> SELinux 标签登记")
    register_labels(baselines_file(), dry_run=dry_run)

    print("[ok] note13 health 集成完成（委托 hal 引擎构建 + note13 配置）")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="xiaomi note13 AIDL health HAL 设备集成层（委托 hal 引擎构建）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args()
    sys.exit(build(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
