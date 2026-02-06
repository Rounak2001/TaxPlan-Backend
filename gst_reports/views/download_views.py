# gst_reports/views/download_views.py

from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from gst_reports.services.gst_data_service import GSTDataService
from gst_reports.utils import get_valid_session
from gst_reports.services.gstr1_excel_service import GSTR1ExcelService
from gst_reports.services.gstr2b_excel_service import GSTR2BExcelService
from gst_reports.services.gstr2a_excel_service import GSTR2AExcelService
from gst_reports.services.gstr3b_excel_service import GSTR3BExcelService
from gst_reports.services.reco_excel_service import ReconciliationExcelService

@api_view(['POST'])
@permission_classes([AllowAny])
def download_gstr1(request):
    """
    Unified GSTR-1 Excel Download endpoint.
    """
    session_id = request.data.get('session_id')
    download_type = (request.data.get('download_type') or request.data.get('type') or 'monthly').lower()
    if download_type == 'month': download_type = 'monthly'
    if download_type == 'quarter': download_type = 'quarterly'
    if download_type in ['year', 'fy']: download_type = 'fy'
    
    # Standardize FY year parameter
    fy = request.data.get('fy') or request.data.get('fy_year')
    quarter = request.data.get('quarter')
    year = request.data.get('year')
    month = request.data.get('month')
    force_refresh = str(request.data.get('force_refresh', 'false')).lower() == 'true'

    if not session_id:
        return Response({'error': 'Session ID required'}, status=400)

    session, error = get_valid_session(session_id, user=request.user)
    if error:
        return Response({'error': error}, status=401)

    try:
        service = GSTR1ExcelService()
        output, filename = service.generate(
            user=session.user,
            gstin=session.gstin,
            access_token=session.taxpayer_token,
            download_type=download_type,
            fy=fy,
            quarter=quarter,
            year=year,
            month=month,
            force_refresh=force_refresh,
            username=session.gst_username
        )
        
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def download_gstr2b(request):
    """
    Unified GSTR-2B Excel Download endpoint.
    """
    session_id = request.data.get('session_id')
    download_type = (request.data.get('download_type') or request.data.get('type') or request.data.get('freq') or 'monthly').lower()
    if download_type in ['monthly', 'month']: download_type = 'monthly'
    elif download_type in ['quarterly', 'quarter']: download_type = 'quarterly'
    elif download_type in ['annually', 'fy', 'year']: download_type = 'fy'
    
    fy = request.data.get('fy_year') or request.data.get('fy')
    quarter = request.data.get('quarter') or request.data.get('qtr')
    year = request.data.get('year')
    month = request.data.get('month')
    force_refresh = str(request.data.get('force_refresh', 'false')).lower() == 'true'

    if not session_id:
        return Response({'error': 'Session ID required'}, status=400)

    session, error = get_valid_session(session_id, user=request.user)
    if error:
        return Response({'error': error}, status=401)

    try:
        service = GSTR2BExcelService()
        output, filename = service.generate(
            user=session.user,
            gstin=session.gstin,
            taxpayer_token=session.taxpayer_token,
            download_type=download_type,
            fy=fy,
            quarter=quarter,
            year=year,
            month=month,
            force_refresh=force_refresh,
            username=session.gst_username
        )
        
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def download_gstr3b(request):
    """
    Unified GSTR-3B Excel Download endpoint (Monthly/Quarterly/FY).
    """
    session_id = request.data.get('session_id')
    # Standardize period/download type
    period_type = (request.data.get('period_type') or request.data.get('download_type') or request.data.get('type') or 'monthly').lower()
    if period_type in ['monthly', 'month']: period_type = 'monthly'
    elif period_type in ['quarterly', 'quarter']: period_type = 'quarterly'
    elif period_type in ['annually', 'fy', 'year']: period_type = 'fy'

    fy = request.data.get('fy') or request.data.get('fy_year')
    quarter = request.data.get('quarter')
    year = request.data.get('year')
    month = request.data.get('month')
    force_refresh = str(request.data.get('force_refresh', 'false')).lower() == 'true'

    if not session_id:
        return Response({'error': 'Session ID required'}, status=400)

    session, error = get_valid_session(session_id, user=request.user)
    if error:
        return Response({'error': error}, status=401)

    try:
        service = GSTR3BExcelService()
        
        if period_type == 'monthly' and month and year:
            output, filename = service.generate(
                user=session.user,
                gstin=session.gstin,
                year=int(year),
                month=int(month),
                taxpayer_token=session.taxpayer_token,
                force_refresh=force_refresh,
                username=session.gst_username
            )
        else:
            # Handle Multi-period
            from gst_reports.services.gstr2b_excel_service import GSTR2BExcelService
            # We can re-use the period logic from 2B service
            periods, label = GSTR2BExcelService.get_periods(
                download_type=period_type if period_type != 'monthly' else 'monthly',
                fy=fy,
                quarter=quarter,
                year=year,
                month=month
            )
            output, filename = service.generate_multi(
                user=session.user,
                gstin=session.gstin,
                periods=periods,
                taxpayer_token=session.taxpayer_token,
                force_refresh=force_refresh,
                username=session.gst_username
            )

        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)
