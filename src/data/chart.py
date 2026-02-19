"""TradingView 차트 캡처 — Playwright headless Chromium.

심볼 + 타임프레임(1h/4h/1D) + 지표(RSI, 볼밴) → PNG 스크린샷.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# TradingView 위젯 HTML 템플릿 — 차트가 화면을 꽉 채우도록 최적화
_TV_WIDGET_HTML = """\
<!DOCTYPE html>
<html><head><style>
  * {{ margin: 0; padding: 0; }}
  html, body {{ width: 100%; height: 100%; overflow: hidden; background: #131722; }}
  .tradingview-widget-container,
  .tradingview-widget-container div,
  #tradingview_widget {{ width: 100% !important; height: 100% !important; }}
  iframe {{ width: 100% !important; height: 100% !important; border: none !important; }}
</style></head>
<body>
<div class="tradingview-widget-container">
  <div id="tradingview_widget"></div>
  <script type="text/javascript"
    src="https://s3.tradingview.com/tv.js"></script>
  <script type="text/javascript">
    new TradingView.widget({{
      "autosize": true,
      "width": "100%",
      "height": "100%",
      "symbol": "{exchange}:{symbol_pair}",
      "interval": "{interval}",
      "timezone": "Asia/Seoul",
      "theme": "dark",
      "style": "1",
      "locale": "kr",
      "toolbar_bg": "#131722",
      "enable_publishing": false,
      "hide_top_toolbar": true,
      "hide_legend": false,
      "hide_side_toolbar": true,
      "allow_symbol_change": false,
      "save_image": false,
      "container_id": "tradingview_widget",
      "studies": [
        "RSI@tv-basicstudies",
        "BB@tv-basicstudies"
      ],
      "overrides": {{
        "paneProperties.background": "#131722",
        "paneProperties.backgroundType": "solid"
      }}
    }});
  </script>
</div>
</body></html>
"""

# 타임프레임 변환
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1d": "D",
    "1D": "D",
    "1w": "W",
    "1W": "W",
}


async def capture_chart(
    symbol: str,
    timeframe: str = "4h",
    exchange: str = "BINANCE",
    width: int = 1280,
    height: int = 720,
) -> bytes:
    """TradingView 차트 스크린샷 캡처.

    Args:
        symbol: 종목 심볼 (예: "BTC", "ETH", "SOL")
        timeframe: 타임프레임 ("1h", "4h", "1D")
        exchange: 거래소 ("BINANCE", "UPBIT")
        width: 캡처 너비 (기본 1280)
        height: 캡처 높이 (기본 720)

    Returns:
        PNG 바이트

    Raises:
        RuntimeError: Playwright 미설치 또는 캡처 실패
    """
    # 심볼 정규화
    symbol_clean = symbol.upper().replace("/", "").replace("USDT", "").replace("KRW", "")
    if exchange.upper() == "UPBIT":
        symbol_pair = f"KRW{symbol_clean}"
    else:
        symbol_pair = f"{symbol_clean}USDT"

    interval = _INTERVAL_MAP.get(timeframe, "240")

    # HTML 생성
    html_content = _TV_WIDGET_HTML.format(
        exchange=exchange.upper(),
        symbol_pair=symbol_pair,
        interval=interval,
    )

    # 임시 HTML 파일 생성
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8",
    ) as f:
        f.write(html_content)
        html_path = Path(f.name)

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=2,
            )

            # 로컬 HTML 로드
            await page.goto(f"file:///{html_path.as_posix()}")

            # TradingView iframe 로딩 대기
            try:
                await page.wait_for_selector("iframe", timeout=10000)
                await asyncio.sleep(6)
            except Exception:
                await asyncio.sleep(10)

            # 스크린샷 캡처 — viewport 전체
            png_bytes = await page.screenshot(
                type="png",
                full_page=False,
            )

            await browser.close()

        logger.info(
            "차트 캡처 완료: %s/%s %s (%d bytes)",
            exchange, symbol_pair, timeframe, len(png_bytes),
        )
        return png_bytes

    except ImportError:
        raise RuntimeError(
            "Playwright 미설치. `pip install playwright && playwright install chromium`"
        )
    except Exception as exc:
        logger.error("차트 캡처 실패: %s", exc, exc_info=True)
        raise RuntimeError(f"차트 캡처 실패: {exc}") from exc
    finally:
        # 임시 파일 정리
        try:
            html_path.unlink(missing_ok=True)
        except Exception:
            pass
