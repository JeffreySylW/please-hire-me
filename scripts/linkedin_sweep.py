#!/usr/bin/env python3
"""LinkedIn sweep: new entry-level postings from LinkedIn's PUBLIC guest job search, no login.

    python3 scripts/linkedin_sweep.py              # since state/last_linkedin_sweep.txt (default 36h), then advance it
    python3 scripts/linkedin_sweep.py --hours 48   # fixed window, does not advance the cutoff
    python3 scripts/linkedin_sweep.py --selftest   # run the parser checks and exit

LinkedIn is a SOURCE, never a channel: never sign in, never use Easy Apply. Each row says whether the
posting applies on the company's own site ("company-site") or only through Easy Apply ("easy-apply", out
of scope). For a company-site row, find the same req on the company's ATS and apply there.

Searches each targets.locations entry that has a LinkedIn location string (LOCATIONS below) for a few
targets.roles keywords at LinkedIn's Entry level and Associate filters, then reads each posting and drops it
when the body requires more years than targets.max_years_experience_required or an ACTIVE clearance.
Prestige, staffing-agency and fit judgment stay with the agent. Run in the FOREGROUND.
Output: state/sweep/linkedin_cands.json and tab-separated rows on stdout.
"""
import datetime, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUT_FILE = os.path.join(R, 'state', 'last_linkedin_sweep.txt')
OUT = os.path.join(R, 'state', 'sweep', 'linkedin_cands.json')
UTC = datetime.timezone.utc
LOCATIONS = {  # targets.locations entry -> LinkedIn location string. Entries without one are not searched.
    'Virginia': 'Virginia, United States',
    'Pittsburgh': 'Pittsburgh, Pennsylvania, United States',
    'New York': 'New York, United States',
    'Seattle': 'Seattle, Washington, United States',
    'San Francisco Bay Area': 'San Francisco Bay Area',
    'Chicago': 'Chicago, Illinois, United States',
}
KEYWORDS = ['software engineer', 'software developer', 'full stack developer']
BAD_TITLE = re.compile(r'senior|\bsr\b|staff|principal|\blead\b|manager|director|architect|vice president|\bVP\b|\bII\b|\bIII\b|\bIV\b|\b[2-5]\b|intern|co-?op', re.I)
YEARS = re.compile(r'(?<!\d)(\d{1,2})(?!\d)\s*\+?\s*(?:-|to|–)?\s*\d{0,2}\s*\+?\s*years?\b(?!\s*(?:of age|old))[^.]{0,60}?experience', re.I)
ACTIVE_CLEARANCE = re.compile(r'(active|current|must (?:hold|have|possess))[^;:]{0,40}?(clearance|TS/SCI|\bTS\b|top secret|secret)', re.I)
UA = {'User-Agent': 'Mozilla/5.0'}

def text_of(page):
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', page)))

def req_years(t):
    """Years of experience in the FIRST requirement that states one (basic qualifications come before
    preferred ones); None when none is stated. The first number, not the smallest: a '1+ years of OO design'
    line under a '3+ years of professional experience' requirement does not make the role entry level."""
    for m in YEARS.finditer(t):
        if int(m.group(1)) < 20: return int(m.group(1))
    return None

def needs_active_clearance(t):
    return bool(ACTIVE_CLEARANCE.search(t))

def get(url):
    for wait in (10, 30, 60, None):  # LinkedIn answers bursts with HTTP 429; back off and retry
        try: return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None: raise
            time.sleep(wait)

