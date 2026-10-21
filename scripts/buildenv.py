"""gold-15 构建环境：固件选择环境变量 + 宿主工具链定位 + 设备事实/路径。

各阶段脚本（build-* / unpack-roms / pack_super / pack-roms / vendor* / vendor_dlkm*）
统一从这里读取：
  - 构建选择：CDH_BUILD_ID（build-id）决定固件（roms/<build-id>.tgz）与基线件；
  - 路径/命名：设备根、out/dist 布局、lineage 底包、版本目录等全部在此推导，
    可被 CDH_* 环境变量覆盖，脚本不硬编码绝对路径；
  - 宿主工具：debugfs / e2fsck / resize2fs / qemu-img / baselines。

工具解析优先级（_tool / _any_tool）：
  1. 环境变量直指（E2FSPROGS_<NAME> / QEMU_IMG / SIMG2IMG）
  2. brew e2fsprogs sbin / Android SDK emulator 等常用安装位置
  3. PATH（shutil.which）
"""

import glob
import json
import os
import re
import shutil

# 宿主工具常用安装位置（macOS brew e2fsprogs / Android SDK）
_E2FSPROGS_SBIN = "/opt/homebrew/opt/e2fsprogs/sbin"
_QEMU_CANDIDATES = (
    "/Users/cuimingqiang/Library/Android/sdk/emulator/qemu-img",
    "/opt/homebrew/bin/qemu-img",
    "/usr/local/bin/qemu-img",
)


def env(name, default):
    """读取环境变量，未设置或为空时回退 default。"""
    return os.environ.get(name) or default


def _tool(name):
    """定位 e2fsprogs 工具；env 覆盖 > brew sbin > PATH，缺失时抛异常。"""
    cand = os.environ.get("E2FSPROGS_" + name.upper())
    if cand and os.path.isfile(cand):
        return cand
    brew = os.path.join(_E2FSPROGS_SBIN, name)
    if os.path.isfile(brew):
        return brew
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"缺少 e2fsprogs 工具: {name}（brew install e2fsprogs）")


def e2fs_tools():
    """返回 (debugfs, e2fsck, resize2fs) 绝对路径元组。"""
    return _tool("debugfs"), _tool("e2fsck"), _tool("resize2fs")


def _qemu_img():
    """定位 qemu-img（QEMU_IMG 环境变量优先，其次常见安装位置，最后 PATH）。"""
    cand = os.environ.get("QEMU_IMG")
    if cand and os.path.isfile(cand):
        return cand
    for p in _QEMU_CANDIDATES:
        if os.path.isfile(p):
            return p
    found = shutil.which("qemu-img")
    if found:
        return found
    raise FileNotFoundError("缺少 qemu-img（可用 QEMU_IMG 环境变量指定）")


def any_tool(names, suffixes=()):
    """依次在 PATH 与常见位置查找任一命令，返回首个可执行绝对路径或 None。"""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        for suf in suffixes:
            cand = os.path.join(suf, name)
            if os.path.isfile(cand):
                return cand
    return None


def baselines_file():
    """定位 build/baselines/<build-id>.json（基线件清单）。

    优先 CDH_BASELINES_JSON；其次 CDH_BUILD_ID 对应文件；否则取 baselines/ 目录
    内唯一 JSON（单 ROM 设备）。无法确定时返回 None。
    """
    p = os.environ.get("CDH_BASELINES_JSON")
    if p:
        return p if os.path.isfile(p) else None
    build_dir = os.path.dirname(os.path.abspath(__file__))
    baselines_dir = os.path.join(os.path.dirname(build_dir), "baselines")
    build_id = os.environ.get("CDH_BUILD_ID")
    if build_id:
        p = os.path.join(baselines_dir, f"{build_id}.json")
        return p if os.path.isfile(p) else None
    entries = sorted(glob.glob(os.path.join(baselines_dir, "*.json")))
    return entries[0] if len(entries) == 1 else None


def load_baselines():
    """读取基线清单 JSON；文件缺失或解析失败时返回 None。"""
    p = baselines_file()
    if not p:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# =====================================================================
# 设备事实与路径（单一数据源，env 优先，其次 manifest.json 推导）
# =====================================================================

# 本文件位于 build/scripts/ 下，向上 3 级即设备根；再向上 3 级为项目根
_DEV_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DEV_ROOT)))

ROMS_DIR = os.path.join(_DEV_ROOT, "roms")          # 官方固件输入（非源码）
OUT_DIR = os.path.join(_DEV_ROOT, "out")            # 设备编译产物


