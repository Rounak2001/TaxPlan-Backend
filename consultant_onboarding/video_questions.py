_gst_video_questions = [
    "What is GST and why was it introduced in India? Explain its main objective in simple terms.",
    "A business provides services to a client in another state. Which type of GST applies and why?",
    "What are GSTR-1 and GSTR-3B? Briefly explain their purpose.",
    "If a client fails to file GST returns for several months, what basic advice would you give them?",
    "How would you explain the difference between output tax liability and input tax credit to a small business owner?",
    "A client has a mismatch between GSTR-1 and GSTR-3B. How would you investigate and respond to that issue?",
    "What is reverse charge under GST, and can you give one practical example where it applies?",
    "If a business receives a GST notice for excess ITC claimed, what documents would you review first?",
    "How would you explain place of supply in a simple interstate vs intrastate transaction example?",
    "What steps would you suggest before filing a GST annual return to avoid future scrutiny?",
]

_income_tax_video_questions = [
    "What is the difference between gross income and taxable income?",
    "Name and explain any two deductions or exemptions commonly used by salaried individuals.",
    "What happens if an individual does not file their income tax return on time?",
    "A person has income from salary and bank interest. What are the basic steps you would suggest before filing their return?",
    "How would you explain the difference between the old tax regime and the new tax regime to a client?",
    "If a client receives a scrutiny notice under the Income Tax Act, what should be the first response?",
    "What documents would you usually ask for before preparing an individual income tax return?",
    "How would you explain capital gains tax to a client who sold shares or property?",
    "If the department questions a large cash deposit, how would you help the client prepare a reply?",
    "What are the common reasons an income tax return may get selected for further verification or scrutiny?",
]

_tds_video_questions = [
    "What is TDS and what is its main purpose in the tax system?",
    "Give one common example where TDS is deducted and mention who deducts it.",
    "What are the consequences if TDS is deducted but not deposited with the government on time?",
    "What is Form 26Q or Form 24Q and why is it important?",
    "How would you explain the difference between deductor and deductee to a client?",
    "If a company misses the due date for depositing TDS, what practical compliance steps should be taken next?",
    "What documents or reports would you check while reconciling TDS defaults for a client?",
    "How would you explain Form 16, Form 16A, and Form 26AS in simple terms?",
    "What is the role of TAN in TDS compliance, and why is it important?",
    "If a client receives a notice for short deduction or non deduction of TDS, how would you approach the case?",
]

_scrutiny_video_questions = [
    "What does a scrutiny notice usually mean, and what should be the first response from a taxpayer or consultant?",
    "Explain the difference between summary processing, scrutiny assessment, and best judgement assessment in simple terms.",
    "If a client receives a tax or GST notice asking for documents, how would you prepare and organize the response?",
    "What are the key timelines and precautions a consultant should keep in mind while replying to scrutiny or compliance notices?",
    "How would you handle a case where the department points out a mismatch between returns and books of accounts?",
    "What is the importance of a point by point written reply in assessment or scrutiny proceedings?",
    "If a client has weak documentation for an expense or claim, how would you still prepare the best possible response?",
    "How would you explain faceless assessment to a taxpayer who has never dealt with a tax notice before?",
    "What should a consultant do after receiving an assessment order that appears incorrect or excessive?",
    "How do you maintain consistency in replies when a client is facing both GST and Income Tax scrutiny on related issues?",
]

_scrutiny_shared_video_questions = [
    "What does a scrutiny notice usually mean, and what should be the first response from a taxpayer or consultant?",
    "If a client receives a tax or GST notice asking for documents, how would you prepare and organize the response?",
    "What are the key timelines and precautions a consultant should keep in mind while replying to scrutiny or compliance notices?",
    "What is the importance of a point by point written reply in assessment or scrutiny proceedings?",
]

_scrutiny_income_tax_tds_video_questions = [
    "If a client receives a scrutiny notice under the Income Tax Act, what should be the first response?",
    "How would you explain faceless assessment to a taxpayer who has never dealt with a tax notice before?",
    "What should a consultant do after receiving an income tax assessment order that appears incorrect or excessive?",
    "What are the common reasons an income tax return may get selected for further verification or scrutiny?",
    "If a client receives a notice for short deduction or non deduction of TDS, how would you approach the case?",
    "What documents or reports would you review first while handling a TDS scrutiny or mismatch case?",
]

_scrutiny_gstr_video_questions = [
    "If a business receives a GST notice for excess ITC claimed, what documents would you review first?",
    "How would you handle a case where the GST department points out a mismatch between GSTR-1 and GSTR-3B?",
    "What steps would you suggest before filing a GST annual return to avoid future scrutiny?",
    "How would you prepare a response when a GST officer asks for reconciliations, books, and return workings together?",
    "What should a consultant review first in a GST appeal or regular assessment matter?",
    "How would you explain GST scrutiny, assessment, and appeal stages to a business owner in simple terms?",
]

_registration_video_questions = [
    "How would you help a client decide whether they need PAN, TAN, GST, or another registration first?",
    "What are the first documents you usually ask for before starting a new business registration assignment?",
    "Explain the difference between PAN and TAN in simple terms for a new business owner.",
    "How would you guide a founder choosing between a partnership, LLP, and private limited company?",
    "What checks would you do before filing an MSME or Udyam registration for a client?",
    "How would you explain IEC registration to a client planning to start import or export activity?",
    "What are the most common mistakes applicants make during company or LLP registration, and how do you prevent them?",
    "How would you handle a trust or NGO client seeking both 12A and 80G registration support?",
    "What is the role of a DSC in registration and compliance filings, and when is it usually needed?",
    "If a foreign entity wants to enter India, what documents and practical registration checkpoints would you review first?",
]


video_questions = {
    "introduction": [
        "Please introduce yourself, including your educational background and your experience in taxation or related fields."
    ],
    "gstr": _gst_video_questions,
    "gst": _gst_video_questions,
    "itr": _income_tax_video_questions + _tds_video_questions,
    "income_tax": _income_tax_video_questions,
    "tds": _tds_video_questions,
    "scrutiny": _scrutiny_video_questions,
    "professional_tax": _scrutiny_video_questions,
    "registrations": _registration_video_questions,
    "registration": _registration_video_questions,
}


def get_scoped_scrutiny_video_questions(scope="all"):
    if scope == "gstr":
        return _scrutiny_gstr_video_questions
    if scope == "income_tax_tds":
        return _scrutiny_income_tax_tds_video_questions
    return _scrutiny_video_questions
