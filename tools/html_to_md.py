"""HTML → 구조화 md (표준 라이브러리만). to_md.py 가 불러 쓴다.

1) 정적 본문: 제목·문단·목록·표·강조를 md로.
2) 클릭·스크립트로 나중에 화면에 그려지는 내용(동적 콘텐츠)도 함께 추출한다.
   - <script> 안의 데이터 리터럴(const itemData = [ {…}, … ] 같은 배열/객체)을 파싱해, 항목별 제목·가이드·선택지(표)로 구조화.
     필드 이름 → 화면 라벨은 스크립트가 데이터를 DOM 에 꽂는 방식(getElementById(...).innerText = item.expert,
     템플릿 `<p>라벨</p><p>${item.expert}</p>`)과 DOM 위치(바로 앞 형제 요소의 글)에서 자동 유추한다.
   - 값에 따라 달라지는 상태 문구(if/else 조건별 innerHTML·innerText 대입), 차트 라벨도 목록으로 뽑는다.
   JS 를 실행하지 않으므로 외부 서버 데이터(fetch/ajax)로 불러오는 내용은 추출할 수 없다.
"""
import re
from html.parser import HTMLParser

VOID = {"br", "hr", "img", "meta", "link", "input", "source", "area", "base", "col", "embed", "wbr", "param", "track"}
SKIP = {"script", "style", "svg", "canvas", "noscript", "template", "head", "iframe", "video", "audio", "map"}
INLINE = {"span", "strong", "b", "em", "i", "u", "a", "code", "small", "mark", "sup", "sub", "button", "label",
          "abbr", "cite", "time", "font", "s", "del", "ins", "kbd", "q", "br", "img", "input", "select", "option"}


# ───────────────────────── DOM ─────────────────────────
class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs or {}), [], parent


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root
        self.scripts = []
        self.title = ""
        self._in = None

    def handle_starttag(self, tag, attrs):
        n = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n
            if tag in ("script", "title"):
                self._in = n

    def handle_startendtag(self, tag, attrs):
        n = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(n)

    def handle_endtag(self, tag):
        c = self.cur
        while c is not None and c.tag != tag:
            c = c.parent
        if c is not None and c.parent is not None:
            if tag == "script":
                self.scripts.append("".join(x for x in c.children if isinstance(x, str)))
            if tag == "title":
                self.title = " ".join("".join(x for x in c.children if isinstance(x, str)).split())
            self.cur = c.parent
            self._in = None

    def handle_data(self, data):
        self.cur.children.append(data)


def parse_html(text):
    b = _Builder()
    b.feed(text)
    b.close()
    return b


def _ws(s):
    return re.sub(r"\s+", " ", s)


def inline_text(n, raw=False):
    """노드를 한 줄 텍스트(+**강조**, 링크)로."""
    out = []
    for c in n.children:
        if isinstance(c, str):
            out.append(_ws(c))
            continue
        if c.tag in SKIP:
            continue
        if c.tag == "br":
            out.append("\n")
            continue
        inner = inline_text(c)
        if c.tag in ("strong", "b") and inner.strip():
            s = inner.strip()
            out.append(" " if inner[:1] == " " else "")
            out.append("**" + s + "**")
            out.append(" " if inner[-1:] == " " else "")
        elif c.tag == "code" and inner.strip():
            out.append("`" + inner.strip() + "`")
        elif c.tag == "a" and inner.strip() and c.attrs.get("href", "").startswith(("http://", "https://")):
            out.append("[" + inner.strip() + "](" + c.attrs["href"] + ")")
        elif c.tag in INLINE:
            out.append(inner)
        else:
            out.append(" " + inner + " ")
    s = "".join(out)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = s.replace("**** ", "").replace("****", "")
    return s.strip()


def text_of(n):
    return inline_text(n).replace("**", "").replace("\n", " ").strip()


