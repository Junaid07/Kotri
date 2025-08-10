import re, calendar
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import streamlit as st
from pathlib import Path

# ---------------- App meta ----------------
APP_NAME = "Flow Insights Downstream Kotri"
APP_TAGLINE = "Ten-daily flow analyzer (Days below threshold + Surplus valuation)"
PAGE_ICON = "💧"

st.set_page_config(page_title=APP_NAME, page_icon=PAGE_ICON, layout="wide")

# ---------------- Core helpers ----------------
MONTHS = ["APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC","JAN","FEB","MAR"]
MONTH_TO_NUM = {m:i for i,m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"], start=1)}

# 1 cusec for 1 day = 86400 ft³; 1 acre-ft = 43560 ft³  →  1 cusec-day = 1.983471… acre-ft
CUSEC_DAY_TO_AF  = 86400.0 / 43560.0
AF_TO_MAF        = 1e-6
CUSEC_DAY_TO_MAF = CUSEC_DAY_TO_AF * AF_TO_MAF   # ≈ 1.983471e-6 MAF per (cusec·day)

DATA_PATH   = "New.xlsx"   # fixed location in repo root
SHEET_NAME  = "Sheet1"

def parse_period(p: str):
    s = str(p).strip()
    m = re.match(r"^(\d{2,4})\s*[-–]\s*(\d{2,4})$", s)
    if not m:
        raise ValueError(f"Unrecognized period format: {p}")
    a, b = m.group(1), m.group(2)
    start = 1900 + int(a) if len(a) == 2 else int(a)
    if len(b) == 4:
        end = int(b)
    else:
        b2 = int(b)
        cent = (start // 100) * 100
        end = cent + b2
        if end < start:
            end += 100
    return start, end

def month_days(month_abbr: str, start_year: int, end_year: int) -> int:
    m = month_abbr.upper()
    year = end_year if m in ["JAN","FEB","MAR"] else start_year
    import calendar as _cal
    return _cal.monthrange(year, MONTH_TO_NUM[m])[1]

def dekad_lengths(month_abbr: str, start_year: int, end_year: int):
    d = month_days(month_abbr, start_year, end_year)
    return [10, 10, d - 20]

@st.cache_data(show_spinner=False)
def load_matrix_from_path(path_str: str, sheet_name="Sheet1") -> pd.DataFrame:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path.resolve()}")
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
    periods = raw.iloc[0, 1:].tolist()
    valid_cols = [i for i, v in enumerate(periods, start=1) if pd.notna(v)]
    records = []
    current_month = None
    for r in range(1, raw.shape[0]):
        label = str(raw.iat[r, 0]).strip()
        if label == "nan":
            continue
        m = re.match(r"^([A-Za-z]{3})\s*1$", label)
        if m:
            current_month = m.group(1).upper()
            dekad = 1
        else:
            if label in ["2", "3"] and current_month is not None:
                dekad = int(label)
            else:
                continue
        for col_idx in valid_cols:
            period = raw.iat[0, col_idx]
            val = raw.iat[r, col_idx]
            if pd.isna(val):
                continue
            try:
                val_float = float(val)
            except:
                continue
            records.append({
                "Month": current_month,
                "Dekad": dekad,
                "Period": str(period),
                "Avg_1000_cusecs": val_float
            })
    return pd.DataFrame.from_records(records)

def compute_days_below_threshold_by_month(tidy_df, month_abbr="APR", threshold_cusecs=5000):
    month_abbr = month_abbr.upper()
    periods = sorted(tidy_df["Period"].unique().tolist(), key=lambda x: parse_period(x)[0])
    rows = []
    for p in periods:
        syear, eyear = parse_period(p)
        lengths = dekad_lengths(month_abbr, syear, eyear)
        sub = tidy_df[(tidy_df["Period"] == p) & (tidy_df["Month"] == month_abbr)]
        dek_to_avg = {int(d): float(a)*1000.0 for d, a in zip(sub["Dekad"], sub["Avg_1000_cusecs"])}
        below_days = 0
        for d in [1,2,3]:
            avg = dek_to_avg.get(d, np.nan)
            if pd.notna(avg) and avg < threshold_cusecs:
                below_days += lengths[d-1]
        rows.append({"Period": p, "Month": month_abbr, "Days_in_Month": sum(lengths),
                     "Days_Below_Threshold": int(below_days)})
    out = pd.DataFrame(rows)
    out["Pct_Days_Below_%"] = (out["Days_Below_Threshold"]/out["Days_in_Month"]*100).round(1)
    out["StartYear"] = out["Period"].apply(lambda p: parse_period(p)[0])
    return out.sort_values("StartYear")

def compute_days_below_threshold_by_period(tidy_df, period, threshold_cusecs=5000):
    syear, eyear = parse_period(period)
    rows = []
    for m in MONTHS:
        lengths = dekad_lengths(m, syear, eyear)
        sub = tidy_df[(tidy_df["Period"] == period) & (tidy_df["Month"] == m)]
        dek_to_avg = {int(d): float(a)*1000.0 for d, a in zip(sub["Dekad"], sub["Avg_1000_cusecs"]) }
        below_days = 0
        for d in [1,2,3]:
            avg = dek_to_avg.get(d, np.nan)
            if pd.notna(avg) and avg < threshold_cusecs:
                below_days += lengths[d-1]
        rows.append({"Period": period, "Month": m, "Days_in_Month": sum(lengths),
                     "Days_Below_Threshold": int(below_days)})
    out = pd.DataFrame(rows)
    out["Pct_Days_Below_%"] = (out["Days_Below_Threshold"]/out["Days_in_Month"]*100).round(1)
    out["MonthOrder"] = out["Month"].apply(lambda x: MONTHS.index(x))
    return out.sort_values("MonthOrder").drop(columns=["MonthOrder"])

# --------- Surplus (only when ALL 3 dekads > threshold) ----------
def month_surplus_maf_for_period(tidy_df, period, month_abbr, threshold_cusecs):
    """Return (all_above, surplus_MAF) for this period+month."""
    month_abbr = month_abbr.upper()
    syear, eyear = parse_period(period)
    lengths = dekad_lengths(month_abbr, syear, eyear)
    sub = tidy_df[(tidy_df["Period"] == period) & (tidy_df["Month"] == month_abbr)]
    if sub.empty or set(sub["Dekad"]) != {1,2,3}:
        return False, 0.0
    avgs_cusecs = [float(a)*1000.0 for a in sub.sort_values("Dekad")["Avg_1000_cusecs"].tolist()]
    all_above = all(a > threshold_cusecs for a in avgs_cusecs)  # strict ">"
    if not all_above:
        return False, 0.0
    surplus_cusec_days = sum((a - threshold_cusecs) * d for a, d in zip(avgs_cusecs, lengths))
    surplus_maf = surplus_cusec_days * CUSEC_DAY_TO_MAF
    return True, surplus_maf

def compute_surplus_by_month_across_periods(tidy_df, month_abbr, threshold_cusecs):
    rows = []
    for p in sorted(tidy_df["Period"].unique().tolist(), key=lambda x: parse_period(x)[0]):
        ok, maf = month_surplus_maf_for_period(tidy_df, p, month_abbr, threshold_cusecs)
        rows.append({"Period": p, "Month": month_abbr.upper(), "AllDaysAbove": ok, "Surplus_MAF": maf})
    return pd.DataFrame(rows)

def compute_surplus_by_period_across_months(tidy_df, period, threshold_cusecs):
    rows = []
    for m in MONTHS:
        ok, maf = month_surplus_maf_for_period(tidy_df, period, m, threshold_cusecs)
        rows.append({"Period": period, "Month": m, "AllDaysAbove": ok, "Surplus_MAF": maf})
    return pd.DataFrame(rows)

def compute_surplus_all_df(tidy_df, threshold_cusecs):
    """Return DF of all (Period, Month) with flags/MAF for the rule."""
    rows = []
    for p in sorted(tidy_df["Period"].unique().tolist(), key=lambda x: parse_period(x)[0]):
        for m in MONTHS:
            ok, maf = month_surplus_maf_for_period(tidy_df, p, m, threshold_cusecs)
            rows.append({"Period": p, "Month": m, "AllDaysAbove": ok, "Surplus_MAF": maf})
    return pd.DataFrame(rows)

def compute_total_surplus_all(tidy_df, threshold_cusecs):
    df = compute_surplus_all_df(tidy_df, threshold_cusecs)
    return df.loc[df["AllDaysAbove"], "Surplus_MAF"].sum()

# --------- formatting helpers ----------
def fmt_money(x):
    x = float(x)
    ax = abs(x)
    if ax >= 1e12:
        return f"${x/1e12:.2f} T"
    if ax >= 1e9:
        return f"${x/1e9:.2f} B"
    if ax >= 1e6:
        return f"${x/1e6:.2f} M"
    return f"${x:,.0f}"

def pct_part(part, whole):
    if whole and whole > 0:
        return f"{(part/whole)*100:.1f}%"
    return "—"

# ---------------- UI ----------------
st.title(f"{APP_NAME} {PAGE_ICON}")
st.caption(APP_TAGLINE)

with st.sidebar:
    st.subheader("Settings")
    threshold = st.number_input("Threshold (cusecs)", min_value=0, value=5000, step=500)
    st.subheader("Valuation")
    cost_per_maf_b = st.number_input(
        "Cost per MAF (USD, billions)", min_value=0.1, value=1.0, step=0.1, format="%.1f"
    )
    cost_per_maf = cost_per_maf_b * 1e9  # convert to USD

# Load data (fixed path/sheet)
try:
    tidy = load_matrix_from_path(DATA_PATH, sheet_name=SHEET_NAME)
except Exception as e:
    st.error(f"Load error: {e}")
    st.stop()

all_periods = sorted(tidy["Period"].unique().tolist(), key=lambda p: parse_period(p)[0])
if not all_periods:
    st.error("No periods detected in the file.")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs([
    "By Month (All Periods)",
    "By Month (Selected Range)",
    "By Period (All Months)",
    "Surplus & Value"
])

# --- Tab 1: Month across ALL periods
with tab1:
    c1, c2 = st.columns([1,3])
    month = c1.selectbox("Month", MONTHS, index=0)
    if c1.button("Run (All Periods)"):
        res = compute_days_below_threshold_by_month(tidy, month_abbr=month, threshold_cusecs=threshold)
        st.dataframe(res[["Period","Month","Days_in_Month","Days_Below_Threshold","Pct_Days_Below_%"]],
                     use_container_width=True)

        fig, ax = plt.subplots(figsize=(12,4))
        ax.bar(res["Period"], res["Days_Below_Threshold"])
        ax.set_title(f"{month}: Days below {threshold} cusecs by Period")
        ax.set_xlabel("Period"); ax.set_ylabel("Days below threshold")
        plt.xticks(rotation=90); plt.tight_layout()
        st.pyplot(fig)

        srt = res.sort_values("Days_Below_Threshold")
        fig2, ax2 = plt.subplots(figsize=(8, max(4, 0.25*len(srt))))
        ax2.barh(srt["Period"], srt["Days_Below_Threshold"])
        ax2.set_title(f"{month}: Days below {threshold} cusecs (sorted)")
        ax2.set_xlabel("Days below threshold"); ax2.set_ylabel("Period")
        plt.tight_layout(); st.pyplot(fig2)

        vals = res.set_index("Period")["Days_Below_Threshold"]
        vmin = int(vals.min()); vmax = int(vals.max())
        mins = ", ".join(vals.index[vals.eq(vmin)].tolist())
        maxs = ", ".join(vals.index[vals.eq(vmax)].tolist())
        zeros = vals.index[vals.eq(0)].tolist()
        median = int(np.median(vals)); q1 = int(np.percentile(vals,25)); q3 = int(np.percentile(vals,75))
        st.markdown(
            f"**Summary for {month} @ {threshold} cusecs**  \n"
            f"- Min days below: **{vmin}** — Periods: {mins}  \n"
            f"- Max days below: **{vmax}** — Periods: {maxs}  \n"
            f"- Zero-days periods: {len(zeros)} ({', '.join(zeros) if zeros else 'none'})  \n"
            f"- Median: **{median}**, IQR: **{q1}–{q3}**"
        )

# --- Tab 2: Month across SELECTED RANGE
with tab2:
    c1, c2, c3 = st.columns([1,1,2])
    month_r = c1.selectbox("Month", MONTHS, index=0, key="month_range")
    start_p = c2.selectbox("Start Period", all_periods, index=0)
    end_p   = c2.selectbox("End Period", all_periods, index=len(all_periods)-1, key="endp")
    if c1.button("Run (Selected Range)"):
        full = compute_days_below_threshold_by_month(tidy, month_abbr=month_r, threshold_cusecs=threshold)
        key = lambda p: parse_period(p)[0]
        allowed = [p for p in full["Period"].tolist() if key(start_p) <= key(p) <= key(end_p)]
        res = full[full["Period"].isin(allowed)].copy()
        if res.empty:
            st.warning("No data in selected range.")
        else:
            st.dataframe(res[["Period","Month","Days_in_Month","Days_Below_Threshold","Pct_Days_Below_%"]],
                         use_container_width=True)

            fig, ax = plt.subplots(figsize=(12,4))
            ax.bar(res["Period"], res["Days_Below_Threshold"])
            ax.set_title(f"{month_r}: Days below {threshold} cusecs by Period  [{start_p} → {end_p}]")
            ax.set_xlabel("Period"); ax.set_ylabel("Days below threshold")
            plt.xticks(rotation=90); plt.tight_layout(); st.pyplot(fig)

            srt = res.sort_values("Days_Below_Threshold")
            fig2, ax2 = plt.subplots(figsize=(8, max(4, 0.25*len(srt))))
            ax2.barh(srt["Period"], srt["Days_Below_Threshold"])
            ax2.set_title(f"{month_r}: Days below {threshold} cusecs (sorted) [{start_p} → {end_p}]")
            ax2.set_xlabel("Days below threshold"); ax2.set_ylabel("Period")
            plt.tight_layout(); st.pyplot(fig2)

            vals = res.set_index("Period")["Days_Below_Threshold"]
            vmin = int(vals.min()); vmax = int(vals.max())
            mins = ", ".join(vals.index[vals.eq(vmin)].tolist())
            maxs = ", ".join(vals.index[vals.eq(vmax)].tolist())
            zeros = vals.index[vals.eq(0)].tolist()
            median = int(np.median(vals)); q1 = int(np.percentile(vals,25)); q3 = int(np.percentile(vals,75))
            st.markdown(
                f"**Summary for {month_r} @ {threshold} cusecs  | Range: {start_p} → {end_p}**  \n"
                f"- Min days below: **{vmin}** — Periods: {mins}  \n"
                f"- Max days below: **{vmax}** — Periods: {maxs}  \n"
                f"- Zero-days periods: {len(zeros)} ({', '.join(zeros) if zeros else 'none'})  \n"
                f"- Median: **{median}**, IQR: **{q1}–{q3}**"
            )

# --- Tab 3: One PERIOD across all months
with tab3:
    c1, c2 = st.columns([1,3])
    period_sel = c1.selectbox("Period", all_periods, index=len(all_periods)-1)
    if c1.button("Run (By Period)"):
        res = compute_days_below_threshold_by_period(tidy, period=period_sel, threshold_cusecs=threshold)
        st.dataframe(res[["Period","Month","Days_in_Month","Days_Below_Threshold","Pct_Days_Below_%"]],
                     use_container_width=True)

        fig, ax = plt.subplots(figsize=(10,4))
        ax.bar(res["Month"], res["Days_Below_Threshold"])
        ax.set_title(f"{period_sel}: Days below {threshold} cusecs by Month")
        ax.set_xlabel("Month (APR…MAR)"); ax.set_ylabel("Days below threshold")
        plt.tight_layout(); st.pyplot(fig)

        srt = res.sort_values("Days_Below_Threshold")
        fig2, ax2 = plt.subplots(figsize=(7,5))
        ax2.barh(srt["Month"], srt["Days_Below_Threshold"])
        ax2.set_title(f"{period_sel}: Days below {threshold} cusecs (sorted by month)")
        ax2.set_xlabel("Days below threshold"); ax2.set_ylabel("Month")
        plt.tight_layout(); st.pyplot(fig2)

        vals = res.set_index("Month")["Days_Below_Threshold"]
        driest_5  = vals.sort_values(ascending=False).head(5).index.tolist()
        wettest_5 = vals.sort_values(ascending=True).head(5).index.tolist()
        vmin = int(vals.min()); vmax = int(vals.max())
        mins = ", ".join(vals.index[vals.eq(vmin)].tolist())
        maxs = ", ".join(vals.index[vals.eq(vmax)].tolist())
        zeros = vals.index[vals.eq(0)].tolist()
        median = int(np.median(vals)); q1 = int(np.percentile(vals,25)); q3 = int(np.percentile(vals,75))
        st.markdown(
            f"**Summary for {period_sel} @ threshold {threshold} cusecs**  \n"
            f"- Min days below: **{vmin}** — Months: {mins}  \n"
            f"- Max days below: **{vmax}** — Months: {maxs}  \n"
            f"- Months with zero days below: {len(zeros)} ({', '.join(zeros) if zeros else 'none'})  \n"
            f"- Median: **{median}**, IQR: **{q1}–{q3}**  \n"
            f"- **Driest 5 months**: {', '.join(driest_5)}  \n"
            f"- **Wettest 5 months**: {', '.join(wettest_5)}"
        )

# --- Tab 4: Surplus & Value ---
with tab4:
    st.subheader("Surplus water above threshold → MAF → Value")
    st.caption("Counting only months/periods where **all three ten-daily averages** exceed the threshold.")

    # Pre-compute totals & rankings across ALL months & periods (for this threshold)
    df_all = compute_surplus_all_df(tidy, threshold)
    qual_all = df_all[df_all["AllDaysAbove"]].copy()
    total_maf_all = qual_all["Surplus_MAF"].sum()
    total_val_all = total_maf_all * cost_per_maf

    colA, colB = st.columns(2)

    # A) Month across periods (optional range)
    with colA:
        st.markdown("**A. By Month across Periods**")
        month_sv = st.selectbox("Month", MONTHS, index=0, key="sv_month")
        start_sv = st.selectbox("Start Period", all_periods, index=0, key="sv_start")
        end_sv   = st.selectbox("End Period", all_periods, index=len(all_periods)-1, key="sv_end")
        if st.button("Compute Surplus (Month across Periods)"):
            df = compute_surplus_by_month_across_periods(tidy, month_sv, threshold)
            k = lambda p: parse_period(p)[0]
            allowed = [p for p in df["Period"] if k(start_sv) <= k(p) <= k(end_sv)]
            df = df[df["Period"].isin(allowed) & df["AllDaysAbove"]].copy()

            if df.empty:
                st.info("No periods where all three dekads exceeded the threshold in this selection.")
            else:
                df["Value_USD"] = df["Surplus_MAF"] * float(cost_per_maf)
                df["Value_$B"]  = (df["Value_USD"] / 1e9).round(2)
                st.dataframe(df[["Period","Month","AllDaysAbove","Surplus_MAF","Value_$B"]],
                             use_container_width=True)

                fig, ax = plt.subplots(figsize=(10,4))
                ax.bar(df["Period"], df["Surplus_MAF"])
                ax.set_title(f"{month_sv}: Surplus (MAF) where ALL dekads > {threshold} cusecs  [{start_sv} → {end_sv}]")
                ax.set_xlabel("Period"); ax.set_ylabel("Surplus (MAF)")
                plt.xticks(rotation=90); plt.tight_layout(); st.pyplot(fig)

                total_maf = df["Surplus_MAF"].sum()
                total_val = df["Value_USD"].sum()
                st.markdown(
                    f"**Selection surplus:** {total_maf:,.3f} MAF  |  **Value:** {fmt_money(total_val)}  "
                    f"|  **Share of total:** {pct_part(total_val, total_val_all)}"
                )

    # B) One period across months
    with colB:
        st.markdown("**B. By Period across Months**")
        period_sv = st.selectbox("Period", all_periods, index=len(all_periods)-1, key="sv_period")
        if st.button("Compute Surplus (Period across Months)"):
            df = compute_surplus_by_period_across_months(tidy, period_sv, threshold)
            df = df[df["AllDaysAbove"]].copy()

            if df.empty:
                st.info("No months in this period where all three dekads exceeded the threshold.")
            else:
                df["Value_USD"] = df["Surplus_MAF"] * float(cost_per_maf)
                df["Value_$B"]  = (df["Value_USD"] / 1e9).round(2)
                st.dataframe(df[["Period","Month","AllDaysAbove","Surplus_MAF","Value_$B"]],
                             use_container_width=True)

                fig, ax = plt.subplots(figsize=(8,4))
                ax.bar(df["Month"], df["Surplus_MAF"])
                ax.set_title(f"{period_sv}: Surplus (MAF) where ALL dekads > {threshold} cusecs")
                ax.set_xlabel("Month (APR…MAR)"); ax.set_ylabel("Surplus (MAF)")
                plt.tight_layout(); st.pyplot(fig)

                total_maf = df["Surplus_MAF"].sum()
                total_val = df["Value_USD"].sum()
                st.markdown(
                    f"**Selection surplus:** {total_maf:,.3f} MAF  |  **Value:** {fmt_money(total_val)}  "
                    f"|  **Share of total:** {pct_part(total_val, total_val_all)}"
                )

    st.divider()
    # Overall totals
    st.markdown(
        f"**Total across ALL months & periods meeting the surplus rule:** "
        f"{total_maf_all:,.3f} MAF  |  **Value:** {fmt_money(total_val_all)}  "
        f"(Cost/MAF = {cost_per_maf_b:.1f} B USD)"
    )

    # NEW: which Month and which Period contribute the most to total cost?
    if total_maf_all > 0 and not qual_all.empty:
        month_totals = qual_all.groupby("Month")["Surplus_MAF"].sum()
        period_totals = qual_all.groupby("Period")["Surplus_MAF"].sum()

        top_month = month_totals.idxmax()
        top_month_val = month_totals.max() * cost_per_maf
        top_month_share = pct_part(top_month_val, total_val_all)

        top_period = period_totals.idxmax()
        top_period_val = period_totals.max() * cost_per_maf
        top_period_share = pct_part(top_period_val, total_val_all)

        st.markdown(
            f"**Largest cost contribution (by month):** **{top_month}** — {fmt_money(top_month_val)} "
            f"(**{top_month_share}** of total)"
        )
        st.markdown(
            f"**Largest cost contribution (by period):** **{top_period}** — {fmt_money(top_period_val)} "
            f"(**{top_period_share}** of total)"
        )
    else:
        st.info("No qualifying surplus months found at this threshold.")

    st.markdown(
        "<small><b>Note:</b> Using ten-daily averages as a proxy for ‘always above daily requirement’. "
        "We count a month only if <i>all three</i> dekad averages exceed the threshold. "
        "Surplus = ∑((avg − threshold) × days_in_dekad) converted using 1 cusec-day ≈ 1.9835 acre-ft.</small>",
        unsafe_allow_html=True
    )
