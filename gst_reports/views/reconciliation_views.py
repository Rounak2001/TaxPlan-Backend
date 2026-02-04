# gst_reports/views/reconciliation_views.py

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from datetime import date, timedelta
from django.http import HttpResponse
import pandas as pd

from gst_reports.services.gst_data_service import GSTDataService
from gst_reports.utils import get_valid_session
from gst_reports.services.gstr1_vs_3b_service import reconcile_gstr1_vs_3b, reconcile_comprehensive
from gst_reports.services.gstr1_vs_books_service import GSTR1BooksReconciliationService
from gst_reports.services.gstr3b_vs_books_service import GSTR3BBooksReconciliationService
from gst_reports.services.gstr2b_manual_reco_service import GSTR2BManualReconciliationService
import pandas as pd
import numpy as np
from gst_reports.services.gstr2b_vs_books_service import GSTR2BBooksReconciliationService
from gst_reports.models import GSTReport
import traceback

@api_view(["POST"])
@permission_classes([AllowAny])
def reconcile_1_vs_3b(request):
    """
    Unified GSTR-1 vs GSTR-3B Reconciliation endpoint.
    """
    try:
        fy_year = int(request.data.get("year"))
        session_id = request.data.get("session_id")
        reco_type = str(request.data.get("reco_type", "FY")).upper()
        if reco_type == "MONTH": reco_type = "MONTHLY"
        if reco_type == "QUARTER": reco_type = "QUARTERLY"
        month = request.data.get("month")
        quarter = request.data.get("quarter")
        force_refresh = str(request.data.get("force_refresh", "false")).lower() == "true"

        if not session_id:
            return Response({"error": "Session ID required"}, status=400)

        session, error = get_valid_session(session_id, user=request.user)
        if error:
            return Response({"error": error}, status=401)

        # Date Cutoff Logic
        today = date.today()
        if today.day <= 10:
            cutoff_date = (today.replace(day=1) - timedelta(days=45))
        else:
            cutoff_date = (today.replace(day=1) - timedelta(days=15))
        
        cutoff_y, cutoff_m = cutoff_date.year, cutoff_date.month

        # Generate months list
        months_to_fetch = []
        if reco_type == "FY":
            for m in range(4, 13): months_to_fetch.append((fy_year, m))
            for m in range(1, 4): months_to_fetch.append((fy_year + 1, m))
        elif reco_type == "QUARTERLY":
            q = int(quarter)
            months_to_fetch = [(fy_year if m >= 4 else fy_year + 1, m) for m in ([4,5,6] if q==1 else [7,8,9] if q==2 else [10,11,12] if q==3 else [1,2,3])]
        elif reco_type == "MONTHLY":
            m = int(month)
            months_to_fetch = [(fy_year if m >= 4 else fy_year + 1, m)]

        final_data = []
        for y, m in months_to_fetch:
            if y > cutoff_y or (y == cutoff_y and m > cutoff_m):
                continue
                
            res = reconcile_gstr1_vs_3b(session.user, y, m, session.gstin, session.taxpayer_token, force_refresh)
            if res:
                final_data.append(res)

        # Save report if needed (Optional: current frontend might not use DB records for this yet)
        GSTReport.objects.create(
            user=session.user,
            report_type='GSTR1_VS_3B',
            gst_username=session.gst_username,
            gstin=session.gstin,
            year=fy_year,
            month=int(month) if month else None,
            quarter=quarter,
            reco_type=reco_type.upper(),
            report_data=final_data
        )

        return Response({
            "status": "success",
            "gstin": session.gstin,
            "data": final_data
        })
    except Exception as e:
        return Response({"error": str(e)}, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def reconcile_1_vs_books(request):
    """
    Unified GSTR-1 vs Books Reconciliation endpoint.
    """
    session_id = request.data.get('session_id')
    reco_type = str(request.data.get('reco_type', 'MONTHLY')).upper()
    if reco_type == "MONTH": reco_type = "MONTHLY"
    if reco_type == "QUARTER": reco_type = "QUARTERLY"
    year = request.data.get('year')
    month = request.data.get('month')
    quarter = request.data.get('quarter')
    force_refresh = str(request.data.get('force_refresh', 'false')).lower() == 'true'

    if not session_id:
        return Response({'error': 'Session ID required'}, status=400)

    session, error = get_valid_session(session_id, user=request.user)
    if error:
        return Response({'error': error}, status=401)

    if 'file' not in request.FILES:
        return Response({'error': 'No file uploaded'}, status=400)

    try:
        file_bytes = request.FILES['file'].read()
        service = GSTR1BooksReconciliationService(user=session.user, taxpayer_token=session.taxpayer_token)
        results = service.run(
            file_bytes=file_bytes,
            reco_type=reco_type,
            year=int(year),
            month=int(month) if month else None,
            quarter=quarter,
            business_gstin=session.gstin,
            force_refresh=force_refresh
        )

        # Transform DataFrames to dict for JSON response
        json_results = {}
        for k, v in results.items():
            if isinstance(v, pd.DataFrame):
                json_results[k] = v.to_dict(orient='records')
            else:
                json_results[k] = v

        # Save report
        GSTReport.objects.create(
            user=session.user,
            report_type='GSTR1_VS_BOOK',
            gst_username=session.gst_username,
            gstin=session.gstin,
            year=int(year),
            month=int(month) if month else None,
            quarter=quarter,
            reco_type=reco_type,
            report_data=json_results
        )

        return Response({
            'status': 'success',
            'data': json_results,
            'summary': json_results.get('summary'),
            'session_info': {
                'party_name': session.gst_username, # Or fetch legal name if available
                'gstin': session.gstin,
                'year': year,
                'month': month,
                'quarter': quarter,
                'reco_type': reco_type
            }
        })
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def reconcile_3b_vs_books(request):
    """
    Unified GSTR-3B vs Books Reconciliation endpoint.
    """
    session_id = request.data.get('session_id')
    reco_type = str(request.data.get('reco_type', 'MONTHLY')).upper()
    if reco_type == "MONTH": reco_type = "MONTHLY"
    if reco_type == "QUARTER": reco_type = "QUARTERLY"
    year = request.data.get('year')
    month = request.data.get('month')
    quarter = request.data.get('quarter')
    force_refresh = str(request.data.get('force_refresh', 'false')).lower() == 'true'

    if not session_id:
        return Response({'error': 'Session ID required'}, status=400)

    session, error = get_valid_session(session_id, user=request.user)
    if error:
        return Response({'error': error}, status=401)

    if 'file' not in request.FILES:
        return Response({'error': 'No file uploaded'}, status=400)

    try:
        df = pd.read_excel(request.FILES['file'])
        service = GSTR3BBooksReconciliationService(user=session.user, taxpayer_token=session.taxpayer_token)
        
        final_report = service.run(
            df=df,
            reco_type=reco_type,
            year=int(year),
            gstin=session.gstin,
            month=month,
            quarter=quarter,
            force_refresh=force_refresh
        )

        # Save report
        GSTReport.objects.create(
            user=session.user,
            report_type='GSTR3B_VS_BOOK',
            gst_username=session.gst_username,
            gstin=session.gstin,
            year=int(year),
            month=int(month) if month else None,
            quarter=quarter,
            reco_type=reco_type,
            report_data=final_report
        )

        return Response({
            'status': 'success',
            'data': final_report,
            'session_info': {
                'party_name': session.gst_username,
                'gstin': session.gstin,
                'year': year,
                'month': month,
                'quarter': quarter,
                'reco_type': reco_type
            }
        })
    except Exception as e:
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)
@api_view(['POST'])
@permission_classes([AllowAny])
def reconcile_2b_vs_books(request):
    """
    Unified GSTR-2B vs Books Reconciliation endpoint.
    """
    session_id = request.data.get('session_id')
    reco_type = str(request.data.get('reco_type', 'MONTHLY')).upper()
    if reco_type == "MONTH": reco_type = "MONTHLY"
    if reco_type == "QUARTER": reco_type = "QUARTERLY"
    fy_year = request.data.get('year')
    month = request.data.get('month')
    quarter = request.data.get('quarter')
    tolerance = float(request.data.get('tolerance', 1.0))
    force_refresh = str(request.data.get('force_refresh', 'false')).lower() == 'true'

    if not session_id:
        return Response({'error': 'Session ID required'}, status=400)

    session, error = get_valid_session(session_id, user=request.user)
    if error:
        return Response({'error': error}, status=401)

    if 'file' not in request.FILES:
        return Response({'error': 'No books file uploaded'}, status=400)

    try:
        books_df = pd.read_excel(request.FILES['file'])
        service = GSTR2BBooksReconciliationService(user=session.user, taxpayer_token=session.taxpayer_token)
        
        result = service.run(
            books_df=books_df,
            reco_type=reco_type,
            year=int(fy_year),
            gstin=session.gstin,
            month_name=month if reco_type == 'MONTHLY' else None,
            quarter_name=quarter if reco_type == 'QUARTERLY' else None,
            tolerance=tolerance,
            force_refresh=force_refresh
        )

        GSTReport.objects.create(
            user=session.user,
            report_type='GSTR2B_VS_BOOK',
            gst_username=session.gst_username,
            gstin=session.gstin,
            year=int(fy_year),
            month=int(month) if month else None,
            quarter=quarter,
            reco_type=reco_type.upper(),
            report_data=result
        )

        response_data = result # Assign result to response_data
        return Response({
            'status': 'success',
            'data': response_data,
            'session_info': {
                'party_name': session.gst_username,
                'gstin': session.gstin,
                'year': fy_year,
                'month': month,
                'quarter': quarter,
                'reco_type': reco_type
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)

@api_view(["POST"])
@permission_classes([AllowAny])
def reconcile_comprehensive_view(request):
    """
    Unified GSTR-1 vs GSTR-3B vs GSTR-2B Comprehensive Reconciliation endpoint.
    """
    try:
        fy_year = int(request.data.get("year"))
        session_id = request.data.get("session_id")
        reco_type = str(request.data.get("reco_type", "FY")).upper()
        if reco_type == "MONTH": reco_type = "MONTHLY"
        if reco_type == "QUARTER": reco_type = "QUARTERLY"
        month = request.data.get("month")
        quarter = request.data.get("quarter")
        force_refresh = str(request.data.get("force_refresh", "false")).lower() == "true"

        if not session_id:
            return Response({"error": "Session ID required"}, status=400)

        session, error = get_valid_session(session_id, user=request.user)
        if error:
            return Response({"error": error}, status=401)

        # Generate months list
        months_to_fetch = []
        if reco_type == "FY":
            for m in range(4, 13): months_to_fetch.append((fy_year, m))
            for m in range(1, 4): months_to_fetch.append((fy_year + 1, m))
        elif reco_type == "QUARTERLY":
            q = int(quarter)
            months_to_fetch = [(fy_year if m >= 4 else fy_year + 1, m) for m in ([4,5,6] if q==1 else [7,8,9] if q==2 else [10,11,12] if q==3 else [1,2,3])]
        elif reco_type == "MONTHLY":
            m = int(month)
            months_to_fetch = [(fy_year if m >= 4 else fy_year + 1, m)]

        final_data = []
        for y, m in months_to_fetch:
            res = reconcile_comprehensive(session.user, y, m, session.gstin, session.taxpayer_token, force_refresh)
            if res:
                final_data.append(res)

        GSTReport.objects.create(
            user=session.user,
            report_type='GSTR1_3B_2B_COMPREHENSIVE',
            gst_username=session.gst_username,
            gstin=session.gstin,
            year=fy_year,
            month=int(month) if month else None,
            quarter=quarter,
            reco_type=reco_type.upper(),
            report_data=final_data
        )

        return Response({
            "status": "success",
            "gstin": session.gstin,
            "data": final_data,
            "session_info": {
                "party_name": session.gst_username,
                "gstin": session.gstin,
                "year": fy_year,
                "reco_type": reco_type,
                "month": month,
                "quarter": quarter
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=500)

@api_view(['POST'])
def reconcile_2b_books_manual(request):
    """
    Manual GSTR-2B vs Books reconciliation using two uploaded files.
    """
    try:
        file_2b = request.FILES.get("file_2b")
        file_books = request.FILES.get("file_books")

        if not file_2b or not file_books:
            return Response({"detail": "Both files required"}, status=400)

        selected_fy = request.data.get("selected_fy")
        period_type = request.data.get("period_type")
        selected_period_val = request.data.get("selected_period_val")
        tolerance = int(request.data.get("tolerance", 1))

        service = GSTR2BManualReconciliationService()
        
        # 1. Process 2B Central (Sheet 0 for B2B)
        xls_2b = pd.ExcelFile(file_2b)
        df_2b_main = pd.read_excel(xls_2b, 0)
        df_2b_main = service.normalize_columns(df_2b_main)
        df_2b_main = service.preprocess_data(df_2b_main)

        # Handle CDNR if exists
        df_2b_cdnr = pd.DataFrame()
        cdnr_sheet = next((s for s in xls_2b.sheet_names if "cdnr" in s.lower() or "credit" in s.lower()), None)
        if cdnr_sheet:
            raw_cdnr = pd.read_excel(xls_2b, cdnr_sheet)
            raw_cdnr = service.normalize_columns(raw_cdnr)
            # Loose renaming map for CDNR specific headers in manual files
            rename_map = {
                "Credit/Debit Note No": "Invoice", "Note No": "Invoice",
                "Credit/Debit Note Date": "Date", "Note Date": "Date",
                "Taxable Value": "Taxable"
            }
            actual_rename = {k: v for k, v in rename_map.items() if k in raw_cdnr.columns}
            raw_cdnr = raw_cdnr.rename(columns=actual_rename)
            df_2b_cdnr = service.preprocess_data(raw_cdnr)
            df_2b_cdnr["Type"] = "CDNR"

        df_2b_final = pd.concat([df_2b_main, df_2b_cdnr], ignore_index=True)

        # 2. Process Books
        df_books_raw = pd.read_excel(file_books, sheet_name=0)
        df_books_raw = service.normalize_columns(df_books_raw)
        df_books_final = service.preprocess_data(df_books_raw)

        # 3. Target Periods
        target_dates, period_label = service.get_target_periods(selected_fy, period_type, selected_period_val)
        
        # 4. Reconcile
        results = service.run_reconciliation(df_2b_final, df_books_final, target_dates, tolerance)

        # 5. Export / Response
        if request.query_params.get("export") == "excel":
            excel_file = service.generate_advanced_excel(results, period_label)
            response = HttpResponse(
                excel_file.getvalue(), 
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="Reconciliation_{period_label}.xlsx"'
            return response

        def clean_for_json(df):
            if df is None or df.empty: return []
            df = df.fillna("").replace([np.inf, -np.inf], 0)
            for col in df.columns:
                if pd.api.types.is_datetime64_any_dtype(df[col]):
                    df[col] = df[col].dt.strftime("%Y-%m-%d")
            return df.to_dict(orient="records")

        return Response({
            "periodLabel": period_label,
            "tolerance": tolerance,
            "metrics": {k: len(v) if isinstance(v, pd.DataFrame) else 0 for k, v in results.items()},
            "tables": {k: clean_for_json(v) for k, v in results.items() if isinstance(v, pd.DataFrame)}
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"detail": str(e)}, status=500)