# ───────────────────────── 정적 본문 → md ─────────────────────────
def _para(text, out):
    text = text.strip()
    if text:
        out.extend(("  \n".join(text.split("\n")), ""))


def render_list(n, out, depth=0):
    ordered = n.tag == "ol"
    k = 0
    for li in n.children:
        if isinstance(li, str) or li.tag != "li":
            continue
        k += 1
        mark = f"{k}. " if ordered else "- "
        parts, nested = [], []
        for c in li.children:
            if not isinstance(c, str) and c.tag in ("ul", "ol"):
                nested.append(c)
            else:
                wrap = Node("x")
                wrap.children = [c]
                parts.append(inline_text(wrap) if not isinstance(c, str) else _ws(c))
        text = " ".join(p for p in " ".join(parts).split("\n"))
        text = re.sub(r"\s+", " ", text).strip()
        out.append("  " * depth + mark + text)
        for nl in nested:
            render_list(nl, out, depth + 1)
    if depth == 0:
        out.append("")


def render_table(n, out):
    rows = []
    for tr in _find(n, "tr"):
        cells = [text_of(c).replace("|", "\\|") for c in tr.children if not isinstance(c, str) and c.tag in ("td", "th")]
        if cells:
            rows.append(cells)
    if not rows:
        return
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    out.append("| " + " | ".join(rows[0]) + " |")
    out.append("|" + "---|" * w)
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    out.append("")


def _find(n, tag):
    for c in n.children:
        if isinstance(c, str):
            continue
        if c.tag == tag:
            yield c
        elif c.tag not in SKIP:
            yield from _find(c, tag)


def render_blocks(n, out):
    buf = Node("#buf")

    def flush():
        if buf.children:
            _para(inline_text(buf), out)
            buf.children = []

    for c in n.children:
        if isinstance(c, str):
            buf.children.append(c)
            continue
        t = c.tag
        if t in SKIP:
            continue
        if t in INLINE:
            buf.children.append(c)
        elif re.fullmatch(r"h[1-6]", t):
            flush()
            s = inline_text(c).replace("\n", " ")
            if s:
                out.extend(("#" * min(6, int(t[1]) + 1) + " " + s, ""))
        elif t in ("ul", "ol"):
            flush()
            render_list(c, out)
        elif t == "table":
            flush()
            render_table(c, out)
        elif t == "hr":
            flush()
            out.extend(("---", ""))
        elif t == "pre":
            flush()
            out.extend(("```", "".join(x for x in c.children if isinstance(x, str)).strip("\n"), "```", ""))
        elif t == "p":
            flush()
            _para(inline_text(c), out)
        else:
            flush()
            render_blocks(c, out)
    flush()


# ───────────────────────── JS 리터럴 파서(실행하지 않음) ─────────────────────────
class _Bad(Exception):
    pass


_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_ID = re.compile(r"[A-Za-z_$][\w$]*")
_ESC = {"n": "\n", "t": "\t", "r": "", "b": "", "f": "", "v": "", "0": ""}


def _skip_ws(s, i):
    n = len(s)
    while i < n:
        if s[i] in " \t\r\n,﻿" and False:
            i += 1
        elif s[i] in " \t\r\n﻿":
            i += 1
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j + 1
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            break
    return i


def _string(s, i):
    q = s[i]
    i += 1
    out = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            d = s[i + 1]
            if d == "u" and i + 5 < n + 0 and re.fullmatch(r"[0-9a-fA-F]{4}", s[i + 2:i + 6] or ""):
                out.append(chr(int(s[i + 2:i + 6], 16)))
                i += 6
                continue
            if d == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", s[i + 2:i + 4] or ""):
                out.append(chr(int(s[i + 2:i + 4], 16)))
                i += 4
                continue
            if d == "\n":
                i += 2
                continue
            out.append(_ESC.get(d, d))
            i += 2
            continue
        if q == "`" and c == "$" and s.startswith("${", i):   # 템플릿 식은 그대로 둔다
            depth, j = 0, i + 1
            while j < n:
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                elif s[j] in "'\"`":
                    j = _string(s, j)[1] - 1
                j += 1
            out.append(s[i:j + 1])
            i = j + 1
            continue
        if c == q:
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise _Bad("문자열 끝 없음")


