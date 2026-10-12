"""อ่านไฟล์ข้อมูล + จัดชนิดคอลัมน์ + ตรวจข้อมูลอ่อนไหว

รองรับ: .xlsx (หลายชีต · หัวตาราง 1–2 ชั้น · แถว Total · เดือนภาษาไทย + ปีจากชื่อชีต) · .csv .txt .tsv (เดาตัวคั่นและรหัสอักขระ)
หลักในการอ่านไฟล์:
- วันที่ใน Excel อาจเป็นเลขวันแบบ Excel, ปี พ.ศ., หรือปี 2 หลักที่ Excel แปลงเป็น 19xx → แปลงเป็น ค.ศ. ให้ถูก
- ชีตปีเดียวกันซ้ำ (ชีตสำเนา) ไม่นับซ้ำ: ตัดชีตที่ชื่อมีคำต่อท้ายหลังปีออกถ้ามีชีตปีเดียวกันอยู่แล้ว
- คอลัมน์ชื่อคน/บริษัท/ทะเบียน/เลขบัญชี ใช้ "นับ" ได้ แต่ไม่ส่งออกไปหน้า dashboard
"""
from __future__ import annotations

import io
import re
import warnings
import zipfile
from dataclasses import dataclass, field

import openpyxl
import pandas as pd

TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
_TH_MONTH_KEY = {m.replace(".", ""): i + 1 for i, m in enumerate(TH_MONTHS)}
_TOTAL_WORDS = re.compile(r"^(total|รวม|ยอดรวม|รวมทั้งสิ้น|grand total|sum|เฉลี่ย|average)\b", re.I)
SENSITIVE_NAME = re.compile(r"customer|ลูกค้า|ผู้ขาย|supplier|ทะเบียน|register|plate|โทร|phone|tel|ที่อยู่|address|"
                            r"บัญชี|account|เลขประจำตัว|citizen|email|อีเมล|พนักงาน|employee", re.I)
WEAK_NAME = re.compile(r"ชื่อ|name|ผู้รับเหมา|contractor", re.I)        # อ่อนไหวเมื่อค่าดูเหมือนชื่อคน/บริษัท หรือมีหลายร้อยค่า
SENSITIVE_VALUE = re.compile(r"(?:^|\s)(?:นาย|นาง|น\.ส\.|นางสาว|mr\.?|mrs\.?|ms\.?)\s*[ก-๙A-Za-z]|บริษัท|บจก|หจก|ห้างหุ้นส่วน|"
                             r"\d{3}-\d-\d{5}-\d|\d{3}-\d{6}-\d|^[ก-ฮ]{1,3}\.?\d{0,2}[-\s]?\d{2,4}(?:\s|,|$)", re.I)
ACCOUNT_IN_NAME = re.compile(r"\d{3}-\d{1,6}-\d{1,6}(?:-\d)?")
TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
CODE_WORDS = re.compile(r"code|รหัส|type|ประเภท|ชนิด|grade|เกรด|unit$", re.I)
QTY_WORDS = re.compile(r"น้ำหนัก|weight|net|qty|quantity|ปริมาณ|ตัน|ton|kg|กก|ผลผลิต|volume|จำนวน", re.I)
MONEY_WORDS = re.compile(r"ราคา|price|amount|ยอด|บาท|baht|value|มูลค่า|total|cost|ต้นทุน|รายได้|revenue|เงิน|pay|receipt|recei|balance", re.I)
PRICE_WORDS = re.compile(r"ราคา|price|ต่อหน่วย|unit", re.I)
BALANCE_WORDS = re.compile(r"balance|คงเหลือ|ยอดคงเหลือ|เงินสด|cash|stock|สต๊อก", re.I)
DATE_WORDS = re.compile(r"date|วันที่|^วัน$|day|ym|period", re.I)


