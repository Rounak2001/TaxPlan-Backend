"""
GST Data Service - Centralized caching layer for Sandbox API responses.
Moved to gst_reports/services/gst_data_service.py
"""
import requests
import io
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from gst_reports.models import CachedGSTResponse
from gst_reports.utils import get_gst_headers, safe_api_call


# Base URL for Sandbox GST API
SANDBOX_BASE_URL = "https://api.sandbox.co.in/gst/compliance/tax-payer"

# Default cache TTL: 7 days (can be overridden per call)
DEFAULT_CACHE_TTL_DAYS = 7


class GSTDataService:
    """
    Centralized service for fetching GST data with automatic caching.
    """
    
    # =========================================================================
    # GSTR-1 FETCHERS
    # =========================================================================
    @staticmethod
    def get_gstr1_section(user, gstin, section, year, month, taxpayer_token, force_refresh=False):
        """
        Fetch a specific GSTR-1 section (b2b, b2cs, exp, cdnr, hsn, etc.).
        """
        return GSTDataService._fetch_with_cache(
            user=user,
            gstin=gstin,
            return_type="GSTR1",
            section=section,
            year=year,
            month=month,
            taxpayer_token=taxpayer_token,
            api_url=f"{SANDBOX_BASE_URL}/gstrs/gstr-1/{section}/{year}/{month:02d}",
            force_refresh=force_refresh
        )
    
    @staticmethod
    def get_gstr1_summary(user, gstin, year, month, taxpayer_token, force_refresh=False):
        """Fetch GSTR-1 summary (no section path)."""
        return GSTDataService._fetch_with_cache(
            user=user,
            gstin=gstin,
            return_type="GSTR1",
            section="summary",
            year=year,
            month=month,
            taxpayer_token=taxpayer_token,
            api_url=f"{SANDBOX_BASE_URL}/gstrs/gstr-1/{year}/{month:02d}",
            force_refresh=force_refresh
        )
    
    # =========================================================================
    # GSTR-3B FETCHERS
    # =========================================================================
    @staticmethod
    def get_gstr3b_filed(user, gstin, year, month, taxpayer_token, force_refresh=False):
        """Fetch filed GSTR-3B data."""
        return GSTDataService._fetch_with_cache(
            user=user,
            gstin=gstin,
            return_type="GSTR3B",
            section="filed",
            year=year,
            month=month,
            taxpayer_token=taxpayer_token,
            api_url=f"{SANDBOX_BASE_URL}/gstrs/gstr-3b/{year}/{month:02d}",
            force_refresh=force_refresh
        )
    
    @staticmethod
    def get_gstr3b_auto_liability(user, gstin, year, month, taxpayer_token, force_refresh=False):
        """Fetch GSTR-3B auto-calculated liability."""
        return GSTDataService._fetch_with_cache(
            user=user,
            gstin=gstin,
            return_type="GSTR3B",
            section="auto-liability-calc",
            year=year,
            month=month,
            taxpayer_token=taxpayer_token,
            api_url=f"{SANDBOX_BASE_URL}/gstrs/gstr-3b/{year}/{month:02d}/auto-liability-calc",
            force_refresh=force_refresh
        )
    
    # =========================================================================
    # GSTR-2B FETCHERS
    # =========================================================================
    @staticmethod
    def get_gstr2b(user, gstin, year, month, taxpayer_token, force_refresh=False):
        """Fetch GSTR-2B data."""
        return GSTDataService._fetch_with_cache(
            user=user,
            gstin=gstin,
            return_type="GSTR2B",
            section="",
            year=year,
            month=month,
            taxpayer_token=taxpayer_token,
            api_url=f"{SANDBOX_BASE_URL}/gstrs/gstr-2b/{year}/{month:02d}",
            force_refresh=force_refresh
        )

    # =========================================================================
    # TAXPAYER DETAILS FETCHERS
    # =========================================================================
    @staticmethod
    def get_taxpayer_details(user, gstin, taxpayer_token, force_refresh=False):
        """Fetch general taxpayer details."""
        return GSTDataService._fetch_with_cache(
            user=user,
            gstin=gstin,
            return_type="DETAILS",
            section="taxpayer-details",
            year=0,
            month=0,
            taxpayer_token=taxpayer_token,
            api_url=f"{SANDBOX_BASE_URL}/details?gstin={gstin}",
            force_refresh=force_refresh
        )

    @staticmethod
    def _fetch_with_cache(user, gstin, return_type, section, year, month, taxpayer_token, api_url, force_refresh=False):
        """Core method with cache-first strategy."""
        cache_key = {
            'user': user,
            'gstin': gstin,
            'return_type': return_type,
            'section': section,
            'year': year,
            'month': month
        }
        
        if not force_refresh:
            cached = CachedGSTResponse.objects.filter(**cache_key).first()
            if cached:
                # SELF-HEALING: Validate cached content before serving
                data = cached.raw_json
                is_invalid = False
                if isinstance(data, dict):
                    sc = data.get("status_cd")
                    err_obj = data.get("error") if isinstance(data.get("error"), dict) else {}
                    msg = str(data.get("message", "") or err_obj.get("message", "")).lower()
                    
                    # status_cd 0 with "No data found" or "No document found" is a successful empty response
                    if (sc == "0" or sc == 0) and ("no data found" in msg or "no document found" in msg):
                        is_invalid = False
                    elif sc == "0" or sc == 0 or "error" in data:
                        is_invalid = True
                    elif data.get("status") == "error" or data.get("success") is False:
                        is_invalid = True
                
                if is_invalid:
                    print(f"[GST_CACHE] INVALID content found on HIT, deleting: {return_type}/{section} for {gstin}")
                    cached.delete()
                    # Do not return, let it fall through to fresh fetch
                else:
                    cache_age = timezone.now() - cached.fetched_at
                    if cache_age < timedelta(days=DEFAULT_CACHE_TTL_DAYS):
                        print(f"[GST_CACHE] HIT: {return_type}/{section} for {gstin} (Period: {month}/{year})")
                        return data
        
        headers = get_gst_headers(taxpayer_token)
        status_code, response_data = safe_api_call("GET", api_url, headers=headers)
        
        if status_code != 200:
            return None
        
        # Check for Sandbox errors within 200 response
        if isinstance(response_data, dict):
            sc = response_data.get("status_cd")
            err_obj = response_data.get("error") if isinstance(response_data.get("error"), dict) else {}
            msg = str(response_data.get("message", "") or err_obj.get("message", "")).lower()
            
            # If it's a "No data found" success-error, we process it as successful but empty
            if (sc == "0" or sc == 0) and ("no data found" in msg or "no document found" in msg):
                pass # Allow it to be unwrapped and saved as empty
            elif sc == "0" or sc == 0 or "error" in response_data:
                print(f"[GST_CACHE] ERROR in response for {gstin}")
                return None

        from gst_reports.utils import unwrap_sandbox_data
        data = unwrap_sandbox_data(response_data)
        
        # Final safety check after unwrapping
        if isinstance(data, dict):
             sc = data.get("status_cd")
             err_obj = data.get("error") if isinstance(data.get("error"), dict) else {}
             msg = str(data.get("message", "") or err_obj.get("message", "")).lower()
             
             if (sc == "0" or sc == 0) and ("no data found" in msg or "no document found" in msg):
                 pass # Legitimate empty response
             elif sc == "0" or sc == 0 or "error" in data:
                 print(f"[GST_CACHE] ERROR after unwrap, NOT saving: {return_type}/{section} for {gstin}")
                 return None

        print(f"[GST_CACHE] SAVING: {return_type}/{section} for {gstin} (Period: {month}/{year})")
        CachedGSTResponse.objects.update_or_create(
            **cache_key,
            defaults={'raw_json': data, 'fetched_at': timezone.now()}
        )
        return data
    
    # =========================================================================
    # CACHE MANAGEMENT
    # =========================================================================
    @staticmethod
    def clear_cache_for_gstin(gstin):
        count, _ = CachedGSTResponse.objects.filter(gstin=gstin).delete()
        return count
    
    @staticmethod
    def clear_cache_for_period(gstin, return_type, year, month):
        count, _ = CachedGSTResponse.objects.filter(
            gstin=gstin, return_type=return_type, year=year, month=month
        ).delete()
        return count
    
    @staticmethod
    def cleanup_expired_cache(days=None):
        ttl_days = days or DEFAULT_CACHE_TTL_DAYS
        cutoff = timezone.now() - timedelta(days=ttl_days)
        count, _ = CachedGSTResponse.objects.filter(fetched_at__lt=cutoff).delete()
        return count