def _skip_expr(s, i):
    """지원하지 않는 값(함수·식 등)을 건너뛴다. 최상위 ',' 또는 닫는 괄호 앞에서 멈춘다."""
    depth, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "'\"`":
            i = _string(s, i)[1]
            continue
        if s.startswith("//", i) or s.startswith("/*", i):
            i = _skip_ws(s, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif c == "," and depth == 0:
            return i
        i += 1
    return i


def _value(s, i):
    i = _skip_ws(s, i)
    if i >= len(s):
        raise _Bad("끝")
    c = s[i]
    if c == "[":
        out, i = [], i + 1
        while True:
            i = _skip_ws(s, i)
            if i < len(s) and s[i] == ",":
                i += 1
                continue
            if i >= len(s):
                raise _Bad("배열 끝 없음")
            if s[i] == "]":
                return out, i + 1
            v, i = _value(s, i)
            out.append(v)
    if c == "{":
        out, i = {}, i + 1
        while True:
            i = _skip_ws(s, i)
            if i < len(s) and s[i] == ",":
                i += 1
                continue
            if i >= len(s):
                raise _Bad("객체 끝 없음")
            if s[i] == "}":
                return out, i + 1
            if s[i] in "'\"`":
                k, i = _string(s, i)
            else:
                m = _ID.match(s, i) or _NUM.match(s, i)
                if not m:
                    raise _Bad("키 해석 실패")
                k, i = m.group(0), m.end()
            i = _skip_ws(s, i)
            if i < len(s) and s[i] == ":":
                v, i = _value(s, i + 1)
            else:                       # 단축 속성 { a, b }
                v = None
            out[k] = v
    if c in "'\"`":
        v, j = _string(s, i)
        k = _skip_ws(s, j)
        if s[k:k + 1] in (",", "]", "}", ""):
            return v, j
        e = _skip_expr(s, j)           # 'a' + 'b' 같은 식
        return v, e
    m = _NUM.match(s, i)
    if m:
        k = _skip_ws(s, m.end())
        if s[k:k + 1] in (",", "]", "}", ""):
            t = m.group(0)
            return (float(t) if re.search(r"[.eE]", t) else int(t)), m.end()
    m = _ID.match(s, i)
    if m and m.group(0) in ("true", "false", "null", "undefined"):
        k = _skip_ws(s, m.end())
        if s[k:k + 1] in (",", "]", "}", ""):
            return {"true": True, "false": False}.get(m.group(0)), m.end()
    j = _skip_expr(s, i)
    if j == i:
        raise _Bad("값 해석 실패")
    return None, j


def extract_literals(script):
    """스크립트에서 const/let/var NAME = [ 또는 { 리터럴을 (이름, 값, 앞쪽 주석) 으로."""
    found = []
    for m in re.finditer(r"(?m)^[ \t]*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?=[\[{])", script):
        try:
            v, end = _value(script, m.end())
        except (_Bad, RecursionError, IndexError):
            continue
        # 바로 앞의 // 주석
        pre = script[:m.start()].rstrip().split("\n")
        cm = pre[-1].strip() if pre and pre[-1].strip().startswith("//") else ""
        found.append((m.group(1), v, cm.lstrip("/ ").strip()))
    return found


def _chars(v):
    if isinstance(v, str):
        return len(v)
    if isinstance(v, list):
        return sum(_chars(x) for x in v)
    if isinstance(v, dict):
        return sum(_chars(x) for x in v.values())
    return 0


# ───────────────────────── 동적 데이터 → md ─────────────────────────
TITLE_KEYS = ("title", "name", "heading", "subject", "label", "question", "text")
STATUS_KO = {"safe": "안전", "warn": "주의", "warning": "주의", "danger": "위험", "ok": "양호", "good": "양호", "bad": "불량"}
KEY_KO = {"text": "내용", "val": "값", "value": "값", "status": "판정", "label": "라벨", "desc": "설명", "description": "설명",
          "title": "제목", "name": "이름", "type": "분류", "id": "ID", "score": "점수",
          "options": "선택지", "choices": "선택지", "items": "항목", "answers": "답", "steps": "단계", "tips": "팁"}


def _text_nodes(n):
    return [text_of(c) for c in n.children if not isinstance(c, str) and c.tag not in SKIP and text_of(c)]


def derive_labels(builder, scripts_text):
    """필드 키 → 화면 라벨. (a) JS 대입 + DOM 앞 형제, (b) 템플릿 라벨, (c) 값 뒤 단위."""
    labels, units, by_id = {}, {}, {}

    def walk(n):
        for c in n.children:
            if isinstance(c, str):
                continue
            if c.attrs.get("id"):
                by_id[c.attrs["id"]] = c
            walk(c)
    walk(builder.root)

    var2id = {m.group(1): m.group(2) for m in re.finditer(
        r"(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", scripts_text)}
    pairs = [(var2id.get(m.group(1)), m.group(2)) for m in re.finditer(
        r"(\w+)\.(?:innerText|textContent|innerHTML)\s*=\s*\w+\.(\w+)\s*;", scripts_text)]
    pairs += [(m.group(1), m.group(2)) for m in re.finditer(
        r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)\.(?:innerText|textContent|innerHTML)\s*=\s*\w+\.(\w+)\s*;", scripts_text)]
    for eid, key in pairs:
        node = by_id.get(eid or "")
        if node is None or key in labels:
            continue
        par = node.parent
        if par is None:
            continue
        sibs = [c for c in par.children if not isinstance(c, str) and c.tag not in SKIP]
        k = sibs.index(node) if node in sibs else -1
        for prev in reversed(sibs[:max(k, 0)]):
            t = text_of(prev)
            if t:
                labels[key] = t
                break
    # 템플릿: <p ...>라벨</p><p ...>${item.key}</p>
    for m in re.finditer(r">\s*([^<>${}\n]{2,60}?)\s*</(?:p|h\d|span|div|label|strong)>\s*<(?:p|div|span)[^>]*>\s*\$\{\w+\.(\w+)\}", scripts_text):
        labels.setdefault(m.group(2), re.sub(r"\s+", " ", m.group(1)).strip())
    for m in re.finditer(r"\$\{\w+\.(\w+)\}([^<$`{}]{1,4})<", scripts_text):
        u = m.group(2).strip()
        if u and not re.search(r"[\w]", u.replace("점", "").replace("원", "").replace("%", "")):
            units.setdefault(m.group(1), u)
        elif u in ("점", "원", "%", "개", "명", "m", "㎡"):
            units.setdefault(m.group(1), u)
    return labels, units, by_id


def _group_heading(val, by_id):
    """분류값(road 등)에 해당하는 영역의 제목(id 가 -road 로 끝나는 요소의 앞 제목)."""
    for eid, node in by_id.items():
        if eid == val or eid.endswith("-" + val) or eid.endswith("_" + val):
            par = node.parent
            if par is None:
                continue
            sibs = [c for c in par.children if not isinstance(c, str)]
            k = sibs.index(node) if node in sibs else 0
            for prev in reversed(sibs[:k]):
                t = text_of(prev)
                if t:
                    return t
    return str(val)


def _scalar(v, unit=""):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "예" if v else "아니오"
    s = str(v).replace("\n", " ").replace("|", "\\|")
    return STATUS_KO.get(s, s) + (unit if isinstance(v, (int, float)) else "")


def _multiline(label, text, out):
    lines = [l.rstrip() for l in str(text).split("\n")]
    if len(lines) == 1:
        out.append(f"- **{label}**: {lines[0]}")
        return
    out.append(f"- **{label}**")
    for l in lines:
        if not l.strip():
            continue
        out.append("  > " + l.strip() if not re.match(r"^\s*[-*•]\s", l) else "  - " + re.sub(r"^\s*[-*•]\s*", "", l))


def render_dict_list(items, labels, units, by_id, out, level=3):
    # 분류 키: 값이 2~8종이고 각 값이 2회 이상 나오는 문자열 필드
    group_key = None
    keys = []
    for d in items:
        for k in d:
            if k not in keys:
                keys.append(k)
    for k in keys:
        vals = [d.get(k) for d in items]
        if all(isinstance(v, str) for v in vals):
            cnt = {}
            for v in vals:
                cnt[v] = cnt.get(v, 0) + 1
            if 2 <= len(cnt) <= 8 and all(c >= 2 for c in cnt.values()) and k not in TITLE_KEYS and k != "id":
                group_key = k
                break
    order = []
    if group_key:
        for d in items:
            if d[group_key] not in order:
                order.append(d[group_key])
    seen = {}
    prev_group = object()
    for d in items:
        g = d.get(group_key) if group_key else None
        if group_key and g != prev_group:
            out.extend(("#" * level + " " + _group_heading(g, by_id), ""))
            prev_group = g
        seen[g] = seen.get(g, 0) + 1
        title = next((str(d[k]) for k in TITLE_KEYS if isinstance(d.get(k), str) and d[k].strip()), "")
        head = f"{seen[g]}. {title}".strip(". ") if title else f"{seen[g]}."
        out.extend(("#" * (level + (1 if group_key else 0)) + " " + head, ""))
        for k, v in d.items():
            if k == group_key or k == "id" or (isinstance(v, str) and v == title and k in TITLE_KEYS):
                continue
            lab = labels.get(k) or KEY_KO.get(k, k)
            if isinstance(v, str):
                _multiline(lab, v, out)
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                out.append(f"- **{lab}**")
                out.append("")
                cols = []
                for x in v:
                    for ck in x:
                        if ck not in cols:
                            cols.append(ck)
                out.append("| " + " | ".join(labels.get(c) or KEY_KO.get(c, c) for c in cols) + " |")
                out.append("|" + "---|" * len(cols))
                for x in v:
                    out.append("| " + " | ".join(_scalar(x.get(c), units.get(c, "")) for c in cols) + " |")
                out.append("")
            elif isinstance(v, list):
                out.append(f"- **{lab}**: " + ", ".join(_scalar(x) for x in v))
            elif v is not None and not isinstance(v, dict):
                out.append(f"- **{lab}**: {_scalar(v, units.get(k, ''))}")
        out.append("")


def render_value(name, v, comment, labels, units, by_id, out):
    out.extend((f"### {comment or name}  (`{name}`)", ""))
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        render_dict_list(v, labels, units, by_id, out, level=4)
    elif isinstance(v, list):
        out.extend(f"- {_scalar(x)}" for x in v)
        out.append("")
    elif isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, str):
                _multiline(labels.get(k) or KEY_KO.get(k, k), x, out)
            elif isinstance(x, list) and x and all(isinstance(y, dict) for y in x):
                out.append(f"- **{k}**")
                render_dict_list(x, labels, units, by_id, out, level=5)
            elif isinstance(x, list):
                out.append(f"- **{k}**: " + ", ".join(_scalar(y) for y in x))
            elif x is not None:
                out.append(f"- **{k}**: {_scalar(x)}")
        out.append("")


# ───────────────────────── 상태별 문구 · 차트 라벨 ─────────────────────────
_ASSIGN = re.compile(r"\.(?:innerHTML|innerText|textContent)\s*=\s*(['\"`])", re.S)


def _html_to_text(s):
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\$\{([^}]*)\}", lambda m: "{" + m.group(1).strip() + "}", s)
    s = "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in s.split("\n"))
    return re.sub(r"\n{2,}", "\n", s).strip()


