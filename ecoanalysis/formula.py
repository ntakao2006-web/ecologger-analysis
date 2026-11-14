"""計算チャンネル：式を読んで、チャンネルの値の配列から新しいチャンネルを作る。

式は Python の eval に渡さない。ここで字句に分け、決まった文法だけを木にして、木を自前で計算する
（名前はチャンネルか、下の表の関数・定数だけ。属性・添字・代入・読み込みなどは文法に無い）。
式の長さ・木の大きさ・深さにも上限を置く。

書き方（例：`Speed / 3.6`、`if(Throttle > 95, 1, 0)`、`integ(Speed / 3.6)`、`[Accel X] * 9.80665`）
  数        1  2.5  .5  1e3
  チャンネル  Speed のような名前。記号や空白を含む名前は [名前] と書く（[名前] は必ずチャンネル）
  四則      + - * / %（余り） ^（べき。右から） 単項の - +
  比較      < <= > >= == !=（成り立てば 1、でなければ 0。1つの式に1つずつ）
  論理      and or not（&& || ! でもよい。0 でなければ成り立つ）
  関数・定数  FUNCS・CONSTS の表（名前がチャンネルと同じなら、チャンネルを先に使う）
値の無い所（NaN）はそのまま NaN を返す。0 で割った所なども NaN にする。

時間を使う関数（integ・deriv・smooth）と time は、渡した時刻の配列 t で計算する。
重ね合わせでは周ごとに計算するので、integ は周の頭で 0 から始まる。
"""
import json
import math
import re
import threading
from pathlib import Path

import numpy as np

MAX_LEN = 500
MAX_NODES = 300
MAX_DEPTH = 40
MAX_NAME = 40
G0 = 9.80665


class FormulaError(ValueError):
    def __init__(self, message, pos=None):
        super().__init__(message)
        self.pos = pos


# ---------------------------------------------------------------- 字句
TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<br>\[[^\[\]]+\])
  | (?P<name>[^\W\d][\w.]*)
  | (?P<op><=|>=|==|!=|&&|\|\||[-+*/%^<>!(),])
