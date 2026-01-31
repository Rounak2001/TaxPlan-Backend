import pandas as pd
import io
import time
from datetime import datetime
from django.conf import settings
from gst_reports.utils import get_gst_headers, safe_api_call, get_platform_token

class GSTR2AExcelService:
    """
    Service for fetching GSTR-2A data and generating Excel reports.
    Bypasses caching as per requirements (dynamic data).
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

    def generate(self, user, gstin, taxpayer_token, year, month, username):
        """
        Main entry point for generating GSTR-2A Excel.
        """
        # 1. Fetch data for all sections
        all_data = self._fetch_all_sections(gstin, year, month, taxpayer_token)
        
        # 2. Extract unique GSTINs and fetch supplier names
        all_ctins = []
        for section, records in all_data.items():
            for record in records:
                if "ctin" in record:
                    all_ctins.append(record["ctin"])
        
        if all_ctins:
            supplier_names = self._fetch_supplier_names(all_ctins)
            # Map names back
            for section, records in all_data.items():
                for record in records:
                    if "ctin" in record:
                        record["supplier_name"] = supplier_names.get(record["ctin"], "")

        # 3. Process into Excel
        output = self._create_excel(all_data)
        filename = f"GSTR2A_{gstin}_{month:02d}{year}.xlsx"
        
        return output, filename

    def _fetch_all_sections(self, gstin, year, month, taxpayer_token):
        headers = get_gst_headers(taxpayer_token)
        all_data = {section: [] for section in self.sections}
        period = f"{month:02d}{year}"

        for section in self.sections:
            # Sandbox path params for GSTR-2A
            api_url = f"{self.BASE_URL}/gst/compliance/tax-payer/gstrs/gstr-2a/{section}/{year}/{month:02d}"
            
            # Note: Sandbox sometimes expects gstin in params
            status_code, response_data = safe_api_call("GET", api_url, headers=headers, params={"gstin": gstin})
            
            if status_code == 200:
                from gst_reports.utils import unwrap_sandbox_data
                data = unwrap_sandbox_data(response_data)
                
                records = []
                # Handle possible structures
                if isinstance(data, dict):
                    if section in data:
                        records = data[section]
                    elif "data" in data and isinstance(data["data"], dict) and section in data["data"]:
                        records = data["data"][section]

                if records:
                    self._flatten_and_append(all_data, section, records, period)
            
            time.sleep(0.3) # Rate limit courtesy
            
        return all_data

    def _flatten_and_append(self, all_data, section, records, period):
        """
        Flattens GSTR-2A nested structures.
        """
        if section in ["b2b", "b2ba", "cdn", "cdna"]:
            for supplier in records:
                supplier_meta = {k: v for k, v in supplier.items() if k not in ["inv", "nt"]}
                
                # B2B/B2BA uses 'inv'
                if "inv" in supplier:
                    for inv in supplier["inv"]:
                        inv_meta = {k: v for k, v in inv.items() if k != "itms"}
                        if "itms" in inv:
                            for item in inv["itms"]:
                                itm_det = item.get("itm_det", {})
                                flat_record = {**supplier_meta, **inv_meta, **itm_det, "Period": period}
                                all_data[section].append(flat_record)
                        else:
                            all_data[section].append({**supplier_meta, **inv_meta, "Period": period})
                
                # CDN/CDNA uses 'nt'
                elif "nt" in supplier:
                    for note in supplier["nt"]:
                        note_meta = {k: v for k, v in note.items() if k != "itms"}
                        if "itms" in note:
                            for item in note["itms"]:
                                itm_det = item.get("itm_det", {})
                                flat_record = {**supplier_meta, **note_meta, **itm_det, "Period": period}
                                all_data[section].append(flat_record)
                        else:
                            all_data[section].append({**supplier_meta, **note_meta, "Period": period})
        else:
            # Flat sections
            for item in records:
                item["Period"] = period
                all_data[section].append(item)

    def _fetch_supplier_names(self, gstin_list):
        """
        Fetches Trade names for unique GSTINs using Public Search API.
        """
        unique_gstins = list(set(gstin_list))
        gstin_map = {}
        
        # Get Platform JWT (Public APIs usually need this or it might be passed down)
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

        for idx, gstin in enumerate(unique_gstins[:20]): # Soft cap for initial implementation sanity
            try:
                # Sandbox Search usually expects POST with gstin in body
                status_code, resp_data = safe_api_call("POST", search_url, headers=headers, json={"gstin": gstin})
                if status_code == 200:
                    # Unwrap Sandbox nested data
                    if "data" in resp_data and isinstance(resp_data["data"], dict) and "data" in resp_data["data"]:
                        inner = resp_data["data"]["data"]
                        name = inner.get("tradeNam") or inner.get("lgnm")
                        gstin_map[gstin] = name
            except:
                pass
            time.sleep(0.1)
                
        return gstin_map

    def _create_excel(self, all_data):
        output = io.BytesIO()
        
        # Column mapping for readability
        col_map = {
            "ctin": "Supplier GSTIN",
            "supplier_name": "Supplier Name",
            "cfs": "GSTR-1 Status",
            "inum": "Invoice Number",
            "idt": "Invoice Date",
            "val": "Invoice Value",
            "rt": "Rate",
            "txval": "Taxable Value",
            "iamt": "IGST",
            "camt": "CGST",
            "samt": "SGST",
            "csamt": "Cess",
            "nt_num": "Note Number",
            "nt_dt": "Note Date",
            "p_gst": "Pre GST"
        }

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            has_data = False
            for section, records in all_data.items():
                if records:
                    has_data = True
                    df = pd.DataFrame(records)
                    df.rename(columns=col_map, inplace=True)
                    
                    # Ensure important columns are upfront
                    priority = ["Supplier Name", "Supplier GSTIN", "Invoice Number", "Invoice Date", "Rate", "Taxable Value", "IGST", "CGST", "SGST"]
                    cols = [c for c in priority if c in df.columns] + [c for c in df.columns if c not in priority]
                    df = df[cols]
                    
                    sheet_name = section.upper()[:31]
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
            
            if not has_data:
                pd.DataFrame({"Message": ["No Data Found for selected period"]}).to_excel(writer, sheet_name="Summary")

        output.seek(0)
        return output
