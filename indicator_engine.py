import pandas as pd

class IndicatorResult:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        if df.empty or len(df) < 200:
            self.valid = False
            return
        
        self.valid = True
        self.current_close = df['close'].iloc[-1]
        self.current_open = df['open'].iloc[-1]
        self.current_high = df['high'].iloc[-1]
        self.current_low = df['low'].iloc[-1]
        self.current_volume = df['volume'].iloc[-1]

        # قراءة المتوسطات المتحركة (الاتجاه)
        self.ema9 = df['EMA_9'].iloc[-1]
        self.ema21 = df['EMA_21'].iloc[-1]
        self.ema50 = df['EMA_50'].iloc[-1]
        self.ema200 = df['EMA_200'].iloc[-1]

        # قراءة الزخم (RSI & MACD)
        self.rsi_14 = df['RSI_14'].iloc[-1]
        self.macd_line = df['MACD_line'].iloc[-1]
        self.macd_signal = df['MACD_signal'].iloc[-1]
        
        # قراءة السيولة والتقلبات (Volume & ATR)
        self.atr_14 = df['ATR_14'].iloc[-1]
        self.vol_sma = df['VOL_SMA_20'].iloc[-1]
        
        # 1. فلتر السيولة: هل الفوليوم الحالي أعلى من المتوسط؟
        self.is_high_volume = self.current_volume > (self.vol_sma * 1.5)

        # 2. فلتر الشموع اليابانية: هل هناك شمعة انعكاسية (Pinbar)؟
        body = abs(self.current_close - self.current_open)
        lower_wick = min(self.current_open, self.current_close) - self.current_low
        self.is_bullish_pinbar = lower_wick > (body * 2) and body > 0

        # 3. فلتر الاتجاه: هل العملة في مسار صاعد؟
        self.is_uptrend = (self.current_close > self.ema50) and (self.ema50 > self.ema200)

        # 4. فلتر التصحيح (Pullback): تجنب الشراء من القمة (FOMO)
        self.is_pullback = (self.current_low <= self.ema21) and (self.current_close > self.ema21)


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    محرك الحساب: يقوم بحساب جميع المؤشرات الفنية العالمية بدقة
    """
    if df.empty or len(df) < 200:
        return df

    # المتوسطات المتحركة
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()

    # مؤشر القوة النسبية RSI
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(window=14).mean()
    loss = -1 * delta.clip(upper=0).rolling(window=14).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))

    # مؤشر الماكد MACD
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD_line'] = ema12 - ema26
    df['MACD_signal'] = df['MACD_line'].ewm(span=9, adjust=False).mean()

    # مؤشر المدى الحقيقي ATR (لإدارة المخاطر)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR_14'] = true_range.rolling(14).mean()

    # متوسط السيولة (Volume SMA)
    df['VOL_SMA_20'] = df['volume'].rolling(window=20).mean()

    return df

def analyze_symbol(df: pd.DataFrame) -> IndicatorResult:
    df_calculated = calculate_indicators(df)
    return IndicatorResult(df_calculated)
