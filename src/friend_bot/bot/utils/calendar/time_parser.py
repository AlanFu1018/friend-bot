import re
from datetime import datetime, timedelta
from typing import Tuple, Optional

def parse_calendar_time(time_input: str, base_now: Optional[datetime] = None) -> Tuple[datetime, int, str, str, str]:
    """
    解析用戶輸入的行事曆時間字串。
    
    支援格式：
    1. y/m/d/h/m （例如：2026/8/27/15/30、2026/08/27/15:30、2026-8-27-15-30）
    2. y/m/d h:m （例如：2026/8/27 15:30、2026-08-27 15:30）
    3. m/d/h/m （例如：8/27/15/30、08/27 15:30，自動補上當年）
    4. h:m （例如：15:30、15/30，自動補上今天，若已過則為明天）
    5. 相對時間（例如：10m、30m、2h、1h30m、1d）

    回傳：
    (target_dt, target_timestamp, date_str, time_str, formatted_time_str)
    例如：(dt, 1787815800, "2026-08-27", "15:30", "2026/08/27 15:30")
    """
    raw = time_input.strip()
    if not raw:
        raise ValueError("時間不能為空！格式範例：`2026/8/27/15/30` 或 `15:30`")

    now = base_now or datetime.now()

    # 1. 相對時間模式（例如 10m, 30m, 2h, 1h30m, 1d）
    rel_match = re.fullmatch(r'(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?', raw.lower())
    if rel_match and any(rel_match.groups()):
        days = int(rel_match.group(1) or 0)
        hours = int(rel_match.group(2) or 0)
        minutes = int(rel_match.group(3) or 0)
        if days > 0 or hours > 0 or minutes > 0:
            target_dt = now + timedelta(days=days, hours=hours, minutes=minutes)
            target_dt = target_dt.replace(second=0, microsecond=0)
            target_ts = int(target_dt.timestamp())
            date_str = target_dt.strftime("%Y-%m-%d")
            time_str = target_dt.strftime("%H:%M")
            formatted_str = target_dt.strftime("%Y/%m/%d %H:%M")
            return target_dt, target_ts, date_str, time_str, formatted_str

    # 2. 提取所有連續數字
    clean_str = re.sub(r'[\/\\\-\:\.\s年月日點分时时]+', ' ', raw).strip()
    parts = clean_str.split()
    
    if not all(p.isdigit() for p in parts):
        raise ValueError(
            f"無法識別的時間格式「{raw}」！\n"
            "請使用 `y/m/d/h/m` 格式，例如：`2026/8/27/15/30` 或 `15:30`。"
        )

    nums = [int(p) for p in parts]
    target_dt: Optional[datetime] = None

    if len(nums) == 5:
        year, month, day, hour, minute = nums
        if year < 100:
            year += 2000
        try:
            target_dt = datetime(year, month, day, hour, minute, 0)
        except ValueError as e:
            raise ValueError(f"無效的日期時間（{year}/{month}/{day} {hour}:{minute}）：{e}")

    elif len(nums) == 4:
        month, day, hour, minute = nums
        year = now.year
        try:
            target_dt = datetime(year, month, day, hour, minute, 0)
            if target_dt < now:
                target_dt = datetime(year + 1, month, day, hour, minute, 0)
        except ValueError as e:
            raise ValueError(f"無效的日期時間（{month}/{day} {hour}:{minute}）：{e}")

    elif len(nums) == 2:
        hour, minute = nums
        try:
            target_dt = datetime(now.year, now.month, now.day, hour, minute, 0)
            if target_dt <= now:
                target_dt += timedelta(days=1)
        except ValueError as e:
            raise ValueError(f"無效的時間（{hour}:{minute}）：{e}")

    elif len(nums) == 3:
        raise ValueError(
            f"時間缺少小時與分鐘！\n"
            f"請提供完整的 `y/m/d/h/m`（例如 `{nums[0]}/{nums[1]}/{nums[2]}/12/00`）。"
        )

    else:
        raise ValueError(
            f"無法解析的時間參數「{raw}」！\n"
            "標準格式為：`{time:y/m/d/h/m}`，例如：`2026/8/27/15/30`。"
        )

    if target_dt <= now:
        raise ValueError(
            f"設定的時間 `{target_dt.strftime('%Y/%m/%d %H:%M')}` 已經是過去的時間了！\n"
            "（時間只能往前走，時間機器可不能隨便借你用哦！）"
        )

    target_ts = int(target_dt.timestamp())
    date_str = target_dt.strftime("%Y-%m-%d")
    time_str = target_dt.strftime("%H:%M")
    formatted_str = target_dt.strftime("%Y/%m/%d %H:%M")
    return target_dt, target_ts, date_str, time_str, formatted_str