def _manifest():
    """读取 build/manifest.json（固件/构建声明），缺失或解析失败返回 {}。"""
    try:
        with open(os.path.join(_DEV_ROOT, "build", "manifest.json"),
                  encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _manifest_build():
    builds = (_manifest().get("build") or [])
    return builds[0] if builds else {}


# ---- 构建选择：CDH_BUILD_ID 优先，其次 manifest.json 首个 build ----
BUILD_ID = env("CDH_BUILD_ID", _manifest_build().get("build-id") or "")

# Android 版本（主/主.次，取自 manifest build 的 android-version，如 15.0.0）
_ANDROID_VERSION = _manifest_build().get("android-version") or ""
OS = _ANDROID_VERSION.split(".")[0] if _ANDROID_VERSION else "15"
_ANDROID_MINOR = ".".join(_ANDROID_VERSION.split(".")[:2]) if _ANDROID_VERSION else "15.0"

# 固件版本（OS3.0.9.0.VNQCNXM，取自 build-id；CDH_OS_VERSION 可覆盖）
_OS_VER_RE = re.compile(r"OS\d+(?:\.\d+)*\.[A-Z0-9]+")


def _os_version():
    cand = os.environ.get("CDH_OS_VERSION")
    if cand:
        return cand
    m = _OS_VER_RE.search(BUILD_ID)
    return m.group(0) if m else ""


OS_VERSION = _os_version()

# 版本目录：out/device/<name>/<os>/<os_version 小写>（多设备共用的项目级 out/device）
VERSION_DIR = os.path.join(PROJECT_ROOT, "out", "device",
                           "gold-15", OS, OS_VERSION.lower())

# ---- 官方固件输入 ----
# ROM_TGZ = roms/<build-id>.tgz（CDH_ROM_TGZ 可覆盖）
ROM_TGZ = env("CDH_ROM_TGZ",
              os.path.join(ROMS_DIR, f"{BUILD_ID}.tgz") if BUILD_ID else "")
# ROM_ROOT_DIR = tgz 内层目录（约定 gold[_<区域>]_images_<OS版本>_<android主.次>，CDH_ROM_DIR 可覆盖）
_ROM_VER_RE = re.compile(r"^(gold(?:_[a-z]+)?_images_OS\d+(?:\.\d+)*\.[A-Z0-9]+)_")


def _rom_root_dir():
    cand = os.environ.get("CDH_ROM_DIR")
    if cand:
        return cand
    m = _ROM_VER_RE.match(BUILD_ID)
    if m:
        return os.path.join(ROMS_DIR, f"{m.group(1)}_{_ANDROID_MINOR}")
    raise RuntimeError(
        f"无法从 build-id 推导 ROM 目录: {BUILD_ID!r}（可设 CDH_ROM_DIR 指定）")


ROM_ROOT_DIR = _rom_root_dir()

# 关键镜像（官方固件平铺在 ROM_ROOT_DIR；本机官方固件仅保留 super.img）
SUPER_IMG = os.path.join(ROM_ROOT_DIR, "super.img")
BOOT_IMG = os.path.join(ROM_ROOT_DIR, "boot.img")
VENDOR_BOOT_IMG = os.path.join(ROM_ROOT_DIR, "vendor_boot.img")
VBMETA_IMG = os.path.join(ROM_ROOT_DIR, "vbmeta.img")
# 官方 super.img 逻辑分区拆解结果（unpack-roms.py super 任务平铺到官方解压目录）
SUPER_OUT = ROM_ROOT_DIR

# ---- lineage 底包（UTM 虚机，同 pixel6-15 的 vda->分区 模式）----
LINEAGE_ZIP = env("CDH_LINEAGE_ZIP", "")
if not LINEAGE_ZIP:
    _zips = sorted(glob.glob(os.path.join(PROJECT_ROOT, "lineage-rom", "*.zip")))
    LINEAGE_ZIP = _zips[0] if _zips else ""
LINEAGE_VDA_MEMBER = "LineageOS_on_arm64.utm/Data/vda.qcow2"  # 底包内 vda（qcow2）
LINEAGE_OUT_DIR = env("CDH_LINEAGE_DIR",
                      os.path.join(PROJECT_ROOT, "lineage-rom", "lineage-22.0"))
# lineage 底包 super 逻辑分区（vendor/vendor_dlkm/odm 基座，virtio 内核匹配）
LINEAGE_SUPER_DIR = os.path.join(LINEAGE_OUT_DIR, "super")

# ---- 输出布局 ----
DIST_DIR = os.path.join(OUT_DIR, "dist")
ROM_DIST = os.path.join(DIST_DIR, "rom")        # 分发镜像（boot/vendor/super/...）
TMP_DIR = os.path.join(OUT_DIR, "tmp")          # 调试临时（不用 /tmp）
KERNEL_OUT = os.path.join(OUT_DIR, "kernel")    # 内核编译产物（Image/modules/config）
KERNEL_MODULES_DIR = os.path.join(DIST_DIR, "kernel", "modules")  # 模块归集
VENDOR_OUT = os.path.join(OUT_DIR, "vendor", "bin", "hw")         # HAL 产物
KERNEL_SRC = os.path.join(_DEV_ROOT, "kernel", "source")          # 内核源码

# ---- vendor patch 注入树 ----
VENDOR_PATCH_DIR = os.path.join(_DEV_ROOT, "build", "vendor", "patch")
# vendor 注入的第二棵树：source 子模块落盘的 /vendor 内容（out/dist/vendor，
# 相对路径即镜像内 /vendor 路径，如 bin/daemon）。由 inject 一并注入。
VENDOR_STAGE_DIR = os.path.join(DIST_DIR, "vendor")

# ---- 分发/最终产物 ----
VENDOR_DIST = os.path.join(ROM_DIST, "vendor.img")                # build-vendor 产物
VENDOR_DLKM_DIST = os.path.join(ROM_DIST, "vendor_dlkm.img")      # build-vendor_dlkm 产物
SUPER_DIST = os.path.join(ROM_DIST, "super.img")                  # pack_super 产物
BOOT_DIST = os.path.join(ROM_DIST, "boot.img")                    # build-boot 产物（写 vda boot_a/boot_b）
VENDOR_BOOT_DIST = os.path.join(ROM_DIST, "vendor_boot.img")      # build-vendor-boot 产物（写 vda vendor_boot_a/_b）
PATCH_DIST = os.path.join(ROM_DIST, "patch.img")                  # vdc 载入的 lineage vendor/vendor_dlkm 打包镜像
VERSION_VDA = os.path.join(VERSION_DIR, "vda.img")                # pack-roms 产物
VERSION_DATA = os.path.join(VERSION_DIR, "data.qcow2")            # pack-roms 数据盘

# ---- 构建阶段 ----
STAGES = ["unpack", "kernel", "boot", "vendor_boot",
          "vendor", "vendor_dlkm", "patch", "super", "pack"]
