"""节日日期：立冬、圣诞节、元旦和农历新年。"""

from __future__ import annotations

from datetime import date, timedelta

# 2000-2100 年的立冬与农历正月初一日期（MMDD）。
_LIDONG_MMDD = (
    '1107', '1107', '1107', '1108', '1107', '1107', '1107', '1108', '1107', '1107',
    '1107', '1108', '1107', '1107', '1107', '1108', '1107', '1107', '1107', '1108',
    '1107', '1107', '1107', '1108', '1107', '1107', '1107', '1107', '1107', '1107',
    '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107',
    '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107',
    '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107', '1107',
    '1106', '1107', '1107', '1107', '1106', '1107', '1107', '1107', '1106', '1107',
    '1107', '1107', '1106', '1107', '1107', '1107', '1106', '1107', '1107', '1107',
    '1106', '1107', '1107', '1107', '1106', '1107', '1107', '1107', '1106', '1107',
    '1107', '1107', '1106', '1106', '1107', '1107', '1106', '1106', '1107', '1107',
    '1107',
)
_CNY_MMDD = (
    '0205', '0124', '0212', '0201', '0122', '0209', '0129', '0218', '0207', '0126',
    '0214', '0203', '0123', '0210', '0131', '0219', '0208', '0128', '0216', '0205',
    '0125', '0212', '0201', '0122', '0210', '0129', '0217', '0206', '0126', '0213',
    '0203', '0123', '0211', '0131', '0219', '0208', '0128', '0215', '0204', '0124',
    '0212', '0201', '0122', '0210', '0130', '0217', '0206', '0126', '0214', '0202',
    '0123', '0211', '0201', '0219', '0208', '0128', '0215', '0204', '0124', '0212',
    '0202', '0121', '0209', '0129', '0217', '0205', '0126', '0214', '0203', '0123',
    '0211', '0131', '0219', '0207', '0127', '0215', '0205', '0124', '0212', '0202',
    '0122', '0209', '0129', '0217', '0206', '0126', '0214', '0203', '0124', '0210',
    '0130', '0218', '0207', '0127', '0215', '0205', '0125', '0212', '0201', '0121',
    '0209',
)

def _date_from_mmdd(year: int, value: str) -> date:
    return date(int(year), int(value[:2]), int(value[2:]))


def lidong_date(year: int | None = None) -> date:
    """返回指定年份的立冬日期。"""

    if year is None:
        year = date.today().year
    year = int(year)
    if 2000 <= year <= 2100:
        return _date_from_mmdd(year, _LIDONG_MMDD[year - 2000])
    return date(year, 11, 7)


def is_lidong(day: date | None = None) -> bool:
    day = day or date.today()
    return day == lidong_date(day.year)


def is_christmas(day: date | None = None) -> bool:
    """圣诞节前后三天（12 月 24 日至 26 日）。"""

    day = day or date.today()
    return day.month == 12 and day.day in (24, 25, 26)


def lunar_new_year_date(year: int | None = None) -> date:
    """返回指定年份农历正月初一对应的公历日期。"""

    if year is None:
        year = date.today().year
    year = int(year)
    if 2000 <= year <= 2100:
        return _date_from_mmdd(year, _CNY_MMDD[year - 2000])
    return date(year, 2, 17)


def is_new_year_period(day: date | None = None) -> bool:
    """元旦假期：1 月 1 日至 1 月 3 日。"""

    day = day or date.today()
    return day.month == 1 and day.day in (1, 2, 3)


def is_spring_festival_period(day: date | None = None) -> bool:
    """农历新年期间：除夕至正月初五。"""

    day = day or date.today()
    new_year = lunar_new_year_date(day.year)
    return new_year - timedelta(days=1) <= day <= new_year + timedelta(days=5)


def is_snow_period(day: date | None = None) -> bool:
    """立冬当天和圣诞节前后启用雪花效果。"""

    day = day or date.today()
    return is_lidong(day) or is_christmas(day)


def is_fireworks_period(day: date | None = None) -> bool:
    """元旦和农历新年期间启用标题栏烟花。"""

    day = day or date.today()
    return is_new_year_period(day) or is_spring_festival_period(day)


__all__ = [
    'lidong_date', 'is_lidong', 'is_christmas', 'lunar_new_year_date',
    'is_new_year_period', 'is_spring_festival_period',
    'is_snow_period', 'is_fireworks_period',
]
