"""KFiveQuant 리스크 매니저 (Davey: ATR 사이징 + 서킷 브레이커)

- ATR 기반 종목당 리스크 포지션 사이징
- 일/월 손실 한도 도달 시 거래 중단 (서킷 브레이커)
- 시스템별 동시 보유 한도
- 종목당 최대 비중 한도

Davey 방식:
    position_size_won = (capital * risk_per_trade) / (atr_multiplier * ATR / price)
또는 단순화:
    quantity = floor((capital * risk_per_trade) / (atr_stop_distance))
where atr_stop_distance = atr_multiplier * ATR (원 단위)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------


@dataclass
class RiskConfig:
    """리스크 한도 설정 (Davey + Unger 합본)"""
    risk_per_trade: float = 0.005        # 종목당 자본의 0.5% 리스크
    atr_stop_multiplier: float = 1.5     # ATR × 1.5 = 손절 거리
    max_position_pct: float = 0.10       # 종목당 최대 비중 10%
    max_concurrent_positions: int = 10   # 동시 보유 종목 수
    max_per_system: int = 3              # 시스템별 동시 보유
    daily_loss_limit_pct: float = 0.02   # 일 -2% 시 중단
    monthly_loss_limit_pct: float = 0.06 # 월 -6% 시 중단
    cooldown_after_breach_days: int = 1  # 한도 위반 후 쿨다운


# ----------------------------------------------------------------------------
# State (영속성)
# ----------------------------------------------------------------------------


@dataclass
class RiskState:
    """일/월 손익 상태 (JSON으로 persistence)"""
    date: str = ""
    starting_capital: int = 0
    realized_pnl_today: int = 0
    realized_pnl_month: int = 0
    unrealized_pnl: int = 0
    breach_until: Optional[str] = None  # ISO date
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Risk manager
# ----------------------------------------------------------------------------


class KFiveRiskManager:
    """ATR 사이징 + 서킷 브레이커 통합 리스크 매니저"""

    def __init__(
        self,
        capital: int,
        config: Optional[RiskConfig] = None,
        state_file: Optional[Path] = None,
    ):
        """
        Args:
            capital: 운용 자본 (원). 서킷 브레이커 기준점.
            config: 리스크 설정. None이면 기본값 사용.
            state_file: 상태 영속화 JSON 경로. None이면 메모리만 사용.
        """
        self.capital = capital
        self.config = config or RiskConfig()
        self.state_file = state_file
        self.state = self._load_state()

        if not self.state.date:
            today = datetime.now().strftime("%Y%m%d")
            self.state.date = today
            self.state.starting_capital = capital
            self._save_state()

    # ------------------------------------------------------------------
    # ATR 포지션 사이징 (Davey)
    # ------------------------------------------------------------------

    def position_size(
        self,
        price: int,
        atr: float,
        system: Optional[str] = None,
    ) -> int:
        """ATR 기반 포지션 사이징 → 매수 수량 반환

        Args:
            price: 현재가 (원)
            atr: ATR (원)
            system: 호출 시스템 (로깅용)

        Returns:
            매수 수량 (정수). 리스크 한도 위반 시 0.
        """
        if price <= 0:
            return 0

        if atr <= 0:
            # ATR 미계산 시 가격의 2%를 손절 거리로 가정
            atr_stop_distance = max(price * 0.02, 1)
        else:
            atr_stop_distance = atr * self.config.atr_stop_multiplier

        # 종목당 리스크 금액
        risk_amount = self.capital * self.config.risk_per_trade

        # 수량 = 리스크금액 / 손절거리(원)
        qty_by_risk = int(risk_amount // atr_stop_distance)

        # 최대 비중 제한
        max_position_value = self.capital * self.config.max_position_pct
        qty_by_position = int(max_position_value // price)

        qty = max(0, min(qty_by_risk, qty_by_position))

        if qty == 0:
            logger.info(
                f"[Risk:{system}] 수량 0 — risk={risk_amount:,.0f} "
                f"atr_stop={atr_stop_distance:.0f} max_pos={max_position_value:,.0f}"
            )
        return qty

    def stop_loss_price(self, entry_price: int, atr: float) -> int:
        """ATR 기반 손절가 (1.5 × ATR 아래)"""
        if atr <= 0:
            return int(entry_price * 0.95)
        return int(entry_price - atr * self.config.atr_stop_multiplier)

    # ------------------------------------------------------------------
    # 서킷 브레이커
    # ------------------------------------------------------------------

    def can_trade(self) -> tuple[bool, str]:
        """오늘 거래 가능 여부 확인 (일/월 손실 한도 체크)

        Returns:
            (allowed, reason)
        """
        # 쿨다운 체크
        if self.state.breach_until:
            if datetime.now().strftime("%Y%m%d") <= self.state.breach_until:
                return False, f"쿨다운 중 (until {self.state.breach_until})"

        # 일 손실 한도
        daily_limit = -int(self.capital * self.config.daily_loss_limit_pct)
        if self.state.realized_pnl_today + self.state.unrealized_pnl <= daily_limit:
            self._set_breach(days=self.config.cooldown_after_breach_days)
            return False, f"일 손실 한도 초과 ({self.state.realized_pnl_today:,})"

        # 월 손실 한도
        monthly_limit = -int(self.capital * self.config.monthly_loss_limit_pct)
        if self.state.realized_pnl_month <= monthly_limit:
            self._set_breach(days=self.config.cooldown_after_breach_days * 5)
            return False, f"월 손실 한도 초과 ({self.state.realized_pnl_month:,})"

        return True, "OK"

    def can_open_new_position(
        self,
        current_holdings_count: int,
        system_holdings_count: int,
    ) -> tuple[bool, str]:
        """신규 포지션 개시 가능 여부"""
        if current_holdings_count >= self.config.max_concurrent_positions:
            return False, f"동시 보유 한도 ({current_holdings_count}/{self.config.max_concurrent_positions})"
        if system_holdings_count >= self.config.max_per_system:
            return False, f"시스템 보유 한도 ({system_holdings_count}/{self.config.max_per_system})"
        return True, "OK"

    # ------------------------------------------------------------------
    # P&L 업데이트
    # ------------------------------------------------------------------

    def record_realized_pnl(self, pnl: int) -> None:
        self.state.realized_pnl_today += pnl
        self.state.realized_pnl_month += pnl
        self._save_state()

    def update_unrealized(self, total_unrealized: int) -> None:
        self.state.unrealized_pnl = total_unrealized
        self._save_state()

    def reset_daily(self) -> None:
        """매일 장 시작 시 호출 (일 손익 리셋)"""
        today = datetime.now().strftime("%Y%m%d")
        if self.state.date != today:
            # 월 변경 시 월 손익도 리셋
            if self.state.date[:6] != today[:6]:
                self.state.realized_pnl_month = 0
            self.state.realized_pnl_today = 0
            self.state.date = today
            self._save_state()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _set_breach(self, days: int) -> None:
        from datetime import timedelta
        until = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")
        self.state.breach_until = until
        self.state.notes.append(f"breach@{datetime.now().isoformat()} until={until}")
        self._save_state()

    def _load_state(self) -> RiskState:
        if not self.state_file or not self.state_file.exists():
            return RiskState()
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return RiskState(**data)
        except Exception as e:
            logger.warning(f"리스크 상태 로드 실패 — 신규 생성: {e}")
            return RiskState()

    def _save_state(self) -> None:
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(asdict(self.state), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"리스크 상태 저장 실패: {e}")
