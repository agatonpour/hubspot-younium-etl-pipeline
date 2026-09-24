import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from main import main


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "input"
        self.output = self.root / "output"
        shutil.copytree(Path(__file__).resolve().parents[1] / "examples/input", self.inputs)

    def run_migration(self, cutoff=None):
        with contextlib.redirect_stdout(io.StringIO()):
            main(self.inputs, self.output, cutoff)
        return pd.read_csv(self.output / "younium.csv", dtype=str).fillna("")

    def test_fictional_accounts_map_without_customer_specific_rules(self):
        result = self.run_migration()
        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertEqual(row["Order number"], "DEMO-ORDER-1")
        self.assertEqual(row["Product Number"], "DEMO-PRODUCT-1")
        self.assertEqual(row["Invoice Account"], "DEMO-INVOICE-1")
        self.assertEqual(float(row["Line Item Price (in local currency)"]), 120)
        self.assertEqual(row["Invoice to date"], "2027-01-01")
        unmapped = pd.read_csv(self.output / "unmapped.csv")
        self.assertEqual(unmapped.iloc[0]["Deal ID"], "DEMO-ORDER-2")

    def test_expiry_cutoff_is_explicit(self):
        result = self.run_migration("2026-12-31")
        self.assertTrue(result.empty)
        old = pd.read_csv(self.output / "old.csv")
        self.assertEqual(old.iloc[0]["Order number"], "DEMO-ORDER-1")

    def test_incomplete_catalog_mapping_is_reported(self):
        catalog = pd.read_csv(self.inputs / "mapping.csv", dtype=str)
        incomplete = catalog.iloc[0].copy()
        incomplete["line_item"] = "Unlisted Subscription"
        incomplete["product_number"] = "Not found"
        pd.concat([catalog, incomplete.to_frame().T]).to_csv(self.inputs / "mapping.csv", index=False)
        self.assertEqual(len(self.run_migration()), 1)
        self.assertEqual(len(pd.read_csv(self.output / "unmapped.csv")), 1)

    def test_ambiguous_duplicate_is_reported_without_customer_override(self):
        source = pd.read_csv(self.inputs / "hubspot-export-summary.csv", dtype=str)
        duplicate = source.iloc[0].copy()
        duplicate["Deal ID"] = "DEMO-ORDER-3"
        duplicate["Partner"] = "(No value)"
        second = duplicate.copy()
        second["Company name"] = "Another Example Customer"
        second["Younium Account Number"] = "DEMO-ACCOUNT-2"
        pd.concat([source, duplicate.to_frame().T, second.to_frame().T]).to_csv(
            self.inputs / "hubspot-export-summary.csv", index=False
        )
        self.assertEqual(len(self.run_migration()), 1)
        self.assertEqual(len(pd.read_csv(self.output / "ties.csv")), 2)
