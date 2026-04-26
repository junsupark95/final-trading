# KFiveQuant — 5인 거장 융합 멀티시스템 전략

> 트룰라스(비상관 시스템) · 웅거(KISS · 변동성 돌파) · 데이비(워크포워드 · ATR 사이징)
> · 김민겸(한국형 팩터) · 카메론(Bull Flag · Strong Close)

다섯 명의 검증된 시스템 트레이더의 **공통 강점**을 한국 주식 시장에 맞게 통합한
4-시스템 멀티전략입니다. 한 시스템이 부진할 때 다른 시스템이 보완하도록 설계했고,
모든 코드는 한국투자증권(KIS) Open API 샘플 코드 패턴을 따릅니다.

---

## 1. 전략 철학

| 거장 | 강점 | 본 전략 반영 |
|---|---|---|
| **다비드 트룰라스 빌라** (Live in Trading 창립자, 2025 World Cup Day Trading 3-time chamber Q2/Q3/Q4) | 비상관 시스템 포트폴리오 운용 | 4개 독립 서브시스템(A/B/C/D) 동시 운용 |
| **안드레아 웅거** (4x World Cup Trading Champion, 30~40개 시장에 trend·MR·breakout·volatility 메소드 분산) | KISS · 견고성 우선 · 단순한 진입 룰 | System B(변동성 돌파) + System C(RSI2 평균회귀) 채택 |
| **케빈 데이비** (*Building Winning Algorithmic Trading Systems*, 2x World Cup) | 워크포워드 + 몬테카를로 + 인큐베이션 검증 파이프라인, ATR 기반 사이징 | `KFiveRiskManager` 의 ATR 사이징 + 일/월 서킷 브레이커 |
| **김민겸 / 한국형 퀀트** (저PER · 저PBR · ROE · 모멘텀 팩터) | 한국 시장 검증된 팩터, 시총·거래대금 필터 | `UniverseBuilder` 의 1차 종목 필터 |
| **로스 카메론** (Warrior Trading, 70%+ accuracy 모멘텀 데이트레이딩) | Bull Flag · Gap-and-Go · 거래량 폭증 · Strong Close | System B 의 거래량 2x · Strong Close ≥ 0.7 트리거 |

---

## 2. 4개 비상관 서브시스템

### System A — 팩터 모멘텀 (Trend Following) 🔥
- **트룰라스 + 김민겸**
- 진입: ROC(252) > 0 **AND** ROC(20) > 0 **AND** Close > MA(200)
- 강도: 12M ROC 정규화 (60% 이상이면 strength 1.0 클램프)
- 보유 기간: ~20–60일 (장기 모멘텀이 무너지면 청산)

### System B — 변동성 돌파 (Day Trading) ⚡
- **웅거 + 카메론**
- 진입: `today_open + K × yesterday_range` **상향 돌파** (K=0.5, Larry Williams)
  + 거래량 ≥ 20일 평균 × 2.0
  + Strong Close 비율 ≥ 0.7 (종가가 고가 근처)
- 청산: 당일 종가 청산(오버나이트 리스크 차단)
- 강도: 거래량 배수 + 강한 종가 결합

### System C — RSI(2) 평균회귀 (Mean Reversion) 🌊
- **웅거 + Larry Connors**
- 진입: Close > MA(200) **AND** RSI(2) < 10 **AND** Close < BB하단(20, 2σ)
- 청산: RSI(2) > 70 또는 +5일 강제 청산
- 강도: RSI 깊이 + BB 이탈 깊이

### System D — 52주 신고가 + VCP (Breakout) 🚀
- **Minervini-style, Davey-validated**
- 진입: 52주 신고가 갱신
  + ATR(14) / ATR(56) ≤ 0.85 (변동성 압축)
  + 거래량 ≥ 50일 평균 × 1.5
  + 베이스(40일) 가격변동폭 < 30%
- 보유 기간: 진입가 -7% 손절, 트레일링 -10%

각 시스템 결과 중 **가장 강한 시그널**을 단일 `Signal` 로 반환합니다 (백테스터 단일-룰
호환). 전 시스템이 HOLD 면 거래 없음.

