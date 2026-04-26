"""KFiveQuant 전략 프리셋 등록

5인의 거장(트룰라스/웅거/데이비/김민겸/카메론) 강점을 융합한
4-시스템 멀티전략. strategy_11_kfive_quant.KFiveQuantStrategy 와 매핑.
"""

from strategy.strategy_11_kfive_quant import KFiveQuantStrategy
from strategy_core.registry import register


@register('kfive_quant', '멀티시스템')
class KFiveQuantPreset:
    strategy_class = KFiveQuantStrategy
    name = 'KFiveQuant — 5인 거장 융합'
    description = (
        '트룰라스(비상관 시스템)·웅거(KISS·변동성 돌파)·데이비(워크포워드/ATR 사이징)·'
        '김민겸(한국형 팩터)·카메론(Bull Flag/Strong Close)을 융합한 4-시스템 전략. '
        'A:팩터모멘텀 / B:변동성돌파 / C:RSI(2)평균회귀 / D:52주신고가+VCP'
    )
    params = [
        {'name': 'enable_a', 'label': 'A 팩터모멘텀', 'type': 'bool', 'default': True},
        {'name': 'enable_b', 'label': 'B 변동성돌파', 'type': 'bool', 'default': True},
        {'name': 'enable_c', 'label': 'C RSI(2) 평균회귀', 'type': 'bool', 'default': True},
        {'name': 'enable_d', 'label': 'D 52주 신고가 VCP', 'type': 'bool', 'default': True},
        {'name': 'a_long_period', 'label': '[A] 장기 모멘텀(일)', 'min': 60, 'max': 252, 'default': 252, 'step': 1},
        {'name': 'a_short_period', 'label': '[A] 단기 모멘텀(일)', 'min': 5, 'max': 60, 'default': 20, 'step': 1},
        {'name': 'a_trend_period', 'label': '[A] 추세 MA', 'min': 50, 'max': 200, 'default': 200, 'step': 10},
        {'name': 'b_k', 'label': '[B] 변동성 K', 'min': 0.3, 'max': 1.0, 'default': 0.5, 'step': 0.05},
        {'name': 'b_volume_mult', 'label': '[B] 거래량 배수', 'min': 1.5, 'max': 5.0, 'default': 2.0, 'step': 0.1},
        {'name': 'b_strong_close', 'label': '[B] 강한 종가', 'min': 0.5, 'max': 0.95, 'default': 0.7, 'step': 0.05},
        {'name': 'c_rsi_period', 'label': '[C] RSI 기간', 'min': 2, 'max': 14, 'default': 2, 'step': 1},
        {'name': 'c_rsi_oversold', 'label': '[C] 과매도 임계', 'min': 5, 'max': 30, 'default': 10, 'step': 1},
        {'name': 'c_bb_period', 'label': '[C] BB 기간', 'min': 10, 'max': 30, 'default': 20, 'step': 1},
        {'name': 'd_lookback', 'label': '[D] 신고가 lookback', 'min': 120, 'max': 252, 'default': 252, 'step': 1},
        {'name': 'd_volume_mult', 'label': '[D] 거래량 배수', 'min': 1.0, 'max': 3.0, 'default': 1.5, 'step': 0.1},
        {'name': 'd_atr_contraction', 'label': '[D] ATR 축소비', 'min': 0.5, 'max': 1.0, 'default': 0.85, 'step': 0.05},
        {'name': 'min_strength', 'label': '최소 시그널 강도', 'min': 0.5, 'max': 0.95, 'default': 0.6, 'step': 0.05},
    ]
    param_map = {
        'enable_a': 'enable_a', 'enable_b': 'enable_b',
        'enable_c': 'enable_c', 'enable_d': 'enable_d',
        'a_long_period': 'a_long_period', 'a_short_period': 'a_short_period',
        'a_trend_period': 'a_trend_period',
        'b_k': 'b_k', 'b_volume_mult': 'b_volume_mult',
        'b_strong_close': 'b_strong_close',
        'c_rsi_period': 'c_rsi_period', 'c_rsi_oversold': 'c_rsi_oversold',
        'c_bb_period': 'c_bb_period',
        'd_lookback': 'd_lookback', 'd_volume_mult': 'd_volume_mult',
        'd_atr_contraction': 'd_atr_contraction',
        'min_strength': 'min_strength',
    }
    builder_state = {
        'metadata': {
            'id': 'kfive_quant',
            'name': 'KFiveQuant — 5인 거장 융합',
            'description': '트룰라스/웅거/데이비/김민겸/카메론 강점 융합 4-시스템 전략',
            'category': 'multi_system',
            'tags': ['multi_system', 'momentum', 'breakout', 'mean_reversion', 'vcp', 'factor'],
            'author': 'KIS',
        },
        'indicators': [
            {'id': 'roc_long', 'indicatorId': 'roc', 'alias': 'roc_long', 'params': {'period': 252}, 'output': 'value'},
            {'id': 'roc_short', 'indicatorId': 'roc', 'alias': 'roc_short', 'params': {'period': 20}, 'output': 'value'},
            {'id': 'sma_trend', 'indicatorId': 'sma', 'alias': 'sma_trend', 'params': {'period': 200}, 'output': 'value'},
            {'id': 'rsi2', 'indicatorId': 'rsi', 'alias': 'rsi2', 'params': {'period': 2}, 'output': 'value'},
            {'id': 'bb_lower', 'indicatorId': 'bb_lower', 'alias': 'bb_lower', 'params': {'period': 20, 'std_dev': 2.0}, 'output': 'value'},
            {'id': 'atr14', 'indicatorId': 'atr', 'alias': 'atr14', 'params': {'period': 14}, 'output': 'value'},
        ],
        'entry': {
            'logic': 'OR',
            'conditions': [
                {'id': 'sysA', 'left': {'type': 'indicator', 'indicatorAlias': 'roc_long'},
                 'operator': 'greater_than', 'right': {'type': 'value', 'value': 0}},
                {'id': 'sysC', 'left': {'type': 'indicator', 'indicatorAlias': 'rsi2'},
                 'operator': 'less_than', 'right': {'type': 'value', 'value': 10}},
            ],
        },
        'exit': {
            'logic': 'OR',
            'conditions': [
                {'id': 'exitC', 'left': {'type': 'indicator', 'indicatorAlias': 'rsi2'},
                 'operator': 'greater_than', 'right': {'type': 'value', 'value': 70}},
            ],
        },
        'risk': {
            'stopLoss': {'enabled': True, 'percent': 5},
            'takeProfit': {'enabled': True, 'percent': 15},
            'trailingStop': {'enabled': True, 'percent': 10},
        },
    }
