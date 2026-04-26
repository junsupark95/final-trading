"""KFiveQuant 자동매매 러너 — 한국투자증권 Open API

✦ 5인 거장 융합 멀티시스템 자동매매 실행 스크립트 ✦

Usage (모의투자, 권장 입문):
    cd examples_user/domestic_stock
    python kfive_quant_runner.py --env vps --capital 10000000 --dry-run

Usage (실전, 모의투자 인큐베이션 후):
    python kfive_quant_runner.py --env prod --capital 50000000

Pipeline:
    1) KIS 인증 (vps/prod 선택)
    2) 유니버스 빌드 (김민겸 팩터 1차 필터)
    3) 보유 종목 잔고 점검 + 청산 신호 처리 (System C 익절, ATR 손절)
    4) 신규 매수 후보 평가 (KFiveQuantStrategy 4-시스템 병렬)
    5) 리스크 게이트 통과 → ATR 사이징 → order_cash 발주
    6) 일별 요약 로그 + 리스크 상태 영속화

본 러너는:
- `kis_devlp.yaml` 의 모의투자 키(paper_app/paper_sec)로 vps 환경 동작
- 안전장치: 일 -2%, 월 -6% 서킷 브레이커
- --dry-run 옵션으로 주문 발송 없이 시그널/사이징만 검증

Reference:
- KIS Developers: https://apiportal.koreainvestment.com/apiservice
- domestic_stock_examples.py 의 order_cash() 패턴 준수
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# 경로 설정 — examples_user 와 strategy_builder 양쪽 import
EXAMPLES_USER_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = EXAMPLES_USER_ROOT.parent
sys.path.append(str(EXAMPLES_USER_ROOT))                              # kis_auth
sys.path.append(str(EXAMPLES_USER_ROOT / "domestic_stock"))           # kfive_*
sys.path.append(str(PROJECT_ROOT / "strategy_builder"))               # core, strategy
sys.path.append(str(PROJECT_ROOT / "strategy_builder" / "strategy"))  # strategy_11

import kis_auth as ka  # noqa: E402
from domestic_stock_functions import order_cash  # noqa: E402

from core import data_fetcher, indicators  # noqa: E402
from core.signal import Action, Signal  # noqa: E402
from strategy.strategy_11_kfive_quant import KFiveQuantStrategy  # noqa: E402

from kfive_risk import KFiveRiskManager, RiskConfig  # noqa: E402
from kfive_universe import UniverseBuilder, UniverseFilter  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kfive_runner")

STATE_DIR = Path.home() / "KIS" / "kfive_state"


# ============================================================================
# Runner config
# ============================================================================


@dataclass
class RunnerConfig:
    env: str = "vps"                  # "vps" 모의투자 / "prod" 실전
    product: str = "01"               # 종합계좌
    capital: int = 10_000_000         # 운용 자본 (원)
    dry_run: bool = False             # True면 주문 발송 없이 시그널만 출력
    universe_csv: Optional[Path] = None  # 종목 리스트 CSV (stock_code,stock_name)
    require_factor_filter: bool = False  # PER/PBR 필터 강제 여부

    @property
    def env_dv(self) -> str:
        # order_cash() 는 "real"/"demo", strategy_builder data_fetcher 는 "real"/"demo"
        return "real" if self.env == "prod" else "demo"

    @property
    def state_file(self) -> Path:
        return STATE_DIR / f"risk_state_{self.env}.json"


# ============================================================================
# Runner
# ============================================================================


class KFiveQuantRunner:
    """KFiveQuant 통합 자동매매 러너"""

    def __init__(self, cfg: RunnerConfig):
        self.cfg = cfg
        self.strategy = KFiveQuantStrategy()
        self.risk = KFiveRiskManager(
            capital=cfg.capital,
            config=RiskConfig(),
            state_file=cfg.state_file,
        )
        self._trenv = None

    # ------------------------------------------------------------------
    # 1. 인증
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        logger.info(f"KIS 인증 중 — env={self.cfg.env} product={self.cfg.product}")
        ka.auth(svr=self.cfg.env, product=self.cfg.product)
        self._trenv = ka.getTREnv()
        if not self._trenv or not getattr(self._trenv, "my_url", ""):
            raise RuntimeError("KIS 인증 실패 — kis_devlp.yaml 키/계좌 점검 필요")
        logger.info(f"인증 완료 — 계좌={self._trenv.my_acct}-{self._trenv.my_prod}")

    # ------------------------------------------------------------------
    # 2. 유니버스
    # ------------------------------------------------------------------

    def build_universe(self) -> pd.DataFrame:
        """1차 필터 통과 종목 목록"""
        if self.cfg.universe_csv and self.cfg.universe_csv.exists():
            logger.info(f"유니버스 CSV 로드: {self.cfg.universe_csv}")
            df = pd.read_csv(self.cfg.universe_csv, dtype={"stock_code": str})
            return df

        logger.info("유니버스 빌드 (KIS API 시세 조회)")
        builder = UniverseBuilder(
            filter=UniverseFilter(require_per_pbr=self.cfg.require_factor_filter)
        )
        df = builder.build()
        logger.info(f"유니버스 {len(df)}종목 확정")
        return df

    # ------------------------------------------------------------------
    # 3. 보유 종목 청산 평가
    # ------------------------------------------------------------------

    def evaluate_exits(self) -> list[Signal]:
        """보유 종목에 대해 청산 시그널 평가"""
        holdings = data_fetcher.get_holdings(env_dv=self.cfg.env_dv)
        if holdings.empty:
            logger.info("보유 종목 없음")
            return []

        sells: list[Signal] = []
        for _, row in holdings.iterrows():
            code = str(row["stock_code"])
            name = str(row["stock_name"])
            avg_price = int(row["avg_price"])
            current = int(row["current_price"])

            # 1) ATR 손절 체크
            df = data_fetcher.get_daily_prices(code, 60, env_dv=self.cfg.env_dv)
            if not df.empty and len(df) >= 30:
                atr = indicators.calc_atr(df, 14).iloc[-1]
                if not pd.isna(atr) and atr > 0:
                    stop_price = self.risk.stop_loss_price(avg_price, float(atr))
                    if current <= stop_price:
                        sells.append(Signal(
                            stock_code=code, stock_name=name,
                            action=Action.SELL, strength=1.0,
                            reason=f"ATR 손절 (현재가 {current} ≤ 손절가 {stop_price})",
                        ))
                        continue

            # 2) 단순 % 손절 (15%) — 안전망
            pl_rate = float(row.get("profit_rate", 0))
            if pl_rate <= -15.0:
                sells.append(Signal(
                    stock_code=code, stock_name=name,
                    action=Action.SELL, strength=1.0,
                    reason=f"-15% 안전망 손절 (수익률 {pl_rate:.2f}%)",
                ))
                continue

            # 3) 전략 청산 신호 (System C: RSI(2) > 70 등)
            sig = self.strategy.generate_signal(code, name)
            if sig.action == Action.SELL and sig.is_actionable():
                sells.append(sig)

        logger.info(f"청산 후보 {len(sells)}건")
        return sells

    # ------------------------------------------------------------------
    # 4. 신규 매수 평가
    # ------------------------------------------------------------------

    def evaluate_buys(self, universe: pd.DataFrame, current_holdings_codes: set[str]) -> list[Signal]:
        """유니버스에 대해 매수 시그널 평가"""
        buys: list[Signal] = []
        for _, row in universe.iterrows():
            code = str(row["stock_code"])
            if code in current_holdings_codes:
                continue
            name = str(row.get("stock_name", code))
            sig = self.strategy.generate_signal(code, name)
            if sig.action == Action.BUY and sig.is_actionable():
                buys.append(sig)

        # 강도 내림차순 정렬
        buys.sort(key=lambda s: s.strength, reverse=True)
        logger.info(f"매수 후보 {len(buys)}건 (정렬: 시그널 강도 내림차순)")
        for s in buys[:10]:
            logger.info(f"  • {s}")
        return buys

    # ------------------------------------------------------------------
    # 5. 발주
    # ------------------------------------------------------------------

    def execute_sell(self, signal: Signal, quantity: int) -> bool:
        """시그널 → 매도 주문 (청산은 항상 시장가로 빠르게 처리)"""
        ord_dvsn, ord_unpr = "01", "0"  # 시장가
        logger.info(f"[SELL] {signal.stock_name}({signal.stock_code}) qty={quantity} {signal.reason}")
        if self.cfg.dry_run:
            return True

        try:
            df = order_cash(
                env_dv=self.cfg.env_dv,
                ord_dv="sell",
                cano=self._trenv.my_acct,
                acnt_prdt_cd=self._trenv.my_prod,
                pdno=signal.stock_code,
                ord_dvsn=ord_dvsn,
                ord_qty=str(quantity),
                ord_unpr=ord_unpr,
                excg_id_dvsn_cd="KRX",
            )
            if df is None or df.empty:
                logger.warning(f"매도 주문 실패: {signal}")
                return False
            data_fetcher.clear_balance_cache()
            return True
        except Exception as e:
            logger.error(f"매도 주문 예외: {e}")
            return False

    def execute_buy(self, signal: Signal, quantity: int) -> bool:
        ord_dvsn, ord_unpr = ("01", "0") if signal.is_strong() else ("00", str(signal.target_price or 0))
        logger.info(
            f"[BUY] {signal.stock_name}({signal.stock_code}) qty={quantity} "
            f"price={ord_unpr} ord_dvsn={ord_dvsn} | {signal.reason}"
        )
        if self.cfg.dry_run:
            return True

        try:
            df = order_cash(
                env_dv=self.cfg.env_dv,
                ord_dv="buy",
                cano=self._trenv.my_acct,
                acnt_prdt_cd=self._trenv.my_prod,
                pdno=signal.stock_code,
                ord_dvsn=ord_dvsn,
                ord_qty=str(quantity),
                ord_unpr=ord_unpr,
                excg_id_dvsn_cd="KRX",
            )
            if df is None or df.empty:
                logger.warning(f"매수 주문 실패: {signal}")
                return False
            data_fetcher.clear_balance_cache()
            return True
        except Exception as e:
            logger.error(f"매수 주문 예외: {e}")
            return False

    # ------------------------------------------------------------------
    # 6. 메인 루프
    # ------------------------------------------------------------------

    def run_once(self) -> None:
        """하루 1회 실행되는 메인 시퀀스"""
        self.authenticate()
        self.risk.reset_daily()

        ok, reason = self.risk.can_trade()
        if not ok:
            logger.warning(f"⛔ 서킷 브레이커 작동 — 거래 중단: {reason}")
            return

        # === 1. 보유 잔고 ===
        holdings = data_fetcher.get_holdings(env_dv=self.cfg.env_dv)
        holding_codes: set[str] = set(holdings["stock_code"].astype(str)) if not holdings.empty else set()
        logger.info(f"보유 종목 {len(holding_codes)}개")

        # === 2. 청산 처리 ===
        sells = self.evaluate_exits()
        for sig in sells:
            qty = int(holdings.loc[holdings["stock_code"] == sig.stock_code, "quantity"].iloc[0]) \
                if sig.stock_code in holding_codes else 0
            if qty > 0:
                self.execute_sell(sig, qty)

        # === 3. 신규 매수 후보 ===
        universe = self.build_universe()
        if universe.empty:
            logger.warning("유니버스 비어있음 — 매수 단계 스킵")
            return

        buys = self.evaluate_buys(universe, holding_codes)

        # === 4. 발주 (상위 N개, 리스크 게이트 통과한 것만) ===
        deposit = data_fetcher.get_deposit(env_dv=self.cfg.env_dv)
        cash_available = deposit.get("deposit", 0)
        logger.info(f"예수금: {cash_available:,}원")

        per_system_count: dict[str, int] = {}
        new_position_count = 0

        for sig in buys:
            system_tag = sig.reason.split("]")[0].lstrip("[")
            sys_count = per_system_count.get(system_tag, 0)

            ok, reason = self.risk.can_open_new_position(
                current_holdings_count=len(holding_codes) + new_position_count,
                system_holdings_count=sys_count,
            )
            if not ok:
                logger.info(f"  ✗ {sig.stock_name}: {reason}")
                continue

            df = data_fetcher.get_daily_prices(sig.stock_code, 60, env_dv=self.cfg.env_dv)
            if df.empty:
                continue
            atr = indicators.calc_atr(df, 14).iloc[-1]
            atr_val = float(atr) if not pd.isna(atr) else 0.0

            current_price = int(df["close"].iloc[-1])
            qty = self.risk.position_size(price=current_price, atr=atr_val, system=system_tag)

            est_cost = qty * current_price
            if qty <= 0 or est_cost > cash_available * 0.95:
                logger.info(f"  ✗ {sig.stock_name}: 사이징 불가 (qty={qty}, cost={est_cost:,})")
                continue

            if self.execute_buy(sig, qty):
                cash_available -= est_cost
                per_system_count[system_tag] = sys_count + 1
                new_position_count += 1

        logger.info(f"✓ 신규 진입 {new_position_count}건 완료")

        # === 5. 일 요약 ===
        self._print_summary(holdings)

    # ------------------------------------------------------------------
    # 헬퍼
    # ------------------------------------------------------------------

    def _print_summary(self, holdings: pd.DataFrame) -> None:
        deposit = data_fetcher.get_deposit(env_dv=self.cfg.env_dv)
        if not deposit:
            return
        total_eval = deposit.get("total_eval", 0)
        pl = deposit.get("profit_loss", 0)
        logger.info("=" * 60)
        logger.info(f"[일 요약 {datetime.now().strftime('%Y-%m-%d %H:%M')}]")
        logger.info(f"  총 평가금액: {total_eval:,}원")
        logger.info(f"  평가 손익  : {pl:,}원")
        logger.info(f"  보유 종목  : {len(holdings)}개")
        logger.info(f"  운용 자본  : {self.cfg.capital:,}원")
        logger.info(f"  일 손익    : {self.risk.state.realized_pnl_today:,}원 (실현)")
        logger.info(f"  월 손익    : {self.risk.state.realized_pnl_month:,}원 (실현)")
        if self.risk.state.breach_until:
            logger.warning(f"  ⚠ 쿨다운 until {self.risk.state.breach_until}")
        logger.info("=" * 60)


# ============================================================================
# CLI
# ============================================================================


def parse_args() -> RunnerConfig:
    p = argparse.ArgumentParser(description="KFiveQuant 자동매매 러너 (KIS Open API)")
    p.add_argument("--env", choices=["vps", "prod"], default="vps", help="vps=모의투자, prod=실전")
    p.add_argument("--product", default="01", help="계좌 상품 코드 (01=종합)")
    p.add_argument("--capital", type=int, default=10_000_000, help="운용 자본 (원)")
    p.add_argument("--dry-run", action="store_true", help="주문 발송 없이 시그널/사이징만 출력")
    p.add_argument("--universe-csv", type=Path, default=None, help="유니버스 CSV (stock_code,stock_name)")
    p.add_argument("--factor-filter", action="store_true", help="PER/PBR 강제 필터 적용")
    args = p.parse_args()
    return RunnerConfig(
        env=args.env,
        product=args.product,
        capital=args.capital,
        dry_run=args.dry_run,
        universe_csv=args.universe_csv,
        require_factor_filter=args.factor_filter,
    )


def main() -> None:
    cfg = parse_args()
    logger.info(f"=== KFiveQuant Runner | env={cfg.env} dry_run={cfg.dry_run} ===")
    runner = KFiveQuantRunner(cfg)
    runner.run_once()


if __name__ == "__main__":
    main()