def dynamic_messages(script):
    """if/else 조건 아래에서 화면에 대입되는 문구."""
    msgs, seen = [], set()
    lines = script.split("\n")
    pos = 0
    starts = []
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    for m in _ASSIGN.finditer(script):
        try:
            text, _ = _string(script, m.end() - 1)
        except _Bad:
            continue
        txt = _html_to_text(text)
        if len(re.findall(r"[가-힣A-Za-z]", txt)) < 2 or txt in seen:
            continue
        li = max(i for i, s in enumerate(starts) if s <= m.start())
        cond = ""
        for j in range(li - 1, max(li - 4, -1), -1):
            cm = re.search(r"(?:\}\s*)?(else\s+if|if)\s*\((.*)\)\s*\{", lines[j])
            if cm:
                cond = cm.group(2).strip()
                break
            if re.search(r"\belse\s*\{", lines[j]):
                cond = "그 외"
                break
        plain = re.sub(r"\{[^}]*\}", "", txt)
        if re.search(r"\{(?:item|opt|d|row|it|x|data)\.", txt) or (not cond and len(re.findall(r"[가-힣]", plain)) < 4):
            continue   # 데이터 렌더링용 템플릿·숫자 갱신은 앞 절에서 이미 구조화했다
        seen.add(txt)
        msgs.append((cond, txt))
    return msgs


