"""EXIF 讀取（JPEG / TIFF / HEIF，含 Sony MakerNote 創意風格）。

只讀檔案開頭 256KB，不會把整張原圖讀進記憶體。HEIC 的 EXIF 直接走 ISOBMFF
盒狀結構（meta → iinf 找出型別是 Exif 的項目、iloc 查它的位置），拿到那一塊之後
裡面就是標準的 TIFF，交給同一套 IFD 解析。

這是網頁版 js/engine/exif.js 的移植，欄位名稱與格式完全一樣，
所以篩選、統計、改名、相框吃到的字串都跟網頁版一致。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_READ_BYTES = 262144  # 256KB

IFD_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

EXPOSURE_PROGRAM = {0: "未定義", 1: "手動", 2: "程式自動", 3: "光圈優先", 4: "快門優先",
                    5: "創意程式", 6: "動作程式", 7: "人像模式", 8: "風景模式"}
METERING_MODE = {0: "未知", 1: "平均測光", 2: "中央重點", 3: "點測光", 4: "多點測光",
                 5: "權衡測光", 6: "部分測光", 255: "其他"}
WHITE_BALANCE_STD = {0: "自動", 1: "手動"}
COLOR_SPACE = {1: "sRGB", 65535: "未校正"}
SCENE_CAPTURE = {0: "標準", 1: "風景", 2: "人像", 3: "夜景"}

TAG_NAMES = {
    "ifd0": {
        0x0100: "ImageWidth", 0x0101: "ImageLength", 0x0102: "BitsPerSample",
        0x0103: "Compression", 0x0106: "PhotometricInterpretation",
        0x010E: "ImageDescription", 0x010F: "Make", 0x0110: "Model",
        0x0112: "Orientation", 0x011A: "XResolution", 0x011B: "YResolution",
        0x0128: "ResolutionUnit", 0x0131: "Software", 0x0132: "DateTime",
        0x013B: "Artist", 0x0213: "YCbCrPositioning", 0x8298: "Copyright",
        0x8769: "ExifIFDPointer", 0x8825: "GPSInfoIFDPointer",
    },
    "exif": {
        0x829A: "ExposureTime", 0x829D: "FNumber", 0x8822: "ExposureProgram",
        0x8827: "ISOSpeedRatings", 0x8830: "SensitivityType", 0x8832: "RecommendedExposureIndex",
        0x9000: "ExifVersion", 0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized",
        0x9010: "OffsetTime", 0x9011: "OffsetTimeOriginal",
        0x9101: "ComponentsConfiguration", 0x9102: "CompressedBitsPerPixel",
        0x9201: "ShutterSpeedValue", 0x9202: "ApertureValue", 0x9203: "BrightnessValue",
        0x9204: "ExposureBiasValue", 0x9205: "MaxApertureValue", 0x9206: "SubjectDistance",
        0x9207: "MeteringMode", 0x9208: "LightSource", 0x9209: "Flash",
        0x920A: "FocalLength", 0x927C: "MakerNote", 0x9286: "UserComment",
        0x9290: "SubSecTime", 0x9291: "SubSecTimeOriginal", 0x9292: "SubSecTimeDigitized",
        0xA000: "FlashpixVersion", 0xA001: "ColorSpace",
        0xA002: "PixelXDimension", 0xA003: "PixelYDimension",
        0xA20E: "FocalPlaneXResolution", 0xA20F: "FocalPlaneYResolution",
        0xA210: "FocalPlaneResolutionUnit", 0xA217: "SensingMethod",
        0xA300: "FileSource", 0xA301: "SceneType", 0xA401: "CustomRendered",
        0xA402: "ExposureMode", 0xA403: "WhiteBalance", 0xA404: "DigitalZoomRatio",
        0xA405: "FocalLengthIn35mmFilm", 0xA406: "SceneCaptureType",
        0xA407: "GainControl", 0xA408: "Contrast", 0xA409: "Saturation",
        0xA40A: "Sharpness", 0xA40C: "SubjectDistanceRange", 0xA420: "ImageUniqueID",
        0xA430: "CameraOwnerName", 0xA431: "BodySerialNumber",
        0xA432: "LensSpecification", 0xA433: "LensMake", 0xA434: "LensModel",
        0xA435: "LensSerialNumber",
    },
    "gps": {
        0x0000: "GPSVersionID", 0x0001: "GPSLatitudeRef", 0x0002: "GPSLatitude",
        0x0003: "GPSLongitudeRef", 0x0004: "GPSLongitude", 0x0005: "GPSAltitudeRef",
        0x0006: "GPSAltitude", 0x0007: "GPSTimeStamp", 0x0008: "GPSSatellites",
        0x0009: "GPSStatus", 0x000A: "GPSMeasureMode", 0x000B: "GPSDOP",
        0x000C: "GPSSpeedRef", 0x000D: "GPSSpeed", 0x0010: "GPSImgDirectionRef",
        0x0011: "GPSImgDirection", 0x0012: "GPSMapDatum", 0x001D: "GPSDateStamp",
    },
}

HEIF_BRANDS = {"heic", "heix", "heim", "heis", "hevc", "hevx", "hevm", "hevs",
               "mif1", "msf1", "heif", "avif", "avis"}


# ---------------------------------------------------------------- 格式化
def fmt_shutter(sec):
    if sec is None:
        return None
    if sec >= 1:
        return (f"{sec:.0f}" if sec % 1 == 0 else f"{sec:.1f}") + " s"
    if sec <= 0:
        return None
    return f"1/{round(1 / sec)} s"


def _round(v, digits):
    if v is None:
        return None
    r = round(v * 10 ** digits) / 10 ** digits
    return int(r) if r == int(r) else r


# ---------------------------------------------------------------- 二進位
class _Buf:
    __slots__ = ("b", "n")

    def __init__(self, data: bytes):
        self.b = data
        self.n = len(data)

    def u8(self, at):
        return self.b[at]

    def u16(self, at, little=False):
        return struct.unpack_from("<H" if little else ">H", self.b, at)[0]

    def i16(self, at, little=False):
        return struct.unpack_from("<h" if little else ">h", self.b, at)[0]

    def u32(self, at, little=False):
        return struct.unpack_from("<I" if little else ">I", self.b, at)[0]

    def i32(self, at, little=False):
        return struct.unpack_from("<i" if little else ">i", self.b, at)[0]

    def string(self, at, length):
        raw = self.b[at:at + length]
        z = raw.find(b"\0")
        if z >= 0:
            raw = raw[:z]
        return raw.decode("latin-1")


@dataclass
class _Entry:
    type: int
    count: int
    value: object
    pos: int  # valueOffsetPos


def _read_value(buf: _Buf, typ, count, pos, tiff, little):
    size = IFD_TYPE_SIZE.get(typ, 1) * count
    data = pos
    if size > 4:
        data = tiff + buf.u32(pos, little)
    if data + size > buf.n:
        raise IndexError("out of range")
    if typ == 2:
        return buf.string(data, count).strip()
    if typ == 7:
        return ("undefined", count, data)

    def one(i):
        if typ in (1, 6):
            return buf.u8(data + i)
        if typ == 3:
            return buf.u16(data + i * 2, little)
        if typ == 8:
            return buf.i16(data + i * 2, little)
        if typ == 4:
            return buf.u32(data + i * 4, little)
        if typ == 9:
            return buf.i32(data + i * 4, little)
        if typ == 5:
            num, den = buf.u32(data + i * 8, little), buf.u32(data + i * 8 + 4, little)
            return 0 if den == 0 else num / den
        if typ == 10:
            num, den = buf.i32(data + i * 8, little), buf.i32(data + i * 8 + 4, little)
            return 0 if den == 0 else num / den
        return buf.u8(data + i)

    if count == 1:
        return one(0)
    return [one(i) for i in range(min(count, 4096))]


def _read_ifd(buf: _Buf, offset, tiff, little):
    tags: dict[int, _Entry] = {}
    n = buf.u16(offset, little)
    pos = offset + 2
    for _ in range(n):
        if pos + 12 > buf.n:
            break
        tag = buf.u16(pos, little)
        typ = buf.u16(pos + 2, little)
        count = buf.u32(pos + 4, little)
        try:
            value = _read_value(buf, typ, count, pos + 8, tiff, little)
        except Exception:
            value = None
        tags[tag] = _Entry(typ, count, value, pos + 8)
        pos += 12
    nxt = buf.u32(pos, little) if pos + 4 <= buf.n else 0
    return tags, nxt


def _dms(dms, ref):
    if not isinstance(dms, list) or len(dms) < 3:
        return None
    v = abs(dms[0]) + abs(dms[1]) / 60 + abs(dms[2]) / 3600
    return -v if ref in ("S", "W") else v


def _parse_gps(tags):
    if not tags:
        return None
    g = lambda i: tags[i].value if i in tags else None  # noqa: E731
    lat_ref, lon_ref = g(0x0001), g(0x0003)
    lat, lon = _dms(g(0x0002), lat_ref), _dms(g(0x0004), lon_ref)
    if lat is None or lon is None:
        return None
    alt = g(0x0006)
    if isinstance(alt, (int, float)) and g(0x0005) == 1:
        alt = -alt
    return {
        "latitude": lat, "longitude": lon,
        "latitudeRef": lat_ref or ("N" if lat >= 0 else "S"),
        "longitudeRef": lon_ref or ("E" if lon >= 0 else "W"),
        "altitude": alt if isinstance(alt, (int, float)) else None,
    }


def _dump(tags, kind):
    if not tags:
        return []
    names = TAG_NAMES.get(kind, {})
    rows = []
    for tid in sorted(tags):
        v = tags[tid].value
        if isinstance(v, tuple) and v and v[0] == "undefined":
            v = f"<{v[1]} bytes>"
        elif isinstance(v, list):
            fmt = [(_round(x, 4) if isinstance(x, float) else x) for x in v[:16]]
            v = ", ".join(map(str, fmt)) + ("…" if len(v) > 16 else "")
        elif isinstance(v, float):
            v = _round(v, 6)
        rows.append({"id": tid, "tag": f"0x{tid:04X}", "name": names.get(tid),
                     "value": "" if v is None else str(v)})
    return rows


def _sony_style(buf, mn, tiff, little):
    start = mn + 12 if buf.string(mn, 12).startswith("SONY") else mn
    try:
        tags, _ = _read_ifd(buf, start, tiff, little)
    except Exception:
        return None
    e = tags.get(0xB020)
    if not e or not isinstance(e.value, str):
        return None
    return e.value.strip() or None


# ---------------------------------------------------------------- HEIF
def _is_heif(buf: _Buf):
    if buf.n < 12 or buf.string(4, 4) != "ftyp":
        return False
    if buf.string(8, 4).lower() in HEIF_BRANDS:
        return True
    size = buf.u32(0)
    at = 16
    while at + 4 <= min(size, buf.n):
        if buf.string(at, 4).lower() in HEIF_BRANDS:
            return True
        at += 4
    return False


def _walk(buf, frm, to, visit):
    at = frm
    while at + 8 <= to:
        size = buf.u32(at)
        typ = buf.string(at + 4, 4)
        head = 8
        if size == 1:
            if at + 16 > to:
                break
            size = buf.u32(at + 12)
            head = 16
        elif size == 0:
            size = to - at
        if size < head or at + size > to:
            break
        if visit(typ, at + head, size - head) is False:
            return
        at += size


def _exif_item_id(buf, frm, ln):
    found = [None]
    version = buf.u8(frm)
    at = frm + 4 + (2 if version == 0 else 4)

    def visit(typ, body, _size):
        if typ != "infe":
            return
        v = buf.u8(body)
        if v < 2:
            return
        iid = buf.u16(body + 4) if v == 2 else buf.u32(body + 4)
        if buf.string(body + (8 if v == 2 else 10), 4) == "Exif":
            found[0] = iid

    _walk(buf, at, frm + ln, visit)
    return found[0]


def _item_location(buf, frm, ln, wanted):
    version = buf.u8(frm)
    at = frm + 4
    s1 = buf.u8(at)
    off_size, len_size = s1 >> 4, s1 & 0xF
    s2 = buf.u8(at + 1)
    base_size = s2 >> 4
    index_size = (s2 & 0xF) if version >= 1 else 0
    at += 2
    count = buf.u16(at) if version < 2 else buf.u32(at)
    at += 2 if version < 2 else 4

    def rint(pos, n):
        if n == 4:
            return buf.u32(pos)
        if n == 8:
            return buf.u32(pos) * 4294967296 + buf.u32(pos + 4)
        if n == 2:
            return buf.u16(pos)
        return 0

    for _ in range(count):
        if at >= frm + ln:
            break
        iid = buf.u16(at) if version < 2 else buf.u32(at)
        at += 2 if version < 2 else 4
        if version >= 1:
            at += 2
        at += 2
        base = rint(at, base_size)
        at += base_size
        extents = buf.u16(at)
        at += 2
        for e in range(extents):
            at += index_size
            off = rint(at, off_size)
            at += off_size
            length = rint(at, len_size)
            at += len_size
            if iid == wanted and e == 0:
                return base + off, length
    return None


def _locate_heif_exif(buf):
    found = [None]

    def visit(typ, body, size):
        if typ != "meta":
            return None
        state = {"id": None, "iloc": None}

        def inner(t, b, ln):
            if t == "iinf":
                state["id"] = _exif_item_id(buf, b, ln)
            elif t == "iloc":
                state["iloc"] = (b, ln)

        _walk(buf, body + 4, body + size, inner)
        if state["id"] is not None and state["iloc"]:
            found[0] = _item_location(buf, *state["iloc"], state["id"])
        return False

    _walk(buf, 0, buf.n, visit)
    return found[0]


def _is_tiff(buf, at):
    if at < 0 or at + 4 > buf.n:
        return False
    bom = buf.u16(at)
    if bom == 0x4949:
        return buf.u16(at + 2, True) == 42
    if bom == 0x4D4D:
        return buf.u16(at + 2) == 42
    return False


def _find_jpeg_app1(buf):
    at = 2
    while at < buf.n - 4:
        marker = buf.u16(at)
        if marker & 0xFF00 != 0xFF00:
            break
        size = buf.u16(at + 2)
        if marker == 0xFFE1 and buf.string(at + 4, 6).startswith("Exif"):
            return at + 4 + 6
        if marker == 0xFFDA:
            break
        at += 2 + size
    return None


# ---------------------------------------------------------------- 入口
def extract_exif(path: str, want_raw: bool = False, want_thumb: bool = False):
    """回傳 (info dict 或 None, 內嵌縮圖 bytes 或 None)。"""
    with open(path, "rb") as f:
        head = f.read(HEADER_READ_BYTES)
        buf = _Buf(head)
        if buf.n < 4:
            return None, None
        base = 0
        container = "jpeg"
        tiff = None
        if buf.u16(0) == 0xFFD8:
            tiff = _find_jpeg_app1(buf)
        elif _is_tiff(buf, 0):
            tiff, container = 0, "tiff"
        elif _is_heif(buf):
            container = "heif"
            loc = _locate_heif_exif(buf)
            if not loc or not loc[1]:
                return None, None
            f.seek(loc[0])
            buf = _Buf(f.read(min(loc[1], 4 * 1024 * 1024)))
            base = loc[0]
            if buf.n < 12:
                return None, None
            skip = buf.u32(0)
            for cand in ((10 if buf.string(4, 4) == "Exif" else None), 4 + skip, 4, 0):
                if cand is not None and _is_tiff(buf, cand):
                    tiff = cand
                    break
        if tiff is None or tiff + 8 > buf.n or not _is_tiff(buf, tiff):
            return None, None

        little = buf.u16(tiff) == 0x4949
        info, thumb_loc = _parse(buf, tiff, little, want_raw, want_thumb)
        info["container"] = container
        thumb = None
        if thumb_loc:
            f.seek(base + tiff + thumb_loc[0])
            data = f.read(thumb_loc[1])
            if data[:2] == b"\xff\xd8":
                thumb = data
        return info, thumb


def _parse(buf, tiff, little, want_raw, want_thumb):
    ifd0, nxt = _read_ifd(buf, tiff + buf.u32(tiff + 4, little), tiff, little)
    g0 = lambda i: ifd0[i].value if i in ifd0 else None  # noqa: E731
    info = {
        "make": g0(0x010F) or None, "model": g0(0x0110) or None,
        "software": g0(0x0131) or None, "dateTime": g0(0x0132) or None,
        "orientation": g0(0x0112),
    }
    for k in ("exposureTime", "exposureTimeRaw", "fNumber", "exposureProgram", "iso",
              "dateTimeOriginal", "exposureBias", "meteringMode", "flash", "focalLength",
              "focalLengthRaw", "focalLength35mm", "colorSpace", "whiteBalance",
              "whiteBalanceRaw", "sceneCaptureType", "lensModel", "creativeStyle"):
        info[k] = None
    info["width"] = g0(0x0100)
    info["height"] = g0(0x0101)

    exif = None
    if 0x8769 in ifd0:
        try:
            exif, _ = _read_ifd(buf, tiff + buf.u32(ifd0[0x8769].pos, little), tiff, little)
        except Exception:
            exif = None
    if exif:
        g = lambda i: exif[i].value if i in exif else None  # noqa: E731
        et = g(0x829A)
        info["exposureTime"] = fmt_shutter(et) if isinstance(et, (int, float)) else None
        info["exposureTimeRaw"] = et if isinstance(et, (int, float)) and et > 0 else None
        fn = g(0x829D)
        info["fNumber"] = f"f/{_round(fn, 1)}" if isinstance(fn, (int, float)) and fn else None
        info["exposureProgram"] = EXPOSURE_PROGRAM.get(g(0x8822))
        iso = g(0x8827)
        info["iso"] = iso[0] if isinstance(iso, list) else iso
        info["dateTimeOriginal"] = g(0x9003) or None
        eb = g(0x9204)
        if isinstance(eb, (int, float)):
            info["exposureBias"] = ("+" if eb >= 0 else "") + f"{_round(eb, 2)} EV"
        info["meteringMode"] = METERING_MODE.get(g(0x9207))
        fl = g(0x9209)
        if isinstance(fl, int):
            info["flash"] = ("已擊發" if fl & 1 else "未擊發") + f" (0x{fl:x})"
        foc = g(0x920A)
        if isinstance(foc, (int, float)) and foc:
            info["focalLength"] = f"{_round(foc, 1)} mm"
            info["focalLengthRaw"] = foc
        f35 = g(0xA405)
        info["focalLength35mm"] = f"{f35} mm" if f35 else None
        info["colorSpace"] = COLOR_SPACE.get(g(0xA001))
        wb = g(0xA403)
        info["whiteBalance"] = WHITE_BALANCE_STD.get(wb)
        info["whiteBalanceRaw"] = wb if isinstance(wb, int) else None
        info["sceneCaptureType"] = SCENE_CAPTURE.get(g(0xA406))
        info["lensModel"] = g(0xA434) or None
        w, h = g(0xA002), g(0xA003)
        if isinstance(w, int) and isinstance(h, int) and w and h:
            info["width"], info["height"] = w, h
        mn = exif.get(0x927C)
        if mn and info["make"] and "SONY" in info["make"].upper():
            at = tiff + buf.u32(mn.pos, little) if mn.count > 4 else mn.pos
            try:
                info["creativeStyle"] = _sony_style(buf, at, tiff, little)
            except Exception:
                info["creativeStyle"] = None

    gps = None
    if 0x8825 in ifd0:
        try:
            gps, _ = _read_ifd(buf, tiff + buf.u32(ifd0[0x8825].pos, little), tiff, little)
        except Exception:
            gps = None
    info["gps"] = _parse_gps(gps)
    if not isinstance(info["iso"], int):
        info["iso"] = None
    if not isinstance(info["width"], int) or not isinstance(info["height"], int):
        info["width"] = info["height"] = None

    if want_raw:
        info["raw"] = {"ifd0": _dump(ifd0, "ifd0"), "exif": _dump(exif, "exif"), "gps": _dump(gps, "gps")}

    thumb_loc = None
    if want_thumb and nxt:
        try:
            ifd1, _ = _read_ifd(buf, tiff + nxt, tiff, little)
            off, ln = ifd1.get(0x0201), ifd1.get(0x0202)
            if off and ln and isinstance(off.value, int) and isinstance(ln.value, int) and ln.value > 0:
                thumb_loc = (off.value, ln.value)
        except Exception:
            thumb_loc = None
    return info, thumb_loc
