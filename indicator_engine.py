import pandas as pd
from dataclasses import dataclass

@dataclass
class IndicatorResult:
    valid: bool = False
    current_close: float = 0.0
    current_open: float = 0.0
    current_high: float = 0.0
    current_low: float = 0.0
    current_volume: float = 0.0
    ema9: float = 0.0
    ema21: float = 0.0
    ema50: float = 0.0
    ema200: float = 0.0
    rsi_14: float = 0.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    atr_14: float = 0.0
    vol_sma: float = 0.0
    is_above_ema9: bool = False
    is_above_ema21: bool = False
    is_high_volume: bool = False
    is_bullish_pinbar: bool = False
    is_uptrend: bool = False
    is_pullback: bool = False

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 50:
        return df

    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()

    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(window=14).mean()
    loss = (-1 * delta.clip(upper=0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, 1e-9)
    df['RSI_14'] = 100 - (100 / (1 + rs))

    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD_line'] = ema12 - ema26
    df['MACD_signal'] = df['MACD_line'].ewm(span=9, adjust=False).mean()

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR_14'] = true_range.rolling(14).mean()

    df['VOL_SMA_20'] = df['volume'].rolling(window=20).mean()
    return df

def analyze_symbol(df: pd.DataFrame) -> IndicatorResult:
    if df.empty or len(df) < 50:
        return IndicatorResult(valid=False)

    df_calc = calculate_indicators(df)
    last = df_calc.iloc[-1]

    c_close = float(last['close'])
    c_open = float(last['open'])
    c_high = float(last['high'])
    c_low = float(last['low'])
    c_vol = float(last['volume'])

    ema9 = float(last['EMA_9'])
    ema21 = float(last['EMA_21'])
    ema50 = float(last['EMA_50'])
    ema200 = float(last['EMA_200'])
    vol_sma = float(last['VOL_SMA_20']) if not pd.isna(last['VOL_SMA_20']) else 0.0

    body = abs(c_close - c_open)
    lower_wick = min(c_open, c_close) - c_low

    return IndicatorResult(
        valid=True,
        current_close=c_close,
        current_open=c_open,
        current_high=c_high,
        current_low=c_low,
        current_volume=c_vol,
        ema9=ema9,
        ema21=ema21,
        ema50=ema50,
        ema200=ema200,
        rsi_14=float(last['RSI_14']),
        macd_line=float(last['MACD_line']),
        macd_signal=float(last['MACD_signal']),
        atr_14=float(last['ATR_14']),
        vol_sma=vol_sma,
        is_above_ema9=(c_close > ema9),
        is_above_ema21=(c_close > ema21),
        is_high_volume=(c_vol > (vol_sma * 1.5)) if vol_sma > 0 else False,
        is_bullish_pinbar=(lower_wick > (body * 2)) and (body > 0),
        is_uptrend=(c_close > ema50),
        is_pullback=(c_low <= ema21) and (c_close > ema21)
    )

