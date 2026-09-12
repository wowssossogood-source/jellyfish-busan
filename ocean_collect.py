"""Python 3.9+ / no extra packages.
py ocean_collect.py                 : fetch once
py ocean_collect.py --watch         : repeat every 10 minutes (trial setting)
py ocean_collect.py --input page.txt: parse a saved Copy response without network
Outputs: ocean_data/latest.json, history.jsonl, status.json beside this script.
This is an observation collector, not a jellyfish forecast. Website response
format may change. Check provider terms/API before continuous deployment.
"""
import argparse
import ast
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

URL = 'https://www.khoa.go.kr/oceangrid/koofs/kor/observation/obs_real_list.do'
FORM = dict(tsType='', tsId='', obsItem='', obsSubItem='S_', imgIdx='', menuNo='', st_area='')
KST = timezone(timedelta(hours=9))
TARGETS = {'TW_0090': '송정해수욕장', 'TW_0062': '해운대해수욕장'}
# Positions correspond to the resultList function supplied by the website.
FIELDS = {'water_temperature_c': (21, 13), 'wave_height_m': (23, 15),
          'wind_direction_deg': (26, 18), 'wind_speed_m_s': (27, 18),
          'current_direction_deg': (28, 19), 'current_speed_cm_s': (29, 19),
          'wave_period_s': (31, 15), 'wave_direction_deg': (32, 15)}
CALL = re.compile(r"^[ \t]*resultList\(([^\r\n]*)\);", re.MULTILINE)
LITERALS = re.compile(r"\s*'(?:[^'\\\r\n]|\\.)*'\s*(?:,\s*'(?:[^'\\\r\n]|\\.)*'\s*)*")

def number(value):
    if value is None or value.strip() == '':
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except ValueError:
        return None

def parse_response(text, now, stale_minutes=30):
    found = {}
    for match in CALL.finditer(text):
        body = match.group(1)
        if not LITERALS.fullmatch(body):
            continue
        # Parse literal strings only; never execute returned JavaScript.
        args = ast.literal_eval('[' + body + ']')
        if len(args) < 31 or args[30] not in TARGETS:
            continue
        station = args[30]
        if args[4] != TARGETS[station] or len(args) != 33 or station in found:
            raise ValueError('관측소명 또는 응답 구조가 변경되었습니다.')
        observed = datetime.strptime(args[2], '%Y.%m.%d %H:%M').replace(tzinfo=KST)
        age = (now - observed).total_seconds() / 60
        time_state = 'future_timestamp' if age < -5 else ('stale' if age > stale_minutes else 'recent')
        item = {'station_id': station, 'station_name': args[4],
                'observed_at': observed.isoformat(), 'longitude': number(args[5]),
                'latitude': number(args[6]), 'age_minutes_at_fetch': round(age, 1),
                'time_state': time_state, 'values': {}, 'raw_values': {}, 'provider_status': {}}
        for name, (pos, status_pos) in FIELDS.items():
            raw = number(args[pos])
            status = args[status_pos]
            item['raw_values'][name] = args[pos]
            item['provider_status'][name] = status
            item['values'][name] = raw if status == '0' and time_state == 'recent' else None
        found[station] = item
    missing = set(TARGETS) - set(found)
    if missing:
        raise ValueError('응답에서 관측소를 찾지 못했습니다: ' + ', '.join(sorted(missing)))
    return [found[key] for key in TARGETS]

def write_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def collect(options):
    out = options.output
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    try:
        if options.input:
            text = options.input.read_text(encoding='utf-8-sig')
        else:
            req = urllib.request.Request(URL, data=urllib.parse.urlencode(FORM).encode(),
                headers={'Content-Type': 'application/x-www-form-urlencoded',
                         'User-Agent': 'OceanObservationStudentPrototype/1.0'}, method='POST')
            with urllib.request.urlopen(req, timeout=25) as response:
                text = response.read().decode(response.headers.get_content_charset() or 'utf-8')
        rows = parse_response(text, now, options.stale_minutes)
        data = {'source_url': URL, 'mode': 'saved_response' if options.input else 'live_fetch',
                'fetched_at': now.isoformat(), 'stale_after_minutes': options.stale_minutes,
                'note': 'Freshness threshold is a prototype setting, not an official quality guarantee. Direction conventions and sensor depth require verification.',
                'stations': rows}
        previous = None
        latest = out / 'latest.json'
        if latest.exists():
            try:
                previous = json.loads(latest.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                pass
        fingerprint = lambda d: [(r['station_id'], r['observed_at'], r['raw_values'], r['provider_status']) for r in d['stations']]
        if previous is None or fingerprint(previous) != fingerprint(data):
            with (out / 'history.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
        write_json(latest, data)
        write_json(out / 'status.json', {'fetch_ok': True, 'attempted_at': now.isoformat()})
        for r in rows:
            v = r['values']
            print(r['station_name'], r['observed_at'], r['time_state'],
                  '풍속(m/s):', v['wind_speed_m_s'], '유속(cm/s):', v['current_speed_cm_s'])
        print('저장:', latest.resolve())
        return True
    except Exception as exc:
        write_json(out / 'status.json', {'fetch_ok': False, 'attempted_at': now.isoformat(), 'error': str(exc)})
        print('수집 실패:', exc)
        print('기존 latest.json은 보존됩니다. 앱에서는 status.json과 관측 시각을 함께 확인하세요.')
        return False

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--watch', action='store_true')
    p.add_argument('--interval', type=int, default=600, help='trial polling interval, seconds')
    p.add_argument('--stale-minutes', type=int, default=30, help='prototype freshness cutoff')
    p.add_argument('--input', type=Path)
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'ocean_data')
    a = p.parse_args()
    if a.interval < 60 or a.stale_minutes <= 0 or (a.input and a.watch):
        p.error('간격은 60초 이상, 유효시간은 양수여야 하며 저장 응답은 반복 조회할 수 없습니다.')
    try:
        while True:
            ok = collect(a)
            if not a.watch:
                return 0 if ok else 1
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print('\n수집을 종료했습니다.')
        return 0

if __name__ == '__main__':
    raise SystemExit(main())
