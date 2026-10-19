#!/usr/bin/env python3
"""PNG "CgBI" de iOS (los que Xcode aplasta con pngcrush de Apple) -> PNG estándar.

Un CgBI tiene un chunk 'CgBI' antes de IHDR, el IDAT en deflate crudo (sin cabecera zlib), los
píxeles en BGR(A) y el alfa premultiplicado. Ni Pillow ni libpng lo leen ("broken data stream").
Solo stdlib.

Uso: python3 -I cgbi.py <entrada.png> [<salida.png>]   (sin salida: reemplaza el archivo)
     python3 -I cgbi.py --test
"""
import os, struct, sys, tempfile, zlib

SIG = b'\x89PNG\r\n\x1a\n'


def is_cgbi(data):
    return data[:8] == SIG and data[12:16] == b'CgBI'


def _chunks(data):
    off = 8
    while off + 8 <= len(data):
        n, typ = struct.unpack_from('>I4s', data, off)
        yield typ, data[off + 8:off + 8 + n]
        off += 12 + n


def _unfilter(raw, w, h, bpp):
    stride = w * bpp
    out, prev, pos = bytearray(), bytearray(stride), 0
    for _ in range(h):
        ft, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if ft == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ft == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ft == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            elif ft != 0:
                raise ValueError(f'filtro PNG inválido {ft}')
        out += line
        prev = line
    return out


def decode(data):
    """CgBI -> (ancho, alto, bytes RGBA sin premultiplicar)."""
    ihdr, idat = None, b''
    for typ, body in _chunks(data):
        if typ == b'IHDR':
            ihdr = struct.unpack('>IIBBBBB', body)
        elif typ == b'IDAT':
            idat += body
    w, h, depth, ctype, _, _, interlace = ihdr
    if depth != 8 or ctype not in (2, 6) or interlace:
        raise ValueError(f'CgBI no soportado (depth={depth} color={ctype} interlace={interlace})')
    bpp = 4 if ctype == 6 else 3
    px = _unfilter(zlib.decompress(idat, -15), w, h, bpp)
    rgba = bytearray(w * h * 4)
    for i in range(w * h):
        b, g, r = px[i * bpp:i * bpp + 3]
        a = px[i * bpp + 3] if bpp == 4 else 255
        if 0 < a < 255:  # deshacer el premultiplicado
            r, g, b = (min(255, (v * 255 + a // 2) // a) for v in (r, g, b))
        rgba[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return w, h, bytes(rgba)


def encode_png(w, h, rgba):
    def chunk(typ, body):
        return struct.pack('>I', len(body)) + typ + body + struct.pack('>I', zlib.crc32(typ + body))
    rows = b''.join(b'\0' + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return SIG + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + \
        chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


def to_standard(data):
    """Bytes de PNG estándar; si ya lo era, los mismos bytes."""
    return encode_png(*decode(data)) if is_cgbi(data) else data


def normalize(path):
    """Ruta que cualquier lector de PNG abre: la misma, o un temporal convertido si era CgBI."""
    with open(path, 'rb') as f:
        data = f.read(16)
    if not is_cgbi(data):
        return path
    with open(path, 'rb') as f:
        out = to_standard(f.read())
    fd, tmp = tempfile.mkstemp(suffix='.png')
    with os.fdopen(fd, 'wb') as f:
        f.write(out)
    return tmp


def _self_test():
    # CgBI sintético 3x2: BGRA premultiplicado, filtros 1 (sub) y 4 (paeth), deflate crudo.
    rgba = bytes([255, 0, 255, 255, 10, 20, 30, 128, 0, 0, 0, 0,
                  200, 100, 50, 255, 255, 255, 255, 64, 1, 2, 3, 255])
    pm = bytearray()
    for i in range(6):
        r, g, b, a = rgba[i * 4:i * 4 + 4]
        pm += bytes((b * a // 255, g * a // 255, r * a // 255, a))
    raw = bytearray()
    for y, ft in ((0, 1), (1, 4)):
        line, prev = pm[y * 12:(y + 1) * 12], pm[(y - 1) * 12:y * 12] if y else bytes(12)
        enc = bytearray()
        for i in range(12):
            a = line[i - 4] if i >= 4 else 0
            b, c = prev[i], prev[i - 4] if i >= 4 else 0
            if ft == 1:
                p = a
            else:
                q = a + b - c
                p = a if abs(q - a) <= abs(q - b) and abs(q - a) <= abs(q - c) else b if abs(q - b) <= abs(q - c) else c
            enc.append((line[i] - p) & 0xFF)
        raw += bytes([ft]) + enc
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    idat = co.compress(bytes(raw)) + co.flush()
    def chunk(typ, body):
        return struct.pack('>I', len(body)) + typ + body + b'\0\0\0\0'
    data = SIG + chunk(b'CgBI', b'\0' * 4) + chunk(b'IHDR', struct.pack('>IIBBBBB', 3, 2, 8, 6, 0, 0, 0)) + \
        chunk(b'IDAT', idat) + chunk(b'IEND', b'')
    assert is_cgbi(data)
    w, h, out = decode(data)
    assert (w, h) == (3, 2)
    for i in range(6):
        exp, got = rgba[i * 4:i * 4 + 4], out[i * 4:i * 4 + 4]
        if exp[3] == 0:
            assert got[3] == 0, (i, got)
        else:  # el premultiplicado pierde precisión con alfa bajo
            assert all(abs(e - g) <= 255 // exp[3] + 1 for e, g in zip(exp, got)), (i, exp, got)
    std = to_standard(data)
    assert std[:8] == SIG and not is_cgbi(std) and to_standard(std) is std
    print('cgbi: ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--test']:
        _self_test()
    else:
        src = sys.argv[1]
        dst = sys.argv[2] if len(sys.argv) > 2 else src
        with open(src, 'rb') as f:
            out = to_standard(f.read())
        with open(dst, 'wb') as f:
            f.write(out)
