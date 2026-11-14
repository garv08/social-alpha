# Strategy sanity

Synthetic bars. These numbers are sanity results, not market returns.

1045 / 1045 checks passed across 95 strategies (11 checks each) on 420 synthetic sessions.

| Strategy | Section | Trades | Return % | Max DD % | Result |
|---|---|---:|---:|---:|---|
| `sma_cross` | Trend | 8 | -38.95 | -39.99 | pass |
| `golden_cross` | Trend | 2 | 2.98 | -20.76 | pass |
| `ema_cross` | Trend | 9 | 43.66 | -12.1 | pass |
| `ema_12_26` | Trend | 8 | 6.4 | -16.14 | pass |
| `triple_ema` | Trend | 7 | 20.05 | -10.38 | pass |
| `hull_trend` | Trend | 32 | 131.54 | -13.23 | pass |
| `above_sma200` | Trend | 5 | 11.81 | -18.13 | pass |
| `above_ema50` | Trend | 12 | 50.55 | -11.84 | pass |
| `dual_momentum` | Trend | 7 | -11.29 | -26.35 | pass |
| `ema_ribbon` | Trend | 7 | 20.05 | -10.38 | pass |
| `rsi_reversion` | Mean reversion | 5 | 12.92 | -8.21 | pass |
| `rsi_pullback` | Mean reversion | 3 | 4.15 | -18.36 | pass |
| `rsi_trend` | Oscillator | 9 | 62.76 | -12.39 | pass |
| `stoch_reversion` | Mean reversion | 9 | -11.09 | -19.5 | pass |
| `stoch_trend` | Oscillator | 29 | 98.24 | -5.19 | pass |
| `stochrsi` | Oscillator | 9 | -20.63 | -26.93 | pass |
| `macd_cross` | Oscillator | 9 | 162.83 | -7.78 | pass |
| `macd_hist` | Oscillator | 9 | 162.83 | -7.78 | pass |
| `macd_zero` | Oscillator | 8 | 8.65 | -16.14 | pass |
| `bb_reversion` | Mean reversion | 9 | -22.07 | -24.99 | pass |
| `bb_breakout` | Breakout | 9 | 72.91 | -8.22 | pass |
| `bb_squeeze` | Breakout | 6 | 57.09 | -8.22 | pass |
| `keltner_break` | Breakout | 8 | 62.49 | -7.94 | pass |
| `donchian_20` | Breakout | 0 | 0.0 | 0.0 | pass |
| `donchian_55` | Breakout | 0 | 0.0 | 0.0 | pass |
| `turtle_20` | Breakout | 9 | 63.05 | -7.77 | pass |
| `supertrend` | Trend | 9 | 83.84 | -12.74 | pass |
| `psar` | Trend | 11 | 250.02 | -5.95 | pass |
| `adx_di` | Trend | 12 | 52.96 | -16.86 | pass |
| `aroon` | Trend | 9 | -2.28 | -21.04 | pass |
| `vortex` | Trend | 10 | 97.85 | -7.83 | pass |
| `cci_zero` | Oscillator | 13 | 142.85 | -5.75 | pass |
| `cci_reversion` | Mean reversion | 6 | -5.29 | -15.81 | pass |
| `willr` | Mean reversion | 9 | -42.69 | -43.07 | pass |
| `roc_mom` | Oscillator | 10 | 50.29 | -12.06 | pass |
| `tsi_cross` | Oscillator | 9 | 145.87 | -6.91 | pass |
| `ao_cross` | Oscillator | 9 | 11.13 | -19.53 | pass |
| `obv_trend` | Volume | 20 | 113.67 | -9.03 | pass |
| `mfi_reversion` | Mean reversion | 5 | 10.29 | -11.15 | pass |
| `cmf_trend` | Volume | 10 | 59.31 | -14.09 | pass |
| `ichimoku` | Trend | 8 | -2.13 | -15.12 | pass |
| `pivot_break` | Breakout | 85 | 66.67 | -10.86 | pass |
| `week52` | Breakout | 1 | -0.4 | -10.5 | pass |
| `volume_break` | Breakout | 0 | 0.0 | 0.0 | pass |
| `nr7` | Price action | 9 | 52.07 | -3.66 | pass |
| `inside_bar` | Price action | 16 | 48.96 | -10.87 | pass |
| `gap_up` | Price action | 1 | 13.53 | -2.36 | pass |
| `gap_fade` | Mean reversion | 0 | 0.0 | 0.0 | pass |
| `three_up` | Price action | 15 | 138.72 | -6.39 | pass |
| `zscore` | Mean reversion | 9 | -22.07 | -24.99 | pass |
| `pullback_ema` | Trend | 6 | 24.22 | -9.27 | pass |
| `cmo_trend` | Oscillator | 12 | 121.61 | -6.61 | pass |
| `donchian_mid` | Trend | 12 | 91.3 | -7.83 | pass |
| `higher_high` | Price action | 1 | 47.96 | -26.69 | pass |
| `atr_trail` | Trend | 10 | 72.58 | -13.34 | pass |
| `ema50_reclaim` | Trend reversal | 7 | 45.83 | -11.84 | pass |
| `breakdown_reclaim` | Trend reversal | 1 | 41.45 | -26.69 | pass |
| `swing_reversal` | Trend reversal | 0 | 0.0 | 0.0 | pass |
| `macd_reversal` | Trend reversal | 4 | 13.59 | -14.99 | pass |
| `harm_gartley` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_bat` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_alt_bat` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_butterfly` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_crab` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_deep_crab` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_cypher` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_shark` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_five_o` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_abcd` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_three_drives` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_carney` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_prz` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_prz_bar` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `harm_prz_touch` | Harmonics | 0 | 0.0 | 0.0 | pass |
| `darvas_breakout` | Breakout | 5 | 40.53 | -10.68 | pass |
| `breakout_trend_vol` | Breakout | 0 | 0.0 | 0.0 | pass |
| `key_level_breakout` | Breakout | 43 | 35.68 | -9.47 | pass |
| `pattern_breakout` | Breakout | 8 | -19.85 | -28.58 | pass |
| `nt_dc_trend` | @neurotrader888 | 8 | 105.94 | -9.14 | pass |
| `nt_atr_dc_trend` | @neurotrader888 | 9 | 165.78 | -5.62 | pass |
| `nt_trendline_breakout` | @neurotrader888 | 8 | 222.44 | -5.59 | pass |
| `nt_inverse_hs` | @neurotrader888 | 1 | 7.2 | -13.92 | pass |
| `nt_bull_flag` | @neurotrader888 | 7 | -12.03 | -12.03 | pass |
| `nt_hawkes_vol` | @neurotrader888 | 2 | 9.01 | -9.64 | pass |
| `nt_cmma_index` | @neurotrader888 | 10 | 71.73 | -6.61 | pass |
| `nt_donchian_runs` | @neurotrader888 | 5 | 13.89 | -14.67 | pass |
| `nt_rsi_pca` | @neurotrader888 | 0 | 0.0 | 0.0 | pass |
| `ut_connors_rsi2` | @UnbiasedTradingGoshawk | 5 | -24.69 | -24.69 | pass |
| `ut_double_seven` | @UnbiasedTradingGoshawk | 6 | -24.44 | -25.17 | pass |
| `ut_ahl_trend` | @UnbiasedTradingGoshawk | 4 | -11.68 | -21.2 | pass |
| `ut_lo_contrarian` | @UnbiasedTradingGoshawk | 36 | -11.99 | -12.48 | pass |
| `tsmom_12m` | Quant research | 2 | 10.13 | -17.6 | pass |
| `kalman_trend` | Quant research | 12 | 84.98 | -9.08 | pass |
| `turn_of_month` | Quant research | 18 | 17.69 | -10.68 | pass |
| `hurst_route` | Quant research | 10 | 8.27 | -11.84 | pass |