""", re.VERBOSE)
WORDS = {'and': '&&', 'or': '||', 'not': '!'}


def tokens(text):
    out, i = [], 0
    while i < len(text):
        m = TOKEN.match(text, i)
        if not m:
            raise FormulaError(f'読めない文字「{text[i]}」', i)
        kind, val = m.lastgroup, m.group()
        if kind == 'num':
            out.append(('num', float(val), i))
        elif kind == 'br':
            name = val[1:-1].strip()
            if not name:
                raise FormulaError('[ ] の中が空', i)
            out.append(('ref', name, i))
        elif kind == 'name':
            out.append(('op', WORDS[val], i) if val in WORDS else ('name', val, i))
        elif kind == 'op':
            out.append(('op', val, i))
        i = m.end()
    out.append(('end', None, len(text)))
    return out


# ---------------------------------------------------------------- 関数・定数
def _deriv(t, x):
    if len(x) < 2:
        return np.full_like(x, np.nan)
    with np.errstate(all='ignore'):
        return np.gradient(x, t)


def _integ(t, x):
    if len(x) < 2:
        return np.zeros_like(x)
    mid = (x[1:] + x[:-1]) / 2
    mid = np.where(np.isfinite(mid), mid, 0.0)          # 値の無い区間は足さない
    return np.concatenate([[0.0], np.cumsum(mid * np.diff(t))])


def _smooth(t, x, w):
    """前後 w 秒（合わせて w 秒の幅）の平均。値の無い点は数えない"""
    if not w > 0:
        raise FormulaError('smooth の秒は 0 より大きく')
    ok = np.isfinite(x)
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, x, 0.0))])
    cn = np.concatenate([[0], np.cumsum(ok)])
    lo = np.searchsorted(t, t - w / 2, 'left')
    hi = np.searchsorted(t, t + w / 2, 'right')
    n = cn[hi] - cn[lo]
    with np.errstate(all='ignore'):
        return np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)


def _if(c, a, b):
    return np.where(np.isnan(c), np.nan, np.where(c != 0, a, b))


def _round(x, d=0.0):
    d = np.asarray(d)
    if d.ndim and not np.all(d == d.flat[0]):
        raise FormulaError('round の桁は数で書く')
    return np.round(x, int(d.flat[0]) if d.ndim else int(d))


def _minmax(f):
    def g(*xs):
        out = xs[0]
        for x in xs[1:]:
            out = f(out, x)           # どれかが NaN なら NaN
        return out
    return g


# 名前 → (引数の数の下限, 上限, 関数, 時間を使うか, 数だけの引数の位置, 説明)
FUNCS = {
    'if':    (3, 3, _if, False, (), 'if(条件, 成り立つとき, 成り立たないとき)'),
    'abs':   (1, 1, np.abs, False, (), '絶対値'),
    'sqrt':  (1, 1, np.sqrt, False, (), '平方根'),
    'exp':   (1, 1, np.exp, False, (), 'e の x 乗'),
    'log':   (1, 1, np.log, False, (), '自然対数'),
    'log10': (1, 1, np.log10, False, (), '常用対数'),
    'sin':   (1, 1, np.sin, False, (), '正弦（ラジアン）'),
    'cos':   (1, 1, np.cos, False, (), '余弦（ラジアン）'),
    'tan':   (1, 1, np.tan, False, (), '正接（ラジアン）'),
    'asin':  (1, 1, np.arcsin, False, (), '逆正弦（ラジアンを返す）'),
    'acos':  (1, 1, np.arccos, False, (), '逆余弦（ラジアンを返す）'),
    'atan':  (1, 1, np.arctan, False, (), '逆正接（ラジアンを返す）'),
    'atan2': (2, 2, np.arctan2, False, (), 'atan2(y, x)（ラジアンを返す）'),
    'rad':   (1, 1, np.radians, False, (), '度 → ラジアン'),
    'deg':   (1, 1, np.degrees, False, (), 'ラジアン → 度'),
    'floor': (1, 1, np.floor, False, (), '切り捨て'),
    'ceil':  (1, 1, np.ceil, False, (), '切り上げ'),
    'round': (1, 2, _round, False, (1,), 'round(x, 桁)（桁は省くと 0）'),
    'min':   (2, 8, _minmax(np.minimum), False, (), 'min(a, b, …) 小さい方'),
    'max':   (2, 8, _minmax(np.maximum), False, (), 'max(a, b, …) 大きい方'),
    'clamp': (3, 3, lambda x, lo, hi: np.minimum(np.maximum(x, lo), hi), False, (), 'clamp(x, 下限, 上限)'),
    'integ': (1, 1, _integ, True, (), '時間で積分（x × 秒を足していく。周の頭で 0）'),
    'deriv': (1, 1, _deriv, True, (), '変化率（1秒あたり）'),
    'smooth': (2, 2, _smooth, True, (1,), 'smooth(x, 秒)：前後合わせてその秒の幅で平均'),
}
CONSTS = {'pi': ('円周率', lambda ctx: math.pi), 'g0': ('標準重力 9.80665 m/s²', lambda ctx: G0),
          'time': ('周の頭からの秒', lambda ctx: ctx.t - ctx.t[0] if len(ctx.t) else ctx.t)}


# ---------------------------------------------------------------- 文法
class Parser:
    def __init__(self, text):
        if not isinstance(text, str) or not text.strip():
            raise FormulaError('式が空', 0)
        if len(text) > MAX_LEN:
            raise FormulaError(f'式は{MAX_LEN}文字まで', MAX_LEN)
        self.toks, self.i, self.nodes, self.depth = tokens(text), 0, 0, 0

    def peek(self):
        return self.toks[self.i]

    def take(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def is_op(self, *ops):
        k, v, _ = self.peek()
        return k == 'op' and v in ops

    def expect(self, op, what):
        k, v, pos = self.peek()
        if not (k == 'op' and v == op):
            raise FormulaError(f'{what}が要る', pos)
        self.take()

    def node(self, n, pos):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FormulaError('式が大きすぎる', pos)
        return n

    def parse(self):
        tree = self.expr()
        k, v, pos = self.peek()
        if k != 'end':
            raise FormulaError(f'ここで式が終わるはず（「{v}」）', pos)
        return tree

    def expr(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError('式の入れ子が深すぎる', self.peek()[2])
        try:
            return self.or_()
        finally:
            self.depth -= 1

    def or_(self):
        a = self.and_()
        while self.is_op('||'):
            pos = self.take()[2]
            a = self.node(('bin', '||', a, self.and_(), pos), pos)
        return a

    def and_(self):
        a = self.not_()
        while self.is_op('&&'):
            pos = self.take()[2]
            a = self.node(('bin', '&&', a, self.not_(), pos), pos)
        return a

    def not_(self):
        if self.is_op('!'):
            pos = self.take()[2]
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise FormulaError('式の入れ子が深すぎる', pos)
            try:
                return self.node(('un', '!', self.not_(), pos), pos)
            finally:
                self.depth -= 1
        return self.cmp()

    def cmp(self):
        a = self.add()
        if self.is_op('<', '<=', '>', '>=', '==', '!='):
            op, pos = self.peek()[1], self.take()[2]
            a = self.node(('bin', op, a, self.add(), pos), pos)
            if self.is_op('<', '<=', '>', '>=', '==', '!='):
                raise FormulaError('比較は1つずつ（a < b and b < c のように書く）', self.peek()[2])
        return a

    def add(self):
        a = self.mul()
        while self.is_op('+', '-'):
            op, pos = self.peek()[1], self.take()[2]
            a = self.node(('bin', op, a, self.mul(), pos), pos)
        return a

    def mul(self):
        a = self.unary()
        while self.is_op('*', '/', '%'):
            op, pos = self.peek()[1], self.take()[2]
            a = self.node(('bin', op, a, self.unary(), pos), pos)
        return a

    def unary(self):
        if self.is_op('-', '+'):
            op, pos = self.peek()[1], self.take()[2]
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise FormulaError('式の入れ子が深すぎる', pos)
            try:
                a = self.unary()
            finally:
                self.depth -= 1
            return a if op == '+' else self.node(('un', '-', a, pos), pos)
        return self.power()

    def power(self):
        a = self.atom()
        if self.is_op('^'):
            pos = self.take()[2]
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise FormulaError('式の入れ子が深すぎる', pos)
            try:
                a = self.node(('bin', '^', a, self.unary(), pos), pos)
            finally:
                self.depth -= 1
        return a

    def atom(self):
        k, v, pos = self.take()
        if k == 'num':
            return self.node(('num', v, pos), pos)
        if k == 'ref':
            return self.node(('ref', v, pos), pos)
        if k == 'name':
            if self.is_op('('):
                return self.call(v, pos)
            return self.node(('name', v, pos), pos)
        if k == 'op' and v == '(':
            a = self.expr()
            self.expect(')', '閉じかっこ「)」')
            return a
        if k == 'end':
            raise FormulaError('式が途中で終わっている', pos)
        raise FormulaError(f'ここに「{v}」は置けない', pos)

    def call(self, name, pos):
        f = FUNCS.get(name.lower())
        if f is None:
            raise FormulaError(f'知らない関数「{name}」', pos)
        self.take()                                       # (
        args = []
        if not self.is_op(')'):
            while True:
                args.append(self.expr())
                if not self.is_op(','):
                    break
                self.take()
        self.expect(')', f'{name} の閉じかっこ「)」')
        lo, hi = f[0], f[1]
        if not lo <= len(args) <= hi:
            want = f'{lo}つ' if lo == hi else f'{lo}〜{hi}つ'
            raise FormulaError(f'{name} の引数は{want}（{f[5]}）', pos)
        for k in f[4]:
            if k < len(args) and refs(args[k]) - {'pi', 'g0'}:
                raise FormulaError(f'{name} の{k + 1}つ目の引数は、チャンネルを使わない数で書く', args[k][-1])
        return self.node(('call', name.lower(), args, pos), pos)


def parse(text):
    return Parser(text).parse()


def refs(tree):
    """式の中の名前（[名前] と、関数でない名前）。定数かチャンネルかは計算のときに決める"""
    out = set()

    def walk(n):
        k = n[0]
        if k in ('ref', 'name'):
            out.add(n[1])
        elif k == 'bin':
            walk(n[2]); walk(n[3])
        elif k == 'un':
            walk(n[2])
        elif k == 'call':
            for a in n[2]:
                walk(a)
    walk(tree)
    return out


# ---------------------------------------------------------------- 計算
class Context:
    """t：時刻の配列。lookup(name)：チャンネルの値の配列（無ければ None）"""

    def __init__(self, t, lookup):
        self.t = np.asarray(t, float)
        self.lookup = lookup


def evaluate(tree, ctx):
    n = len(ctx.t)
    with np.errstate(all='ignore'):
        v = _ev(tree, ctx)
    v = np.broadcast_to(np.asarray(v, float), (n,)).copy()
    v[~np.isfinite(v)] = np.nan
    return v


def _ev(n, ctx):
    k = n[0]
    if k == 'num':
        return n[1]
    if k == 'ref' or k == 'name':
        v = ctx.lookup(n[1])
        if v is not None:
            return v
        if k == 'name' and n[1] in CONSTS:
            return CONSTS[n[1]][1](ctx)
        raise FormulaError(f'チャンネル「{n[1]}」が無い', n[2])
    if k == 'un':
        a = np.asarray(_ev(n[2], ctx), float)
        return -a if n[1] == '-' else np.where(np.isnan(a), np.nan, (a == 0).astype(float))
    if k == 'bin':
        op = n[1]
        a, b = np.asarray(_ev(n[2], ctx), float), np.asarray(_ev(n[3], ctx), float)
        if op == '+':
            return a + b
        if op == '-':
            return a - b
        if op == '*':
            return a * b
        if op == '/':
            return a / b
        if op == '%':
            return np.mod(a, b)
        if op == '^':
            return np.power(a, b)
        nan = np.isnan(a) | np.isnan(b)
        if op == '&&':
            r = (a != 0) & (b != 0)
        elif op == '||':
            r = (a != 0) | (b != 0)
        else:
            r = {'<': np.less, '<=': np.less_equal, '>': np.greater, '>=': np.greater_equal,
                 '==': np.equal, '!=': np.not_equal}[op](a, b)
        return np.where(nan, np.nan, np.asarray(r, float))
    if k == 'call':
        f = FUNCS[n[1]]
        args = [np.broadcast_to(np.asarray(_ev(a, ctx), float), ctx.t.shape) if i not in f[4] else np.asarray(_ev(a, ctx), float)
                for i, a in enumerate(n[2])]
        if f[3]:
            if n[1] == 'smooth':
                return _smooth(ctx.t, args[0], float(args[1]))
            return f[2](ctx.t, args[0])
        return f[2](*args)
    raise FormulaError('式の木が壊れている')


# ---------------------------------------------------------------- 残す
class Formulas:
    """計算チャンネルの一覧。置き場：~/.ecologger-analysis/channels.json
    [{"name": ..., "unit": ..., "expr": ...}]（並びは作った順）"""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _read(self):
        try:
            d = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        out = []
        for x in d if isinstance(d, list) else []:
            if isinstance(x, dict) and isinstance(x.get('name'), str) and isinstance(x.get('expr'), str):
                out.append({'name': x['name'], 'unit': x.get('unit') if isinstance(x.get('unit'), str) else '', 'expr': x['expr']})
        return out

    def _write(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    def list(self):
        return self._read()

    def compiled(self):
        """名前 → (木, 単位)。読めない式は飛ばす"""
        out = {}
        for x in self._read():
            try:
                out[x['name']] = (parse(x['expr']), x['unit'])
            except FormulaError:
                pass
        return out

    def save(self, name, unit, expr, old=None, taken=()):
        """old：名前を変えるときの元の名前。taken：元のチャンネルの名前（同じ名前は付けられない）"""
        name = name.strip() if isinstance(name, str) else ''
        if not name or len(name) > MAX_NAME:
            raise FormulaError(f'名前は1〜{MAX_NAME}文字')
        if '[' in name or ']' in name:
            raise FormulaError('名前に [ ] は使えない')
        if name in taken:
            raise FormulaError(f'「{name}」は元のチャンネルと同じ名前')
        if name.lower() in FUNCS:
            raise FormulaError(f'「{name}」は関数と同じ名前')
        unit = unit.strip()[:20] if isinstance(unit, str) else ''
        tree = parse(expr)
        with self.lock:
            items = self._read()
            if any(x['name'] == name for x in items) and name != old:
                raise FormulaError(f'計算チャンネル「{name}」はもうある')
            entry = {'name': name, 'unit': unit, 'expr': expr.strip()}
            if old and old != name:
                users = _used_by(items, old)
                if users:
                    raise FormulaError(f'「{old}」は {"・".join(users)} が使っているので、名前を変えられない')
            idx = next((i for i, x in enumerate(items) if x['name'] == (old or name)), None)
            if idx is None:
                items.append(entry)
            else:
                items[idx] = entry
            graph = {}
            for x in items:
                try:
                    graph[x['name']] = refs(parse(x['expr']))
                except FormulaError:
                    graph[x['name']] = set()
            graph[name] = refs(tree)
            if _cycle(graph, name):
                raise FormulaError('計算チャンネルどうしが、ぐるりと互いを使っている')
            self._write(items)

    def delete(self, name):
        with self.lock:
            items = self._read()
            if not any(x['name'] == name for x in items):
                raise FormulaError(f'計算チャンネル「{name}」が無い')
            users = _used_by(items, name)
            if users:
                raise FormulaError(f'「{name}」は {"・".join(users)} が使っているので、消せない')
            self._write([x for x in items if x['name'] != name])


def _used_by(items, name):
    """name を使っているほかの計算チャンネルの名前"""
    out = []
    for x in items:
        if x['name'] == name:
            continue
        try:
            if name in refs(parse(x['expr'])):
                out.append(x['name'])
        except FormulaError:
            pass
    return out


def _cycle(graph, start):
    seen, stack = set(), [(start, iter(graph.get(start, ())))]
    path = {start}
    while stack:
        node, it = stack[-1]
        nxt = next(it, None)
        if nxt is None:
            stack.pop()
            path.discard(node)
            continue
        if nxt not in graph:
            continue
        if nxt in path:
            return True
        if nxt not in seen:
            seen.add(nxt)
            path.add(nxt)
            stack.append((nxt, iter(graph[nxt])))
    return False


def channel_getter(channels, rows, formulas):
    """チャンネルの値（rows で選んだ行）を引く関数。計算チャンネルは、使われたときに計算して覚える。
    channels：元のチャンネル 名前 → 配列。formulas：名前 → (木, 単位)。返す関数は ctx を受けて lookup を作る"""
    cache, busy = {}, set()

    def make(ctx):
        def lookup(name):
            if name in channels:
                return channels[name][rows]
            if name in formulas:
                if name in cache:
                    return cache[name]
                if name in busy:
                    raise FormulaError(f'計算チャンネル「{name}」が自分を使っている')
                busy.add(name)
                try:
                    cache[name] = evaluate(formulas[name][0], ctx)
                finally:
                    busy.discard(name)
                return cache[name]
            return None
        return lookup
    return make


def compute(names, channels, t, rows, formulas):
    """計算チャンネル names を、rows の行（時刻 t[rows]）で計算する。(値 名前→配列, 計算できない理由 名前→文)"""
    make = channel_getter(channels, rows, formulas)
    ctx = Context(np.asarray(t)[rows], None)
    ctx.lookup = make(ctx)
    vals, why = {}, {}
    for name in names:
        if name not in formulas:
            continue
        try:
            vals[name] = ctx.lookup(name)
        except FormulaError as e:
            why[name] = str(e)
    return vals, why