def selftest():
    assert req_years('BS with 4+ years’ industry experience') == 4
    assert req_years('0-1 years of hands-on experience in scripting') == 0
    assert req_years('3+ years of non-internship professional software development experience 1+ years of Object Oriented Design experience') == 3
    assert req_years('Raytheon brings more than 100 years of experience. a minimum of 2 years of prior relevant experience') == 2
    assert req_years('Bachelor degree. Experience with Java.') is None
    assert req_years("Are 18 years of age or older Bachelor's degree or equivalent Experience with at least one language") is None
    assert needs_active_clearance('This position requires an active U.S. Top Secret Security Clearance')
    assert needs_active_clearance('Bachelor’s Degree Active TS and SCI eligible')
    assert needs_active_clearance('must hold an active government Secret security clearance')
    assert needs_active_clearance('Active TS/SCI security clearance.')
    assert not needs_active_clearance('Ability to obtain a Secret clearance')
    assert not needs_active_clearance('obtain and maintain a security clearance at the TS/SCI level')
    print('selftest ok')

def main():
    args = sys.argv[1:]
    if '--selftest' in args: return selftest()
    S = json.load(open(os.path.join(R, 'config', 'settings.json')))
    T = S['targets']
    max_years = T.get('max_years_experience_required', 1)
    skip = [c.lower() for c in T.get('skip_companies', [])]
    now = datetime.datetime.now(UTC)
    if '--hours' in args:
        since, advance = now - datetime.timedelta(hours=float(args[args.index('--hours') + 1])), False
    elif os.path.exists(CUT_FILE):
        since, advance = datetime.datetime.fromisoformat(open(CUT_FILE).read().strip().replace('Z', '+00:00')), True
    else:
        since, advance = now - datetime.timedelta(hours=36), True
    window = max(3600, int((now - since).total_seconds()))
    locs = [(l, LOCATIONS[l]) for l in T.get('locations', []) if l in LOCATIONS]
    print(f'window {window // 3600}h  locations {[l for l, _ in locs]}')

    cards = {}
    for label, loc in locs:
        for kw in KEYWORDS:
            for start in (0, 25):
                q = urllib.parse.urlencode({'keywords': kw, 'location': loc, 'f_TPR': f'r{window}', 'f_E': '2,3', 'start': start})
                try: page = get('https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?' + q)
                except Exception as e: print('search error', label, kw, e); continue
                for card in page.split('<li>')[1:]:
                    g = lambda p: html.unescape(re.sub(r'\s+', ' ', (re.search(p, card, re.S) or [None, ''])[1])).strip()
                    jid = g(r'jobPosting:(\d+)')
                    if jid and jid not in cards:
                        cards[jid] = dict(id=jid, title=g(r'base-search-card__title">(.*?)<'), company=g(r'base-search-card__subtitle">.*?>(.*?)<'),
                                          location=g(r'job-search-card__location">(.*?)<'), posted=g(r'datetime="(.*?)"'), area=label)
                time.sleep(1.5)

    rows = []
    for c in cards.values():
        if BAD_TITLE.search(c['title']) or any(s in c['company'].lower() for s in skip): continue
        try: page = get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{c['id']}")
        except Exception as e: print('detail error', c['id'], e); continue
        t = text_of(page)
        time.sleep(3)
        yrs = req_years(t)
        if (yrs is not None and yrs > max_years) or needs_active_clearance(t): continue
        pay = re.search(r'\$\s?[\d,]{5,}(?:\.\d+)?\s*(?:-|to|–)\s*\$?\s?[\d,]{5,}', t)
        c.update(years='?' if yrs is None else yrs, pay=pay.group(0) if pay else '',
                 apply='company-site' if 'offsite-apply' in page else 'easy-apply',
                 url=f"https://www.linkedin.com/jobs/view/{c['id']}")
        rows.append(c)

    rows.sort(key=lambda c: (c['apply'] != 'company-site', c['area'], c['posted']), reverse=False)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(rows, open(OUT, 'w'), indent=1)
    print(f'{len(cards)} cards, {len(rows)} after title/years/clearance filters')
    for c in rows:
        print('\t'.join(str(c[k]) for k in ('area', 'posted', 'company', 'title', 'location', 'years', 'pay', 'apply', 'url')))
    if advance:
        open(CUT_FILE, 'w').write(now.isoformat(timespec='minutes').replace('+00:00', 'Z') + '\n')

if __name__ == '__main__':
    main()
