from rest_framework import viewsets, status
from rest_framework.decorators import action, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.utils import timezone
import random
import uuid

from ..models import TestType, UserSession, VideoResponse
from ..serializers import (
    TestTypeSerializer, UserSessionSerializer,
    ViolationSerializer
)
from ..authentication import IsApplicant

def get_all_questions_from_module(module, var_names=None):
    all_questions = []
    if var_names:
        for var in var_names:
            if hasattr(module, var):
                all_questions.extend(getattr(module, var))
    else:
        for name, val in vars(module).items():
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict) and 'question' in val[0]:
                all_questions.extend(val)
    return all_questions

from .. import gst as gst_module
from .. import income_tax as income_tax_module
from .. import tds as tds_module
from .. import professional_tax as pt_module
from .. import video_questions as video_questions_module

DOMAIN_MAPPING = {
    "gst": {"module": gst_module, "vars": ["gst_assessment"]},
    "income-tax": {"module": income_tax_module, "vars": ["income_tax_batch1", "income_tax_assessment_batch2"]},
    "tds": {"module": tds_module, "vars": ["tds_assessment"]},
    "professional-tax": {"module": pt_module, "vars": ["professional_tax_batch1"]} 
}

SLUG_MAPPING = {
    "GST": "gst",
    "gst": "gst",
    "Income Tax": "income-tax",
    "income_tax": "income-tax",
    "income-tax": "income-tax",
    "TDS": "tds",
    "tds": "tds",
    "Professional Tax": "professional-tax",
    "professional_tax": "professional-tax",
    "profession-tax": "professional-tax"
}


class TestTypeViewSet(viewsets.ModelViewSet):
    queryset = TestType.objects.all()
    serializer_class = TestTypeSerializer
    lookup_field = 'slug'
    permission_classes = [IsApplicant]

    def list(self, request, *args, **kwargs):
        if not TestType.objects.exists():
            default_types = [
                {'name': 'GST', 'slug': 'gst'},
                {'name': 'Income Tax', 'slug': 'income-tax'},
                {'name': 'TDS', 'slug': 'tds'},
                {'name': 'Professional Tax', 'slug': 'professional-tax'},
            ]
            for dt in default_types:
                TestType.objects.create(name=dt['name'], slug=dt['slug'])
            print("Auto-seeded TestTypes")
            
        return super().list(request, *args, **kwargs)

