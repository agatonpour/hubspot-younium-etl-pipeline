# HubSpot → Younium Data Migration Pipeline

## Overview

A Python and **pandas** ETL tool that transforms **HubSpot CRM exports** into CSV files for **Younium subscription billing imports**.

## What it does

- Maps CRM line items to product and charge-plan identifiers
- Normalizes account, partner, pricing, and subscription dates
- Resolves duplicates using partner and order mappings
- Separates unmapped rows, ambiguous matches, duplicates, and expired licenses for review
- Produces import files without calling either platform's API

## Architecture

**HubSpot CSV Exports → Validation, Mapping & Transformation → Younium Import CSV**

## Try the example

Requires Python 3.10+. All included example records are fictional.

```bash
pip install -r requirements.txt
python main.py --input-dir examples/input --output-dir output/demo
```

For your own migration, place files matching the example schemas in `input/` and run `python main.py`. Use `--expired-before YYYY-MM-DD` to separate licenses ending on or before a chosen date. Review the generated reports and adjust the billing defaults in `main.py` before importing. Real exports, mappings, and generated output are excluded from Git.
