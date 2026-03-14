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
            ("ITR Salary Filing", 999, "1-2 days", "Form 16, Bank Statements, PAN, Aadhaar"),
            ("ITR Individual Business Filing", 2499, "2-3 days", "Books/Receipts, Bank Statements"),
            ("ITR LLP Filing", 3999, "3-5 days", "Financials, Partner Details, Tax Challans"),
            ("ITR NRI Filing", 2999, "2-4 days", "Passport, India Income Statements"),
            ("ITR Partnership Filing", 3499, "3-5 days", "Financials, Partner Details"),
            ("ITR Company Filing", 4999, "5-7 days", "Audited Financials, Tax Audit forms"),
            ("ITR Trust Filing", 5999, "5-7 days", "Trust Financials, Donation Records"),
            ("GSTR-1 & GSTR-3B (Monthly)", 1999, "1-2 days", "Sales/Purchase registers"),
            ("GSTR-1 & GSTR-3B (Quarterly)", 1499, "2-3 days", "Sales/Purchase registers"),
            ("GSTR CMP-08", 999, "1-2 days", "Composition turnover details"),
            ("GSTR-9", 2499, "3-5 days", "Yearly GST return data"),
            ("GSTR-9C", 3999, "3-7 days", "Audited financials, Reconciliation sheets"),
            ("GSTR-4 (Annual Return)", 1299, "2-3 days", "Annual turnover summary"),
            ("GSTR-10 (Final Return)", 1499, "1-3 days", "Closing stock, cancellation order"),
            ("TDS Monthly Payment", 499, "1 day", "Deduction summary, PAN details"),
            ("TDS Quarterly Filing", 999, "2-3 days", "Quarter deduction register, Challans"),
            ("TDS Revised Quarterly Filing", 1499, "2-4 days", "Original statement, Correction requirements"),
            ("Sale of Property (26QB)", 1999, "1-2 days", "Buyer/seller PAN, Agreement details"),
        ])

        # 2. Registrations Category
        seed_category("Registrations", "Business and compliance registrations", [
            ("PAN Application", 499, "1-2 days", "ID/Address proof"),
            ("TAN Registration", 999, "1-2 days", "Entity PAN, Address proof"),
            ("Aadhaar Validation", 299, "1 day", "Aadhaar info"),
            ("MSME Registration", 999, "1-2 days", "Aadhaar, PAN, Bank details"),
            ("Import Export Code (IEC)", 1999, "2-3 days", "PAN, Bank Proof, DSC"),
            ("Partnership Firm Registration", 3999, "5-7 days", "Deed, Partner IDs"),
            ("LLP Registration", 6999, "10-15 days", "Name approval, Partner KYC"),
            ("Private Limited Company Registration", 9999, "10-15 days", "Director KYC, Address Proof"),
            ("Startup India Registration", 2499, "7-10 days", "COI, Business Model"),
            ("Trust Formation", 5999, "10-15 days", "Trust Deed, Trustees IDs"),
            ("12A Registration", 4999, "Varies", "Trust Deed, Activity Proofs"),
            ("80G Registration", 4999, "Varies", "Trust Deed, Financials"),
            ("DSC (Digital Signature Certificate)", 1999, "1-2 days", "PAN, Aadhaar, Photo"),
            ("HUF PAN", 699, "1-2 days", "HUF Deed/Declaration, Karta ID"),
            ("NRI PAN", 999, "1-2 days", "Passport, Overseas address proof"),
            ("Foreign Entity Registration", 6999, "15-20 days", "Parent entity docs, Board resolution"),
        ])

        # 3. Notices Category
        seed_category("Notices", "Notice response and appeal handling", [
            ("ITR Appeal", 4999, "3-5 days", "Order copy, computations"),
            ("ITR Regular Assessment", 3999, "Duration of cycle", "Assessment notices, books"),
            ("ITR Tribunal", 9999, "Pre-hearing docs", "Prior orders, evidence"),
            ("GST Appeal", 4999, "3-6 days", "Order copy, reconciliations"),
            ("GST Regular Assessment", 3999, "Duration of cycle", "Officer queries, registers"),
            ("GST Tribunal", 9999, "Pre-hearing docs", "Lower orders, evidence"),
            ("TDS Appeal", 4499, "3-5 days", "TDS demand, deductee details"),
            ("TDS Regular Assessment", 3499, "Duration of cycle", "Quarterly statements, challans"),
            ("TDS Tribunal", 8999, "Pre-hearing docs", "Appellate orders, challans"),
        ])

        # 4. Consultation Category
        seed_category("Consultation", "General advisory and strategy sessions", [
            ("Tax Consultation", 299, "Session based", "Relevant income/details"),
            ("Compliance Advice", 299, "Session based", "Entity details"),
            ("Business Structuring", 499, "Session based", "Business goals/plans"),
        ])
        
        total_categories = ServiceCategory.objects.count()
        total_services = Service.objects.count()
        total_topics = Topic.objects.count()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully seeded {total_categories} categories, {total_services} services, and {total_topics} topics!'
            )
        )