class UserSessionViewSet(viewsets.ModelViewSet):
    queryset = UserSession.objects.all()
    serializer_class = UserSessionSerializer
    permission_classes = [IsApplicant]

    def get_queryset(self):
        return UserSession.objects.filter(application=self.request.application)

    def create(self, request, *args, **kwargs):
        selected_tests = request.data.get('selected_tests', []) 
        test_type_id = request.data.get('test_type')
        
        if not selected_tests and test_type_id:
             try:
                 tt_name = TestType.objects.get(id=test_type_id).name
                 selected_tests = [tt_name]
             except Exception:
                 pass

        if not selected_tests:
            return Response({'error': 'No domains selected'}, status=status.HTTP_400_BAD_REQUEST)

        # Check for Max Attempts (2 failures allowed)
        past_sessions = UserSession.objects.filter(application=request.application).exclude(status='ongoing')
        failed_attempts = 0
        for s in past_sessions:
            if s.status == 'flagged' or (s.status == 'completed' and s.score < 30):
                failed_attempts += 1
        
        if failed_attempts >= 2:
             return Response(
                 {'error': 'You have exceeded the maximum of 2 failed attempts. You are disqualified from further assessments.'}, 
                 status=status.HTTP_403_FORBIDDEN
             )
       
        total_mcqs = 50
        num_domains = len(selected_tests)
        questions_per_domain = total_mcqs // num_domains
        remainder = total_mcqs % num_domains
        
        final_question_set = []
        valid_domains = []

        for idx, test_name in enumerate(selected_tests):
            slug = SLUG_MAPPING.get(test_name, test_name.lower().replace(" ", "_"))
            if slug not in DOMAIN_MAPPING:
                continue 
            
            valid_domains.append(slug)
            config = DOMAIN_MAPPING[slug]
            questions = get_all_questions_from_module(config['module'], config['vars'])
            
            count = questions_per_domain + (1 if idx < remainder else 0)
            selected = random.sample(questions, min(len(questions), count))
            for q in selected:
                q_copy = dict(q)
                q_copy['domain'] = slug
                q_copy['original_id'] = q_copy.get('id')
                q_copy['id'] = f"{slug}_{q_copy.get('id')}"
                final_question_set.append(q_copy)

        random.shuffle(final_question_set)
        
        final_video_questions = []
        video_data = video_questions_module.video_questions
        if "introduction" in video_data:
            final_video_questions.append({
                "id": "v_intro",
                "text": video_data["introduction"][0],
                "type": "introduction"
            })
        
        domain_video_pool = []
        for domain in valid_domains:
            vq_key = domain.replace("-", "_")
            if vq_key in video_data:
                domain_video_pool.extend(video_data[vq_key])
        
        selected_vqs = random.sample(domain_video_pool, min(len(domain_video_pool), 4))
        for i, vq_text in enumerate(selected_vqs):
            final_video_questions.append({
                "id": f"v_{i+1}",
                "text": vq_text,
                "type": "domain"
            })

        test_type_obj = None
        if len(valid_domains) == 1:
            try:
                test_type_obj = TestType.objects.get(slug=valid_domains[0])
            except TestType.DoesNotExist:
                pass
        
        session = UserSession.objects.create(
            application=request.application,
            test_type=test_type_obj, 
            selected_domains=valid_domains,
            question_set=final_question_set,
            video_question_set=final_video_questions,
            status='ongoing'
        )

        sanitized_questions = []
        for q in final_question_set:
            q_safe = dict(q)
            if 'answer' in q_safe:
                del q_safe['answer']
            sanitized_questions.append(q_safe)

        serializer = UserSessionSerializer(session)
        data = serializer.data
        data['questions'] = sanitized_questions
        data['video_questions'] = final_video_questions
        
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def submit_test(self, request, pk=None):
        session = self.get_object()
        if session.status == 'completed':
            return Response({'error': 'Test already submitted'}, status=status.HTTP_400_BAD_REQUEST)

        user_answers = request.data.get('answers', {}) 
        
        score = 0
        total_questions = len(session.question_set)
        
        for question in session.question_set:
            q_id = question.get('id')
            correct_answer = question.get('answer')
            user_selected = user_answers.get(q_id)
            if user_selected and user_selected == correct_answer:
                score += 1
        
        session.score = score
        session.status = 'completed'
        session.end_time = timezone.now()
        session.save()
        
        return Response({'status': 'Test submitted', 'score': score, 'total': total_questions}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def submit_video(self, request, pk=None):
        session = self.get_object()
        video_file = request.FILES.get('video')
        question_id = request.data.get('question_id')

        if not video_file or not question_id:
            return Response({'error': 'Video file and question_id are required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            file_ext = video_file.name.split('.')[-1]
            file_path = f"assessment_videos/{session.application.id}/{session.id}/{question_id}_{uuid.uuid4()}.{file_ext}"

            from django.core.files.storage import default_storage
            saved_path = default_storage.save(file_path, video_file)

            video_response = VideoResponse.objects.create(
                session=session,
                question_identifier=str(question_id),
                video_file=saved_path,
                ai_status='pending'
            )
            
            question_text = "Please evaluate this video response."
            found_question = next((q for q in session.video_question_set if q.get('id') == question_id), None)
            if found_question:
                question_text = found_question.get('text', question_text)

            # Trigger Celery Task asynchronously
            from ..tasks import evaluate_video_task
            evaluate_video_task.delay(video_response.id, question_text)

            return Response({'status': 'Video uploaded. Evaluation processing in background.', 'path': saved_path}, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def latest_result(self, request):
        past_sessions = UserSession.objects.filter(application=request.application).exclude(status='ongoing')
        failed_attempts = 0
        for s in past_sessions:
            if s.status == 'flagged' or (s.status == 'completed' and s.score < 30):
                failed_attempts += 1
        
        is_disqualified = failed_attempts >= 2

        session = UserSession.objects.filter(application=request.application, status__in=['completed', 'flagged']).order_by('-end_time').first()
        
        response_data = {
            'disqualified': is_disqualified,
            'failed_attempts': failed_attempts
        }

        if session:
            video_responses = VideoResponse.objects.filter(session=session)
            video_score = sum([vr.ai_score for vr in video_responses if vr.ai_score])
            video_total_possible = len(session.video_question_set) * 5 
            
            expected_videos = len(session.video_question_set)
            completed_videos = video_responses.filter(ai_status='completed').count()
            video_evaluation_complete = (completed_videos >= expected_videos)

            response_data.update({
                'score': session.score,
                'total': len(session.question_set),
                'passed': session.score >= 30 and session.status != 'flagged',
                'status': session.status,
                'session_id': session.id,
                'video_score': video_score,
                'video_total_possible': video_total_possible,
                'video_evaluation_complete': video_evaluation_complete
            })
            return Response(response_data, status=status.HTTP_200_OK)
        
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def log_violation(self, request, pk=None):
        session = self.get_object()
        serializer = ViolationSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(session=session)
            session.violation_count += 1
            
            if session.violation_count >= 10: 
                session.status = 'flagged'
                session.end_time = timezone.now()
                session.save()
                return Response({'status': 'terminated', 'violation_count': session.violation_count}, status=status.HTTP_200_OK)
            
            session.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def process_proctoring_snapshot(self, request, pk=None):
        session = self.get_object()
        if session.status != 'ongoing':
            return Response({'error': 'Session not active'}, status=status.HTTP_400_BAD_REQUEST)

        image_file = request.FILES.get('image')
        if not image_file:
            return Response({'error': 'Image required'}, status=status.HTTP_400_BAD_REQUEST)

        if session.violation_count >= 10:
             session.status = 'flagged'
             session.save()
             return Response({'status': 'terminated', 'violation_count': session.violation_count}, status=status.HTTP_200_OK)

        try:
            from django.core.files.storage import default_storage
            from django.core.files.base import ContentFile
            
            image_content = image_file.read()
            file_path = f"proctoring/{session.application.id}/{session.id}/{uuid.uuid4()}.jpg"
            saved_path = default_storage.save(file_path, ContentFile(image_content))

            from ..utils.rekognition_client import get_rekognition_client
            rekognition = get_rekognition_client()

            det_response = rekognition.detect_faces(Image={'Bytes': image_content})
            face_details = det_response.get('FaceDetails', [])
            face_count = len(face_details)

            is_violation = False
            violation_reason = None
            match_score = 0.0

            if face_count > 1:
                is_violation = True
                violation_reason = f"Multiple faces detected: {face_count}"
            
            elif face_count == 1:
                from ..models import FaceVerification
                try:
                    verification = FaceVerification.objects.get(application=session.application)
                    ref_image_path = verification.id_image_path
                    
                    if ref_image_path:
                        with default_storage.open(ref_image_path, 'rb') as ref_f:
                            ref_bytes = ref_f.read()
                        
                        comp_response = rekognition.compare_faces(
                            SourceImage={'Bytes': ref_bytes},
                            TargetImage={'Bytes': image_content},
                            SimilarityThreshold=80
                        )
                        
                        matches = comp_response.get('FaceMatches', [])
                        if matches:
                            match_score = matches[0]['Similarity']
                        else:
                            is_violation = True
                            violation_reason = "Face mismatch with reference photo"
                            match_score = 0.0
                except FaceVerification.DoesNotExist:
                     pass

            elif face_count == 0:
                 is_violation = True
                 violation_reason = "No face detected"
    
            if is_violation:
                session.violation_count += 1
                session.save()

                if session.violation_count >= 10:
                     session.status = 'flagged'
                     session.end_time = timezone.now()
                     session.save()
            
            from ..models import ProctoringSnapshot
            ProctoringSnapshot.objects.create(
                session=session,
                image_url=saved_path,
                is_violation=is_violation,
                violation_reason=violation_reason,
                face_count=face_count,
                match_score=match_score
            )
            
            response_data = {
                'status': 'ok',
                'violation': is_violation,
                'violation_count': session.violation_count
            }

            if session.status == 'flagged':
                 response_data['status'] = 'terminated'
            elif is_violation:
                 response_data['status'] = 'warning'
                 response_data['reason'] = violation_reason

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
