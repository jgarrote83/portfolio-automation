Over 2024–2025 (502 trading days, none skipped), the morning bond market tells you very little about SPY that SPY does not already show itself.

- **How strongly do they move together in the morning?** Weakly, and in the "bonds up when stocks down" direction only in the first half
  hour. From 09:30 to 10:00 the correlation is about −0.15 for TLT and −0.21 for IEF. Nearly all of that comes from 2025 (−0.28 / −0.36);
  in 2024 it was about zero. Over a full day the sign flips to slightly positive (TLT +0.09, IEF about 0), and quarter by quarter it wanders
  between −0.12 and +0.34. In this period bonds were not a reliable mirror of stocks.
- **Do bonds move first, by minutes?** No. The only clear link is in the **same minute** (TLT −0.14, IEF −0.20). One to ten minutes later
  every correlation is within ±0.04, and the honest (day-bootstrap) band includes zero at every positive lag. The +3-minute value (TLT −0.027)
  and the small −3/−4 values (SPY first) are the size you would expect from noise across 20 lags. Bond ETFs and SPY react to the same news
  at the same time.
- **Does the morning bond move say anything about SPY's afternoon?** Not detectably. Adding the bond's overnight gap and 09:30–10:00 move to
  a model that already has SPY's own gap and morning adds about 1 point of R² (0.9 for TLT, 1.4 for IEF). The joint test is nowhere near
  significant (p 0.41 / 0.37), and the same holds in each year separately. SPY's afternoon is mostly unexplained by anything in the morning
  (R² about 4%).
- **What changes on big bond-move days?** On the 50 days with the largest TLT gaps (|gap| ≥ 1.08%), the bond terms *look* informative
  (p 0.03 for TLT, 0.01 for IEF, and R² rises from 0.28 to 0.44–0.49). **That result is the April 2025 tariff shock, not a pattern.** Five of
  the 50 days are April 2025. Remove them and p goes to 0.28 / 0.35 and the added R² to 3–4 points. Dropping 2025-04-04 alone already takes
  TLT's p to 0.08. One crisis week cannot support a rule.

**Bottom line:** no evidence here that watching TLT or IEF in the first half hour gives an edge on SPY for the rest of the day. The one
promising-looking corner (big bond-gap days) rests on a single week. If you want to pursue it, the honest next step is a pre-registered test
on data never looked at. The pre-2024 years are the natural place, since 2026 stays reserved.

Two data notes. The prices are raw, so the monthly bond distributions show up as small negative gaps on ex-dates. A crude month-start check
moves no headline number by more than about 0.02. And the independent re-derivation of 20 random days matched exactly (240 of 240 values).
