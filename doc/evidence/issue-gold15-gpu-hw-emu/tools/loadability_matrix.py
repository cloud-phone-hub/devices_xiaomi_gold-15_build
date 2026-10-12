#!/usr/bin/env python3
"""厂商图形模块在「当前 CDH 内核」上的可加载性矩阵（CRC 级判据）。

用法：
  python3 loadability_matrix.py <Module.symvers> <官方模块目录> [模块名...]

输出：每个厂商模块的导入符号分类统计 + 关键缺失/CRC 不符样例。
依据：kernel/module/version.c `same_magic()`——有 modversions 时引导只比较 flag 段，
      内核版本串被忽略；真正的门是每个导入符号的 CRC（genksyms 类型签名）。
"""
import struct
import sys


def parse_symvers(path):
    versions = {}
    for line in open(path, encoding="utf8", errors="replace"):
        f = line.split()
        if len(f) < 4 or not f[0].startswith("0x"):
            continue
        versions[f[1]] = int(f[0][2:], 16)
    return versions


def sections(data):
    e_shoff, = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        v = struct.unpack_from("<IIQQQQIIQQ", data, off)
        secs.append(dict(name_off=v[0], type=v[1], offset=v[4], size=v[5],
                         link=v[6], entsize=v[9]))
    shstr = secs[e_shstrndx]
    strs = data[shstr["offset"]:shstr["offset"] + shstr["size"]]

    def nm(o):
        e = strs.find(b"\0", o)
        return strs[o:e].decode("utf8", "replace")

    for s in secs:
        s["name"] = nm(s["name_off"])
    return secs


def module_info(path):
    """返回 (imports{name:crc}, exports{name}) 。"""
    data = open(path, "rb").read()
    secs = sections(data)
    imports, exports = {}, set()
    vers = [s for s in secs if s["name"] == "__versions"]
    if vers:
        s = vers[0]
        blob = data[s["offset"]:s["offset"] + s["size"]]
        for o in range(0, len(blob), 64):
            end = blob.find(b"\0", o + 8)
            name = blob[o + 8:end].decode("ascii", "replace")
            imports[name] = struct.unpack_from("<I", blob, o)[0]
    elif any(s["name"] == "__version_ext_names" for s in secs):
        crcs = [s for s in secs if s["name"] == "__version_ext_crcs"][0]
        names = [s for s in secs if s["name"] == "__version_ext_names"][0]
        cb = data[crcs["offset"]:crcs["offset"] + crcs["size"]]
        nb = data[names["offset"]:names["offset"] + names["size"]]
        for i, n in enumerate(x for x in nb.decode("ascii", "replace").split("\0") if x):
            imports[n] = struct.unpack_from("<I", cb, i * 4)[0]
    for s in secs:
        if s["type"] != 2:  # SHT_SYMTAB
            continue
        strtab = secs[s["link"]]
        strs = data[strtab["offset"]:strtab["offset"] + strtab["size"]]
        for i in range(s["size"] // s["entsize"]):
            off = s["offset"] + i * s["entsize"]
            name, info, other, shndx, value, size = struct.unpack_from("<IBBHQQ", data, off)
            if not name:
                continue
            e = strs.find(b"\0", name)
            nm = strs[name:e].decode("utf8", "replace")
            bind = info >> 4
            if shndx != 0 and bind in (1, 2):  # GLOBAL/WEAK 且已定义
                exports.add(nm)
    return imports, exports


def main():
    symvers, moddir = sys.argv[1], sys.argv[2]
    names = sys.argv[3:] or [
        "mali_kbase_mt6833_r49", "mali_mgm_mt6833_r49", "mali_prot_alloc_mt6833_r49",
        "ged", "mtk_gpufreq_mt6833", "mtk_gpufreq_wrapper_legacy", "mtk_gpu_hal",
        "mtk_gpu_qos", "sspm", "iommu_gz", "memory-amms", "mtk-mbox",
        "met_gpu_api", "met_gpu_adv_api",
    ]
    kernel = parse_symvers(symvers)
    infos, provided = {}, set()
    for n in names:
        try:
            infos[n] = module_info(f"{moddir}/{n}.ko")
            provided |= infos[n][1]
        except Exception as exc:  # noqa: BLE001
            print(f"!! {n}: {exc}")
    print(f"内核 Module.symvers 导出符号: {len(kernel)}")
    print(f"厂商闭包模块: {len(infos)}，自身导出符号并集: {len(provided)}")
    total_imports, ok, crc_bad, sibling, missing = 0, set(), set(), set(), set()
    for n, (imports, _) in sorted(infos.items()):
        a = b = c = d = 0
        for sym, crc in imports.items():
            total_imports += 1
            if sym in kernel:
                if kernel[sym] == crc:
                    a += 1
                    ok.add(sym)
                else:
                    b += 1
                    crc_bad.add(sym)
            elif sym in provided:
                c += 1
                sibling.add(sym)
            else:
                d += 1
                missing.add(sym)
        print(f"  {n:32s} 导入 {len(imports):4d} | 本内核CRC一致 {a:4d} | "
              f"CRC不符 {b:4d} | 兄弟模块提供 {c:4d} | 完全缺失 {d:4d}")
    print(f"\n合计导入 {total_imports} 条引用；"
          f"唯一符号：一致 {len(ok)}，CRC 不符 {len(crc_bad)}，"
          f"兄弟提供 {len(sibling)}，缺失 {len(missing)}")
    print("\n[CRC 不符样例]")
    for s in sorted(crc_bad)[:20]:
        print(f"  {s}: 厂商 0x{[i for i in [infos[n][0].get(s) for n in infos] if i][0]:08x} "
              f"vs 本内核 0x{kernel[s]:08x}")
    print("\n[完全缺失样例（需 MTK 内核）]")
    for s in sorted(missing)[:40]:
        print("  ", s)


if __name__ == "__main__":
    main()
