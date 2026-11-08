#!/usr/bin/env python
"""폴더 아래 문서를 읽기 쉬운 md로 변환하고 원본에 [md변환전] 접두어를 붙이는 유틸.

변환: .md .txt .docx .pdf(텍스트 PDF) .hwp(HWP5) .html → 같은 이름의 .md
      (.html 은 클릭·스크립트로 나중에 표시되는 동적 콘텐츠도 <script> 데이터에서 구조화해 함께 추출. tools/html_to_md.py)
수동: 스캔 PDF, .png/.jpg/.jpeg                          → 목록으로만 출력(Claude가 눈으로 읽어 md 작성 후 --finalize)

사용:
  python -I tools/to_md.py <폴더>                 미리보기(아무것도 바꾸지 않음)
  python -I tools/to_md.py <폴더> --apply         변환 + 원본 이름 앞에 [md변환전] 붙이기
  python -I tools/to_md.py <폴더> --finalize      같은 이름 .md 가 생긴 남은 원본(이미지·스캔 PDF)에도 [md변환전] 붙이기
옵션:
  --skip 문자열   경로에 이 문자열이 들어간 곳은 건드리지 않음(여러 번 지정 가능, 기본 "[기본폴더구조]")
  --prefix 문자열 원본에 붙일 접두어(기본 "[md변환전]")

규칙: 원본은 삭제하지 않는다. 같은 이름(stem)의 원본이 여러 형식이면 md/txt 가 stem.md,
나머지는 stem_<확장자>.md. 이미 접두어가 붙은 파일은 건너뛰므로 다시 실행해도 안전하다.
필요: Python 3.8+ (표준 라이브러리만). 텍스트 PDF 는 pdftotext(poppler)가 PATH 에 있어야 한다.
"""
import collections
import os
import re
import struct
import subprocess
import sys
import zlib
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # -I 모드에서도 형제 모듈을 찾도록
from html_to_md import html_to_lines  # noqa: E402

CONVERT = {".md", ".txt", ".docx", ".pdf", ".hwp", ".html", ".htm"}
MANUAL_IMG = {".png", ".jpg", ".jpeg"}


# ───────────────────────── 공통 정규화 ─────────────────────────
def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


UNESC = re.compile(r"\\([~#\[\]])")
DECOR = re.compile(r"^\s*([=\-_~*]{4,})\s*$")
LISTISH = re.compile(r"^(\s*)([-*+]\s|\d+[.)]\s|#{1,6}\s|>|\||```)")


def tidy(lines):
    out, blank, in_code = [], 0, False
    for l in lines:
        l = l.rstrip()
        if l.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code:
            l = UNESC.sub(r"\1", l)
        if l == "":
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(l)
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return out


def indent_width(s):
    s = s.expandtabs(4)
    return len(s) - len(s.lstrip(" "))


def outline_to_md(lines):
    """제목·불릿이 거의 없는 들여쓰기 개요 텍스트 → 중첩 불릿 목록."""
    levels = sorted({indent_width(l) for l in lines if l.strip() and not DECOR.match(l)})
    lvl = {w: i for i, w in enumerate(levels)}
    out, first = [], True
    for l in lines:
        if DECOR.match(l) or not l.strip():
            out.append("")
            continue
        w, body = indent_width(l), l.strip()
        if LISTISH.match(l):
            out.append("  " * lvl[w] + body)
            continue
        if first and w == 0 and all(x == "" for x in out):
            out.append("# " + (body.lstrip("★■▶▼◆● ").strip() or body))
            first = False
            continue
        first = False
        out.append("  " * lvl[w] + "- " + body)
    return tidy(out)


def normalize_text(text, ext):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").split("\n")
    n_head = sum(1 for l in lines if re.match(r"^#{1,6}\s", l))
    n_list = sum(1 for l in lines if re.match(r"^\s*([-*+]\s|\d+[.)]\s)", l))
    nonblank = sum(1 for l in lines if l.strip())
    if ext == ".txt" and n_head == 0 and nonblank and n_list / nonblank < 0.3 \
            and any(indent_width(l) > 0 for l in lines if l.strip()):
        return outline_to_md(lines)
    if ext == ".txt":
        lines = ["" if DECOR.match(l) else l for l in lines]
    return tidy(lines)


# ───────────────────────── docx ─────────────────────────
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _run_text(p):
    parts = []
    for r in p.iter(W + "r"):
        t = "".join((x.text or "") for x in r.iter(W + "t"))
        if r.find(W + "tab") is not None:
            t = "\t" + t
        if not t:
            continue
        rpr = r.find(W + "rPr")
        b = rpr.find(W + "b") if rpr is not None else None
        if b is not None and b.get(W + "val") not in ("0", "false") and t.strip():
            t = "**" + t.strip() + "**" + (" " if t.endswith(" ") else "")
        parts.append(t)
    return "".join(parts).replace("****", "")


