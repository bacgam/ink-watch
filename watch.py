"""Ink 새 컨트랙트, 팀 지갑, GitHub, DefiLlama 신규 프로토콜을 감시해 텔레그램으로 알린다.

GitHub Actions에서 5분마다 실행된다. 텔레그램 설정(환경변수)이 없으면 알림을 화면에 출력만 한다.
마지막으로 본 위치는 state.json에 저장한다. 처음 실행할 때는 기준점만 잡고 알림을 보내지 않는다.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request

STATE_FILE = "state.json"

CHAINS = {
    "Ink 메인넷": "https://explorer.inkonchain.com",
    "Ink 테스트넷": "https://explorer-sepolia.inkonchain.com",
}
# 팀 지갑: (체인, 주소, 알림에 붙일 이름). 출처: NADO 공식 SDK @nadohq/shared 의 deployer 값
WALLETS = [
    ("Ink 메인넷", "0xC1cC56caB60e832665E6c3780BfEBe3C1C971603", "NADO 배포자"),
    ("Ink 테스트넷", "0x59841b3761Ed1D089a783E4d7CB49E4534CD4F85", "NADO 테스트넷 배포자"),
]
GITHUB_ORGS = {"nadohq": "NADO", "inkonchain": "Ink"}  # GitHub 조직: 알림에 붙일 이름
DOMAINS = ["nado.xyz", "inkonchain.com"]  # 새 하위 주소(인증서)를 감시할 공식 도메인
MAX_COMMITS = 5  # 저장소 하나에서 한 번에 따로 알릴 커밋 수. 나머지는 묶어서 알린다

# 에어드랍 신호: 컨트랙트 이름, 커밋 메시지, 바뀐 파일 이름, 브랜치, 릴리스에 이게 있으면 🚨
AIRDROP = re.compile(r"airdrop|claim|merkle|distribut|vesting|\btge\b|\bpoints\b", re.I)
# 그 밖에 알릴 컨트랙트 이름. 'ink'는 Link·Blink에 섞이므로 대문자 Ink/INK 또는 단어 앞의 ink만 본다
WATCH_NAMES = re.compile(r"(?i:reward|nado)|Ink|INK|(?<![A-Za-z])ink")
# 연습·튜토리얼용으로 자주 쓰이는 이름. 정확히 이 이름이면 알리지 않는다
IGNORE_NAMES = {"InkContract"}
# 토큰 만들기 예제의 기본 이름(기호만 jigsa·zem 등으로 바꿔 대량 배포됨). 토큰 기호가 INK일 때만 알린다
TEMPLATE_TOKEN_NAMES = {"InkToken"}
# 팀 지갑이 배포하지 않았고, 배포 후 이 일수가 지나도록 잔고·거래·토큰 이동이 없으면 방치된 것으로 보고 알리지 않는다
DORMANT_DAYS = 7


def get_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "ink-watch", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def kst_date(iso):
    return (datetime.fromisoformat(iso.replace("Z", "+00:00")) + timedelta(hours=9)).strftime("%Y-%m-%d")


def amount(v):
    return f"{v:,.2f}" if v >= 1 else f"{v:.4g}"


def token_symbol(base, addr):
    try:
        return (get_json(f"{base}/api/v2/tokens/{addr}").get("symbol") or "").upper()
    except Exception:
        return "INK"  # 확인 못 하면 놓치지 않도록 알리는 쪽으로


def describe_contract(base, addr, verified_at):
    """새 컨트랙트 알림(🆕·🚨)에 붙일 판단 근거 한 줄: 배포 시점, 배포자(팀 지갑인지), 잔고.
    팀 지갑이 배포하지 않았는데 오래 방치된 컨트랙트면 None(알리지 않음)."""
    api = f"{base}/api/v2"
    team = {a.lower(): label for _, a, label in WALLETS}
    a = get_json(f"{api}/addresses/{addr}")
    parts = []
    made_ts = None
    if a.get("creation_transaction_hash"):
        made_ts = get_json(f"{api}/transactions/{a['creation_transaction_hash']}")["timestamp"]
        made = kst_date(made_ts)
        ver = kst_date(verified_at)
        parts.append(f"배포 {made}" + (f"(검증은 {ver})" if made != ver else ""))
    creator = a.get("creator_address_hash") or ""
    is_team = creator.lower() in team
    if is_team:
        parts.append(f"배포자 ⭐ {team[creator.lower()]}")
    elif creator:
        c = get_json(f"{api}/addresses/{creator}")
        if c.get("is_contract"):
            parts.append(f"배포자 컨트랙트 {c.get('name') or creator[:10]}")
        else:
            n = get_json(f"{api}/addresses/{creator}/counters").get("transactions_count")
            parts.append(f"배포자 개인 지갑(거래 {n}건)")
    bal = []
    eth = int(a.get("coin_balance") or 0) / 1e18
    if eth:
        bal.append(f"{amount(eth)} ETH")
    for t in get_json(f"{api}/addresses/{addr}/token-balances")[:3]:
        tok = t["token"]
        bal.append(f"{amount(int(t['value']) / 10 ** int(tok.get('decimals') or 0))} {tok.get('symbol') or '?'}")
    parts.append("잔고 " + (", ".join(bal) if bal else "없음"))
    if not is_team:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(made_ts.replace("Z", "+00:00")) if made_ts else None
        if not bal and age and age > timedelta(days=DORMANT_DAYS):
            cnt = get_json(f"{api}/addresses/{addr}/counters")
            if int(cnt.get("transactions_count") or 0) == 0 and int(cnt.get("token_transfers_count") or 0) == 0:
                return None
        parts.insert(0, "⚠️ 팀 지갑 아님")
    return " · ".join(parts)


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
            if name not in IGNORE_NAMES and (AIRDROP.search(name) or WATCH_NAMES.search(name)):
                addr = c["address"]["hash"]
                if name in TEMPLATE_TOKEN_NAMES and token_symbol(base, addr) != "INK":
                    continue
                try:  # 이름은 누구나 지을 수 있으므로 판단 근거를 붙인다
                    info = describe_contract(base, addr, c["verified_at"])
                except Exception as e:
                    info = f"추가 정보 조회 실패: {e!r}"
                if info is None:  # 방치된 개인 컨트랙트
                    continue
                line = f"{'🚨' if AIRDROP.search(name) else '🆕'} [{chain}] 새 컨트랙트: {name}\n{info}\n"
                found.append(line + f"{base}/address/{addr}")
        else:
            if page.get("next_page_params"):
                page = get_json(url + "?" + urllib.parse.urlencode(page["next_page_params"]))
                continue
        break

    st["last_verified_at"] = newest
    out.extend(reversed(found))  # 오래된 것부터


def check_wallet(chain, addr, label, st, out):
    """팀 지갑이 보낸 거래 중 컨트랙트 배포와 기능 호출을 out에 추가한다. 단순 토큰 전송은 뺀다."""
    base = CHAINS[chain]
    txs = [t for t in get_json(f"{base}/api/v2/addresses/{addr}/transactions?filter=from")["items"]
           if t.get("block_number")]
    if not txs:
        return
    newest = txs[0]["block_number"]
    last = st.get("last_block")
    if last is None:  # 첫 실행: 기준점만 잡는다
        st["last_block"] = newest
        return

    found = []
    for t in txs:
        if t["block_number"] <= last:
            break
        method = t.get("method") or ""
        made = t.get("created_contract")
        if made:
            name = made.get("name") or made["hash"]
            found.append(f"🚨 [{chain}] {label}: 새 컨트랙트 배포 {name}\n{base}/address/{made['hash']}")
        elif method and method not in ("transfer", "0xa9059cbb"):  # 0xa9059cbb = transfer
            to = t.get("to") or {}
            impl = (to.get("implementations") or [{}])[0].get("name")  # 프록시면 실제 코드 이름
            target = impl or to.get("name") or to.get("hash", "")
            mark = "🚨" if AIRDROP.search(method + " " + target) else "👛"
            found.append(f"{mark} [{chain}] {label}: {method} → {target}\n{base}/tx/{t['hash']}")

    st["last_block"] = newest
    out.extend(reversed(found))


def check_defillama(st, out):
    """DefiLlama에 Ink 체인 프로토콜이 새로 올라오면 out에 추가한다. 목록이 약 10MB라 1시간에 한 번만 본다."""
    now = time.time()
    if now - st.get("checked_at", 0) < 3600:
        return
    ink = {p["slug"]: p for p in get_json("https://api.llama.fi/protocols") if "Ink" in (p.get("chains") or [])}
    known = st.get("slugs")
    if known is not None:
        for slug, p in ink.items():
            if slug not in known:
                out.append(f"🦙 [DefiLlama] Ink에 새 프로토콜: {p['name']} "
                           f"({p.get('category') or '-'}, TVL ${p.get('tvl') or 0:,.0f})\n"
                           f"https://defillama.com/protocol/{slug}")
    st["slugs"] = sorted(set(known or []) | set(ink))
    st["checked_at"] = now


def check_subdomains(domain, st, out):
    """공식 도메인 아래 새 하위 주소의 인증서가 발급되면 out에 추가한다(인증서 공개 기록, Cert Spotter API, 1시간마다).
    도메인 관리자만 인증서를 받을 수 있으므로 사칭 도메인은 섞이지 않는다.
    crt.sh는 502 장애가 잦아서 Cert Spotter를 쓴다. 마지막으로 본 발급 번호(after) 이후 것만 받는다."""
    now = time.time()
    if now - st.get("checked_at", 0) < 3600:
        return
    baseline = "after" not in st  # 처음(또는 crt.sh 시절 상태)이면 기준점만 잡는다
    known = set(st.get("names", []))
    names, after = set(), st.get("after")
    for _ in range(20):  # 한 번에 100건씩
        url = f"https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names"
        if after:
            url += f"&after={after}"
        page = get_json(url)
        if not page:
            break
        for c in page:
            names.update(n.lower() for n in c["dns_names"] if n.lower() == domain or n.lower().endswith("." + domain))
        after = page[-1]["id"]
        if len(page) < 100:
            break
    if not baseline:
        for n in sorted(names - known):
            mark = "🚨" if AIRDROP.search(n) else "🌐"
            # 링크는 사이트가 아니라 인증서 기록. 확인 안 된 사이트에 바로 들어가지 않게
            out.append(f"{mark} [서브도메인] 새 인증서: {n}\nhttps://crt.sh/?q={n}")
    st["names"] = sorted(known | names)
    st["after"] = after
    st["checked_at"] = now


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
            files = get_json(c["url"], h).get("files", [])
            hot = [f["filename"] for f in files if AIRDROP.search(f["filename"])]
            hits = []  # 새로 추가된 코드 줄에 에어드랍 단어가 있는지 (파일당 첫 줄만)
            for f in files:
                for ln in (f.get("patch") or "").splitlines():
                    if ln.startswith("+") and AIRDROP.search(ln):
                        hits.append(f"{f['filename']}: {ln[1:].strip()[:80]}")
                        break
            mark = "🚨" if hot or hits or AIRDROP.search(msg) else "📝"
            line = f"{mark} {tag} 커밋: {msg.splitlines()[0]}\n{c['html_url']}"
            if hot:
                line += "\n관련 파일: " + ", ".join(hot[:5])
            if hits:
                line += "\n추가된 코드: " + " / ".join(hits[:3])
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
    for chain, addr, label in WALLETS:
        sources.append((f"{chain} {label}", lambda st, out, c=chain, a=addr, l=label: check_wallet(c, a, l, st, out)))
    for org, label in GITHUB_ORGS.items():
        sources.append((f"{label} GitHub", lambda st, out, o=org, l=label: check_github(o, l, st, out)))
    sources.append(("DefiLlama", check_defillama))
    for d in DOMAINS:
        sources.append((f"서브도메인 {d}", lambda st, out, d=d: check_subdomains(d, st, out)))

    alerts = []
    for name, fn in sources:
        st = state.setdefault(name, {})
        try:
            fn(st, alerts)
            if st.pop("fails", 0) >= 3:
                alerts.append(f"✅ {name} 수집 복구")
        except Exception as e:  # 한 곳이 실패해도 나머지는 계속. 일시 장애는 넘기고 3번 연속 실패 때만 알린다
            print(f"{name} 실패: {e!r}", file=sys.stderr)
            st["fails"] = st.get("fails", 0) + 1
            if st["fails"] == 3:
                alerts.append(f"⚠️ {name} 수집 3번 연속 실패: {e!r}")

    # Actions 탭에서 수동 실행하면 새 소식이 없어도 확인 메시지를 보낸다(텔레그램 설정 점검용). 외부 알람(QUIET)은 제외
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and os.environ.get("QUIET") != "true" and not alerts:
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