@api_view(['POST'])
@permission_classes([AllowAny])
def download_reco_1vs3b(request):
    """
    Download 1vs3b reconciliation results as Excel.
    """
    results = request.data.get('results', [])
    username = request.data.get('username', '')
    gstin = request.data.get('gstin', '')
    fy_year = request.data.get('fy_year', '')

    try:
        service = ReconciliationExcelService()
        output, filename = service.generate_1vs3b_excel(results, username, gstin, fy_year)

        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def download_reco_1vsbooks(request):
    """
    Download GSTR-1 vs Books reconciliation results as Excel.
    """
    results = request.data.get('results', {})
    username = request.data.get('username', '')
    gstin = request.data.get('gstin', '')
    year = request.data.get('year', '')

    try:
        service = ReconciliationExcelService()
        output, filename = service.generate_books_reco_excel(
            results, username, gstin, year, title="GSTR-1 vs Books Reconciliation"
        )

        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def download_reco_3bvsbooks(request):
    """
    Download GSTR-3B vs Books reconciliation results as Excel.
    """
    results = request.data.get('results', [])
    username = request.data.get('username', '')
    gstin = request.data.get('gstin', '')
    year = request.data.get('year', '')

    try:
        service = ReconciliationExcelService()
        output, filename = service.generate_books_reco_excel(
            results, username, gstin, year, title="GSTR-3B vs Books Reconciliation"
        )

        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def download_gstr2a(request):
    """
    Real-time GSTR-2A Excel Download endpoint.
    Bypasses cache.
    """
    session_id = request.data.get('session_id')
    download_type = (request.data.get('download_type') or request.data.get('type') or 'monthly').lower()
    if download_type == 'month': download_type = 'monthly'
    elif download_type == 'quarter': download_type = 'quarterly'
    elif download_type in ['year', 'fy']: download_type = 'fy'

    fy = request.data.get('fy') or request.data.get('fy_year')
    quarter = request.data.get('quarter')
    year = request.data.get('year')
    month = request.data.get('month')

    if not session_id:
        return Response({'error': 'Session ID required'}, status=400)
    
    # Validation for Monthly
    if download_type == 'monthly' and (not year or not month):
        return Response({'error': 'Year and Month are required for monthly download'}, status=400)
    # Validation for Quarterly
    if download_type == 'quarterly' and (not fy or not quarter):
        return Response({'error': 'Financial Year and Quarter are required for quarterly download'}, status=400)
    # Validation for FY
    if download_type == 'fy' and not fy:
        return Response({'error': 'Financial Year is required for annual download'}, status=400)

    session, error = get_valid_session(session_id, user=request.user)
    if error:
        return Response({'error': error}, status=401)

    try:
        service = GSTR2AExcelService()
        output, filename = service.generate(
            user=session.user,
            gstin=session.gstin,
            taxpayer_token=session.taxpayer_token,
            username=session.gst_username,
            download_type=download_type,
            fy=fy,
            quarter=quarter,
            year=year,
            month=month
        )
        
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)
