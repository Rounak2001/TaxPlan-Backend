"""
Management command to seed service categories and services from frontend catalogs
Run with: python manage.py seed_services
"""

from django.core.management.base import BaseCommand
from consultants.models import ServiceCategory, Service
from consultations.models import Topic


class Command(BaseCommand):
    help = 'Wipe and seed service categories, topics, and services from frontend catalog'

    def handle(self, *args, **kwargs):
        self.stdout.write('Wiping existing services data...')
        
        # Need to delete ConsultationBooking first because Topic is protected by it
        from consultations.models import ConsultationBooking
        ConsultationBooking.objects.all().delete()
        
        Service.objects.all().delete()
        Topic.objects.all().delete()
        ServiceCategory.objects.all().delete()
        
        self.stdout.write('Seeding service categories and services...')
        
        # Helper to create services and their corresponding topics
        def seed_category(cat_name, cat_desc, services_list):
            cat = ServiceCategory.objects.create(name=cat_name, description=cat_desc)
            # Create a "General" topic for the category
            Topic.objects.create(name=f"General {cat_name} Advice", category=cat, description=f"General consultation for {cat_name}")
            
            for title, price, tat, docs in services_list:
                service = Service.objects.create(category=cat, title=title, price=price, tat=tat, documents_required=docs)
                # Create a specific topic for this service
                Topic.objects.create(
                    name=title,
                    service=service,
                    category=cat,
                    description=f"Specific consultation for {title}"
                )
            return cat

        # 1. Returns Category
        seed_category("Returns", "ITR, GSTR, and TDS filing services", [
            ("ITR Salary Filing", 1, "1-2 days", "Form 16, Bank Statements, PAN, Aadhaar"),
            ("ITR Individual Business Filing", 1, "2-3 days", "Books/Receipts, Bank Statements"),
            ("ITR LLP Filing", 1, "3-5 days", "Financials, Partner Details, Tax Challans"),
            ("ITR NRI Filing", 1, "2-4 days", "Passport, India Income Statements"),
            ("ITR Partnership Filing", 1, "3-5 days", "Financials, Partner Details"),
            ("ITR Company Filing", 1, "5-7 days", "Audited Financials, Tax Audit forms"),
            ("ITR Trust Filing", 1, "5-7 days", "Trust Financials, Donation Records"),
            ("GSTR-1 & GSTR-3B (Monthly)", 1, "1-2 days", "Sales/Purchase registers"),
            ("GSTR-1 & GSTR-3B (Quarterly)", 1, "2-3 days", "Sales/Purchase registers"),
            ("GSTR CMP-08", 1, "1-2 days", "Composition turnover details"),
            ("GSTR-9", 1, "3-5 days", "Yearly GST return data"),
            ("GSTR-9C", 1, "3-7 days", "Audited financials, Reconciliation sheets"),
            ("GSTR-4 (Annual Return)", 1, "2-3 days", "Annual turnover summary"),
            ("GSTR-10 (Final Return)", 1, "1-3 days", "Closing stock, cancellation order"),
            ("TDS Monthly Payment", 1, "1 day", "Deduction summary, PAN details"),
            ("TDS Quarterly Filing", 1, "2-3 days", "Quarter deduction register, Challans"),
            ("TDS Revised Quarterly Filing", 1, "2-4 days", "Original statement, Correction requirements"),
            ("Sale of Property (26QB)", 1, "1-2 days", "Buyer/seller PAN, Agreement details"),
        ])

        # 2. Registrations Category
        seed_category("Registrations", "Business and compliance registrations", [
            ("PAN Application", 1, "1-2 days", "ID/Address proof"),
            ("TAN Registration", 1, "1-2 days", "Entity PAN, Address proof"),
            ("Aadhaar Validation", 1, "1 day", "Aadhaar info"),
            ("MSME Registration", 1, "1-2 days", "Aadhaar, PAN, Bank details"),
            ("Import Export Code (IEC)", 1, "2-3 days", "PAN, Bank Proof, DSC"),
            ("Partnership Firm Registration", 1, "5-7 days", "Deed, Partner IDs"),
            ("LLP Registration", 1, "10-15 days", "Name approval, Partner KYC"),
            ("Private Limited Company Registration", 1, "10-15 days", "Director KYC, Address Proof"),
            ("Startup India Registration", 1, "7-10 days", "COI, Business Model"),
            ("Trust Formation", 1, "10-15 days", "Trust Deed, Trustees IDs"),
            ("12A Registration", 1, "Varies", "Trust Deed, Activity Proofs"),
            ("80G Registration", 1, "Varies", "Trust Deed, Financials"),
            ("DSC (Digital Signature Certificate)", 1, "1-2 days", "PAN, Aadhaar, Photo"),
            ("HUF PAN", 1, "1-2 days", "HUF Deed/Declaration, Karta ID"),
            ("NRI PAN", 1, "1-2 days", "Passport, Overseas address proof"),
            ("Foreign Entity Registration", 1, "15-20 days", "Parent entity docs, Board resolution"),
        ])

        # 3. Notices Category
        seed_category("Notices", "Notice response and appeal handling", [
            ("ITR Appeal", 1, "3-5 days", "Order copy, computations"),
            ("ITR Regular Assessment", 1, "Duration of cycle", "Assessment notices, books"),
            ("ITR Tribunal", 1, "Pre-hearing docs", "Prior orders, evidence"),
            ("GST Appeal", 1, "3-6 days", "Order copy, reconciliations"),
            ("GST Regular Assessment", 1, "Duration of cycle", "Officer queries, registers"),
            ("GST Tribunal", 1, "Pre-hearing docs", "Lower orders, evidence"),
            ("TDS Appeal", 1, "3-5 days", "TDS demand, deductee details"),
            ("TDS Regular Assessment", 1, "Duration of cycle", "Quarterly statements, challans"),
            ("TDS Tribunal", 1, "Pre-hearing docs", "Appellate orders, challans"),
        ])

        # 4. Consultation Category
        seed_category("Consultation", "General advisory and strategy sessions", [
            ("Tax Consultation", 1, "Session based", "Relevant income/details"),
            ("Compliance Advice", 1, "Session based", "Entity details"),
            ("Business Structuring", 1, "Session based", "Business goals/plans"),
        ])
        
        total_categories = ServiceCategory.objects.count()
        total_services = Service.objects.count()
        total_topics = Topic.objects.count()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully seeded {total_categories} categories, {total_services} services, and {total_topics} topics!'
            )
        )