---

## 3. 리스크 관리 (Davey)

| 항목 | 값 | 근거 |
|---|---|---|
| 종목당 리스크 | 자본의 0.5% | Davey: 1R = 자본의 0.5–1% |
| 손절 거리 | 1.5 × ATR(14) | Unger / Davey 표준 |
| 종목당 최대 비중 | 자본의 10% | 분산 |
| 동시 보유 | 최대 10종목 | 트룰라스: 분산 운용 |
| 시스템별 보유 | 최대 3종목 | 한 시스템 편중 방지 |
| 일 손실 한도 | -2% (서킷 브레이커) | Davey |
| 월 손실 한도 | -6% (서킷 브레이커) | Davey |

ATR 사이징 공식 (`KFiveRiskManager.position_size`):

```
qty = floor(min(
  (capital × 0.005) / (1.5 × ATR),       # 리스크 한도
  (capital × 0.10) / price                # 비중 한도
))
```

---

## 4. 종목 유니버스 필터 (김민겸 1차 필터)

`UniverseBuilder` (`examples_user/domestic_stock/kfive_universe.py`)

- KOSPI 시총 상위 + KOSDAQ 우량주 시드(35종목, 확장 가능)
- **시가총액**: 300억 ~ 5조원
- **일평균 거래대금**: ≥ 30억원
- **종목 상태**: 관리·투자주의·거래정지 제외 (`iscd_stat_cls_code` 체크)
- **재무**: PER ≤ 20, PBR ≤ 2 *(옵션, `--factor-filter` 플래그로 활성화)*

KIS API 사용:
- `inquire-price` (FHKST01010100): 현재가 + PER + PBR + 시총 + 종목 상태
- `inquire-daily-itemchartprice` (FHKST03010100): 일봉 → 거래대금 산출

---

## 5. 검증 파이프라인 (Davey 6단계)

```
1) In-Sample Backtest        : 2020-01 ~ 2023-06 (3.5년)
   ↓
2) Out-of-Sample Backtest    : 2023-07 ~ 2025-12 (2.5년)
   ↓
3) Walk-Forward Optimization : 6m train / 3m test 롤링 윈도우
   ↓
4) Monte Carlo Simulation    : 1,000회 거래 시퀀스 셔플 → MDD 95th, Sharpe 분포
   ↓
5) Paper Trading Incubation  : KIS vps 모의투자 3-5일 (본 러너 사용)
   ↓
6) Live Trading (소액 시작)   : KIS prod 실전, --capital 작게 시작
```

**백테스트 실행:**

```bash
cd backtester
./start.sh
# 웹 UI(localhost:3001) → "Import YAML" → backtester/kis_backtest/file/templates/kfive_quant.kis.yaml
# → Universe 선택 → Run Backtest → 리포트 확인
```

---

## 6. 실행 (Quick Start)

### 6.1. 환경 설정

```bash
# 1) 프로젝트 의존성 설치
uv sync

# 2) ~/KIS/config/kis_devlp.yaml 에 실전/모의 앱키 입력
mkdir -p ~/KIS/config && cp kis_devlp.yaml ~/KIS/config/
# ↑ 파일을 열고 paper_app, paper_sec, my_paper_stock(8자리), my_prod="01" 입력
```

### 6.2. 모의투자 시동 (3-5일 인큐베이션)

```bash
cd examples_user/domestic_stock

# Step 1: dry-run 으로 시그널/사이징 검증 (주문 발송 없음)
uv run python kfive_quant_runner.py --env vps --capital 10000000 --dry-run

# Step 2: 실제 모의투자 발주 (장중 09:05 ~ 15:00 사이 1회 실행 권장)
uv run python kfive_quant_runner.py --env vps --capital 10000000

# 매일 일정 시간에 실행 (cron / Windows 작업 스케줄러)
# 예: 매일 09:05 KST
# 5 9 * * 1-5 cd /path/to/final-trading/examples_user/domestic_stock && \
#   /path/to/uv run python kfive_quant_runner.py --env vps --capital 10000000
```