# ============================================================ READ
def read_any(name: str, data: bytes) -> tuple[pd.DataFrame, list[str]]:
    notes: list[str] = []
    ext = name.lower().rsplit(".", 1)[-1]
    if not data or not data.strip():
        raise ValueError("ไฟล์ว่าง ไม่มีข้อมูล")
    if ext in ("xlsx", "xlsm"):
        try:
            frames = _read_xlsx(data, notes)
        except (zipfile.BadZipFile, KeyError, OSError) as e:
            raise ValueError("เปิดไฟล์ Excel ไม่ได้ (ไฟล์เสีย ถูกล็อกรหัสผ่าน หรือไม่ใช่ .xlsx จริง) — เปิดใน Excel แล้วบันทึกเป็น .xlsx ใหม่") from e
    elif ext == "xls":
        raise ValueError("ไฟล์ .xls รุ่นเก่า — เปิดใน Excel แล้วบันทึกเป็น .xlsx ก่อน")
    else:
        try:
            frames = [("", _read_text(data, notes))]
        except pd.errors.EmptyDataError as e:
            raise ValueError("ไม่พบตารางข้อมูลในไฟล์ (ต้องเป็นตาราง 1 แถว = 1 รายการ มีหัวคอลัมน์)") from e
    df = _combine(frames, notes)
    if df.empty:
        raise ValueError("ไม่พบตารางข้อมูลในไฟล์ (ต้องเป็นตาราง 1 แถว = 1 รายการ มีหัวคอลัมน์)")
    return df, notes


def run_steps(gen):
    """เดิน generator ที่ yield ความคืบหน้า (สัดส่วน, ข้อความ) จนจบ แล้วคืนผลลัพธ์ (ใช้ตอนไม่ต้องแสดง progress)"""
    while True:
        try:
            next(gen)
        except StopIteration as e:
            return e.value


def read_many(files: list[tuple[str, bytes]]) -> list[tuple[str, pd.DataFrame, list[str]]]:
    return run_steps(read_many_steps(files))


def read_many_steps(files: list[tuple[str, bytes]]):
    """อ่านหลายไฟล์ · ไฟล์ที่หัวคอลัมน์ตรงกัน ≥ 80% (เช่น export จากระบบเดียวกันแยกปี) รวมเป็นชุดเดียวและตัดแถวซ้ำ
    yield (สัดส่วนที่อ่านแล้วตามขนาดไฟล์, ข้อความ) · คืน [(ชื่อชุด, df, notes)] — ไฟล์ต่างโครงสร้างกันเป็นคนละชุด"""
    groups, skipped = [], []
    total = max(sum(len(d) for _, d in files), 1)
    done = 0
    for i, (name, data) in enumerate(files, 1):
        yield done / total, f"อ่านไฟล์ {i}/{len(files)}: {name}"
        done += len(data)
        try:
            df, notes = read_any(name, data)
        except ValueError as e:                     # ไฟล์ที่อ่านไม่ได้ข้ามไป ไม่ให้ทั้งชุดล้ม — แจ้งไว้ในหมายเหตุ
            skipped.append(f"{name}: {e}")
            continue
        cols = set(map(str, df.columns))
        for g in groups:
            if len(cols & g["cols"]) / max(len(cols | g["cols"]), 1) >= .8:
                g["names"].append(name)
                g["dfs"].append(df)
                break
        else:
            groups.append(dict(cols=cols, names=[name], dfs=[df], notes=notes))
    out = []
    for g in groups:
        df, notes = g["dfs"][0], list(g["notes"])
        if len(g["dfs"]) > 1:
            df = pd.concat(g["dfs"], ignore_index=True)
            n0 = len(df)
            df = df.drop_duplicates(ignore_index=True)       # export ที่ช่วงวันที่ทับกันจะมีแถวเดียวกันซ้ำ
            notes = [f"รวม {len(g['names'])} ไฟล์โครงสร้างเดียวกัน · {len(df):,} แถว"
                     + (f" (ตัดแถวซ้ำระหว่างไฟล์ {n0 - len(df):,} แถว)" if n0 > len(df) else "")] + notes[1:]
        names = sorted(g["names"])
        label = names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1} ไฟล์"
        out.append((label, df, notes))
    if not out:
        raise ValueError(" · ".join(skipped) if len(files) > 1 else str(skipped[0]).split(": ", 1)[-1])
    if skipped:
        out[0][2].extend(f"⚠ ข้ามไฟล์ที่อ่านไม่ได้ — {s}" for s in skipped)
    yield 1.0, "อ่านไฟล์ครบแล้ว"
    return out


