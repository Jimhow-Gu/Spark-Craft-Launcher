'''节日日期：立冬、圣诞节、元旦和农历新年。'''

from __future__ import annotations

from datetime import date, datetime, timedelta


def lidong_date(year: int | None = None) -> date:
    '''返回指定年份的立冬日期（按亚洲/上海时区计算）。'''

    if year is None:
        year = date.today().year
    try:
        from lunarcalendar.solarterm import LiDong
        value = LiDong.date_func(int(year))
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
    except Exception:
        pass
    # 兜底：立冬通常落在 11 月 7 日或 8 日。
    return date(int(year), 11, 7)


def is_lidong(day: date | None = None) -> bool:
    day = day or date.today()
    return day == lidong_date(day.year)


def is_christmas(day: date | None = None) -> bool:
    '''圣诞节前后三天（12 月 24 日至 26 日）。'''

    day = day or date.today()
    return day.month == 12 and day.day in (24, 25, 26)


def lunar_new_year_date(year: int | None = None) -> date:
    '''返回指定年份农历正月初一对应的公历日期。'''

    if year is None:
        year = date.today().year
    try:
        from lunarcalendar import Lunar
        value = Lunar(int(year), 1, 1).to_date()
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
    except Exception:
        pass
    # 兜底仅用于缺少农历库的环境；正常安装依赖时不会走到这里。
    return date(int(year), 2, 17)


def is_new_year_period(day: date | None = None) -> bool:
    '''元旦假期：1 月 1 日至 1 月 3 日。'''

    day = day or date.today()
    return day.month == 1 and day.day in (1, 2, 3)


def is_spring_festival_period(day: date | None = None) -> bool:
    '''农历新年期间：除夕至正月初五。'''

    day = day or date.today()
    new_year = lunar_new_year_date(day.year)
    return new_year - timedelta(days=1) <= day <= new_year + timedelta(days=5)


def is_snow_period(day: date | None = None) -> bool:
    '''立冬当天和圣诞节前后启用雪花效果。'''

    day = day or date.today()
    return is_lidong(day) or is_christmas(day)


def is_fireworks_period(day: date | None = None) -> bool:
    '''元旦和农历新年期间启用标题栏烟花。'''

    day = day or date.today()
    return is_new_year_period(day) or is_spring_festival_period(day)


__all__ = [
    'lidong_date', 'is_lidong', 'is_christmas', 'lunar_new_year_date',
    'is_new_year_period', 'is_spring_festival_period',
    'is_snow_period', 'is_fireworks_period',
]