### 6.3. 실전 투입 (인큐베이션 통과 후)

```bash
# 실전 — 처음에는 작은 자본으로
uv run python kfive_quant_runner.py --env prod --capital 5000000

# CSV 로 직접 유니버스 지정도 가능
uv run python kfive_quant_runner.py --env prod --capital 10000000 \
    --universe-csv my_universe.csv --factor-filter
```

### 6.4. 5일 모의투자 체크리스트

| Day | 점검 항목 |
|---|---|
| **D1** | 인증 OK / 유니버스 빌드 시간(<30s) / dry-run 시그널 출력 / 실제 매수 1~2건 발생 |
| **D2** | 매수 종목 손익률 / 청산 시그널이 발생하면 정상 매도 / 잔고 캐시 동작 |
| **D3** | 일 요약 로그(평가금액·평가손익) / 리스크 상태 JSON(`~/KIS/kfive_state/risk_state_vps.json`) |
| **D4** | 강제로 손절가에 닿게 종목 선정 → ATR 손절 동작 검증 / 동시 보유 한도 검증 |
| **D5** | 총 5일간 손익 분포 → MDD 확인 / 시스템별 진입 빈도 분석 → 실전 자본 결정 |

---

## 7. MCP / AI 통합

`MCP/` 디렉토리에 아래 두 MCP 서버가 있어 Claude Code · Cursor 등 AI 도구에서
자연어로 KIS API를 호출할 수 있습니다.

- **KIS Code Assistant MCP**: 자연어 → 적합한 API 함수 검색 + 샘플 코드
- **KIS Trading MCP**: 시세 조회 / 주문 / 잔고 / 시장 분석

KFiveQuant 운용 중 AI 보조 활용 예시:

```
"오늘 보유 종목 중 손실률 -10% 이상인 종목 보여줘"
→ MCP가 inquire-balance + 분석 후 응답

"KFiveQuant 매수 후보 중 시총 500억 이하 종목 빼줘"
→ MCP가 inquire-price 호출 + 필터링

"어제 System B 가 진입했던 종목들의 다음날 종가 추적해줘"
→ MCP 가 inquire-daily 호출 + DataFrame
```

설정: `MCP/MCP AI 도구 연결 방법.md` 참고.

---

## 8. 파일 구조

```
final-trading/
├── strategy_builder/
│   ├── strategy/
│   │   └── strategy_11_kfive_quant.py        # 4-시스템 전략 본체
│   └── strategy_core/preset/
│       └── kfive_quant.py                    # Visual Builder 등록
│
├── backtester/kis_backtest/file/templates/
│   └── kfive_quant.kis.yaml                  # 백테스터 임포트용
│
├── examples_user/domestic_stock/
│   ├── kfive_quant_runner.py                 # KIS API 자동매매 러너
│   ├── kfive_universe.py                     # 1차 종목 필터 (김민겸)
│   └── kfive_risk.py                         # ATR 사이징 + 서킷 브레이커
│
└── docs/
    └── kfive_quant.md                        # 본 문서
```

---

## 9. 한계와 주의사항

- **데일리 인터벌**: 본 구현은 일봉 기반입니다. System B(변동성 돌파)는 본래 인트라데이
  데이터가 이상적이며, 일봉 종가 기준이라 다음날 갭에 노출됩니다. 5분봉 인트라데이
  버전은 별도 구현이 필요합니다.
- **유니버스 시드**: 35종목 시드는 시작점이며, KOSPI200 · KOSDAQ150 전 종목으로 확장
  하면 시그널 품질이 더 좋아집니다 (`stocks_info/` 의 종목 마스터 활용).
- **세금/수수료**: 백테스트와 실전 사이의 슬리피지·거래세(0.18%)·수수료를 항상 감안.
- **모의투자 ≠ 실전**: KIS 모의투자는 체결 알고리즘이 단순화되어 있어 실전 슬리피지를
  완전히 재현하지 않습니다. 인큐베이션 통과 후에도 실전은 **소액부터 시작**하세요.
- **본 코드는 참고용 샘플**입니다. 손익 책임은 사용자 본인에게 있습니다.