def rx(s: pd.Series, pattern, how: str = "search") -> pd.Series:
    """regex ด้วย re ของ Python ทุกเวอร์ชัน pandas — pandas 3 ใช้ regex ของ pyarrow ที่ขอบคำ (word boundary)
    ไม่รู้จักอักษรไทย เช่น แถว 'ยอดรวม' ไม่ถูกตัด ทำให้ผลในเครื่องกับในเบราว์เซอร์ต่างกัน"""
    pat = re.compile(pattern) if isinstance(pattern, str) else pattern
    f = getattr(pat, how)
    return s.map(lambda v: isinstance(v, str) and f(v) is not None).astype(bool)


def _read_text(data: bytes, notes) -> pd.DataFrame:
    # อ่านจาก bytes โดยตรง (ไม่ถอดเป็นข้อความทั้งไฟล์ก่อน) — ไฟล์ข้อความหลายร้อย MB จะได้ไม่ใช้หน่วยความจำซ้ำ 2 เท่าในเบราว์เซอร์
    for enc in ("utf-8-sig", "cp874", "utf-16"):
        try:
            head = data[:20000].decode(enc, errors="ignore")          # ตัดกลางตัวอักษรได้ จึงใช้แค่หาตัวคั่น
            delim = max(["|", "\t", ",", ";"], key=lambda d: sum(line.count(d) for line in head.splitlines()[:10]))
            df = pd.read_csv(io.BytesIO(data), sep=delim, dtype=str, keep_default_na=False, na_values=[""], low_memory=False, encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError, pd.errors.ParserError):
            continue
    else:
        raise ValueError("อ่านรหัสอักขระของไฟล์ไม่ได้")
    df = df.loc[:, [not str(c).startswith("Unnamed") or df[c].notna().any() for c in df.columns]]
    notes.append(f"ไฟล์ข้อความ · ตัวคั่น '{'TAB' if delim == chr(9) else delim}' · {len(df):,} แถว")
    return df


