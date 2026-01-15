import logging
from django.db import models
from core_auth.models import ConsultantProfile, User

logger = logging.getLogger(__name__)

def get_available_consultant(service_type: str):
    """
    Finds an available consultant based on service type and lowest workload.
    """
    # For SQLite compatibility and general robustness
    consultants = ConsultantProfile.objects.all().order_by('current_load')
    
    for profile in consultants:
        if service_type in profile.services and profile.current_load < profile.max_capacity:
            return profile.user
            
    return None

def link_gst_data(client_id):
    """
    Placeholder for Phase 2 GST integration.
    """
    logger.info(f"Ready for GST Integration for client {client_id}")
    print(f"Ready for GST Integration for client {client_id}")
