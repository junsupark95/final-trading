"""KFiveQuant 종목 유니버스 빌더 (김민겸 팩터 1차 필터)

KOSPI200 / KOSDAQ150 우량주를 베이스로 한국형 가치·퀄리티·유동성 필터 적용.
- 시가총액: 300억 ~ 5조원 (유동성 + 성장 여지)
- 일평균 거래대금: ≥ 30억 (소형주 함정 회피)
- 관리·투자주의·거래정지 종목 제외
- (가능 시) PER ≤ 20, PBR ≤ 2, ROE ≥ 5%

KIS Open API:
- inquire-price (FHKST01010100): 현재가/PER/PBR
- inquire-daily-itemchartprice (FHKST03010100): 일봉 (거래대금 산출)
- finance-ratio (FHKST66430300): 재무비율 (장기적 ROE 등)

Note:
    모든 API 호출은 호출제한(EGW00201) 회피를 위해 0.2 ~ 0.5s 슬립을 둔다.
    유니버스 갱신은 하루 한 번(장 시작 전)만 수행하는 것을 권장.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

# 상위 디렉터리 import 경로 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

import kis_auth as ka  # noqa: E402

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# 기본 시드 유니버스 (KOSPI 시총 상위 + 인기 KOSDAQ 일부)
# 사용자가 직접 확장하거나 stocks_info/ 의 종목 마스터로 대체 가능
# ----------------------------------------------------------------------------

DEFAULT_SEED_UNIVERSE: list[tuple[str, str]] = [
    # KOSPI 시총 상위 (필터를 통해 자동 제외/통과)
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"),
    ("005380", "현대차"),
    ("005490", "POSCO홀딩스"),
    ("000270", "기아"),
    ("068270", "셀트리온"),
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("005935", "삼성전자우"),
    ("105560", "KB금융"),
    ("055550", "신한지주"),
    ("051910", "LG화학"),
    ("006400", "삼성SDI"),
    ("028260", "삼성물산"),
    ("012330", "현대모비스"),
    ("003670", "포스코퓨처엠"),
    ("066570", "LG전자"),
    ("032830", "삼성생명"),
    ("017670", "SK텔레콤"),
    ("015760", "한국전력"),
    ("034730", "SK"),
    ("018260", "삼성에스디에스"),
    ("009150", "삼성전기"),
    # KOSDAQ 대표주
    ("247540", "에코프로비엠"),
    ("086520", "에코프로"),
    ("091990", "셀트리온헬스케어"),
    ("196170", "알테오젠"),
    ("277810", "레인보우로보틱스"),
    ("293490", "카카오게임즈"),
    ("041510", "에스엠"),
    ("058470", "리노공업"),
    ("357780", "솔브레인"),
    ("095340", "ISC"),
]


# ----------------------------------------------------------------------------
# Filter config
# ----------------------------------------------------------------------------


@dataclass
class UniverseFilter:
    """유니버스 1차 필터 설정 (김민겸 팩터)"""
    min_market_cap_won: int = 30_000_000_000        # 300억
    max_market_cap_won: int = 5_000_000_000_000     # 5조
    min_avg_trade_value_won: int = 3_000_000_000    # 30억 (일평균 거래대금)
    avg_volume_lookback: int = 20                   # 거래대금 평균 lookback
    max_per: float = 20.0
    max_pbr: float = 2.0
    require_per_pbr: bool = False                   # 일부 종목 PER/PBR 미공개 → 기본 off
    skip_codes: tuple[str, ...] = ()                # 제외할 종목코드

    def applies_per_pbr(self) -> bool:
        return self.require_per_pbr


# ----------------------------------------------------------------------------
# Universe builder
# ----------------------------------------------------------------------------


@dataclass
class UniverseBuilder:
    seed: list[tuple[str, str]] = field(default_factory=lambda: list(DEFAULT_SEED_UNIVERSE))
    filter: UniverseFilter = field(default_factory=UniverseFilter)
    sleep_sec: float = 0.25

    def build(self) -> pd.DataFrame:
        """필터 통과 종목 DataFrame 반환

        Columns:
            stock_code, stock_name, price, market_cap, per, pbr,
            avg_trade_value, status_ok
        """
        rows: list[dict] = []
        for code, name in self.seed:
            if code in self.filter.skip_codes:
                continue
            try:
                snapshot = self._fetch_price_snapshot(code)
            except Exception as e:
                logger.warning(f"[{code}] 시세 조회 실패: {e}")
                continue

            if not snapshot:
                continue

            avg_value = self._fetch_avg_trade_value(code)

            row = {
                "stock_code": code,
                "stock_name": name,
                "price": snapshot.get("price", 0),
                "market_cap": snapshot.get("market_cap", 0),
                "per": snapshot.get("per", 0.0),
                "pbr": snapshot.get("pbr", 0.0),
                "avg_trade_value": avg_value,
                "status_ok": snapshot.get("status_ok", True),
            }
            rows.append(row)
            time.sleep(self.sleep_sec)

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        return self._apply_filter(df)

    # ------------------------------------------------------------------
    # KIS API 호출 (inquire-price)
    # ------------------------------------------------------------------

    def _fetch_price_snapshot(self, stock_code: str) -> Optional[dict]:
        """현재가 + PER/PBR + 시총 + 정상거래여부 단발 조회"""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        res = ka._url_fetch(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", "", params,
        )
        if not res.isOK():
            return None

        out = res.getBody().output
        try:
            price = int(out.get("stck_prpr", 0))
            per = float(out.get("per", 0) or 0)
            pbr = float(out.get("pbr", 0) or 0)
            # 시가총액(억 단위 문자열) → 원
            market_cap_eok = float(out.get("hts_avls", 0) or 0)
            market_cap = int(market_cap_eok * 100_000_000)
            # 종목 상태 (관리/투자주의/거래정지)
            status_code = out.get("iscd_stat_cls_code", "")
            # "00" 정상, 그 외(51 관리, 52 거래정지 등)는 제외
            status_ok = status_code in ("", "00", "0", "55")  # 55=정상권
            return {
                "price": price,
                "per": per,
                "pbr": pbr,
                "market_cap": market_cap,
                "status_ok": status_ok,
            }
        except Exception as e:
            logger.warning(f"[{stock_code}] snapshot 파싱 실패: {e}")
            return None

    def _fetch_avg_trade_value(self, stock_code: str) -> int:
        """최근 N일 평균 거래대금(원) 추정 — 일봉 종가 × 거래량"""
        from datetime import datetime, timedelta

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=self.filter.avg_volume_lookback + 10)).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        res = ka._url_fetch(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100", "", params,
        )
        if not res.isOK():
            return 0

        rows = res.getBody().output2 or []
        if not rows:
            return 0

        df = pd.DataFrame(rows).head(self.filter.avg_volume_lookback)
        try:
            df["close"] = pd.to_numeric(df.get("stck_clpr"), errors="coerce")
            df["volume"] = pd.to_numeric(df.get("acml_vol"), errors="coerce")
            df["trade_value"] = df["close"] * df["volume"]
            return int(df["trade_value"].mean(skipna=True) or 0)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # 필터 적용
    # ------------------------------------------------------------------

    def _apply_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        f = self.filter
        before = len(df)

        df = df[df["status_ok"]]
        df = df[(df["market_cap"] >= f.min_market_cap_won) & (df["market_cap"] <= f.max_market_cap_won)]
        df = df[df["avg_trade_value"] >= f.min_avg_trade_value_won]

        if f.applies_per_pbr():
            # PER 양수만 (적자 기업 제외), PBR 양수만
            df = df[(df["per"] > 0) & (df["per"] <= f.max_per)]
            df = df[(df["pbr"] > 0) & (df["pbr"] <= f.max_pbr)]

        df = df.reset_index(drop=True)
        logger.info(f"[유니버스] {before}종목 → {len(df)}종목 (필터 적용 후)")
        return df


def build_default_universe(env_dv: str = "vps") -> pd.DataFrame:
    """기본 유니버스 빌드 (단순 진입점)"""
    if env_dv not in ("prod", "vps"):
        raise ValueError(f"env_dv must be prod or vps, got {env_dv}")
    return UniverseBuilder().build()
