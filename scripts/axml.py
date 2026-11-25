"""Read package name and version from an APK's binary AndroidManifest.xml.

Pure Python, no external tools, so it works the same locally and in Actions.
"""
import struct
import zipfile

RES_STRING_POOL = 0x0001
RES_XML_START_ELEMENT = 0x0102
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11


def _string_pool(data, off):
    _, header_size, _ = struct.unpack_from("<HHI", data, off)
    count, _, flags, strings_start, _ = struct.unpack_from("<IIIII", data, off + 8)
    utf8 = bool(flags & (1 << 8))
    offsets = struct.unpack_from(f"<{count}I", data, off + header_size)
    base = off + strings_start
    out = []
    for o in offsets:
        p = base + o
        if utf8:
            n = data[p]
            p += 2 if n & 0x80 else 1
            n = data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p + 1]
                p += 2
            else:
                p += 1
            out.append(data[p:p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]
                p += 2
            out.append(data[p:p + n * 2].decode("utf-16-le", "replace"))
    return out


def manifest_info(apk_path):
    with zipfile.ZipFile(apk_path) as z:
        data = z.read("AndroidManifest.xml")
    off = 8  # skip XML file header
    strings = []
    while off < len(data):
        ctype, header_size, size = struct.unpack_from("<HHI", data, off)
        if ctype == RES_STRING_POOL:
            strings = _string_pool(data, off)
        elif ctype == RES_XML_START_ELEMENT:
            name_idx = struct.unpack_from("<I", data, off + 20)[0]
            if strings[name_idx] == "manifest":
                attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, off + 24)
                p = off + 16 + attr_start
                info = {}
                for i in range(attr_count):
                    a = p + i * attr_size
                    _, aname, raw, _, _, dtype, dval = struct.unpack_from("<IIIHBBI", data, a)
                    key = strings[aname]
                    if dtype == TYPE_STRING:
                        val = strings[dval]
                    elif dtype in (TYPE_INT_DEC, TYPE_INT_HEX):
                        val = dval
                    elif raw != 0xFFFFFFFF:
                        val = strings[raw]
                    else:
                        val = dval
                    info[key] = val
                return {
                    "package": info.get("package"),
                    "versionName": str(info.get("versionName", "")),
                    "versionCode": info.get("versionCode"),
                }
        off += size
    raise ValueError("manifest element not found")


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(manifest_info(sys.argv[1])))