def docx_to_lines(path):
    z = zipfile.ZipFile(path)
    styles = {}
    if "word/styles.xml" in z.namelist():
        for s in ET.fromstring(z.read("word/styles.xml")).iter(W + "style"):
            n = s.find(W + "name")
            if n is not None:
                styles[s.get(W + "styleId")] = n.get(W + "val", "")
    body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
    out = []

    def para(p):
        ppr = p.find(W + "pPr")
        sid, numpr = "", None
        if ppr is not None:
            ps = ppr.find(W + "pStyle")
            sid = ps.get(W + "val") if ps is not None else ""
            numpr = ppr.find(W + "numPr")
        text = _run_text(p).strip()
        if not text:
            return None
        name = styles.get(sid, sid).lower()
        m = re.match(r"(heading|제목)\s*(\d)", name)
        if name == "title":
            return "# " + text.replace("**", "")
        if m:
            return "#" * min(6, int(m.group(2)) + 1) + " " + text.replace("**", "")
        if numpr is not None or "list" in name or "목록" in name:
            il = numpr.find(W + "ilvl") if numpr is not None else None
            return "  " * (int(il.get(W + "val")) if il is not None else 0) + "- " + text
        return text

    for el in body:
        if el.tag == W + "p":
            s = para(el)
            if s is not None:
                out += [s, ""]
        elif el.tag == W + "tbl":
            rows = []
            for tr in el.iter(W + "tr"):
                rows.append([" ".join(_run_text(p).strip() for p in tc.iter(W + "p")).strip().replace("|", "\\|")
                             for tc in tr.findall(W + "tc")])
            if rows:
                n = max(len(r) for r in rows)
                rows = [r + [""] * (n - len(r)) for r in rows]
                out.append("| " + " | ".join(rows[0]) + " |")
                out.append("|" + "---|" * n)
                out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                out.append("")
    return tidy(out)


# ───────────────────────── pdf (텍스트 PDF) ─────────────────────────
def pdf_to_lines(path):
    try:
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", "-layout", path, "-"], capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("pdftotext(poppler)가 PATH 에 없음")
    if r.returncode != 0:
        raise RuntimeError("pdftotext 실패")
    lines = r.stdout.decode("utf-8", "replace").replace("\f", "\n").split("\n")
    ind = [indent_width(l) for l in lines if l.strip()]
    cut = min(ind) if ind else 0
    lines = [l.expandtabs(4)[cut:] if l.strip() else "" for l in lines]
    lines = tidy(lines)
    if not any(l.strip() for l in lines):
        raise RuntimeError("텍스트 없음(스캔본)")
    return lines


