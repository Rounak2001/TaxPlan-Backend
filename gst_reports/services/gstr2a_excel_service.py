import pandas as pd
import io
import time
from datetime import datetime
from django.conf import settings
from gst_reports.utils import get_gst_headers, safe_api_call, get_platform_token


class GSTR2AExcelService:
    """
    Service for fetching GSTR-2A data and generating clean Excel reports for CAs.
    Uses explicit field extraction (like GSTR-2B service) instead of raw sandbox dumps.
    """

    BASE_URL = "https://api.sandbox.co.in"

    def __init__(self):
        self.sections = [
            "b2b", "b2ba",
            "cdn", "cdna",
            "isd",
            "impg", "impgsez",
            "tcs", "tds"
        ]

    @staticmethod
    def safe_float(value, default=0.0):
        if value is None or value == "" or value == "null":
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def get_periods(self, download_type, fy=None, quarter=None, year=None, month=None):
        if download_type == "fy":
            y = int(fy.split("-")[0])
            now = datetime.now()
            months = []
            for m in range(4, 13):
                if y < now.year or (y == now.year and m <= now.month): months.append((y, m))
            for m in range(1, 4):
                if y + 1 < now.year or (y + 1 == now.year and m <= now.month): months.append((y + 1, m))
            return months, fy
        elif download_type == "quarterly":
            y = int(fy.split("-")[0])
            q = int(quarter)
            months = [(y, 4), (y, 5), (y, 6)] if q == 1 else \
                     [(y, 7), (y, 8), (y, 9)] if q == 2 else \
                     [(y, 10), (y, 11), (y, 12)] if q == 3 else \
                     [(y + 1, 1), (y + 1, 2), (y + 1, 3)]
            return months, f"{fy}_Q{quarter}"
        else:
            m = int(month)
            y = int(year)
            if fy:
                start_year = int(fy.split("-")[0])
                y = start_year if m >= 4 else start_year + 1
            return [(y, m)], f"{m:02d}{y}"

    def generate(self, user, gstin, taxpayer_token, username, download_type="monthly", fy=None, quarter=None, year=None, month=None):
        """
        Main entry point for generating GSTR-2A Excel.
        """
        periods, period_label = self.get_periods(download_type, fy, quarter, year, month)

        all_b2b = []
        all_cdnr = []
        all_other = []

        for y, m in periods:
            period_data = self._fetch_all_sections(user, gstin, y, m, taxpayer_token)
            period_str = f"{m:02d}-{y}"
            b2b, cdnr, other = self._extract_data(period_data, period_str)
            all_b2b.extend(b2b)
            all_cdnr.extend(cdnr)
            all_other.extend(other)

        # Enrich with supplier names
        all_ctins = set()
        for row in all_b2b + all_cdnr:
            if row.get("GSTIN/UIN"):
                all_ctins.add(row["GSTIN/UIN"])

        if all_ctins:
            supplier_names = self._fetch_supplier_names(list(all_ctins))
            for row in all_b2b + all_cdnr:
                gstin_val = row.get("GSTIN/UIN", "")
                if gstin_val in supplier_names:
                    row["Supplier Name"] = supplier_names[gstin_val]

        # Build Excel
        output = self._create_clean_excel(all_b2b, all_cdnr, all_other)
        filename = f"GSTR2A_{gstin}_{period_label}.xlsx"

        return output, filename

    def _fetch_all_sections(self, user, gstin, year, month, taxpayer_token):
        from gst_reports.services.gst_data_service import GSTDataService
        period_data = {section: [] for section in self.sections}
        period = f"{month:02d}{year}"

        for section in self.sections:
            data = GSTDataService.get_gstr2a_section(user, gstin, section, year, month, taxpayer_token)

            records = []
            if isinstance(data, dict):
                if section in data:
                    records = data[section]
                elif "data" in data and isinstance(data["data"], dict) and section in data["data"]:
                    records = data["data"][section]

            if records:
                period_data[section] = records

            time.sleep(0.1)

        return period_data

    def _extract_data(self, period_data, period_str):
        """
        Explicitly extract only CA-relevant fields from raw sandbox data.
        Mirrors the approach used in GSTR2BExcelService.extract_data.
        """
        b2b_rows = []
        cdnr_rows = []
        other_rows = []

        sf = self.safe_float

        # ---- B2B & B2BA ----
        for key in ["b2b", "b2ba"]:
            for supplier in period_data.get(key, []):
                ctin = supplier.get("ctin", "")
                cfs = supplier.get("cfs", "")  # GSTR-1 Filing Status

                for inv in supplier.get("inv", []):
                    # Aggregate tax from items
                    txval, igst, cgst, sgst, cess = 0, 0, 0, 0, 0
                    rate = ""
                    items = inv.get("itms", [])

                    if items:
                        for itm in items:
                            det = itm.get("itm_det", itm)
                            txval += sf(det.get("txval"))
                            igst += sf(det.get("iamt"))
                            cgst += sf(det.get("camt"))
                            sgst += sf(det.get("samt"))
                            cess += sf(det.get("csamt"))
                            if det.get("rt"):
                                rate = det.get("rt")
                    else:
                        txval = sf(inv.get("txval") or inv.get("val"))

                    b2b_rows.append({
                        "Return Period": period_str,
                        "GSTIN/UIN": ctin,
                        "Supplier Name": "",  # Will be enriched later
                        "Invoice No": inv.get("inum", ""),
                        "Invoice Date": inv.get("idt", ""),
                        "Invoice Value": sf(inv.get("val")),
                        "Place of Supply": inv.get("pos", ""),
                        "Reverse Charge": inv.get("rchrg", ""),
                        "Invoice Type": inv.get("inv_typ", ""),
                        "Rate": rate,
                        "Taxable Value": round(txval, 2),
                        "IGST": round(igst, 2),
                        "CGST": round(cgst, 2),
                        "SGST": round(sgst, 2),
                        "Cess": round(cess, 2),
                        "GSTR-1 Filed": cfs,
                        "Type": key.upper(),
                    })

        # ---- CDN & CDNA ----
        for key in ["cdn", "cdna"]:
            for supplier in period_data.get(key, []):
                ctin = supplier.get("ctin", "")

                for note in supplier.get("nt", []):
                    txval, igst, cgst, sgst, cess = 0, 0, 0, 0, 0
                    rate = ""
                    items = note.get("itms", [])

                    if items:
                        for itm in items:
                            det = itm.get("itm_det", itm)
                            txval += sf(det.get("txval"))
                            igst += sf(det.get("iamt"))
                            cgst += sf(det.get("camt"))
                            sgst += sf(det.get("samt"))
                            cess += sf(det.get("csamt"))
                            if det.get("rt"):
                                rate = det.get("rt")
                    else:
                        txval = sf(note.get("txval") or note.get("val"))

                    cdnr_rows.append({
                        "Return Period": period_str,
                        "GSTIN/UIN": ctin,
                        "Supplier Name": "",
                        "Note No": note.get("nt_num", "") or note.get("ntnum", ""),
                        "Note Date": note.get("nt_dt", "") or note.get("dt", ""),
                        "Note Value": sf(note.get("val")),
                        "Note Type": note.get("ntty", ""),
                        "Rate": rate,
                        "Taxable Value": round(txval, 2),
                        "IGST": round(igst, 2),
                        "CGST": round(cgst, 2),
                        "SGST": round(sgst, 2),
                        "Cess": round(cess, 2),
                        "Type": key.upper(),
                    })

        # ---- ISD ----
        for record in period_data.get("isd", []):
            other_rows.append({
                "Section": "ISD",
                "Return Period": period_str,
                "GSTIN/UIN": record.get("ctin", ""),
                "Document No": record.get("docnum", "") or record.get("inum", ""),
                "Document Date": record.get("docdt", "") or record.get("idt", ""),
                "IGST": sf(record.get("iamt")),
                "CGST": sf(record.get("camt")),
                "SGST": sf(record.get("samt")),
                "Cess": sf(record.get("csamt")),
            })

        # ---- IMPG & IMPGSEZ ----
        for key in ["impg", "impgsez"]:
            for record in period_data.get(key, []):
                other_rows.append({
                    "Section": key.upper(),
                    "Return Period": period_str,
                    "Port Code": record.get("portcd", ""),
                    "BOE No": record.get("benum", "") or record.get("boenum", ""),
                    "BOE Date": record.get("bedt", "") or record.get("boedt", ""),
                    "BOE Value": sf(record.get("beval")),
                    "Taxable Value": sf(record.get("txval")),
                    "IGST": sf(record.get("iamt")),
                    "Cess": sf(record.get("csamt")),
                })

        # ---- TCS ----
        for record in period_data.get("tcs", []):
            other_rows.append({
                "Section": "TCS",
                "Return Period": period_str,
                "GSTIN/UIN": record.get("ctin", ""),
                "Gross Value": sf(record.get("grsval")),
                "IGST": sf(record.get("iamt")),
                "CGST": sf(record.get("camt")),
                "SGST": sf(record.get("samt")),
            })

        # ---- TDS ----
        for record in period_data.get("tds", []):
            other_rows.append({
                "Section": "TDS",
                "Return Period": period_str,
                "GSTIN/UIN": record.get("ctin", ""),
                "Gross Value": sf(record.get("grsval")),
                "IGST": sf(record.get("iamt")),
                "CGST": sf(record.get("camt")),
                "SGST": sf(record.get("samt")),
            })

        return b2b_rows, cdnr_rows, other_rows

    def _fetch_supplier_names(self, gstin_list):
        """Fetches Trade names for unique GSTINs using Public Search API."""
        unique_gstins = list(set(gstin_list))
        gstin_map = {}

        platform_token = get_platform_token()
        if not platform_token:
            return {}

        headers = {
            "Authorization": platform_token,
            "x-api-key": settings.SANDBOX_API_KEY,
            "x-api-version": "1.0",
            "Content-Type": "application/json",
            "x-source": "primary"
        }

        search_url = f"{self.BASE_URL}/gst/compliance/public/gstin/search"

        # Fetch up to 100 suppliers to balance completeness and performance.
        for idx, gstin in enumerate(unique_gstins[:100]):
            try:
                status_code, resp_data = safe_api_call("POST", search_url, headers=headers, json={"gstin": gstin})
                if status_code == 200:
                    if "data" in resp_data and isinstance(resp_data["data"], dict) and "data" in resp_data["data"]:
                        inner = resp_data["data"]["data"]
                        name = inner.get("tradeNam") or inner.get("lgnm")
                        gstin_map[gstin] = name
            except:
                pass
            time.sleep(0.05)

        return gstin_map

    def _create_clean_excel(self, b2b_rows, cdnr_rows, other_rows):
        """Create Excel with strict column ordering - no sandbox noise."""
        output = io.BytesIO()

        b2b_cols = [
            "Return Period", "GSTIN/UIN", "Supplier Name", "Invoice No", "Invoice Date",
            "Invoice Value", "Place of Supply", "Reverse Charge", "Invoice Type",
            "Rate", "Taxable Value", "IGST", "CGST", "SGST", "Cess", "GSTR-1 Filed", "Type"
        ]
        b2b_num_fields = ["Invoice Value", "Taxable Value", "IGST", "CGST", "SGST", "Cess"]

        cdnr_cols = [
            "Return Period", "GSTIN/UIN", "Supplier Name", "Note No", "Note Date",
            "Note Value", "Note Type", "Rate", "Taxable Value", "IGST", "CGST", "SGST", "Cess", "Type"
        ]
        cdnr_num_fields = ["Note Value", "Taxable Value", "IGST", "CGST", "SGST", "Cess"]

        other_cols = [
            "Section", "Return Period", "GSTIN/UIN", "Port Code", "Document No", "Document Date",
            "BOE No", "BOE Date", "BOE Value", "Gross Value",
            "Taxable Value", "IGST", "CGST", "SGST", "Cess"
        ]
        other_num_fields = ["BOE Value", "Gross Value", "Taxable Value", "IGST", "CGST", "SGST", "Cess"]

        def process_df(rows, cols, num_fields):
            if not rows:
                return pd.DataFrame(columns=cols)
            df = pd.DataFrame(rows)
            for col in cols:
                if col not in df.columns:
                    df[col] = None
            for col in num_fields:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            return df[cols]

        df_b2b = process_df(b2b_rows, b2b_cols, b2b_num_fields)
        df_cdnr = process_df(cdnr_rows, cdnr_cols, cdnr_num_fields)
        df_other = process_df(other_rows, other_cols, other_num_fields)

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            has_data = False
            if not df_b2b.empty:
                df_b2b.to_excel(writer, sheet_name='B2B_Data', index=False)
                has_data = True
            if not df_cdnr.empty:
                df_cdnr.to_excel(writer, sheet_name='CDNR_Data', index=False)
                has_data = True
            if not df_other.empty:
                df_other.to_excel(writer, sheet_name='Other_Data', index=False)
                has_data = True

            if not has_data:
                pd.DataFrame({"Message": ["No Data Found for selected period"]}).to_excel(
                    writer, sheet_name="Summary", index=False
                )

        output.seek(0)
        return output