def chart_info(script):
    out = []
    for m in re.finditer(r"labels\s*:\s*\[([^\]]*)\]", script):
        try:
            v, _ = _value("[" + m.group(1) + "]", 0)
            out.append("축/라벨: " + ", ".join(str(x) for x in v if isinstance(x, str)))
        except _Bad:
            pass
    for m in re.finditer(r"type\s*:\s*['\"](radar|bar|line|pie|doughnut|scatter)['\"]", script):
        out.append("차트 종류: " + m.group(1))
    return list(dict.fromkeys(out))


# ───────────────────────── 진입점 ─────────────────────────
def html_to_lines(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            text = None
    if text is None:
        text = raw.decode("utf-8", "replace")
    b = parse_html(text)
    out = []
    if b.title:
        out += ["# " + b.title, ""]
    body = None
    for c in b.root.children:
        if not isinstance(c, str) and c.tag == "html":
            for h in c.children:
                if not isinstance(h, str) and h.tag == "body":
                    body = h
    render_blocks(body if body is not None else b.root, out)

    scripts_text = "\n".join(b.scripts)
    labels, units, by_id = derive_labels(b, scripts_text)
    dyn = []
    for sc in b.scripts:
        for name, v, cm in extract_literals(sc):
            if isinstance(v, (list, dict)) and _chars(v) >= 80:
                render_value(name, v, cm, labels, units, by_id, dyn)
    msgs = dynamic_messages(scripts_text)
    charts = chart_info(scripts_text)
    if msgs:
        dyn += ["### 상태에 따라 바뀌는 표시 문구", "", "| 조건 | 표시 문구 |", "|---|---|"]
        dyn += ["| " + (c.replace("|", "\\|") or "-") + " | " + t.replace("\n", " / ").replace("|", "\\|") + " |" for c, t in msgs]
        dyn.append("")
    if charts:
        dyn += ["### 차트", ""] + ["- " + x for x in charts] + [""]
    if dyn:
        out += ["---", "", "## 동적 콘텐츠 (클릭·스크립트로 화면에 표시되는 내용)", "",
                "> 페이지의 `<script>` 데이터에서 추출했다. 버튼·항목을 클릭하면 이 내용이 화면에 나타난다.", ""] + dyn
    return out