def _cell(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _read_xlsx(data: bytes, notes) -> list[tuple[str, pd.DataFrame]]:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [[_cell(v) for v in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(v is not None for v in r)]
        if len(rows) < 3:
            continue
        df = _table_from_rows(rows)
        if df is not None and len(df) >= 2:
            out.append((ws.title.strip(), df))
    return out


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) or (isinstance(v, str) and re.fullmatch(r"-?[\d,]+(\.\d+)?", v or "") is not None)


def _table_from_rows(rows) -> pd.DataFrame | None:
    width = max(len(r) for r in rows)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    # หัวตาราง = แถวในช่วงต้นที่มีข้อความมากที่สุด และแถวถัดไปมีข้อมูล
    best, best_i = -1, None
    for i in range(min(20, len(rows) - 1)):
        n_txt = sum(isinstance(v, str) and not _is_num(v) for v in rows[i])
        if n_txt >= 2 and n_txt > best:
            best, best_i = n_txt, i
    if best_i is None:
        return None
    names = [str(v) if v is not None else "" for v in rows[best_i]]
    start = best_i + 1
    # หัวสองชั้น: แถวถัดไปเป็นข้อความ แต่ข้างล่างเป็นตัวเลข → รวมชื่อ
    if start < len(rows) - 2:
        sub = rows[start]
        below = rows[start + 1:start + 12]
        txt_idx = [j for j, v in enumerate(sub) if isinstance(v, str) and not _is_num(v)]
        if txt_idx and all(not _is_num(sub[j]) for j in txt_idx):
            over_num = sum(sum(_is_num(r[j]) or hasattr(r[j], "year") for r in below) >= len(below) * .6 for j in txt_idx)
            if over_num >= max(1, len(txt_idx) // 2):
                names = [(f"{a} {sub[j]}".strip() if sub[j] and a and a != sub[j] else (a or str(sub[j] or "")))
                         for j, a in enumerate(names)]
                start += 1
    body = [r for r in rows[start:] if any(v is not None for v in r)]
    if not body:
        return None
    seen: dict[str, int] = {}
    cols = []
    for j, n in enumerate(names):
        n = re.sub(r"\s+", " ", n).strip() or f"คอลัมน์_{j + 1}"
        seen[n] = seen.get(n, 0) + 1
        cols.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    df = pd.DataFrame(body, columns=cols)
    df = df.loc[:, [not c.startswith("คอลัมน์_") or df[c].notna().sum() >= max(3, len(df) * .2) for c in df.columns]]
    tot = pd.Series(False, index=df.index)
    for c in df.columns[:3]:
        tot |= rx(df[c].astype(str).str.strip(), _TOTAL_WORDS, "match").values
    df = df[~tot]                                                            # ตัดแถว Total / รวม / ยอดรวม
    df = df.dropna(how="all")
    # ตัดหมายเหตุท้ายตาราง: แถวที่มีค่าแค่ 1–2 ช่อง ในตารางที่กว้าง
    if df.shape[1] >= 5:
        df = df[df.notna().sum(axis=1) >= 2]
    return df.reset_index(drop=True)


def _sheet_year(title: str) -> int | None:
    m = re.search(r"(\d{4}|\d{2})\s*$", title.strip()) or re.search(r"(\d{4}|\d{2})", title)
    if not m:
        return None
    y = int(m.group(1))
    if y < 100:
        y += 2500 if y >= 40 else 2000
    return y - 543 if y > 2400 else y


def _combine(frames, notes) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        title, df = frames[0]
        return _add_month_date(df, title, notes)
    # ชีตสำเนา: ชื่อมีคำต่อท้ายหลังปี และมีชีตปีเดียวกันที่ชื่อสั้นกว่าอยู่แล้ว
    keep = []
    for t, df in frames:
        y = _sheet_year(t)
        clean = re.search(r"(\d{2,4})\s*$", t) is not None
        dup = (not clean) and y is not None and any(_sheet_year(t2) == y and re.search(r"(\d{2,4})\s*$", t2) for t2, _ in frames if t2 != t)
        if dup:
            notes.append(f"ข้ามชีต '{t}' (น่าจะเป็นสำเนาของปีเดียวกัน)")
        else:
            keep.append((t, df))
    base = max(keep, key=lambda x: len(x[1]))[1]
    named = lambda d: {c for c in d.columns if not c.startswith("คอลัมน์_")}
    parts, skipped = [], []
    for t, df in keep:
        share = len(named(df) & named(base)) / max(1, min(len(named(df)), len(named(base))))
        if share >= .5:
            parts.append(_add_month_date(df, t, notes, quiet=True).assign(_sheet=t))
        else:
            skipped.append(t)
    if skipped:
        notes.append(f"ไม่รวม {len(skipped)} ชีตที่หัวตารางต่างจากชีตหลัก: {', '.join(skipped[:6])}")
    out = pd.concat(parts, ignore_index=True, sort=False)
    notes.append(f"รวม {len(parts)} ชีต · {len(out):,} แถว")
    return out


def _add_month_date(df: pd.DataFrame, title: str, notes, quiet=False) -> pd.DataFrame:
    """คอลัมน์ที่เป็นชื่อเดือนไทย + ปีจากชื่อชีต → สร้างคอลัมน์วันที่ (วันที่ 1 ของเดือน)"""
    y = _sheet_year(title)
    if y is None:
        return df
    for c in df.columns[:3]:
        s = df[c].astype(str).str.replace(".", "", regex=False).str.strip()
        m = s.map(_TH_MONTH_KEY)
        if m.notna().mean() >= .6:
            df = df.copy()
            df.insert(0, "วันที่", [pd.Timestamp(y, int(v), 1) if pd.notna(v) else pd.NaT for v in m])
            if not quiet:
                notes.append(f"สร้างคอลัมน์วันที่จากชื่อเดือนในคอลัมน์ '{c}' + ปี {y + 543} จากชื่อชีต")
            return df[df["วันที่"].notna()]
    return df


# ============================================================ PROFILE
@dataclass
class Col:
    name: str
    kind: str                 # date | number | category | text | id
    nonnull: float
    nunique: int
    sensitive: bool = False
    hints: set = field(default_factory=set)   # qty | money | price | balance


@dataclass
class Profile:
    df: pd.DataFrame
    cols: dict
    notes: list
    n: int
    name: str = ""            # ชื่อชุดข้อมูล (ชื่อไฟล์) เมื่อแนบหลายไฟล์

    def of(self, kind):
        return [c for c in self.cols.values() if c.kind == kind]

    @property
    def date_col(self):
        ds = [c for c in self.of("date") if c.nonnull > .5]
        if not ds:
            return None
        def key(c):
            d = self.df[c.name]
            return (bool(DATE_WORDS.search(c.name)), d.dt.normalize().nunique(), c.nonnull)
        return max(ds, key=key).name

    def numbers(self, hint=None):
        cs = [c for c in self.of("number") if not c.sensitive]
        return [c.name for c in cs if (hint is None or hint in c.hints)]

    def flows(self, hint=None):
        """ตัวเลขที่บวกกันได้ (ไม่ใช่ % และไม่ใช่ยอดคงเหลือ/ยอดสะสม)"""
        return [c for c in self.numbers(hint) if not (self.cols[c].hints & {"pct", "level"})]


def _to_date(s: pd.Series, name: str) -> pd.Series | None:
    if pd.api.types.is_datetime64_any_dtype(s):
        d = s
    else:
        raw = s.dropna()
        if raw.empty:
            return None
        if rx(raw.astype(str).str.strip(), TIME_ONLY, "match").mean() > .5 or raw.map(lambda v: hasattr(v, "hour") and not hasattr(v, "year")).mean() > .5:
            return None                                                      # คอลัมน์เวลาอย่างเดียว ไม่ใช่วันที่
        if raw.map(lambda v: hasattr(v, "year")).mean() > .8:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                d = pd.to_datetime(s, errors="coerce")
        else:
            num = pd.to_numeric(raw.astype(str).str.replace(",", ""), errors="coerce")
            if num.notna().mean() > .9:
                if DATE_WORDS.search(name) and num.between(20000, 80000).mean() > .9:      # เลขวันแบบ Excel
                    d = pd.Timestamp("1899-12-30") + pd.to_timedelta(pd.to_numeric(s, errors="coerce"), unit="D")
                else:
                    return None
            else:
                if raw.astype(str).str.len().median() < 6:
                    return None
                # แบบไทย 15/03/2024 = วันก่อนเดือน · แบบ ISO 2024-03-15 = ปีก่อน (ถ้าใช้ dayfirst กับ ISO วัน/เดือนจะสลับ)
                iso = rx(raw.astype(str).str.strip(), r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", "match").mean() > .8
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    d = pd.to_datetime(s.astype(str), errors="coerce", dayfirst=not iso, yearfirst=iso, format="mixed")
    if d.notna().sum() < max(3, s.notna().sum() * .8):
        return None
    if d.dt.normalize().nunique() <= 1 and d.notna().sum() > 10:
        return None                                                          # ค่าเวลาที่ถูกแปลงเป็นวันเดียวกันทั้งหมด
    yrs = d.dt.year
    fix = d.copy()
    be = yrs > 2400
    if be.any():
        fix[be] = d[be] - pd.DateOffset(years=543)
    old = yrs.between(1950, 1999) & (yrs.max() > 2400 or (yrs > 2015).any())      # ปี 2 หลักที่ Excel แปลงเป็น 19xx (เช่น 1967 = พ.ศ. 2567)
    if old.any():
        fix[old] = d[old] + pd.DateOffset(years=56)
    fut = yrs.between(2040, 2099)                      # ปี 2 หลักแบบไทย '1/12/67' ถูกอ่านเป็น 2067 → พ.ศ. 2567 = ค.ศ. 2024
    if fut.any():
        fix[fut] = d[fut] - pd.DateOffset(years=43)
    return fix


def profile(df: pd.DataFrame, notes: list) -> Profile:
    return run_steps(profile_steps(df, notes))


def profile_steps(df: pd.DataFrame, notes: list):
    """จัดชนิดข้อมูลทีละคอลัมน์ · yield (สัดส่วนคอลัมน์ที่ทำแล้ว, ข้อความ) · คืน Profile"""
    df = df.copy()
    # ชื่อคอลัมน์ที่มีเลขบัญชี → ปิดเลข (เหลือ 2 หลักท้าย) ตั้งแต่ต้น เพื่อไม่ให้หลุดไปถึงหน้า dashboard
    ren, k = {}, 0
    for c in df.columns:
        if ACCOUNT_IN_NAME.search(str(c)):
            dig = re.sub(r"\D", "", ACCOUNT_IN_NAME.search(str(c)).group(0))
            ren[c] = f"บัญชี {chr(65 + k)} (ลงท้าย {dig[-2:]})"
            k += 1
    if ren:
        df = df.rename(columns=ren)
        notes.append(f"ปิดเลขบัญชีในชื่อคอลัมน์ {len(ren)} คอลัมน์")
    cols = {}
    n = len(df)
    names = list(df.columns)
    for i, c in enumerate(names):
        yield .9 * i / max(len(names), 1), f"คอลัมน์ {i + 1}/{len(names)}"
        if c == "_sheet":
            cols[c] = Col(c, "category", 1.0, df[c].nunique())
            continue
        s = df[c]
        nonnull = s.notna().mean()
        if nonnull == 0:
            df = df.drop(columns=c)
            continue
        d = _to_date(s, str(c)) if (DATE_WORDS.search(str(c)) or not pd.api.types.is_numeric_dtype(s)) else None
        if d is not None:
            if .05 < d.notna().mean() < .6:                     # รายงานแบบใบแจ้งหนี้: วันที่อยู่แถวหัว → เติมลงแถวรายการ
                d = d.ffill()
                notes.append(f"เติมวันที่ '{c}' จากแถวหัวรายการลงแถวรายละเอียด")
            df[c] = d
            cols[c] = Col(c, "date", d.notna().mean(), d.nunique())
            continue
        num = pd.to_numeric(s.astype(str).str.replace(",", "").str.strip().replace({"": None, "None": None, "nan": None}), errors="coerce") \
            if not pd.api.types.is_numeric_dtype(s) else s
        if num.notna().sum() >= s.notna().sum() * .95 and num.notna().sum() >= 3:
            df[c] = num.astype(float)
            col = Col(c, "number", num.notna().mean(), num.nunique())
            name = str(c)
            if "%" in name or re.search(r"percent|ร้อยละ|อัตรา|rate", name, re.I):
                col.hints.add("pct")
            elif QTY_WORDS.search(name):
                col.hints.add("qty")
            if MONEY_WORDS.search(name):
                col.hints.add("money")
            if PRICE_WORDS.search(name) and not re.search(r"amount|total|ยอด|มูลค่า|value|รวม", name, re.I):
                col.hints.add("price")                               # ราคาต่อหน่วย (ไม่ใช่ยอดเงิน)
            if BALANCE_WORDS.search(name):
                col.hints.add("balance")
            if re.search(r"^(id|no|ลำดับ|ครั้งที่|เลขที่|code|รหัส)$|weightno|_id$|running", name, re.I) or (num.nunique() > .95 * n and (num.dropna() % 1 == 0).all() and "qty" not in col.hints):
                col.kind = "id"
            if num.nunique() <= 1 and not (col.hints & {"qty", "money"}):   # ปริมาณคงที่ (เช่น ส่งเต็มคันทุกครั้ง) ยังใช้ถ่วงน้ำหนักได้
                col.kind = "id"
            elif col.kind == "number" and num.nunique() <= 25 and (num.dropna() % 1 == 0).all() and CODE_WORDS.search(name):
                col.kind = "category"                                # รหัสสินค้า/ประเภท เป็นกลุ่ม ไม่ใช่ตัวเลขที่บวกกันได้
                df[c] = num.astype("Int64").astype(str).replace("<NA>", None)
            cols[c] = col
            continue
        st = s.astype(str)
        nun = s.nunique()
        sample = st[s.notna()].drop_duplicates().head(2000)
        look = rx(sample, SENSITIVE_VALUE).mean() if len(sample) else 0
        sens = bool(SENSITIVE_NAME.search(str(c))) or look > .05 or (bool(WEAK_NAME.search(str(c))) and nun > 200)
        if nun > .9 * n and n > 50:
            kind = "id"
        elif nun <= max(40, int(.02 * n)):
            kind = "category"
        else:
            kind = "text"
        cols[c] = Col(c, kind, nonnull, nun, sens)
    yield .9, "ตรวจหน่วยข้อมูล"
    # หน่วยต่างกันระหว่างชีต (เช่น ปีหนึ่งบันทึกเป็น กก. ปีอื่นเป็นตัน) → ปรับหาร 1,000 ให้ชีตที่ใหญ่ผิดปกติ
    if "_sheet" in df.columns:
        qcols = [c.name for c in cols.values() if c.kind == "number" and ("qty" in c.hints or "money" in c.hints) and "pct" not in c.hints]
        if qcols:
            med = df.groupby("_sheet")[qcols].median().median(axis=1)
            base = med.median()
            for sh, v in med.items():
                if base > 0 and v > base * 300:
                    m = df["_sheet"] == sh
                    df.loc[m, qcols] = df.loc[m, qcols] / 1000
                    notes.append(f"ชีต '{sh}' ตัวเลขใหญ่กว่าชีตอื่นราว 1,000 เท่า (น่าจะบันทึกเป็น กก.) — หาร 1,000 ให้เป็นหน่วยเดียวกัน")
    # คอลัมน์ "ระดับ/ยอดสะสม" (ยอดคงเหลือ สต๊อก ยอดสะสม) ห้ามนำมาบวกกัน — ใช้กับโจทย์ต่ำกว่าเกณฑ์เท่านั้น
    dates = [c.name for c in cols.values() if c.kind == "date"]
    dcol = max(dates, key=lambda c: df[c].nunique()) if dates else None
    day = df[dcol].dt.normalize() if dcol is not None else None       # คำนวณครั้งเดียว ไม่ทำซ้ำทุกคอลัมน์
    numeric = [c for c in cols.values() if c.kind == "number"]
    for j, c in enumerate(numeric):
        yield .9 + .1 * j / max(len(numeric), 1), f"ตรวจยอดคงเหลือ {j + 1}/{len(numeric)}"
        if re.search(r"accum|สะสม|cumul|คงเหลือ|balance|stock|สต๊อก", c.name, re.I):
            c.hints.add("level")
        elif dcol is not None and not re.search(r"ราคา|price|amount|ยอด|มูลค่า|value|receipt|recei|pay|จ่าย|รับ|ปริมาณ|qty|weight|น้ำหนัก|ผลิต|%", c.name, re.I):
            # ค่าสุดท้ายของแต่ละวันเปลี่ยนน้อยมากเมื่อเทียบกับระดับ (เช่น ยอดเงินรวมทุกบัญชี) = เป็นระดับ ไม่ใช่ยอดไหล
            x = df[c.name].groupby(day).last().dropna()
            if len(x) > 60 and x.abs().median() > 0 and x.diff().abs().median() / x.abs().median() < .05 and (x > 0).mean() > .95:
                c.hints.add("level")
    if any("balance" in c.hints or "level" in c.hints for c in cols.values()):
        for c in cols.values():                               # "Total/รวม" ในไฟล์ที่มียอดคงเหลือ = ยอดรวมคงเหลือทุกบัญชี
            if c.kind == "number" and re.fullmatch(r"\s*(total|รวม|ยอดรวม)\s*", c.name, re.I):
                c.hints.add("level")
    # คอลัมน์ตัวเลขชื่ออ่อนไหว (เช่น เลขบัญชี/รหัสสมาชิก) ไม่ใช้เป็นค่าวิเคราะห์
    for c in cols.values():
        if c.kind in ("number", "id") and SENSITIVE_NAME.search(c.name) and not (c.hints & {"qty", "money"}):
            c.sensitive = True
    return Profile(df, cols, notes, n)


def sensitive_cols(p: Profile) -> list[str]:
    return [c.name for c in p.cols.values() if c.sensitive]
