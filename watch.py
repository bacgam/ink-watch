"""Ink 메인넷·테스트넷 새 컨트랙트 + NADO GitHub 변화를 감시해 텔레그램으로 알린다.

GitHub Actions에서 5분마다 실행된다. 텔레그램 설정(환경변수)이 없으면 알림을 화면에 출력만 한다.
마지막으로 본 위치는 state.json에 저장한다. 처음 실행할 때는 기준점만 잡고 알림을 보내지 않는다.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

STATE_FILE = "state.json"

CHAINS = {
    "Ink 메인넷": "https://explorer.inkonchain.com",
    "Ink 테스트넷": "https://explorer-sepolia.inkonchain.com",
}
GITHUB_ORGS = {"nadohq": "NADO", "inkonchain": "Ink"}  # GitHub 조직: 알림에 붙일 이름
MAX_COMMITS = 5  # 저장소 하나에서 한 번에 따로 알릴 커밋 수. 나머지는 묶어서 알린다

# 에어드랍 신호: 컨트랙트 이름, 커밋 메시지, 바뀐 파일 이름, 브랜치, 릴리스에 이게 있으면 🚨
AIRDROP = re.compile(r"airdrop|claim|merkle|distribut|vesting|\btge\b|\bpoints\b", re.I)
# 그 밖에 알릴 컨트랙트 이름. 'ink'는 Link·Blink에 섞이므로 대문자 Ink/INK 또는 단어 앞의 ink만 본다
WATCH_NAMES = re.compile(r"(?i:reward|nado)|Ink|INK|(?<![A-Za-z])ink")


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "ink-watch", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def check_chain(chain, base, st, out):
    """검증된 새 컨트랙트 중 이름이 키워드에 걸린 것을 out에 추가한다."""
    url = f"{base}/api/v2/smart-contracts"
    page = get_json(url)
    if not page["items"]:
        return
    newest = page["items"][0]["verified_at"]
    last = st.get("last_verified_at")
    if last is None:  # 첫 실행: 기준점만 잡는다
        st["last_verified_at"] = newest
        return

    found = []
    for _ in range(20):  # 한 번에 최대 20페이지(1000개)까지만 거슬러 올라간다
        for c in page["items"]:
            if c["verified_at"] <= last:
                break
            name = c["address"].get("name") or ""
            if AIRDROP.search(name) or WATCH_NAMES.search(name):
                mark = "🚨" if AIRDROP.search(name) else "🆕"
                found.append(f"{mark} [{chain}] 새 컨트랙트: {name}\n{base}/address/{c['address']['hash']}")
        else:
            if page.get("next_page_params"):
                page = get_json(url + "?" + urllib.parse.urlencode(page["next_page_params"]))
                continue
        break

    st["last_verified_at"] = newest
    out.extend(reversed(found))  # 오래된 것부터


def check_github(org, label, st, out):
    """org의 새 저장소, 기본 브랜치 새 커밋, 새 브랜치, 새 릴리스를 out에 추가한다.
    API 호출을 아끼려고 마지막 push 시각(pushed_at)이 바뀐 저장소만 자세히 본다."""
    h = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    api = f"https://api.github.com/repos/{org}"

    first_run = "repos" not in st
    known = st.setdefault("repos", {})
    for r in get_json(f"https://api.github.com/orgs/{org}/repos?per_page=100", h):
        name = r["name"]
        tag = f"[{label}/{name}]"
        rs = known.get(name)
        if rs is not None and rs.get("pushed_at") == r["pushed_at"]:
            continue
        commits = get_json(f"{api}/{name}/commits?sha={r['default_branch']}&per_page=30", h) if r["size"] else []
        branches = [b["name"] for b in get_json(f"{api}/{name}/branches?per_page=100", h)]
        releases = get_json(f"{api}/{name}/releases?per_page=10", h)
        new_state = {
            "pushed_at": r["pushed_at"],
            "head": commits[0]["sha"] if commits else None,
            "branches": branches,
            "releases": [x["id"] for x in releases],
        }

        if rs is None:  # 처음 보는 저장소: 기준점만 잡는다
            if not first_run:
                out.append(f"📦 {tag} 새 공개 저장소\n{r['html_url']}")
            known[name] = new_state
            continue

        found = []
        new_commits = []
        for c in commits:
            if c["sha"] == rs["head"]:
                break
            new_commits.append(c)
        rest = new_commits[MAX_COMMITS:]  # 최신순이므로 뒤쪽이 오래된 커밋
        if rest:
            mark = "🚨" if any(AIRDROP.search(c["commit"]["message"]) for c in rest) else "📝"
            found.append(f"{mark} {tag} 커밋 {len(rest)}건 더(아래 최근 {MAX_COMMITS}건 이전)\n"
                         f"{r['html_url']}/commits/{r['default_branch']}")
        for c in reversed(new_commits[:MAX_COMMITS]):
            msg = c["commit"]["message"]
            files = [f["filename"] for f in get_json(c["url"], h).get("files", [])]
            hot = [f for f in files if AIRDROP.search(f)]
            mark = "🚨" if hot or AIRDROP.search(msg) else "📝"
            line = f"{mark} {tag} 커밋: {msg.splitlines()[0]}\n{c['html_url']}"
            if hot:
                line += "\n관련 파일: " + ", ".join(hot[:5])
            found.append(line)
        for b in branches:
            if b not in rs["branches"]:
                mark = "🚨" if AIRDROP.search(b) else "🌿"
                found.append(f"{mark} {tag} 새 브랜치: {b}\n{r['html_url']}/tree/{urllib.parse.quote(b)}")
        for x in releases:
            if x["id"] not in rs["releases"]:
                title = x["name"] or x["tag_name"]
                mark = "🚨" if AIRDROP.search(title + " " + (x["body"] or "")) else "🏷️"
                found.append(f"{mark} {tag} 새 릴리스: {title}\n{x['html_url']}")

        known[name] = new_state
        out.extend(found)


def send(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("--- 텔레그램 설정 없음, 출력만 함 ---\n" + text + "\n")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30)


def main():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}

    sources = [(c, lambda st, out, c=c, b=b: check_chain(c, b, st, out)) for c, b in CHAINS.items()]
    for org, label in GITHUB_ORGS.items():
        sources.append((f"{label} GitHub", lambda st, out, o=org, l=label: check_github(o, l, st, out)))

    alerts = []
    for name, fn in sources:
        st = state.setdefault(name, {})
        try:
            fn(st, alerts)
            if st.pop("failing", False):
                alerts.append(f"✅ {name} 수집 복구")
        except Exception as e:  # 한 곳이 실패해도 나머지는 계속. 실패는 처음 한 번만 알린다
            print(f"{name} 실패: {e!r}", file=sys.stderr)
            if not st.get("failing"):
                st["failing"] = True
                alerts.append(f"⚠️ {name} 수집 실패: {e!r}")

    # Actions 탭에서 수동 실행하면 새 소식이 없어도 확인 메시지를 보낸다(텔레그램 설정 점검용)
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and not alerts:
        alerts.append("✅ ink-watch 수동 실행 완료: 새 소식 없음")

    # 텔레그램 메시지 한도(4096자) 안에서 묶어 보낸다
    chunk = ""
    for a in alerts:
        if len(chunk) + len(a) > 3800:
            send(chunk)
            chunk = ""
        chunk += a + "\n\n"
    if chunk:
        send(chunk)

    # 전송이 끝난 뒤에만 저장한다(전송 실패 시 다음 실행에서 다시 보냄)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    print(f"완료: 알림 {len(alerts)}건")


if __name__ == "__main__":
    main()
