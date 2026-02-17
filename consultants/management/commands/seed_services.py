"""
Management command to seed service categories and services from ClientServices.jsx
Run with: python manage.py seed_services
"""

from django.core.management.base import BaseCommand
from consultants.models import ServiceCategory, Service
from consultations.models import Topic


class Command(BaseCommand):
    help = 'Seed service categories and services from frontend catalog'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding service categories and services...')
        
        # Income Tax Category
        income_tax, _ = ServiceCategory.objects.get_or_create(
            name="Income Tax",
            defaults={'description': 'Income tax filing and related services'}
        )
        Topic.objects.get_or_create(name="Income Tax", defaults={'category': income_tax, 'description': 'Income tax related consultations'})
        
        income_tax_services = [
            ("Capital Gains Tax Planning", 1, "5-10 days", "PAN Card\nAadhaar\nTransaction documents"),
            ("Income Tax E-Filing", 1, "1-2 days", "PAN\nAadhaar\nForm 16/16A\nBank Statements"),
            ("Business Tax Filing", 1, "3-5 days", "PAN Card\nAadhaar Card\nIncome Tax Returns (last 2–3 years)\nForm 16 / Salary Slips\nBank Statements (last 6 months)\nInvestment Proofs\nBusiness Income/Profit & Loss Account\nBalance Sheet"),
            ("Partnership Firm / LLP ITR", 1, "5-7 days", "Partnership Deed/LLP Agreement\nPAN of Firm/LLP\nAudited Financials\nPartner Details\nDigital Signature"),
            ("Company ITR Filing", 1, "7-10 days", "Certificate of Incorporation\nAudited Balance Sheet & P&L\nDirector Details & KYC\nDigital Signature (Class 3)\nForm 26AS"),
            ("Trust / NGO Tax Filing", 1, "7-10 days", "Trust Deed/Registration Certificate\nPAN of Trust/NGO\nAudit Report (Form 10B/10BB)\nDonor List\nUtilization Certificate"),
            ("15CA - 15CB Filing", 1, "1-2 days", "Invoice\nRemittee details\nTax Residency Certificate (TRC)\nForm 15CA/CB Engagement Letter"),
            ("TAN Registration", 1, "1-2 days", "PAN of Entity\nID Proof of Authorized Signatory\nAddress Proof"),
            ("TDS Return Filing", 1, "2-3 days", "TAN\nTDS Challans\nDeductee List\nSalary/Payment Register"),
            ("Revised ITR Return (ITR-U)", 1, "2-4 days", "Original ITR\nAdditional income details\nProof of additional tax paid"),
        ]
        
        for title, price, tat, docs in income_tax_services:
            Service.objects.update_or_create(
                category=income_tax,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # GST Category
        gst, _ = ServiceCategory.objects.get_or_create(
            name="GST",
            defaults={'description': 'GST registration, filing, and compliance services'}
        )
        Topic.objects.get_or_create(name="GST", defaults={'category': gst, 'description': 'GST related consultations'})
        
        gst_services = [
            ("GST Registration", 1, "7-10 days", "PAN, Aadhaar, Address Proof, Bank Proof"),
            ("GST Registration for Foreigners", 1, "10-15 days", "Passport, Address Proof, Nominee Details"),
            ("GST Return Filing by Accountant", 1, "Monthly/Quarterly", "Sales/Purchase Invoices, Bank Statement"),
            ("GST NIL Return Filing", 1, "1 day", "Login Credentials, OTP"),
            ("GST Amendment", 1, "3-5 days", "New Address Proof, Supporting documents"),
            ("GST Revocation", 1, "15-30 days", "Cancellation Order, Pending Returns"),
            ("GST LUT Form", 1, "1-2 days", "GSTIN, Digital Signature"),
            ("GSTR-10 (Final Return)", 1, "5-7 days", "Cancellation Order, Closing Stock"),
            ("GST Annual Return Filing (GSTR-9)", 1, "10-15 days", "Annual Financials, GSTR data"),
        ]
        
        for title, price, tat, docs in gst_services:
            Service.objects.update_or_create(
                category=gst,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Registration Category
        registration, _ = ServiceCategory.objects.get_or_create(
            name="Registration",
            defaults={'description': 'Business and professional registrations'}
        )
        Topic.objects.get_or_create(name="Registration", defaults={'category': registration, 'description': 'Registration related consultations'})
        
        registration_services = [
            ("PAN Registration (Individual/Company)", 1, "1-2 days", "ID Proof, Address Proof, Photo"),
            ("IEC Certificate", 1, "2-3 days", "PAN, Aadhaar, Bank Proof, DSC"),
            ("DSC Signature", 1, "1-2 days", "Photo, PAN, Aadhaar, Email, Mobile"),
            ("Startup India Registration", 1, "7-10 days", "COI, Funding Proof, Business Description"),
            ("FSSAI Registration", 1, "5-7 days", "Photo, ID, Address Proof, Product List"),
            ("Trade License", 1, "15-20 days", "Address Proof, ID, Property Tax Receipt"),
            ("Udyam Registration", 1, "1-2 days", "Aadhaar, PAN, Bank Details"),
        ]
        
        for title, price, tat, docs in registration_services:
            Service.objects.update_or_create(
                category=registration,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Startup & Advisory Category
        startup, _ = ServiceCategory.objects.get_or_create(
            name="Startup & Advisory",
            defaults={'description': 'Startup and business advisory services'}
        )
        Topic.objects.get_or_create(name="Startup & Advisory", defaults={'category': startup, 'description': 'Startup and advisory consultations'})
        
        startup_services = [
            ("Business Structure Selection", 1, "1-2 days", "Founders PAN, Business Model"),
            ("Startup Certificate", 1, "5-10 days", "COI, PAN, Directors Details"),
            ("Proprietorship", 1, "3-5 days", "PAN, Aadhaar, Address Proof"),
            ("Partnership", 1, "5-7 days", "PAN & Aadhaar of Partners, Partnership Deed"),
            ("One Person Company", 1, "10-15 days", "PAN, Aadhaar, DSC, MOA & AOA"),
            ("Limited Liability Partnership", 1, "10-15 days", "PAN, Aadhaar, DSC, LLP Agreement"),
            ("Private Limited Company", 1, "10-15 days", "PAN, Aadhaar, DSC, MOA & AOA"),
            ("Section 8 Company", 1, "15-20 days", "PAN, Aadhaar, DSC, Non-profit MOA"),
            ("Trust Registration", 1, "10-15 days", "PAN, Trust Deed, Trustees List"),
            ("Public Limited Company", 1, "20-30 days", "PAN, DSC, MOA & AOA, Prospectus"),
            ("Producer Company", 1, "15-20 days", "PAN, DSC, MOA & AOA, Members List"),
            ("Indian Subsidiary", 1, "20-25 days", "Parent Co Documents, Board Resolution"),
        ]
        
        for title, price, tat, docs in startup_services:
            Service.objects.update_or_create(
                category=startup,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Compliance Category
        compliance, _ = ServiceCategory.objects.get_or_create(
            name="Compliance",
            defaults={'description': 'Ongoing compliance and filing services'}
        )
        Topic.objects.get_or_create(name="Compliance", defaults={'category': compliance, 'description': 'Compliance related consultations'})
        
        compliance_services = [
            ("PF Return Filing", 1, "Monthly", "Employee ECR, Contribution data"),
            ("ESI Return Filing", 1, "Monthly", "Employee-wise ESI contribution data"),
            ("Professional Tax Return Filing", 1, "Monthly/Annual", "Salary details, PT Deduction"),
            ("FDI Filing with RBI", 1, "15-20 days", "FC-GPR Form, Valuation Certificate"),
            ("FLA Return Filing", 1, "5-7 days", "Audited Financials, Foreign assets details"),
            ("FSSAI Renewal", 1, "7-10 days", "Original License, Declaration Form"),
            ("FSSAI Return Filing", 1, "3-5 days", "Sales/Purchase details, Product categories"),
            ("Partnership Compliance", 1, "5-7 days", "Partnership Deed, Financials"),
            ("Proprietorship Compliance", 1, "3-5 days", "PAN, Bank Statement, Sales details"),
            ("Business Plan", 1, "10-15 days", "Project Description, Market analysis"),
            ("PF Registration", 1, "7-10 days", "PAN, DSC, Address Proof"),
            ("ESI Registration", 1, "7-10 days", "Registration Certificate, Employee List"),
            ("Professional Tax Registration", 1, "3-5 days", "PAN, Address Proof, Employee Details"),
        ]
        
        for title, price, tat, docs in compliance_services:
            Service.objects.update_or_create(
                category=compliance,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Capital Gains & Tax Planning Category
        tax_planning, _ = ServiceCategory.objects.get_or_create(
            name="Capital Gains & Tax Planning",
            defaults={'description': 'Tax planning and capital gains services'}
        )
        Topic.objects.get_or_create(name="Capital Gains & Tax Planning", defaults={'category': tax_planning, 'description': 'Capital gains and tax planning consultations'})
        
        tax_planning_services = [
            ("Filing 26QB", 1, "1-2 days", "PAN of Buyer/Seller, Sale Agreement"),
            ("Tax Planning Consultation", 1, "1-3 days", "PAN, ITR, Bank Statements, Investment Proofs"),
        ]
        
        for title, price, tat, docs in tax_planning_services:
            Service.objects.update_or_create(
                category=tax_planning,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        # Certification Services Category
        certification, _ = ServiceCategory.objects.get_or_create(
            name="Certification Services",
            defaults={'description': 'Professional certification services'}
        )
        Topic.objects.get_or_create(name="Certification Services", defaults={'category': certification, 'description': 'Certification service consultations'})
        
        certification_services = [
            ("Net Worth Certificate", 1, "1-3 days", "PAN, ITR, Bank Statements, Property Docs"),
            ("Turnover Certificate", 1, "1-3 days", "PAN, GST Certificate, Audited Financials"),
            ("15CA/15CB (FEMA Remittance)", 1, "7 days", "PAN, Invoice, TRC, Agreement"),
            ("Capital Contribution Certificate", 1, "1-3 days", "PAN, Partnership Deed, Bank Statements"),
        ]
        
        for title, price, tat, docs in certification_services:
            Service.objects.update_or_create(
                category=certification,
                title=title,
                defaults={'price': price, 'tat': tat, 'documents_required': docs}
            )
        
        total_categories = ServiceCategory.objects.count()
        total_services = Service.objects.count()
        total_topics = Topic.objects.count()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully seeded {total_categories} categories, {total_services} services, and {total_topics} topics!'
            )
        )
