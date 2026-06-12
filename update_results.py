#!/usr/bin/env python3
"""
Hourly World Cup 2026 results updater.
Fetches completed scores from ESPN, patches the RESULTS block in index.html,
and pushes to GitHub only if something changed.
"""

import os
import re
import base64
import requests
from datetime import datetime, timezone, timedelta

PAT = os.environ['GH_PAT']
REPO = 'SoulInvictus/kpodmg_worldcup'
FILE_PATH = 'index.html'
API_BASE = 'https://api.github.com'
HEADERS = {
    'Authorization': f'token {PAT}',
    'Accept': 'application/vnd.github.v3+json',
}

GROUPS = {
    'A': ['Mexico', 'South Africa', 'South Korea', 'Czechia'],
    'B': ['Canada', 'Bosnia-Herzegovina', 'Qatar', 'Switzerland'],
    'C': ['Brazil', 'Morocco', 'Scotland', 'Haiti'],
    'D': ['United States', 'Paraguay', 'Australia', 'Türkiye'],
    'E': ['Germany', 'Curaçao', 'Ivory Coast', 'Ecuador'],
    'F': ['Netherlands', 'Japan', 'Sweden', 'Tunisia'],
    'G': ['Belgium', 'Egypt', 'Iran', 'New Zealand'],
    'H': ['Spain', 'Cape Verde', 'Saudi Arabia', 'Uruguay'],
    'I': ['France', 'Senegal', 'Iraq', 'Norway'],
    'J': ['Argentina', 'Algeria', 'Austria', 'Jordan'],
    'K': ['Portugal', 'DR Congo', 'Uzbekistan', 'Colombia'],
    'L': ['England', 'Croatia', 'Ghana', 'Panama'],
}

MP = [[0, 1], [2, 3], [0, 2], [1, 3], [0, 3], [1, 2]]

# Map ESPN/API names to canonical names used in GROUPS
TEAM_ALIASES = {
    'USA': 'United States',
    'US': 'United States',
    'Turkey': 'Türkiye',
    'Turkiye': 'Türkiye',
    'Korea Republic': 'South Korea',
    'Czech Republic': 'Czechia',
    "Cote d'Ivoire": 'Ivory Coast',
    'Curacao': 'Curaçao',
    'Congo DR': 'DR Congo',
    'Congo, DR': 'DR Congo',
    'Democratic Republic of Congo': 'DR Congo',
    'Cape Verde Islands': 'Cape Verde',
    'Bosnia and Herzegovina': 'Bosnia-Herzegovina',
    'Bosnia & Herzegovina': 'Bosnia-Herzegovina',
}

# Build lookup: canonical_name -> (group, position)
TEAM_INDEX: dict[str, tuple[str, int]] = {}
for grp, teams in GROUPS.items():
    for pos, team in enumerate(teams):
        TEAM_INDEX[team] = (grp, pos)