# ───────────────────────── hwp (HWP5, OLE) ─────────────────────────
class _OLE:
    def __init__(self, data):
        self.d = data
        h = data[:512]
        if h[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
            raise RuntimeError("HWP5(OLE) 형식이 아님(구버전 hwp 또는 hwpx)")
        self.ss = 1 << struct.unpack_from("<H", h, 30)[0]
        self.mss = 1 << struct.unpack_from("<H", h, 32)[0]
        _, self.dirstart, _, self.cutoff, self.mf_start, _, difat_start, ndifat = struct.unpack_from("<IIIIIIII", h, 44)
        difat = list(struct.unpack_from("<109I", h, 76))
        sec = difat_start
        while sec not in (0xFFFFFFFE, 0xFFFFFFFF) and ndifat > 0:
            s = self._sector(sec)
            difat += list(struct.unpack_from("<%dI" % (self.ss // 4 - 1), s, 0))
            sec = struct.unpack_from("<I", s, self.ss - 4)[0]
            ndifat -= 1
        self.fat = []
        for s in difat:
            if s not in (0xFFFFFFFF, 0xFFFFFFFE):
                self.fat += list(struct.unpack("<%dI" % (self.ss // 4), self._sector(s)))
        dd = self._chain(self.dirstart)
        self.ent = []
        for i in range(0, len(dd), 128):
            e = dd[i:i + 128]
            nl = struct.unpack_from("<H", e, 64)[0]
            left, right, child = struct.unpack_from("<III", e, 68)
            self.ent.append(dict(name=e[:max(nl - 2, 0)].decode("utf-16le", "replace"), l=left, r=right, c=child,
                                 start=struct.unpack_from("<I", e, 116)[0],
                                 size=struct.unpack_from("<Q", e, 120)[0] & 0xFFFFFFFF))
        self.mini = self._chain(self.ent[0]["start"])[:self.ent[0]["size"]]
        self.minifat = []
        if self.mf_start not in (0xFFFFFFFE, 0xFFFFFFFF):
            mf = self._chain(self.mf_start)
            self.minifat = list(struct.unpack("<%dI" % (len(mf) // 4), mf))

    def _sector(self, n):
        o = 512 + n * self.ss
        return self.d[o:o + self.ss]

    def _chain(self, s):
        out = []
        while s not in (0xFFFFFFFE, 0xFFFFFFFF):
            out.append(self._sector(s))
            s = self.fat[s]
        return b"".join(out)

    def _minichain(self, s):
        out = []
        while s not in (0xFFFFFFFE, 0xFFFFFFFF):
            out.append(self.mini[s * self.mss:(s + 1) * self.mss])
            s = self.minifat[s]
        return b"".join(out)

    def find(self, path):
        cur, hit = self.ent[0]["c"], None
        for name in path:
            hit, stack, seen = None, [cur], set()
            while stack:
                i = stack.pop()
                if i == 0xFFFFFFFF or i in seen:
                    continue
                seen.add(i)
                if self.ent[i]["name"] == name:
                    hit = i
                    break
                stack += [self.ent[i]["l"], self.ent[i]["r"]]
            if hit is None:
                return None
            cur = self.ent[hit]["c"]
        return self.ent[hit]

    def read(self, e):
        if e["size"] < self.cutoff:
            return self._minichain(e["start"])[:e["size"]]
        return self._chain(e["start"])[:e["size"]]


def _records(buf):
    i = 0
    while i + 4 <= len(buf):
        h = struct.unpack_from("<I", buf, i)[0]
        tag, level, size = h & 0x3FF, (h >> 10) & 0x3FF, h >> 20
        i += 4
        if size == 0xFFF:
            size = struct.unpack_from("<I", buf, i)[0]
            i += 4
        yield tag, level, buf[i:i + size]
        i += size


_EXT = set(list(range(1, 9)) + [11, 12] + list(range(14, 24)))


def _para_text(p):
    i, out = 0, []
    while i + 2 <= len(p):
        c = struct.unpack_from("<H", p, i)[0]
        if c in _EXT:
            i += 16
            continue
        i += 2
        if c == 13:
            break
        if c == 9 or c in (24, 30, 31):
            out.append(" ")
        elif c == 10:
            out.append("\n")
        elif c >= 32:
            out.append(chr(c))
    return "".join(out)


def hwp_to_lines(path):
    ole = _OLE(open(path, "rb").read())
    flags = struct.unpack_from("<I", ole.read(ole.find(["FileHeader"])), 36)[0]
    if flags & 2:
        raise RuntimeError("암호 걸린 hwp")
    secs, n = [], 0
    while True:
        e = ole.find(["BodyText", "Section%d" % n])
        if e is None:
            break
        raw = ole.read(e)
        secs.append(zlib.decompress(raw, -15) if flags & 1 else raw)
        n += 1
    blocks = []
    for raw in secs:
        tbl, cell = None, None
        for tag, level, pl in _records(raw):
            if tag == 71 and pl[:4] == b" lbt":
                tbl, cell = dict(level=level, cells=[]), None
                continue
            if tbl is not None and level <= tbl["level"] and tag in (66, 67):
                blocks.append(("t", tbl["cells"]))
                tbl, cell = None, None
            if tag == 72 and tbl is not None and len(pl) >= 16:
                col, row, cs, rs = struct.unpack_from("<HHHH", pl, 8)
                cell = dict(col=col, row=row, cs=cs, rs=rs, text=[])
                tbl["cells"].append(cell)
                continue
            if tag == 67:
                t = _para_text(pl).strip()
                if t:
                    if tbl is not None and cell is not None:
                        cell["text"].append(t)
                    else:
                        blocks.append(("p", t))
        if tbl is not None:
            blocks.append(("t", tbl["cells"]))
    lines = []
    for kind, v in blocks:
        if kind == "p":
            lines += [v, ""]
            continue
        if not v:
            continue
        nr = max(c["row"] + c["rs"] for c in v)
        nc = max(c["col"] + c["cs"] for c in v)
        grid = [[""] * nc for _ in range(nr)]
        for c in v:
            grid[c["row"]][c["col"]] = " ".join(c["text"]).replace("|", "\\|")
        grid = [r for r in grid if any(r)]
        if grid:
            lines.append("| " + " | ".join(grid[0]) + " |")
            lines.append("|" + "---|" * nc)
            lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
            lines.append("")
    lines = tidy(lines)
    if not any(l.strip() for l in lines):
        raise RuntimeError("본문 텍스트 없음")
    return lines


# ───────────────────────── 실행부 ─────────────────────────
def walk(root, skips, prefix):
    for d, ds, fs in os.walk(root):
        ds[:] = [x for x in ds if not any(s in x for s in skips)]
        if any(s in d for s in skips):
            continue
        for f in fs:
            if not f.startswith(prefix):
                yield os.path.join(d, f)


GEN_MARK = "읽기 쉬운 md로 변환한 사본"
GEN_MARK2 = "화면을 직접 읽어 md로 옮긴 사본"


def is_generated(path):
    """변환본 머리말(> 원본: `[md변환전]…` → …사본)이 있으면 True."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            head = fh.read(800)
    except OSError:
        return False
    return "> 원본: `" in head and (GEN_MARK in head or GEN_MARK2 in head or "변환한 사본" in head or "옮긴 사본" in head)


def build_plan(root, skips, prefix):
    groups = collections.defaultdict(list)
    for p in walk(root, skips, prefix):
        stem, e = os.path.splitext(p)
        groups[stem].append((p, e.lower()))
    plan, manual = [], []
    for stem, lst in groups.items():
        conv = [(p, e) for p, e in lst if e in CONVERT]
        for p, e in lst:
            if e in MANUAL_IMG:
                manual.append(p)
        for p, e in conv:
            if e == ".md" and is_generated(p):   # 이 도구(또는 수동 전사)가 만든 변환본은 다시 처리하지 않는다
                continue
            new = stem + ".md" if (len(conv) == 1 or e in (".md", ".txt")) else stem + "_" + e[1:] + ".md"
            plan.append((p, e, new))
    return plan, manual


def convert_one(p, e):
    if e in (".md", ".txt"):
        return normalize_text(read_text(p), e)
    if e == ".docx":
        return docx_to_lines(p)
    if e == ".pdf":
        return pdf_to_lines(p)
    if e in (".html", ".htm"):
        return html_to_lines(p)
    return hwp_to_lines(p)


def main(argv):
    args = [a for a in argv[1:]]
    skips, prefix = [], "[md변환전]"
    i = 0
    rest = []
    while i < len(args):
        if args[i] == "--skip":
            skips.append(args[i + 1]); i += 2
        elif args[i] == "--prefix":
            prefix = args[i + 1]; i += 2
        else:
            rest.append(args[i]); i += 1
    flags = {a for a in rest if a.startswith("--")}
    paths = [a for a in rest if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    root = os.path.abspath(paths[0])
    skips = skips or ["[기본폴더구조]"]
    if "--finalize" in flags:
        n = 0
        for p in walk(root, skips, prefix):
            stem, e = os.path.splitext(p)
            if e.lower() in MANUAL_IMG | {".pdf"} and os.path.exists(stem + ".md"):
                os.rename(p, os.path.join(os.path.dirname(p), prefix + os.path.basename(p)))
                n += 1
        print(f"{prefix} 처리: {n}건")
        return 0
    plan, manual = build_plan(root, skips, prefix)
    apply = "--apply" in flags
    ok, skipped, failed, scanned = 0, 0, 0, []
    for p, e, new in plan:
        title = os.path.splitext(os.path.basename(p))[0]
        try:
            lines = convert_one(p, e)
        except Exception as ex:
            if e == ".pdf" and "스캔" in str(ex):
                scanned.append(p)
            else:
                failed += 1
                print("실패:", os.path.relpath(p, root), "-", ex)
            continue
        bak = os.path.join(os.path.dirname(p), prefix + os.path.basename(p))
        if os.path.exists(bak) or (os.path.exists(new) and os.path.abspath(new) != os.path.abspath(p)):
            skipped += 1
            print("이름 충돌, 건너뜀:", os.path.relpath(p, root))
            continue
        if not apply:
            ok += 1
            continue
        note = [f"> 원본: `{prefix}{os.path.basename(p)}` → 읽기 쉬운 md로 변환한 사본 (내용 요약·삭제 없음).", ""]
        if lines and lines[0].startswith("# "):      # 본문 첫 줄이 제목이면 그 아래에 출처 표시
            out_lines = [lines[0], ""] + note + lines[1:]
        else:
            out_lines = ["# " + title, ""] + note + lines
        os.rename(p, bak)
        with open(new, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out_lines) + "\n")
        ok += 1
    print(f"{'변환' if apply else '변환 가능'} {ok}건 / 충돌 {skipped}건 / 실패 {failed}건")
    todo = scanned + manual
    if todo:
        print(f"\n눈으로 읽어 정리해야 할 파일 {len(todo)}건 (스캔 PDF·이미지):")
        for p in todo:
            print("  ", os.path.relpath(p, root))
        print("→ md 작성 후 `--finalize` 로 원본에 접두어를 붙이세요.")
    if not apply:
        print("\n(미리보기입니다. 실제 변환은 --apply)")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main(sys.argv))
