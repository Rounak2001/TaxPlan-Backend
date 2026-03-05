import boto3
import time
import json
import os
import requests
import google.generativeai as genai
from django.conf import settings
from botocore.config import Config

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
        Sends transcript and video to Gemini for evaluation.
        """
        prompt = f"""
        You are an expert interviewer evaluating a candidate's video response.
        
        Question: "{question_text}"
        Candidate's Answer (Transcribed): "{transcript_text}"
        
        Task:
        1. Watch and listen to the video carefully to evaluate the candidate's confidence, presentation skills, and body language.
        2. Read the transcript to evaluate the answer based on relevance, clarity, and correctness.
        3. Assign an overall score from 0 to 5 (integer) combining both content and presentation.
        4. Provide brief, constructive feedback covering both the content of the answer and their presentation style.
        
        Output JSON format:
        {{
            "score": <int>,
            "feedback": "<string>",
            "reasoning": "<string>"
        }}
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
        import tempfile
        import os
        from django.core.files.storage import default_storage
        
        local_video_path = None
        try:
            # 1. Transcribe
            print(f"Starting transcription for video {video_response.id}...")
            transcript = self.transcribe_video(video_response)
            
            # 2. Download Video to local temp file for Gemini
            print("Downloading video from storage for Gemini analysis...")
            
            # Get file extension safely, default to mp4
            file_ext = video_response.video_file.split('.')[-1] if '.' in video_response.video_file else 'mp4'
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp_file:
                local_video_path = tmp_file.name
                
                # Use Django storage to read the file
                with default_storage.open(video_response.video_file, 'rb') as f:
                    # Write in chunks to handle large files efficiently
                    for chunk in f.chunks() if hasattr(f, 'chunks') else [f.read()]:
                        tmp_file.write(chunk)
            
            # 3. Evaluate with both Transcript and Video
            print(f"Evaluating transcript and video...")
            evaluation = self.evaluate_transcript(transcript, question_text, local_video_path)
            
            # 4. Return results
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
            
            Respond strictly in the following JSON format:
            {
                "document_type": "Aadhaar Card" | "PAN Card" | "Masked Aadhaar" | "Masked PAN" | "Unknown",
                "verification_status": "Verified" | "Invalid",
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
            
            return {
                "document_type": result.get("document_type", "Unknown"),
                "verification_status": result.get("verification_status", "Unverified"),
                "raw_response": result_json
            }
            
        except Exception as e:
            print(f"Identity Verification Error: {e}")
            return {
                "document_type": "Error",
                "verification_status": "Failed",
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
            
            Respond strictly in the following JSON format:
            {{
                "determined_type": "Bachelor's Degree",
                "verification_status": "Verified",
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
            
            return {
                "determined_type": result.get("determined_type", "Unknown"),
                "verification_status": result.get("verification_status", "Unverified"),
                "raw_response": result_json
            }
            
        except Exception as e:
            print(f"Error during Gemini verification: {e}")
            return {
                "determined_type": "Unknown",
                "verification_status": "Error",
                "raw_response": json.dumps({"error": str(e)})
            }
        finally:
            if local_image_path and os.path.exists(local_image_path):
                try:
                    os.remove(local_image_path)
                except OSError as e:
                    print(f"Error removing temp file {local_image_path}: {e}")

