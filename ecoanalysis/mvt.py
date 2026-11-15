"""ベクトルタイル（Mapbox Vector Tile 2.1、protobuf）を読む。外の部品を使わず自前で読む。

コース図（outline.py）が、国土地理院の「地理院地図Vector」のタイルから道路の縁を取り出すのに使う。
読むのは層（layer）・地物（feature）の種類・属性・形だけ。座標はタイルの中の数（0〜extent）のまま返す。
"""
import struct


class TileError(ValueError):
    pass


def _varint(b, i):
    r = s = 0
    while True:
        if i >= len(b):
            raise TileError('途中で切れている')
        c = b[i]
        i += 1
        r |= (c & 0x7f) << s
        s += 7
        if c < 0x80:
            return r, i
        if s > 63:
            raise TileError('数が長すぎる')


def _fields(b):
    """(番号, 形, 値) を順に。形 0 の値は int、2 は bytes"""
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 2:
            n, i = _varint(b, i)
            if i + n > len(b):
                raise TileError('途中で切れている')
            v, i = b[i:i + n], i + n
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        else:
            raise TileError(f'知らない形 {w}')
        yield f, w, v


def _packed(b):
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i)
        out.append(v)
    return out


def _zigzag(n):
    return (n >> 1) ^ -(n & 1)


def _value(b):
    for f, _w, v in _fields(b):
        if f == 1:
            return v.decode('utf-8', 'replace')
        if f == 2:
            return struct.unpack('<f', v)[0]
        if f == 3:
            return struct.unpack('<d', v)[0]
        if f in (4, 5):
            return v
        if f == 6:
            return _zigzag(v)
        if f == 7:
            return bool(v)
    return None


def _geometry(cmds):
    """線・面の形を、点の列のリストにする（面は閉じる）。点の地物も1点の列として返す"""
    parts, cur, x, y, i = [], None, 0, 0, 0
    while i < len(cmds):
        c = cmds[i]
        i += 1
        cid, cnt = c & 7, c >> 3
        if cid in (1, 2):
            if i + 2 * cnt > len(cmds):
                raise TileError('形が途中で切れている')
            for _ in range(cnt):
                x += _zigzag(cmds[i])
                y += _zigzag(cmds[i + 1])
                i += 2
                if cid == 1:
                    cur = [(x, y)]
                    parts.append(cur)
                elif cur is not None:
                    cur.append((x, y))
        elif cid == 7:
            if cur:
                cur.append(cur[0])
        else:
            raise TileError(f'知らない描き方 {cid}')
    return parts


def read(b):
    """{層の名前: (extent, [(形の種類 1点 2線 3面, 属性 dict, [点の列])])}"""
    layers = {}
    for f, _w, v in _fields(b):
        if f != 3:
            continue
        name, keys, vals, feats, extent = None, [], [], [], 4096
        for f2, _w2, v2 in _fields(v):
            if f2 == 1:
                name = v2.decode('utf-8', 'replace')
            elif f2 == 2:
                feats.append(v2)
            elif f2 == 3:
                keys.append(v2.decode('utf-8', 'replace'))
            elif f2 == 4:
                vals.append(_value(v2))
            elif f2 == 5:
                extent = v2
        out = []
        for fb in feats:
            tags, gtype, geom = [], 0, []
            for f3, _w3, v3 in _fields(fb):
                if f3 == 2:
                    tags = _packed(v3)
                elif f3 == 3:
                    gtype = v3
                elif f3 == 4:
                    geom = _packed(v3)
            props = {}
            for k in range(0, len(tags) - 1, 2):
                if tags[k] < len(keys) and tags[k + 1] < len(vals):
                    props[keys[tags[k]]] = vals[tags[k + 1]]
            out.append((gtype, props, _geometry(geom)))
        if name is not None:
            layers[name] = (extent or 4096, out)
    return layers
