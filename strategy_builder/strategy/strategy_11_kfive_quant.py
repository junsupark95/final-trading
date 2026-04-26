"""
Strategy 11: KFiveQuant — 5인 거장 융합 멀티시스템 전략

5인의 강점 융합:
- 다비드 트룰라스 빌라 (3x World Cup Day Trading Champion 2025): 복수의 비상관 시스템 운용
- 안드레아 웅거 (4x World Cup Trading Champion): KISS, 변동성 돌파 + RSI(2) 평균회귀
- 케빈 데이비 (Strategy Factory): 워크포워드/몬테카를로 + ATR 포지션 사이징
- 김민겸 (한국 퀀트): 한국형 가치/모멘텀 팩터 (외부 유니버스 필터에서 적용)
- 로스 카메론 (Warrior Trading): Bull Flag · Strong Close · 거래량 급증

4개의 비상관 서브시스템 운용 (단일 종목당 동시 평가):
    A) 팩터 모멘텀 (Trend Following)
       - 12M ROC > 0, 1M ROC > 0, Close > MA200
    B) 변동성 돌파 (Day Trading)
       - 시초가 + (전일 Range × K) 상향 돌파 + 거래량 급증 + Strong Close
    C) RSI(2) 평균회귀 (Mean Reversion)
       - Close > MA200 (장기 추세 필터) + RSI(2) < 10 + Close < BB Lower
    D) 52주 신고가 + VCP (Breakout)
       - 52주 신고가 갱신 + ATR 축소(변동성 압축) + 거래량 급증

각 서브시스템은 독립 점수(0~1)를 산출하고, 가장 강한 신호를 단일 Signal로 반환한다.
이렇게 하면 strategy_builder의 단일종목 generate_signal 인터페이스를 유지하면서도
한쪽 시장 환경(추세/박스/급락)에 의존하지 않는 견고한 신호를 얻는다.

Note:
    유니버스 1차 필터(저PER/저PBR/시총/거래대금)는 외부 러너에서 처리한다.
    본 전략은 필터링된 종목에 대해서만 시그널을 생성한다.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from core import data_fetcher, indicators
from core.signal import Action, Signal
from strategy.base_strategy import BaseStrategy


# ============================================================================
# 서브시스템 결과 데이터 클래스
# ============================================================================


@dataclass
class SubSignal:
    """단일 서브시스템 평가 결과"""
    system: str
    action: Action
    strength: float
    reason: str
    metrics: dict = field(default_factory=dict)

    @classmethod
    def hold(cls, system: str, reason: str = "조건 미충족") -> "SubSignal":
        return cls(system=system, action=Action.HOLD, strength=0.0, reason=reason)


# ============================================================================
# 메인 전략
# ============================================================================


class KFiveQuantStrategy(BaseStrategy):
    """5인 거장 융합 멀티시스템 전략

    Args:
        # System enable flags
        enable_a: 팩터 모멘텀 시스템 활성화 (default True)
        enable_b: 변동성 돌파 시스템 활성화 (default True)
        enable_c: RSI(2) 평균회귀 시스템 활성화 (default True)
        enable_d: 52주 신고가 VCP 시스템 활성화 (default True)

        # System A — 팩터 모멘텀
        a_long_period: 장기 모멘텀 기간 (default 252 ≈ 12M)
        a_short_period: 단기 모멘텀 기간 (default 20 ≈ 1M)
        a_trend_period: 추세 필터 MA 기간 (default 200)

        # System B — 변동성 돌파
        b_k: 변동성 돌파 K값 (default 0.5, Larry Williams)
        b_volume_mult: 거래량 배수 임계 (default 2.0)
        b_strong_close: 강한 종가 비율 (default 0.7)

        # System C — RSI(2) 평균회귀
        c_rsi_period: RSI 기간 (default 2)
        c_rsi_oversold: 과매도 임계 (default 10)
        c_bb_period: BB 기간 (default 20)
        c_bb_std: BB 표준편차 (default 2.0)
        c_trend_period: 장기 추세 MA 기간 (default 200)

        # System D — 52주 신고가 VCP
        d_lookback: 52주 신고가 lookback (default 252)
        d_base_period: 베이스 형성 기간 (default 40)
        d_volume_mult: 거래량 배수 (default 1.5)
        d_atr_contraction: ATR 축소 비율 (default 0.85)

        # Risk
        atr_period: ATR 기간 (default 14)
        min_strength: 최소 시그널 강도 (default 0.6)
    """

    def __init__(
        self,
        enable_a: bool = True,
        enable_b: bool = True,
        enable_c: bool = True,
        enable_d: bool = True,
        # System A
        a_long_period: int = 252,
        a_short_period: int = 20,
        a_trend_period: int = 200,
        # System B
        b_k: float = 0.5,
        b_volume_mult: float = 2.0,
        b_strong_close: float = 0.7,
        # System C
        c_rsi_period: int = 2,
        c_rsi_oversold: float = 10.0,
        c_bb_period: int = 20,
        c_bb_std: float = 2.0,
        c_trend_period: int = 200,
        # System D
        d_lookback: int = 252,
        d_base_period: int = 40,
        d_volume_mult: float = 1.5,
        d_atr_contraction: float = 0.85,
        # Risk / common
        atr_period: int = 14,
        min_strength: float = 0.6,
    ):
        self.enable_a = enable_a
        self.enable_b = enable_b
        self.enable_c = enable_c
        self.enable_d = enable_d

        self.a_long_period = a_long_period
        self.a_short_period = a_short_period
        self.a_trend_period = a_trend_period

        self.b_k = b_k
        self.b_volume_mult = b_volume_mult
        self.b_strong_close = b_strong_close

        self.c_rsi_period = c_rsi_period
        self.c_rsi_oversold = c_rsi_oversold
        self.c_bb_period = c_bb_period
        self.c_bb_std = c_bb_std
        self.c_trend_period = c_trend_period

        self.d_lookback = d_lookback
        self.d_base_period = d_base_period
        self.d_volume_mult = d_volume_mult
        self.d_atr_contraction = d_atr_contraction

        self.atr_period = atr_period
        self.min_strength = min_strength

    # ------------------------------------------------------------------
    # BaseStrategy 인터페이스
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "KFiveQuant"

    @property
    def required_days(self) -> int:
        # 가장 긴 lookback에 여유분(+30) 추가
        return max(
            self.a_long_period,
            self.a_trend_period,
            self.c_trend_period,
            self.d_lookback,
        ) + 30

    def generate_signal(self, stock_code: str, stock_name: str) -> Signal:
        """4개 서브시스템을 병렬 평가하고 가장 강한 시그널을 반환

        Returns:
            BUY: 한 시스템이라도 min_strength 이상 매수 신호 발생
            SELL: 보유 종목 청산 조건이 충족된 경우 (백테스터/러너에서 활용)
            HOLD: 어느 시스템도 트리거되지 않음
        """
        df = data_fetcher.get_daily_prices(stock_code, self.required_days)

        if df.empty or len(df) < 60:
            return Signal(
                stock_code=stock_code,
                stock_name=stock_name,
                action=Action.HOLD,
                strength=0.0,
                reason="데이터 부족",
            )

        sub_signals: list[SubSignal] = []

        if self.enable_a:
            sub_signals.append(self._evaluate_system_a(df))
        if self.enable_b:
            sub_signals.append(self._evaluate_system_b(df))
        if self.enable_c:
            sub_signals.append(self._evaluate_system_c(df))
        if self.enable_d:
            sub_signals.append(self._evaluate_system_d(df))

        # BUY 후보 중 가장 강한 시그널 선택 (Davey: 최고 신뢰 시스템 우선)
        buy_candidates = [s for s in sub_signals if s.action == Action.BUY]
        sell_candidates = [s for s in sub_signals if s.action == Action.SELL]

        # 매도(System B/C 익절·손절) 우선 — 리스크 관리는 항상 최우선
        if sell_candidates:
            best = max(sell_candidates, key=lambda s: s.strength)
            return self._build_signal(stock_code, stock_name, best, df)

        if buy_candidates:
            best = max(buy_candidates, key=lambda s: s.strength)
            if best.strength >= self.min_strength:
                return self._build_signal(stock_code, stock_name, best, df)

        # 모든 서브시스템 HOLD
        reasons = " | ".join(f"[{s.system}]{s.reason}" for s in sub_signals)
        return Signal(
            stock_code=stock_code,
            stock_name=stock_name,
            action=Action.HOLD,
            strength=0.0,
            reason=f"전 시스템 HOLD: {reasons}",
        )

    # ------------------------------------------------------------------
    # System A — 팩터 모멘텀 (김민겸 + 트룰라스)
    # ------------------------------------------------------------------

    def _evaluate_system_a(self, df: pd.DataFrame) -> SubSignal:
        """팩터 모멘텀: 장기/단기 모멘텀 양수 + 장기추세 위 → 매수"""
        if len(df) < self.a_long_period + 1:
            return SubSignal.hold("A:Momentum", "데이터 부족")

        long_roc = indicators.calc_roc(df, self.a_long_period).iloc[-1]
        short_roc = indicators.calc_roc(df, self.a_short_period).iloc[-1]
        ma_trend = indicators.calc_ma(df, self.a_trend_period).iloc[-1]
        close = df["close"].iloc[-1]

        if pd.isna(long_roc) or pd.isna(short_roc) or pd.isna(ma_trend):
            return SubSignal.hold("A:Momentum", "지표 NaN")

        if not (long_roc > 0 and short_roc > 0 and close > ma_trend):
            return SubSignal.hold(
                "A:Momentum",
                f"조건 미충족 (12M={long_roc:.1f}%, 1M={short_roc:.1f}%)",
            )

        # 강도: 장기 모멘텀 정규화 (30% = 0.5, 60% = 1.0 클램프)
        strength = min(1.0, 0.5 + (long_roc / 100) * 0.83)
        strength = max(0.6, strength)

        return SubSignal(
            system="A:Momentum",
            action=Action.BUY,
            strength=strength,
            reason=f"팩터모멘텀 12M={long_roc:.1f}% 1M={short_roc:.1f}% Close>MA{self.a_trend_period}",
            metrics={"long_roc": float(long_roc), "short_roc": float(short_roc)},
        )

    # ------------------------------------------------------------------
    # System B — 변동성 돌파 (Unger + Cameron)
    # ------------------------------------------------------------------

    def _evaluate_system_b(self, df: pd.DataFrame) -> SubSignal:
        """변동성 돌파: 시가 + (전일 Range × K) 상향 돌파
        + 거래량 폭증 + 강한 종가 → 매수 (당일 청산 가정)
        """
        if len(df) < 21:
            return SubSignal.hold("B:VolBreak", "데이터 부족")

        prev = df.iloc[-2]
        today = df.iloc[-1]

        prev_range = float(prev["high"]) - float(prev["low"])
        if prev_range <= 0:
            return SubSignal.hold("B:VolBreak", "전일 Range=0")

        breakout_price = float(today["open"]) + self.b_k * prev_range
        close = float(today["close"])

        if close <= breakout_price:
            return SubSignal.hold(
                "B:VolBreak",
                f"종가 {close:.0f} ≤ 돌파선 {breakout_price:.0f}",
            )

        vol_ma = indicators.calc_volume_ma(df, 20).iloc[-1]
        if pd.isna(vol_ma) or vol_ma <= 0:
            return SubSignal.hold("B:VolBreak", "거래량 MA NaN")

        vol_ratio = float(today["volume"]) / float(vol_ma)
        if vol_ratio < self.b_volume_mult:
            return SubSignal.hold(
                "B:VolBreak",
                f"거래량 {vol_ratio:.2f}x < {self.b_volume_mult}x",
            )

        strong_close = indicators.calc_strong_close_ratio(df)
        if strong_close is None or strong_close < self.b_strong_close:
            return SubSignal.hold(
                "B:VolBreak",
                f"강한종가 {strong_close} < {self.b_strong_close}",
            )

        # 강도: 거래량 배수 + 강한 종가 결합 (max 1.0)
        strength = min(1.0, 0.6 + (vol_ratio - self.b_volume_mult) * 0.1 + (strong_close - 0.7) * 0.5)

        return SubSignal(
            system="B:VolBreak",
            action=Action.BUY,
            strength=strength,
            reason=f"변동성돌파 K={self.b_k} Vol={vol_ratio:.1f}x 강한종가={strong_close:.2f}",
            metrics={
                "breakout_price": breakout_price,
                "vol_ratio": vol_ratio,
                "strong_close": strong_close,
            },
        )

    # ------------------------------------------------------------------
    # System C — RSI(2) 평균회귀 (Connors / Unger)
    # ------------------------------------------------------------------

    def _evaluate_system_c(self, df: pd.DataFrame) -> SubSignal:
        """장기 추세 위에서의 단기 과매도 진입
        진입: Close > MA(200), RSI(2) < 10, Close < BB lower
        청산: RSI(2) > 70 (별도 신호로 SELL 반환)
        """
        if len(df) < self.c_trend_period + 1:
            return SubSignal.hold("C:RSI2-MR", "데이터 부족")

        close = float(df["close"].iloc[-1])
        ma_trend = indicators.calc_ma(df, self.c_trend_period).iloc[-1]
        rsi = indicators.calc_rsi(df, self.c_rsi_period).iloc[-1]
        bb_lower = indicators.calc_bb_lower(df, self.c_bb_period, self.c_bb_std).iloc[-1]

        if pd.isna(ma_trend) or pd.isna(rsi) or pd.isna(bb_lower):
            return SubSignal.hold("C:RSI2-MR", "지표 NaN")

        # 청산 신호 우선: RSI(2) > 70
        if rsi > 70:
            return SubSignal(
                system="C:RSI2-MR",
                action=Action.SELL,
                strength=0.7,
                reason=f"RSI(2) 청산 RSI={rsi:.1f}",
                metrics={"rsi": float(rsi)},
            )

        # 진입 조건
        if close < ma_trend:
            return SubSignal.hold(
                "C:RSI2-MR",
                f"장기추세 미달 Close<{self.c_trend_period}일MA",
            )
        if rsi >= self.c_rsi_oversold:
            return SubSignal.hold("C:RSI2-MR", f"RSI(2) {rsi:.1f} 과매도 미진입")
        if close >= bb_lower:
            return SubSignal.hold("C:RSI2-MR", f"BB하단 미돌파 Close≥Lower")

        # 강도: RSI 깊이 + BB 이탈 깊이
        rsi_depth = (self.c_rsi_oversold - rsi) / self.c_rsi_oversold
        bb_depth = max(0.0, (bb_lower - close) / bb_lower)
        strength = min(1.0, 0.6 + rsi_depth * 0.25 + bb_depth * 5.0)

        return SubSignal(
            system="C:RSI2-MR",
            action=Action.BUY,
            strength=strength,
            reason=f"평균회귀 RSI(2)={rsi:.1f}<{self.c_rsi_oversold} Close<BB하단",
            metrics={
                "rsi": float(rsi),
                "bb_lower": float(bb_lower),
                "ma_trend": float(ma_trend),
            },
        )

    # ------------------------------------------------------------------
    # System D — 52주 신고가 + VCP (Minervini-style, Davey-validated)
    # ------------------------------------------------------------------

    def _evaluate_system_d(self, df: pd.DataFrame) -> SubSignal:
        """52주 신고가 돌파 + 변동성 압축(VCP) + 거래량 급증"""
        if len(df) < self.d_lookback + 1:
            return SubSignal.hold("D:VCP-52W", "데이터 부족")

        today_close = float(df["close"].iloc[-1])
        today_high = float(df["high"].iloc[-1])
        today_volume = float(df["volume"].iloc[-1])

        # 52주 신고가 갱신 (오늘 high가 lookback 최고)
        prior_high = float(df["high"].iloc[-self.d_lookback - 1 : -1].max())
        if today_high < prior_high:
            return SubSignal.hold(
                "D:VCP-52W",
                f"52주 신고가 미달 ({today_high:.0f} < {prior_high:.0f})",
            )

        # ATR 축소 (변동성 압축)
        atr_short = indicators.calc_atr(df, self.atr_period).iloc[-1]
        atr_long = indicators.calc_atr(df, self.atr_period * 4).iloc[-1]
        if pd.isna(atr_short) or pd.isna(atr_long) or atr_long <= 0:
            return SubSignal.hold("D:VCP-52W", "ATR NaN")

        atr_ratio = atr_short / atr_long
        if atr_ratio > self.d_atr_contraction:
            return SubSignal.hold(
                "D:VCP-52W",
                f"변동성 압축 부족 ATR ratio={atr_ratio:.2f} > {self.d_atr_contraction}",
            )

        # 거래량 급증
        vol_ma = indicators.calc_volume_ma(df, 50).iloc[-1]
        if pd.isna(vol_ma) or vol_ma <= 0:
            return SubSignal.hold("D:VCP-52W", "거래량 MA NaN")
        vol_ratio = today_volume / vol_ma
        if vol_ratio < self.d_volume_mult:
            return SubSignal.hold(
                "D:VCP-52W",
                f"거래량 부족 {vol_ratio:.2f}x < {self.d_volume_mult}x",
            )

        # 베이스 견고성: base_period 동안 가격 변동폭(High-Low)/Close < 25%
        base_high = float(df["high"].iloc[-self.d_base_period : -1].max())
        base_low = float(df["low"].iloc[-self.d_base_period : -1].min())
        base_range_pct = (base_high - base_low) / today_close
        if base_range_pct > 0.30:
            return SubSignal.hold(
                "D:VCP-52W",
                f"베이스 깊이 과다 {base_range_pct:.1%}",
            )

        # 강도: ATR 압축 강도 + 거래량
        contraction_strength = (self.d_atr_contraction - atr_ratio) / self.d_atr_contraction
        strength = min(1.0, 0.7 + contraction_strength * 0.2 + (vol_ratio - 1.5) * 0.05)

        return SubSignal(
            system="D:VCP-52W",
            action=Action.BUY,
            strength=strength,
            reason=(
                f"52주 신고가 + VCP ATR압축={atr_ratio:.2f} "
                f"Vol={vol_ratio:.1f}x Base={base_range_pct:.1%}"
            ),
            metrics={
                "prior_high": prior_high,
                "atr_ratio": float(atr_ratio),
                "vol_ratio": vol_ratio,
                "base_range_pct": base_range_pct,
            },
        )

    # ------------------------------------------------------------------
    # 헬퍼
    # ------------------------------------------------------------------

    def _build_signal(
        self,
        stock_code: str,
        stock_name: str,
        sub: SubSignal,
        df: pd.DataFrame,
    ) -> Signal:
        """SubSignal → Signal 변환 (target_price는 ATR 기반 안전마진 적용)"""
        close = int(df["close"].iloc[-1])
        atr = indicators.calc_atr(df, self.atr_period).iloc[-1]

        # System B(데이트레이딩)는 시장가 → target_price=None
        # 그 외 시스템은 지정가(현재가 + 0.3*ATR 안전마진)
        target_price: Optional[int] = None
        if sub.action == Action.BUY and sub.system != "B:VolBreak":
            if not pd.isna(atr) and atr > 0:
                # 추격매수 방지: 현재가 또는 그보다 살짝 아래 지정가
                target_price = max(close - int(0.3 * atr), close - int(close * 0.005))
            else:
                target_price = close

        return Signal(
            stock_code=stock_code,
            stock_name=stock_name,
            action=sub.action,
            strength=sub.strength,
            reason=f"[{sub.system}] {sub.reason}",
            target_price=target_price,
        )
