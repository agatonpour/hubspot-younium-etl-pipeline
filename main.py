from pathlib import Path
import sys
import argparse
import pandas as pd


EXCLUDED_ORDER_ACCOUNT_PREFIXES = []

EXCLUDE_NO_VALUE_ACCOUNT_NUMBERS = True
KEEP_ONLY_NO_VALUE_PARTNERS = False
EXCLUDE_NO_VALUE_PARTNERS = False



def normalize(text):
    if pd.isna(text):
        return ""
    return str(text).lower().strip()


def overlap_score(input_text, mapping_text):
    input_text = normalize(input_text)
    mapping_text = normalize(mapping_text)

    if not mapping_text:
        return 0

    if mapping_text in input_text:
        return len(mapping_text)

    return 0


def main(input_dir=Path("input"), output_dir=Path("output"), expired_before=None) -> None:
    HUBSPOT_FILE = input_dir / "hubspot-export-summary.csv"
    MAPPING_FILE = input_dir / "mapping.csv"
    UNIQUE_PARTNERS_FILE = input_dir / "unique_partners.csv"
    UNIQUE_ORDER_NUMBERS_FILE = input_dir / "unique_order_numbers_correct.csv"
    OUTPUT_FILE = output_dir / "younium.csv"
    UNMAPPED_FILE = output_dir / "unmapped.csv"
    DUPLICATES_FILE = output_dir / "duplicates.csv"
    TIES_FILE = output_dir / "ties.csv"
    OLD_FILE = output_dir / "old.csv"
    hubspot_df = pd.read_csv(HUBSPOT_FILE, dtype=str).fillna("")
    mapping_df = pd.read_csv(MAPPING_FILE, dtype=str).fillna("")
    unique_partners_df = pd.read_csv(UNIQUE_PARTNERS_FILE, dtype=str).fillna("")
    unique_order_numbers_df = pd.read_csv(
        UNIQUE_ORDER_NUMBERS_FILE,
        dtype=str,
    ).fillna("")

    hubspot_df["Partner"] = hubspot_df["Partner"].astype(str).str.strip()
    hubspot_df["Younium Account Number"] = (
        hubspot_df["Younium Account Number"].astype(str).str.strip()
    )

    hubspot_df["Deal ID"] = hubspot_df["Deal ID"].astype(str).str.strip()
    hubspot_df["Name"] = hubspot_df["Name"].astype(str).str.strip()
    hubspot_df["Partner"] = hubspot_df["Partner"].astype(str).str.strip()

    mapping_df["line_item"] = mapping_df["line_item"].astype(str).str.strip()
    mapping_df["product_name"] = mapping_df["product_name"].astype(str).str.strip()
    mapping_df["product_number"] = mapping_df["product_number"].astype(str).str.strip()
    mapping_df["charge_plan_ID"] = mapping_df["charge_plan_ID"].astype(str).str.strip()
    unique_partners_df["Partner"] = unique_partners_df["Partner"].astype(str).str.strip()
    unique_partners_df["Invoice Account"] = (
        unique_partners_df["Invoice Account"].astype(str).str.strip()
    )
    unique_order_numbers_df["Order number"] = (
        unique_order_numbers_df["Order number"].astype(str).str.strip()
    )
    unique_order_numbers_df["Partner"] = (
        unique_order_numbers_df["Partner"].astype(str).str.strip()
    )

    filtered_df = hubspot_df.copy()

    if filtered_df.empty:
        raise ValueError("Input file is empty.")

    kept_filtered_df = filtered_df.copy()
    duplicate_excluded_input_df = filtered_df.iloc[0:0].copy()

    kept_filtered_df = kept_filtered_df.drop(columns=["_partner_priority"], errors="ignore")
    duplicate_excluded_input_df = duplicate_excluded_input_df.drop(
        columns=["_partner_priority"], errors="ignore"
    )

    # -----------------------------
    # Match line items using the longest catalog phrase
    # -----------------------------
    mapping_records = mapping_df.to_dict("records")

    def find_best_match(input_line_item):
        best_match = None
        best_score = 0

        def mapping_completeness(mapping_row):
            return sum(
                bool(mapping_row[field] and mapping_row[field] != "Not found")
                for field in ["product_name", "product_number", "charge_plan_ID"]
            )

        for m in mapping_records:
            score = overlap_score(input_line_item, m["line_item"])
            if score > best_score:
                best_score = score
                best_match = m
            elif (
                score == best_score
                and best_match is not None
                and mapping_completeness(m) > mapping_completeness(best_match)
            ):
                best_match = m

        return best_match

    mapped_rows = []
    unmapped_rows = []

    for _, row in kept_filtered_df.iterrows():
        match = find_best_match(row["Name"])

        if (
            match
            and all(match[field] and match[field] != "Not found"
                    for field in ["product_name", "product_number", "charge_plan_ID"])
        ):
            row = row.copy()
            row["product_name"] = match["product_name"]
            row["product_number"] = match["product_number"]
            row["charge_plan_ID"] = match["charge_plan_ID"]
            mapped_rows.append(row)
        else:
            unmapped_rows.append(row.copy())

    mapped_df = pd.DataFrame(mapped_rows)
    unmapped_df = pd.DataFrame(unmapped_rows)

    if mapped_df.empty:
        raise ValueError("No fully mapped rows found in the input.")

    # -----------------------------
    # Transform mapped output only
    # -----------------------------
    mapped_df = mapped_df.rename(
        columns={
            "Younium Account Number": "Account number",
            "Company name": "Order account: Name",
            "Deal ID": "Order number",
            "Amount in company currency": "Total Price in SEK",
            "Deal Name": "Order:Description",
            "Currency": "Currency code",
            "Name": "line item",
            "product_name": "Product Name",
            "product_number": "Product Number",
            "charge_plan_ID": "Charge Plan ID",
            "License Start Date / Go-live date - Daily": "License start date",
            "License End Date - Daily": "License end date",
        }
    )

    no_value_set = {"", "(No value)", "None", None}

    mask_start = mapped_df["License start date"].isin(no_value_set)
    mapped_df.loc[mask_start, "License start date"] = mapped_df.loc[
        mask_start, "Close Date - Daily"
    ]

    mask_end = mapped_df["License end date"].isin(no_value_set)
    fallback_end_dates = pd.to_datetime(
        mapped_df.loc[mask_end, "Close Date - Daily"],
        errors="coerce",
    )
    fallback_end_dates = (
        fallback_end_dates + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    )
    fallback_end_dates = fallback_end_dates.dt.strftime("%Y-%m-%d")
    mapped_df.loc[mask_end, "License end date"] = fallback_end_dates.fillna(
        mapped_df.loc[mask_end, "Close Date - Daily"]
    )

    mapped_df["Line Item Price (in local currency)"] = (
        pd.to_numeric(mapped_df["Net price"], errors="coerce")
        / pd.to_numeric(mapped_df["Quantity"], errors="coerce")
    ).round(5)

    mapped_df["Order: status"] = "active"
    mapped_df["Discount"] = "0"
    mapped_df["Order charge: Price period"] = "annual"
    mapped_df["Order Charge: Billing period"] = "annual"
    mapped_df["Order charge: Period Alignment"] = "AlignToOrder"
    mapped_df["Initial terms"] = "12"
    mapped_df["Charge Type"] = "Recurring"
    mapped_df["Billing Timing"] = "InAdvance"

    invoice_account_lookup = (
        unique_partners_df.drop_duplicates(subset=["Partner"], keep="last")
        .set_index("Partner")["Invoice Account"]
        .to_dict()
    )
    order_number_partner_lookup = (
        unique_order_numbers_df.drop_duplicates(subset=["Order number"], keep="first")
        .set_index("Order number")["Partner"]
        .to_dict()
    )

    def invoice_account_for_partner(partner_name):
        if partner_name in no_value_set:
            return "(No value)"
        mapped_invoice_account = invoice_account_lookup.get(partner_name, "")
        if mapped_invoice_account and mapped_invoice_account not in no_value_set:
            return mapped_invoice_account
        return partner_name

    mapped_df["Invoice Account"] = "(No value)"
    partner_has_value_mask = mapped_df["Partner"].astype(str).ne("(No value)")
    invoice_account_values = mapped_df.loc[partner_has_value_mask, "Partner"].map(
        invoice_account_lookup
    )
    missing_invoice_account_mask = (
        invoice_account_values.isna() | invoice_account_values.astype(str).eq("")
    )
    invoice_account_values = invoice_account_values.where(
        ~missing_invoice_account_mask,
        mapped_df.loc[partner_has_value_mask, "Partner"],
    )
    mapped_df.loc[partner_has_value_mask, "Invoice Account"] = invoice_account_values

    mapped_df["Order row"] = (
        mapped_df.groupby("Order number", sort=False).cumcount() + 1
    ).astype(str)

    invoice_to_dates = pd.to_datetime(
        mapped_df["License end date"],
        errors="coerce",
    ) + pd.Timedelta(days=1)
    mapped_df["Invoice to date"] = invoice_to_dates.dt.strftime("%Y-%m-%d").fillna(
        mapped_df["License end date"]
    )

    preferred_order = [
        "Account number",
        "Order account: Name",
        "Partner",
        "Invoice Account",
        "Invoice to date",
        "Order number",
        "Order row",
        "Order: status",
        "line item",
        "Product Name",
        "Product Number",
        "Charge Plan ID",
        "Quantity",
        "Total Price in SEK",
        "Line Item Price (in local currency)",
        "Discount",
        "Order:Description",
        "Currency code",
        "License start date",
        "License end date",
        "Order charge: Price period",
        "Order Charge: Billing period",
        "Order charge: Period Alignment",
        "Initial terms",
        "Charge Type",
        "Billing Timing",
    ]

    existing_cols = mapped_df.columns.tolist()
    ordered_cols = [col for col in preferred_order if col in existing_cols]
    remaining_cols = [col for col in existing_cols if col not in ordered_cols]
    mapped_df = mapped_df[ordered_cols + remaining_cols]
    mapped_df = mapped_df.drop(columns=["Company ID", "Company ID.1"], errors="ignore")

    excluded_order_account_mask = mapped_df["Order account: Name"].astype(str).str.startswith(
        tuple(EXCLUDED_ORDER_ACCOUNT_PREFIXES),
        na=False,
    )
    excluded_order_account_count = int(excluded_order_account_mask.sum())
    mapped_df = mapped_df[~excluded_order_account_mask].copy()

    excluded_no_value_account_count = 0
    if EXCLUDE_NO_VALUE_ACCOUNT_NUMBERS:
        excluded_no_value_account_mask = mapped_df["Account number"].astype(str).eq(
            "(No value)"
        )
        excluded_no_value_account_count = int(excluded_no_value_account_mask.sum())
        mapped_df = mapped_df[~excluded_no_value_account_mask].copy()

    excluded_partner_value_count = 0
    excluded_no_value_partner_count = 0

    duplicate_excluded_output_df = mapped_df.iloc[0:0].copy()
    tie_excluded_output_df = mapped_df.iloc[0:0].copy()
    duplicate_excluded_count = 0
    tie_excluded_count = 0

    dedupe_exclude = [
        "Account number",
        "Order account: Name",
        "Partner",
        "Invoice Account",
        "Order row",
    ]
    dedupe_cols = [col for col in mapped_df.columns if col not in dedupe_exclude]

    if dedupe_cols:
        mapped_df = mapped_df.copy()
        mapped_df["_partner_has_value"] = mapped_df["Partner"].astype(str).ne("(No value)")
        mapped_df["_dedupe_group_key"] = (
            mapped_df[dedupe_cols].astype(str).agg("\x1f".join, axis=1)
        )

        group_sizes = mapped_df.groupby("_dedupe_group_key").size()
        no_value_partner_group_sizes = (
            mapped_df.loc[~mapped_df["_partner_has_value"]]
            .groupby("_dedupe_group_key")
            .size()
        )
        tie_group_keys = no_value_partner_group_sizes.index[
            (no_value_partner_group_sizes >= 2)
            & (group_sizes.reindex(no_value_partner_group_sizes.index, fill_value=0) >= 2)
        ]
        tie_groups_df = mapped_df[
            mapped_df["_dedupe_group_key"].isin(tie_group_keys)
        ].copy()
        resolved_tie_kept_rows = []
        resolved_tie_duplicate_rows = []
        unresolved_tie_rows = []

        for _, tie_group_df in tie_groups_df.groupby("_dedupe_group_key", sort=False):
            order_number = tie_group_df["Order number"].iloc[0]
            mapped_partner_name = order_number_partner_lookup.get(order_number, "").strip()

            if not mapped_partner_name or mapped_partner_name in no_value_set:
                unresolved_tie_rows.append(tie_group_df.copy())
                continue

            partner_row_mask = tie_group_df["Order account: Name"].map(normalize).eq(
                normalize(mapped_partner_name)
            )
            partner_rows_df = tie_group_df[partner_row_mask].copy()
            primary_rows_df = tie_group_df[~partner_row_mask].copy()

            if primary_rows_df.empty or partner_rows_df.empty:
                unresolved_tie_rows.append(tie_group_df.copy())
                continue

            if len(primary_rows_df) != 1:
                unresolved_tie_rows.append(tie_group_df.copy())
                continue

            kept_row = primary_rows_df.iloc[0].copy()
            kept_row["Partner"] = mapped_partner_name
            kept_row["Invoice Account"] = invoice_account_for_partner(mapped_partner_name)
            kept_row["_partner_has_value"] = True
            resolved_tie_kept_rows.append(kept_row)
            resolved_tie_duplicate_rows.append(partner_rows_df.copy())

        resolved_tie_kept_df = (
            pd.DataFrame(resolved_tie_kept_rows)
            if resolved_tie_kept_rows
            else mapped_df.iloc[0:0].copy()
        )
        resolved_tie_duplicate_df = (
            pd.concat(resolved_tie_duplicate_rows, ignore_index=False)
            if resolved_tie_duplicate_rows
            else mapped_df.iloc[0:0].copy()
        )
        tie_excluded_output_df = (
            pd.concat(unresolved_tie_rows, ignore_index=False)
            if unresolved_tie_rows
            else mapped_df.iloc[0:0].copy()
        )
        tie_excluded_count = len(tie_excluded_output_df)
        duplicate_excluded_output_df = pd.concat(
            [duplicate_excluded_output_df, resolved_tie_duplicate_df],
            ignore_index=False,
        )
        mapped_df = mapped_df[
            ~mapped_df["_dedupe_group_key"].isin(tie_group_keys)
        ].copy()
        if not resolved_tie_kept_df.empty:
            mapped_df = pd.concat([mapped_df, resolved_tie_kept_df], ignore_index=False)

        mapped_df = mapped_df.sort_values(
            by="_partner_has_value",
            ascending=False,
            kind="stable",
        )
        keep_mask = ~mapped_df.duplicated(subset=dedupe_cols, keep="first")
        regular_duplicate_excluded_df = mapped_df[~keep_mask].copy()
        duplicate_excluded_output_df = pd.concat(
            [duplicate_excluded_output_df, regular_duplicate_excluded_df],
            ignore_index=False,
        )
        mapped_df = mapped_df[keep_mask].copy()
        duplicate_excluded_count = len(duplicate_excluded_output_df)
        mapped_df = mapped_df.drop(
            columns=["_partner_has_value", "_dedupe_group_key"],
            errors="ignore",
        )
        duplicate_excluded_output_df = duplicate_excluded_output_df.drop(
            columns=["_partner_has_value", "_dedupe_group_key"],
            errors="ignore",
        )
        tie_excluded_output_df = tie_excluded_output_df.drop(
            columns=["_partner_has_value", "_dedupe_group_key"],
            errors="ignore",
        )

    if KEEP_ONLY_NO_VALUE_PARTNERS:
        excluded_partner_value_mask = mapped_df["Partner"].astype(str).ne("(No value)")
        excluded_partner_value_count = int(excluded_partner_value_mask.sum())
        mapped_df = mapped_df[~excluded_partner_value_mask].copy()

    if EXCLUDE_NO_VALUE_PARTNERS:
        excluded_no_value_partner_mask = mapped_df["Partner"].astype(str).eq(
            "(No value)"
        )
        excluded_no_value_partner_count = int(excluded_no_value_partner_mask.sum())
        mapped_df = mapped_df[~excluded_no_value_partner_mask].copy()

    mapped_df["Invoice Account"] = mapped_df["Partner"].map(invoice_account_for_partner)
    old_row_mask = pd.Series(False, index=mapped_df.index)
    if expired_before is not None:
        old_cutoff = pd.Timestamp(expired_before)
        license_end_dates = pd.to_datetime(mapped_df["License end date"], errors="coerce")
        old_row_mask = license_end_dates.le(old_cutoff)
    old_excluded_output_df = mapped_df[old_row_mask].copy()
    mapped_df = mapped_df[~old_row_mask].copy()

    # -----------------------------
    # Export
    # -----------------------------
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    mapped_df.to_csv(OUTPUT_FILE, index=False)

    # Keep unmapped.csv in RAW INPUT format only
    if not unmapped_df.empty:
        unmapped_df["Unmapped reason"] = "No valid product mapping"

    unmapped_df.to_csv(UNMAPPED_FILE, index=False)
    duplicate_excluded_output_df.to_csv(DUPLICATES_FILE, index=False)
    tie_excluded_output_df.to_csv(TIES_FILE, index=False)
    old_excluded_output_df.to_csv(OLD_FILE, index=False)

    print(f"Done. Output written to: {OUTPUT_FILE}")
    print(f"Unmapped rows written to: {UNMAPPED_FILE}")
    print(f"Duplicate rows written to: {DUPLICATES_FILE}")
    print(f"Tie rows written to: {TIES_FILE}")
    print(f"Old rows written to: {OLD_FILE}")
    print(f"Mapped rows exported: {len(mapped_df)}")
    print(
        "Rows excluded by Order account prefix filter: "
        f"{excluded_order_account_count}"
    )
    print(
        "Rows excluded by '(No value)' Account number filter: "
        f"{excluded_no_value_account_count}"
    )
    print(
        "Rows excluded by Partner filter: "
        f"{excluded_partner_value_count}"
    )
    print(
        "Rows excluded by '(No value)' Partner filter: "
        f"{excluded_no_value_partner_count}"
    )
    print(f"Rows skipped due to missing mapping: {len(unmapped_df)}")
    print(f"Rows skipped due to duplicates: {duplicate_excluded_count}")
    print(
        "Rows written to ties.csv: "
        f"{tie_excluded_count}"
    )
    print(
        "Rows excluded by old License end date filter: "
        f"{len(old_excluded_output_df)}"
    )
    print(f"License start date replaced: {int(mask_start.sum())}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transform HubSpot CSV exports for Younium import.")
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--expired-before", help="Exclude licenses ending on or before this date (YYYY-MM-DD).")
    args = parser.parse_args()
    try:
        main(args.input_dir, args.output_dir, args.expired_before)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
