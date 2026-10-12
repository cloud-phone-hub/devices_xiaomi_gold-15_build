#!/usr/bin/env python3
"""最小 ELF 解析：导出 .modinfo / .modinfo.modversions 计数 / 未定义符号清单。

用途：分析官方（厂商）内核模块，得到硬件仿真的「必须满足面」证据。
无第三方依赖（macOS 无 readelf）。
"""
import struct
import sys


def parse_elf(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF":
        raise ValueError("not ELF")
    is64 = data[4] == 2
    endian = "<" if data[5] == 1 else ">"
    if not is64:
        raise ValueError("only ELF64")
    e_shoff, = struct.unpack_from(endian + "Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(endian + "HHH", data, 0x3A)
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name, typ, flags, addr, offset, size, link, info, align, entsize = \
            struct.unpack_from(endian + "IIQQQQIIQQ", data, off)
        secs.append(dict(name_off=name, type=typ, flags=flags, addr=addr,
                         offset=offset, size=size, link=link, info=info,
                         entsize=entsize, idx=i))
    shstr = secs[e_shstrndx]
    strs = data[shstr["offset"]:shstr["offset"] + shstr["size"]]

    def sname(o):
        e = strs.find(b"\0", o)
        return strs[o:e].decode("utf-8", "replace")

    for s in secs:
        s["name"] = sname(s["name_off"])
    return data, secs, endian


def modinfo(path):
    data, secs, endian = parse_elf(path)
    out = {}
    for s in secs:
        if s["name"] in (".modinfo",):
            blob = data[s["offset"]:s["offset"] + s["size"]]
            for item in blob.split(b"\0"):
                if not item:
                    continue
                k, _, v = item.decode("utf-8", "replace").partition("=")
                out.setdefault(k, []).append(v)
    return out, data, secs, endian


def undefined_syms(path):
    data, secs, endian = parse_elf(path)
    syms = {}
    for s in secs:
        if s["type"] != 2:  # SHT_SYMTAB
            continue
        strtab = secs[s["link"]]
        strs = data[strtab["offset"]:strtab["offset"] + strtab["size"]]
        n = s["size"] // s["entsize"]
        for i in range(n):
            off = s["offset"] + i * s["entsize"]
            name, info, other, shndx, value, size = struct.unpack_from(
                endian + "IBBHQQ", data, off)
            if shndx == 0 and name:
                e = strs.find(b"\0", name)
                syms[strs[name:e].decode("utf-8", "replace")] = value
    return syms


def versions_count(path):
    data, secs, endian = parse_elf(path)
    n = 0
    for s in secs:
        if s["name"] in ("__versions", "___versions"):
            n = s["size"] // 64
    return n


def main():
    for path in sys.argv[1:]:
        print("=" * 78)
        print(path)
        try:
            mi, _, _, _ = modinfo(path)
        except Exception as exc:  # noqa: BLE001
            print("  !! 解析失败:", exc)
            continue
        for key in ("vermagic", "name", "alias", "depends", "softdep", "intree",
                    "srcversion", "license", "description", "retpoline",
                    "import_ns", "parm", "of_modalias"):
            if key in mi:
                for v in mi[key][:24]:
                    print(f"  {key}: {v}")
                if key == "alias" and len(mi[key]) > 24:
                    print(f"  {key}: ...(共 {len(mi[key])} 条)")
        try:
            us = undefined_syms(path)
            print(f"  未定义符号数: {len(us)}")
            mtk = sorted(k for k in us if any(t in k for t in (
                "mtk", "ged", "sspm", "mrdump", "m4u", "emi", "scmi", "mbox",
                "apusys", "ccci", "cmdq", "disp", "dvfs", "regulator", "clk")))
            print(f"  其中 MTK/平台相关: {len(mtk)}")
            for k in mtk[:60]:
                print("    -", k)
            print(f"  __versions 条目数: {versions_count(path)}")
        except Exception as exc:  # noqa: BLE001
            print("  !! 符号解析失败:", exc)


if __name__ == "__main__":
    main()
