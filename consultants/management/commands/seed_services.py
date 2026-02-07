"""
Management command to seed service categories and services from ClientServices.jsx
Run with: python manage.py seed_services
"""

from django.core.management.base import BaseCommand
from consultants.models import ServiceCategory, Service


class Command(BaseCommand):
    help = 'Seed service categories and services from frontend catalog'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding service categories and services...')
        
        # Income Tax Category
        income_tax, _ = ServiceCategory.objects.get_or_create(
            name="Income Tax",
            defaults={'description': 'Income tax filing and related services'}
        )
        
        income_tax_services = [
            ("Capital Gains Tax Planning", 4999, "5-10 days", "PAN Card, Aadhaar, Transaction documents"),
            ("Income Tax E-Filing", 999, "1-2 days", "PAN, Aadhaar, Form 16/16A, Bank Statements"),
            ("Business Tax Filing", 4999, "3-5 days", "PAN, Balance Sheet, P&L, Bank Statements"),
            ("Partnership Firm / LLP ITR", 7499, "5-7 days", "Partnership Deed, PAN, Audited Financials"),
            ("Company ITR Filing", 9999, "7-10 days", "COI, Audited Financials, Director Details"),
            ("Trust / NGO Tax Filing", 7499, "7-10 days", "Trust Deed, PAN, Audit Report"),
            ("15CA - 15CB Filing", 4999, "1-2 days", "Invoice, Remittee details, TRC"),
            ("TAN Registration", 999, "1-2 days", "PAN, ID Proof, Address Proof"),
            ("TDS Return Filing", 1499, "2-3 days", "TAN, TDS Challans, Deductee List"),
            ("Revised ITR Return (ITR-U)", 2499, "2-4 days", "Original ITR, Additional income details"),
        ]
        
        for title, price, tat, docs in income_tax_services:
            Service.objects.get_or_create(
                category=income_tax,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # GST Category
        gst, _ = ServiceCategory.objects.get_or_create(
            name="GST",
            defaults={'description': 'GST registration, filing, and compliance services'}
        )
        
        gst_services = [
            ("GST Registration", 2999, "7-10 days", "PAN, Aadhaar, Address Proof, Bank Proof"),
            ("GST Registration for Foreigners", 9999, "10-15 days", "Passport, Address Proof, Nominee Details"),
            ("GST Return Filing by Accountant", 1499, "Monthly/Quarterly", "Sales/Purchase Invoices, Bank Statement"),
            ("GST NIL Return Filing", 499, "1 day", "Login Credentials, OTP"),
            ("GST Amendment", 1999, "3-5 days", "New Address Proof, Supporting documents"),
            ("GST Revocation", 4999, "15-30 days", "Cancellation Order, Pending Returns"),
            ("GST LUT Form", 999, "1-2 days", "GSTIN, Digital Signature"),
            ("GSTR-10 (Final Return)", 2999, "5-7 days", "Cancellation Order, Closing Stock"),
            ("GST Annual Return Filing (GSTR-9)", 4999, "10-15 days", "Annual Financials, GSTR data"),
        ]
        
        for title, price, tat, docs in gst_services:
            Service.objects.get_or_create(
                category=gst,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Registration Category
        registration, _ = ServiceCategory.objects.get_or_create(
            name="Registration",
            defaults={'description': 'Business and professional registrations'}
        )
        
        registration_services = [
            ("PAN Registration (Individual/Company)", 499, "1-2 days", "ID Proof, Address Proof, Photo"),
            ("IEC Certificate", 2999, "2-3 days", "PAN, Aadhaar, Bank Proof, DSC"),
            ("DSC Signature", 2999, "1-2 days", "Photo, PAN, Aadhaar, Email, Mobile"),
            ("Startup India Registration", 4999, "7-10 days", "COI, Funding Proof, Business Description"),
            ("FSSAI Registration", 1999, "5-7 days", "Photo, ID, Address Proof, Product List"),
            ("Trade License", 3499, "15-20 days", "Address Proof, ID, Property Tax Receipt"),
            ("Udyam Registration", 1499, "1-2 days", "Aadhaar, PAN, Bank Details"),
        ]
        
        for title, price, tat, docs in registration_services:
            Service.objects.get_or_create(
                category=registration,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Startup & Advisory Category
        startup, _ = ServiceCategory.objects.get_or_create(
            name="Startup & Advisory",
            defaults={'description': 'Startup and business advisory services'}
        )
        
        startup_services = [
            ("Business Structure Selection", 4999, "1-2 days", "Founders PAN, Business Model"),
            ("Startup Certificate", 29999, "5-10 days", "COI, PAN, Directors Details"),
            ("Proprietorship", 4999, "3-5 days", "PAN, Aadhaar, Address Proof"),
            ("Partnership", 9999, "5-7 days", "PAN & Aadhaar of Partners, Partnership Deed"),
            ("One Person Company", 14999, "10-15 days", "PAN, Aadhaar, DSC, MOA & AOA"),
            ("Limited Liability Partnership", 14999, "10-15 days", "PAN, Aadhaar, DSC, LLP Agreement"),
            ("Private Limited Company", 19999, "10-15 days", "PAN, Aadhaar, DSC, MOA & AOA"),
            ("Section 8 Company", 24999, "15-20 days", "PAN, Aadhaar, DSC, Non-profit MOA"),
            ("Trust Registration", 14999, "10-15 days", "PAN, Trust Deed, Trustees List"),
            ("Public Limited Company", 49999, "20-30 days", "PAN, DSC, MOA & AOA, Prospectus"),
            ("Producer Company", 34999, "15-20 days", "PAN, DSC, MOA & AOA, Members List"),
            ("Indian Subsidiary", 39999, "20-25 days", "Parent Co Documents, Board Resolution"),
        ]
        
        for title, price, tat, docs in startup_services:
            Service.objects.get_or_create(
                category=startup,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Compliance Category
        compliance, _ = ServiceCategory.objects.get_or_create(
            name="Compliance",
            defaults={'description': 'Ongoing compliance and filing services'}
        )
        
        compliance_services = [
            ("PF Return Filing", 999, "Monthly", "Employee ECR, Contribution data"),
            ("ESI Return Filing", 999, "Monthly", "Employee ESI contribution data"),
            ("Professional Tax Return Filing", 999, "Monthly/Annual", "Salary details, PT Deduction"),
            ("FDI Filing with RBI", 19999, "15-20 days", "FC-GPR Form, Valuation Certificate"),
            ("FLA Return Filing", 4999, "5-7 days", "Audited Financials, Foreign assets details"),
            ("FSSAI Renewal", 2499, "7-10 days", "Original License, Declaration Form"),
            ("FSSAI Return Filing", 1999, "3-5 days", "Sales/Purchase details, Product categories"),
            ("Partnership Compliance", 2999, "5-7 days", "Partnership Deed, Financials"),
            ("Proprietorship Compliance", 2499, "3-5 days", "PAN, Bank Statement, Sales details"),
            ("Business Plan", 14999, "10-15 days", "Project Description, Market analysis"),
            ("PF Registration", 3999, "7-10 days", "PAN, DSC, Address Proof"),
            ("ESI Registration", 3999, "7-10 days", "Registration Certificate, Employee List"),
            ("Professional Tax Registration", 2999, "3-5 days", "PAN, Address Proof, Employee Details"),
        ]
        
        for title, price, tat, docs in compliance_services:
            Service.objects.get_or_create(
                category=compliance,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Capital Gains & Tax Planning Category
        tax_planning, _ = ServiceCategory.objects.get_or_create(
            name="Capital Gains & Tax Planning",
            defaults={'description': 'Tax planning and capital gains services'}
        )
        
        tax_planning_services = [
            ("Filing 26QB", 4999, "1-2 days", "PAN of Buyer/Seller, Sale Agreement"),
            ("Tax Planning Consultation", 4999, "1-3 days", "PAN, ITR, Bank Statements, Investment Proofs"),
        ]
        
        for title, price, tat, docs in tax_planning_services:
            Service.objects.get_or_create(
                category=tax_planning,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Certification Services Category
        certification, _ = ServiceCategory.objects.get_or_create(
            name="Certification Services",
            defaults={'description': 'Professional certification services'}
        )
        
        certification_services = [
            ("Net Worth Certificate", 9999, "1-3 days", "PAN, ITR, Bank Statements, Property Docs"),
            ("Turnover Certificate", 4999, "1-3 days", "PAN, GST Certificate, Audited Financials"),
            ("15CA/15CB (FEMA Remittance)", 9999, "7 days", "PAN, Invoice, TRC, Agreement"),
            ("Capital Contribution Certificate", 4999, "1-3 days", "PAN, Partnership Deed, Bank Statements"),
        ]
        
        for title, price, tat, docs in certification_services:
            Service.objects.get_or_create(
                category=certification,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        total_categories = ServiceCategory.objects.count()
        total_services = Service.objects.count()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully seeded {total_categories} categories and {total_services} services!'
            )
        )