def normalize(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def get_file() -> tuple[str, str]:
    resp = requests.get(f'{API_BASE}/repos/{REPO}/contents/{FILE_PATH}', headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    content = base64.b64decode(data['content']).decode('utf-8')
    return content, data['sha']


def parse_existing_results(block: str) -> dict[str, list]:
    results = {g: [None] * 6 for g in GROUPS}
    for line in block.split('\n'):
        m = re.search(r'([A-L]):\s*(\[.+\])', line)
        if not m:
            continue
        grp = m.group(1)
        tokens = re.findall(r'\[\d+,\d+\]|null', m.group(2))
        parsed = []
        for tok in tokens:
            if tok == 'null':
                parsed.append(None)
            else:
                nums = re.findall(r'\d+', tok)
                parsed.append([int(nums[0]), int(nums[1])])
        if len(parsed) == 6:
            results[grp] = parsed
    return results


def fetch_espn_results() -> list[dict]:
    matches = []
    start = datetime(2026, 6, 11, tzinfo=timezone.utc)
    today = datetime.now(timezone.utc)
    current = start
    while current <= today + timedelta(days=1):
        date_str = current.strftime('%Y%m%d')
        url = (
            'https://site.api.espn.com/apis/site/v2/sports/soccer'
            f'/fifa.world/scoreboard?dates={date_str}'
        )
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            for event in resp.json().get('events', []):
                comp = event.get('competitions', [{}])[0]
                if not comp.get('status', {}).get('type', {}).get('completed'):
                    continue
                competitors = comp.get('competitors', [])
                if len(competitors) != 2:
                    continue
                home = next((c for c in competitors if c.get('homeAway') == 'home'), competitors[0])
                away = next((c for c in competitors if c.get('homeAway') == 'away'), competitors[1])
                matches.append({
                    'home': normalize(home['team']['displayName']),
                    'away': normalize(away['team']['displayName']),
                    'home_score': int(float(home.get('score', 0))),
                    'away_score': int(float(away.get('score', 0))),
                })
        except Exception as e:
            print(f'  Warning: failed to fetch {date_str}: {e}')
        current += timedelta(days=1)
    return matches


def apply_espn_results(existing: dict[str, list], matches: list[dict]) -> dict[str, list]:
    """Merge ESPN results into existing; never overwrite a known result with null."""
    updated = {g: list(existing[g]) for g in GROUPS}
    for match in matches:
        h_loc = TEAM_INDEX.get(match['home'])
        a_loc = TEAM_INDEX.get(match['away'])
        if not h_loc or not a_loc:
            print(f'  Skipping unknown teams: {match["home"]} vs {match["away"]}')
            continue
        if h_loc[0] != a_loc[0]:
            continue
        grp = h_loc[0]
        h_pos, a_pos = h_loc[1], a_loc[1]
        for mi, (p1, p2) in enumerate(MP):
            if h_pos == p1 and a_pos == p2:
                updated[grp][mi] = [match['home_score'], match['away_score']]
                break
            elif h_pos == p2 and a_pos == p1:
                updated[grp][mi] = [match['away_score'], match['home_score']]
                break
    return updated


def format_results_block(results: dict[str, list], old_block: str) -> str:
    comments: dict[str, str] = {}
    for line in old_block.split('\n'):
        m = re.match(r'\s*([A-L]):\s*\[.*?\]\s*(//.*)', line)
        if m:
            comments[m.group(1)] = m.group(2)

    lines = ['const RESULTS = {']
    for grp in sorted(GROUPS.keys()):
        entries = ','.join(
            'null' if v is None else f'[{v[0]},{v[1]}]'
            for v in results[grp]
        )
        comment = f' {comments[grp]}' if grp in comments else ''
        lines.append(f'  {grp}: [{entries}],{comment}')
    lines.append('};')
    return '\n'.join(lines)


def push_file(content: str, sha: str, message: str) -> None:
    encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    resp = requests.put(
        f'{API_BASE}/repos/{REPO}/contents/{FILE_PATH}',
        headers=HEADERS,
        json={'message': message, 'content': encoded, 'sha': sha, 'branch': 'main'},
    )
    resp.raise_for_status()


def main() -> None:
    print('Fetching current file from GitHub...')
    content, sha = get_file()

    m = re.search(r'const RESULTS = \{[\s\S]*?\};', content)
    if not m:
        print('ERROR: Could not find RESULTS block in file')
        return
    old_block = m.group(0)
    existing = parse_existing_results(old_block)

    print('Fetching results from ESPN...')
    matches = fetch_espn_results()
    print(f'  Found {len(matches)} completed matches')

    updated = apply_espn_results(existing, matches)
    new_block = format_results_block(updated, old_block)

    if new_block == old_block:
        print('No changes — RESULTS already up to date.')
        return

    print('Changes detected — updating file...')
    new_content = content.replace(old_block, new_block)
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    push_file(new_content, sha, f'Auto-update results {now_utc}')
    print('Done — pushed to GitHub.')


if __name__ == '__main__':
    main()
