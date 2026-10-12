"""รูปแบบวันที่ที่พบในไฟล์จริง ต้องอ่านเป็นวันเดียวกันทุกแบบ (กันวัน/เดือนสลับ และปี พ.ศ.)

รัน:  python tests/test_dates.py
"""
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.data_io import _to_date  # noqa: E402

EXPECT = ["2024-12-01", "2024-01-02", "2024-03-15"]
CASES = {
    "ISO": ["2024-12-01", "2024-01-02", "2024-03-15"],
    "ISO มีเวลา": ["2024-12-01 08:30:00", "2024-01-02 14:00:00", "2024-03-15 09:00"],
    "ISO ทับ": ["2024/12/01", "2024/01/02", "2024/03/15"],
    "วัน/เดือน/ปี": ["01/12/2024", "02/01/2024", "15/03/2024"],
    "วัน-เดือน-ปี": ["01-12-2024", "02-01-2024", "15-03-2024"],
    "พ.ศ. วัน/เดือน/ปี": ["01/12/2567", "02/01/2567", "15/03/2567"],
    "พ.ศ. ISO": ["2567-12-01", "2567-01-02", "2567-03-15"],
    "พ.ศ. ปี 2 หลัก": ["1/12/67", "2/1/67", "15/3/67"],
}
bad = 0
for name, vals in CASES.items():
    d = _to_date(pd.Series(vals * 5), "วันที่")
    got = None if d is None else [str(x.date()) for x in d[:3]]
    ok = got == EXPECT
    bad += not ok
    print(f"{'✓' if ok else '✗'} {name:18} {got}")
print("ผ่านทั้งหมด" if not bad else f"ไม่ผ่าน {bad} แบบ")
sys.exit(1 if bad else 0)
