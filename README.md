# ink-watch

Ink 메인넷·테스트넷의 새 컨트랙트와 NADO(github.com/nadohq) 변화를 5분마다 확인해 텔레그램으로 알린다. GitHub Actions에서 돌아가므로 내 컴퓨터는 꺼져 있어도 된다.

## 무엇을 알리나

| 표시 | 내용 |
|---|---|
| 🚨 | 에어드랍 신호: 이름·커밋·파일·브랜치·릴리스에 airdrop, claim, merkle, distribut, vesting, tge, points |
| 🆕 | 이름에 Ink, nado, reward가 들어간 새 검증 컨트랙트 |
| 📝 🌿 🏷️ 📦 | NADO 기본 브랜치 새 커밋 / 새 브랜치 / 새 릴리스 / 새 저장소 |
| ⚠️ ✅ | 수집 실패(처음 한 번만) / 복구 |

키워드는 `watch.py` 위쪽의 `AIRDROP`, `WATCH_NAMES`에서 고친다.

## 설정 (처음 한 번)

1. **텔레그램 봇 만들기**: 텔레그램에서 `@BotFather` → `/newbot` → 받은 토큰을 메모한다.
2. **채팅 ID 확인**: 만든 봇에게 아무 메시지나 보낸 뒤, 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열어 `"chat":{"id": 숫자` 의 숫자를 메모한다.
3. **GitHub 공개 저장소 만들기**: 이 폴더를 그대로 올린다.
4. **Secrets 등록**: 저장소 → Settings → Secrets and variables → Actions → New repository secret
   - `TELEGRAM_BOT_TOKEN` = 1번 토큰
   - `TELEGRAM_CHAT_ID` = 2번 숫자
5. **첫 실행**: Actions 탭 → ink-watch → Run workflow. 첫 실행은 기준점만 잡고 알림을 보내지 않는다. 이후 5분마다 자동 실행된다.

## 로컬 테스트

```
python watch.py
```

텔레그램 환경변수가 없으면 알림 내용을 화면에만 출력한다. 마지막으로 본 위치는 `state.json`에 저장된다.

## 알아 둘 점

- 소스가 공개(검증)된 컨트랙트만 본다. 검증하지 않은 컨트랙트와 이름이 `ERC1967Proxy` 같은 일반 프록시는 놓친다.
- 이름만 보고 거르므로 사칭 토큰(가짜 `InkToken` 등)도 걸린다. 알림을 받으면 배포자를 확인한다.
- GitHub 예약 실행은 몇 분씩 밀릴 수 있다.
