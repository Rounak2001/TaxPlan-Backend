import boto3
import time
import json
import os
import requests
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from django.conf import settings
from botocore.config import Config

TARGET_BACHELOR_FIELD_KEYWORDS = [
    "commerce",
    "bcom",
    "b.com",
    "account",
    "accounting",
    "accountancy",
    "finance",
    "financial management",
    "banking",
    "tax",
    "taxation",
    "audit",
    "auditing",
]

def _parse_retry_delay_seconds(message):
    text = str(message or '')
    import re
    m = re.search(r'Please retry in ([0-9]+(?:\.[0-9]+)?)s', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = re.search(r'retry_delay\\s*\\{\\s*seconds:\\s*([0-9]+)', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _is_quota_exhausted_error(exc):
    if isinstance(exc, ResourceExhausted):
        return True
    msg = str(exc or '').lower()
    return 'quota' in msg and 'exceeded' in msg and ('429' in msg or 'resourceexhausted' in msg)


class VideoEvaluator:
    def __init__(self):
        # Configure AWS Transcribe Client
        self.transcribe_client = boto3.client(
            'transcribe',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION
        )
        
        # Configure Gemini
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.gemini_model = genai.GenerativeModel('gemini-2.5-flash')

    def download_video_to_temp(self, video_response):
        """
        Download the stored video to a local temp file and return its path.
        Caller owns cleanup (os.remove).
        """
        import tempfile
        from django.core.files.storage import default_storage

        # Get file extension safely, default to mp4
        file_ext = video_response.video_file.split('.')[-1] if '.' in video_response.video_file else 'mp4'

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp_file:
            local_video_path = tmp_file.name
            with default_storage.open(video_response.video_file, 'rb') as f:
                for chunk in f.chunks() if hasattr(f, 'chunks') else [f.read()]:
                    tmp_file.write(chunk)

        return local_video_path

    def transcribe_video(self, video_response):
        """
        Starts an AWS Transcribe job and waits for the result.
        Returns the transcript text.
        """
        
        job_name = f"transcribe_job_{video_response.id}_{int(time.time())}"
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        s3_uri = f"s3://{bucket_name}/{video_response.video_file}"
        
        file_ext = video_response.video_file.split('.')[-1].lower()
        if file_ext == 'webm':
             media_format = 'webm'
        elif file_ext == 'mp4':
             media_format = 'mp4'
        else:
             media_format = 'webm' 
        
        try:
            self.transcribe_client.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={'MediaFileUri': s3_uri},
                MediaFormat=media_format,
                LanguageCode='en-US'
            )
            
            # Poll for completion
            while True:
                status = self.transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
                job_status = status['TranscriptionJob']['TranscriptionJobStatus']
                
                if job_status in ['COMPLETED', 'FAILED']:
                    break
                time.sleep(2) 
                
            if job_status == 'COMPLETED':
                transcript_uri = status['TranscriptionJob']['Transcript']['TranscriptFileUri']
                # Download transcript JSON
                response = requests.get(transcript_uri)
                data = response.json()
                transcript_text = data.get('results', {}).get('transcripts', [{}])[0].get('transcript', '')
                return transcript_text
            else:
                error_msg = status['TranscriptionJob'].get('FailureReason', 'Unknown Error')
                raise Exception(f"Transcribe job failed: {error_msg}")
                
        except Exception as e:
            print(f"Transcription Error: {e}")
            raise e

    def evaluate_transcript(self, transcript_text, question_text, local_video_path):
        """
        Sends video to Gemini for native transcription + evaluation.
        """
        prompt = f"""
        You are an expert interviewer evaluating a candidate's video response.
        
        You will receive:
        1. A video of the candidate answering an interview question
        2. The interview question text

        Your tasks are:
        1. Watch and listen to the video carefully
        2. Transcribe the candidate's spoken answer as faithfully as possible
        3. Evaluate the response using both:
           - the spoken/verbal content
           - the candidate's visible delivery, confidence, clarity, and professionalism
        4. Assign one overall integer score from 0 to 5
        5. Provide concise, professional feedback
        6. Provide brief reasoning for the assigned score

        Scoring rubric:
        - 0 = no meaningful usable answer, silence, fully irrelevant response, or unusable audio/video
        - 1 = very weak answer with major gaps in understanding or communication
        - 2 = partially correct answer but weak clarity, confidence, or substance
        - 3 = acceptable answer with reasonable understanding and communication
        - 4 = strong answer with good clarity, confidence, and correctness
        - 5 = excellent answer with accurate, clear, confident, and well-structured delivery

        Important rules:
        - The transcript must reflect what the candidate actually said as closely as possible
        - Do not invent or infer spoken content that is not present
        - If some words are unclear, produce the best faithful transcript possible
        - If the candidate is silent, inaudible, or the response cannot be understood, return the best possible transcript or an empty string
        - In such cases, score appropriately and mention the issue briefly in feedback and reasoning
        - Base the evaluation on both the content of the answer and the presentation in the video
        - Keep feedback concise, constructive, and professional
        - Keep reasoning short and evidence-based
        - Return valid JSON only
        - Do not wrap the JSON in markdown
        - Do not include any text before or after the JSON

        Return JSON in exactly this format:
        {{
            "transcript": "<string>",
            "score": <int>,
            "feedback": "<string>",
            "reasoning": "<string>"
        }}

        Interview question:
        "{question_text}"
        """
        
        uploaded_file = None
        try:
            print(f"Uploading video {local_video_path} to Gemini...")
            uploaded_file = genai.upload_file(path=local_video_path)
            
            # Wait for file processing to complete
            while uploaded_file.state.name == "PROCESSING":
                print(".", end="", flush=True)
                time.sleep(2)
                uploaded_file = genai.get_file(uploaded_file.name)
            print("Video ready for Gemini.")
                
            if uploaded_file.state.name == "FAILED":
                raise Exception("Video processing failed in Gemini.")

            # Generate content using both video and prompt
            response = self.gemini_model.generate_content(
                [uploaded_file, prompt],
                generation_config={"response_mime_type": "application/json"}
            )
            
            result = json.loads(response.text)
            if not isinstance(result, dict):
                raise ValueError("Gemini did not return a JSON object.")
            return result
            
        except Exception as e:
            print(f"Gemini Error: {e}")
            raise e
        finally:
            if uploaded_file:
                try:
                    uploaded_file.delete()
                    print("Cleaned up Gemini uploaded file.")
                except Exception as del_e:
                    print(f"Failed to delete Gemini file: {del_e}")

    def process_video(self, video_response, question_text):
        """
        Orchestrates the full process.
        """
        import os
        
        local_video_path = None
        try:
            # 1. Download Video to local temp file for Gemini
            print("Downloading video from storage for Gemini analysis...")
            local_video_path = self.download_video_to_temp(video_response)
            
            # 2. Evaluate with native Gemini transcription + scoring
            print(f"Evaluating transcript and video...")
            evaluation = self.evaluate_transcript('', question_text, local_video_path)
            transcript = evaluation.get('transcript', '') if isinstance(evaluation, dict) else ''
            if not isinstance(transcript, str):
                transcript = str(transcript or '')
            
            # 3. Return results
            return {
                "transcript": transcript,
                "score": evaluation.get('score', 0),
                "feedback": evaluation 
            }
            
        except Exception as e:
            raise e
        finally:
            # Clean up local temp file
            if local_video_path and os.path.exists(local_video_path):
                try:
                    os.remove(local_video_path)
                    print(f"Cleaned up local temp file: {local_video_path}")
                except OSError as e:
                    print(f"Error removing temp file {local_video_path}: {e}")

class IdentityDocumentVerifier:
    def __init__(self):
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.gemini_model = genai.GenerativeModel('gemini-2.5-flash')

    def verify_document(self, identity_document):
        import tempfile
        import os
        from django.core.files.storage import default_storage
        
        local_image_path = None
        try:
            print(f"Downloading identity document {identity_document.id} for Gemini verification...")
            
            file_ext = identity_document.file_path.split('.')[-1] if '.' in identity_document.file_path else 'jpg'
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp_file:
                local_image_path = tmp_file.name
                with default_storage.open(identity_document.file_path, 'rb') as f:
                    for chunk in f.chunks() if hasattr(f, 'chunks') else [f.read()]:
                        tmp_file.write(chunk)
            
            prompt = """
            You are an expert identity document verification system.
            Examine the provided image of a government-issued ID card.
            
            Identify the type of document. Is it an Aadhaar Card, a PAN Card, a Masked Aadhaar, a Masked PAN, or something else (Unknown/Invalid)?
            Also, verify if the document looks like a valid, legitimate document (Verification Status: Verified or Invalid).
            Extract the following details from the document if they are visible: Full Name, Date of Birth (DOB), and the ID Number (e.g. Aadhaar Number or PAN Number).
            Privacy policy: the document must be privacy-safe (sensitive numbers masked/redacted except minimal readable suffix if present).
            
            Respond strictly in the following JSON format:
            {
                "document_type": "Aadhaar Card" | "PAN Card" | "Masked Aadhaar" | "Masked PAN" | "Unknown",
                "verification_status": "Verified" | "Invalid",
                "is_sensitive_data_masked": true | false,
                "privacy_notes": "Brief reason about masking",
                "extracted_name": "Full Name",
                "extracted_dob": "DD/MM/YYYY text",
                "extracted_id_number": "ID Number text",
                "notes": "Any additional observations"
            }
            """
            
            print(f"Uploading image {local_image_path} to Gemini...")
            uploaded_file = genai.upload_file(path=local_image_path)
            
            while uploaded_file.state.name == "PROCESSING":
                print(".", end="", flush=True)
                time.sleep(1)
                uploaded_file = genai.get_file(uploaded_file.name)
                
            if uploaded_file.state.name == "FAILED":
                raise Exception("Image processing failed in Gemini.")

            response = self.gemini_model.generate_content(
                [uploaded_file, prompt],
                generation_config={"response_mime_type": "application/json"}
            )
            
            try:
                uploaded_file.delete()
            except Exception as e:
                print(f"Clean up Gemini file failed: {e}")
                
            result_json = response.text
            result = json.loads(result_json)
            is_masked = bool(result.get("is_sensitive_data_masked", False))
            
            return {
                "document_type": result.get("document_type", "Unknown"),
                "verification_status": result.get("verification_status", "Unverified"),
                "is_sensitive_data_masked": is_masked,
                "privacy_notes": result.get("privacy_notes", ""),
                "extracted_name": result.get("extracted_name", ""),
                "extracted_dob": result.get("extracted_dob", ""),
                "extracted_id_number": result.get("extracted_id_number", ""),
                "raw_response": result_json,
            }
            
        except Exception as e:
            print(f"Identity Verification Error: {e}")
            if _is_quota_exhausted_error(e):
                retry_in_s = _parse_retry_delay_seconds(e)
                return {
                    "document_type": "Error",
                    "verification_status": "Error",
                    "error_code": "GEMINI_QUOTA_EXCEEDED",
                    "retry_in_s": retry_in_s,
                    "is_sensitive_data_masked": False,
                    "privacy_notes": "Verification temporarily unavailable (Gemini quota/rate limit).",
                    "extracted_name": "",
                    "extracted_dob": "",
                    "extracted_id_number": "",
                    "raw_response": json.dumps({"error": str(e)})
                }
            return {
                "document_type": "Error",
                "verification_status": "Error",
                "error_code": "IDENTITY_VERIFICATION_UNAVAILABLE",
                "retry_in_s": None,
                "is_sensitive_data_masked": False,
                "privacy_notes": "Verification temporarily unavailable.",
                "extracted_name": "",
                "extracted_dob": "",
                "extracted_id_number": "",
                "raw_response": json.dumps({"error": str(e)})
            }
        finally:
            if local_image_path and os.path.exists(local_image_path):
                try:
                    os.remove(local_image_path)
                except OSError as e:
                    print(f"Error removing temp file {local_image_path}: {e}")

class QualificationDocumentVerifier:
    def __init__(self):
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.gemini_model = genai.GenerativeModel('gemini-2.5-flash')

    def verify_document(self, consultant_document):
        import tempfile
        import os
        from django.core.files.storage import default_storage
        import json
        
        local_image_path = None
        try:
            print(f"Downloading qualification document {consultant_document.id} for Gemini verification...")
            
            file_ext = consultant_document.file_path.split('.')[-1] if '.' in consultant_document.file_path else 'jpg'
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp_file:
                local_image_path = tmp_file.name
                with default_storage.open(consultant_document.file_path, 'rb') as f:
                    for chunk in f.chunks() if hasattr(f, 'chunks') else [f.read()]:
                        tmp_file.write(chunk)
            
            prompt = f"""
            You are an expert educational and professional document verification system.
            Examine the provided image of a document. The user claims this is a "{consultant_document.document_type}" (Category: {consultant_document.qualification_type}).
            
            1. Identify the type of document. Is it a Bachelor's Degree, Master's Degree, Certificate, Transcript, or something else (Unknown/Invalid)?
            2. Verify if the document looks like a valid, legitimate document (Verification Status: Verified or Invalid).
            3. If it is a Bachelor's degree, extract the program/field and decide if it belongs to a finance, tax, accounting, commerce, banking, or auditing domain.
               Relevant-domain keywords: {", ".join(TARGET_BACHELOR_FIELD_KEYWORDS)}.
               Mark is_target_field=true only when the extracted field clearly matches one of those keywords.
            
            Respond strictly in the following JSON format:
            {{
                "determined_type": "Bachelor's Degree" | "Master's Degree" | "Certificate" | "Transcript" | "Unknown",
                "verification_status": "Verified" | "Invalid" | "Error",
                "degree_level": "bachelors" | "masters" | "other",
                "extracted_name": "Full name of document holder if visible",
                "degree_field": "Extracted field text if any",
                "is_target_field": true | false,
                "rejection_reason": "Short rejection reason if invalid",
                "notes": "Any additional observations, e.g., University Name, Student Name, etc."
            }}
            """
            
            print(f"Uploading image {local_image_path} to Gemini...")
            uploaded_file = genai.upload_file(path=local_image_path)
            
            import time
            while uploaded_file.state.name == "PROCESSING":
                print(".", end="", flush=True)
                time.sleep(1)
                uploaded_file = genai.get_file(uploaded_file.name)
                
            if uploaded_file.state.name == "FAILED":
                raise Exception("Image processing failed in Gemini.")

            response = self.gemini_model.generate_content(
                [uploaded_file, prompt],
                generation_config={"response_mime_type": "application/json"}
            )
            
            try:
                uploaded_file.delete()
            except Exception as e:
                print(f"Clean up Gemini file failed: {e}")
                
            result_json = response.text
            result = json.loads(result_json)

            determined_type = result.get("determined_type", "Unknown")
            degree_level = result.get("degree_level", "other")
            is_target_field = bool(result.get("is_target_field", False))
            verification_status = result.get("verification_status", "Unverified")
            rejection_reason = result.get("rejection_reason", "")

            # Enforce validity for bachelor's submissions:
            # - Must actually be a bachelor's degree
            # - Must be in a finance/tax/accounting related field (is_target_field)
            claimed_doc_type = str(consultant_document.document_type or "").strip().lower()
            if claimed_doc_type == "bachelors_degree":
                is_bachelors = (determined_type == "Bachelor's Degree") or (str(degree_level).strip().lower() == "bachelors")
                if not is_bachelors:
                    verification_status = "Invalid"
                    rejection_reason = rejection_reason or "Not a Bachelor's degree"
                    is_target_field = False
                elif not is_target_field:
                    verification_status = "Invalid"
                    rejection_reason = rejection_reason or "Bachelor's degree field not related to finance/tax/accounting"

            normalized = {
                "determined_type": determined_type,
                "verification_status": verification_status,
                "degree_level": degree_level,
                "extracted_name": result.get("extracted_name", ""),
                "degree_field": result.get("degree_field", ""),
                "is_target_field": is_target_field,
                "rejection_reason": rejection_reason,
                "notes": result.get("notes", ""),
            }

            normalized_json = json.dumps(normalized)
            return {
                **normalized,
                "raw_response": normalized_json,
            }
            
        except Exception as e:
            print(f"Error during Gemini verification: {e}")
            return {
                "determined_type": "Unknown",
                "verification_status": "Error",
                "degree_level": "other",
                "extracted_name": "",
                "degree_field": "",
                "is_target_field": False,
                "rejection_reason": "Verification service error",
                "raw_response": json.dumps({"error": str(e)})
            }
        finally:
            if local_image_path and os.path.exists(local_image_path):
                try:
                    os.remove(local_image_path)
                except OSError as e:
                    print(f"Error removing temp file {local_image_path}: {e}")
