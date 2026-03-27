import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from consultant_onboarding.views.test_engine import DOMAIN_QUESTION_BANKS
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import green

def generate_pdf(filename="Onboarding_Questions.pdf"):
    doc = SimpleDocTemplate(filename, pagesize=letter)
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = styles['Heading1']
    category_style = styles['Heading2']
    question_style = ParagraphStyle(
        'Question',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        spaceBefore=10,
        spaceAfter=5
    )
    option_style = ParagraphStyle(
        'Option',
        parent=styles['Normal'],
        leftIndent=20,
        spaceAfter=2
    )
    answer_style = ParagraphStyle(
        'Answer',
        parent=styles['Normal'],
        leftIndent=20,
        fontName='Helvetica-Oblique',
        textColor=green,
        spaceBefore=2,
        spaceAfter=15
    )
    
    story = []
    story.append(Paragraph("Consultant Onboarding - Category Questions", title_style))
    story.append(Spacer(1, 12))
    
    domain_names = {
        "itr": "Income Tax & TDS",
        "gstr": "Goods and Services Tax (GST)",
        "scrutiny": "Scrutiny & Professional Tax",
        "registrations": "Registrations"
    }
    
    for domain_slug, bank in DOMAIN_QUESTION_BANKS.items():
        domain_title = domain_names.get(domain_slug, domain_slug.upper())
        story.append(Paragraph(f"Category: {domain_title} (Total: {len(bank)})", category_style))
        story.append(Spacer(1, 10))
        
        for idx, q_data in enumerate(bank, start=1):
            q_text = q_data.get('question', '')
            options = q_data.get('options', {})
            correct_ans = q_data.get('answer', '')
            
            # Question Paragraph
            story.append(Paragraph(f"Q{idx}. {q_text}", question_style))
            
            # Options Paragraphs
            for key in sorted(options.keys()):
                opt_text = options[key]
                story.append(Paragraph(f"{key}) {opt_text}", option_style))
                
            # Answer
            if correct_ans:
                story.append(Paragraph(f"Answer: {correct_ans}", answer_style))
            else:
                story.append(Spacer(1, 15))
                
        story.append(Spacer(1, 20))
        
    doc.build(story)
    print(f"Successfully generated {os.path.abspath(filename)}")

if __name__ == "__main__":
    generate_pdf()
