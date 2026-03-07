from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
import random
import json
import uuid

from ..models import TestType, UserSession, VideoResponse, Violation
from ..serializers import (
    TestTypeSerializer, UserSessionSerializer,
    ViolationSerializer
)
from ..authentication import IsApplicant
from ..proctoring_policy import (
    MAX_SESSION_VIOLATIONS,
    MAX_VIOLATIONS_PER_TYPE,
    HEAD_POSE_YAW_THRESHOLD,
    HEAD_POSE_PITCH_THRESHOLD,
    HEAD_POSE_ROLL_THRESHOLD,
    HEAD_POSE_SUSTAINED_WINDOW,
    HEAD_POSE_SUSTAINED_MIN_HITS,
    GAZE_SUSTAINED_WINDOW,
    GAZE_SUSTAINED_MIN_HITS,
    policy_payload,
    STATUS_OK,
    STATUS_WARNING,
    STATUS_TERMINATED,
    is_supported_device,
    parse_bool,
    proctoring_response,
)
from ..risk import compute_proctoring_risk_summary


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

    def _compute_mcq_score(self, question_set, user_answers):
        answers = user_answers if isinstance(user_answers, dict) else {}
        score = 0
        total_questions = len(question_set or [])

        for question in (question_set or []):
            q_id = question.get('id')
            correct_answer = question.get('answer')
            user_selected = answers.get(q_id)
            if user_selected is None and q_id is not None:
                user_selected = answers.get(str(q_id))
            if user_selected is not None and user_selected == correct_answer:
                score += 1

        return score, total_questions

    def _apply_violation(self, session, violation_type):
        """Centralized violation tracking using violation_counters JSONField."""
        violation_type = (violation_type or 'unknown').strip().lower()
        # Fullscreen exits are intentionally not counted as violations.
        if violation_type == 'fullscreen_exit':
            counters = dict(session.violation_counters or {})
            return {
                'terminated': False,
                'reason': "Fullscreen exit is not counted as a violation",
                'violation_type': violation_type,
                'violation_type_count': int(counters.get(violation_type, 0)),
                'violation_counters': counters,
                'violation_count': int(session.violation_count or 0),
                'ignored': True,
            }

        counters = dict(session.violation_counters or {})
        counters[violation_type] = int(counters.get(violation_type, 0)) + 1
        session.violation_counters = counters
        session.violation_count = int(session.violation_count or 0) + 1

        reason = None
        terminated = False
        if counters[violation_type] >= MAX_VIOLATIONS_PER_TYPE:
            terminated = True
            reason = f"Maximum '{violation_type}' violations reached ({counters[violation_type]})"
        elif session.violation_count >= MAX_SESSION_VIOLATIONS:
            terminated = True
            reason = f"Maximum total violations reached ({session.violation_count})"

        if terminated:
            try:
                score, _total = self._compute_mcq_score(session.question_set, session.mcq_answers)
                session.score = score
            except Exception:
                # Best-effort scoring; do not block termination.
                pass
            session.status = 'flagged'
            session.end_time = timezone.now()
        session.save()
        return {
            'terminated': terminated,
            'reason': reason,
            'violation_type': violation_type,
            'violation_type_count': counters[violation_type],
            'violation_counters': counters,
            'violation_count': session.violation_count,
        }

    def create(self, request, *args, **kwargs):
        # Device policy check
        if not is_supported_device(request.META.get('HTTP_USER_AGENT', '')):
            return Response(
                {
                    'error': 'Assessment is supported only on desktop or laptop browsers.',
                    'device_policy': policy_payload().get('device_policy', {}),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

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

        # Check for Max Attempts (2 failures allowed) OR permanent disqualification (flagged)
        past_sessions = UserSession.objects.filter(application=request.application).exclude(status='ongoing')
        failed_attempts = 0
        for s in past_sessions:
            if s.status == 'flagged':
                return Response(
                    {'error': 'You have been permanently disqualified due to a proctoring violation. You cannot take further assessments.'}, 
                    status=status.HTTP_403_FORBIDDEN
                )
            if s.status == 'completed' and s.score < 30:
                failed_attempts += 1
        
        if failed_attempts >= 2:
             return Response(
                 {'error': 'You have exceeded the maximum of 2 failed attempts. You are disqualified from further assessments.'}, 
                 status=status.HTTP_403_FORBIDDEN
             )

        # Block if there's already an ongoing session
        if UserSession.objects.filter(application=request.application, status='ongoing').exists():
            return Response(
                {'error': 'You already have an ongoing session. Please complete or wait for it to finish.'},
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

    @action(detail=False, methods=['get'])
    def proctoring_policy(self, request):
        """Expose proctoring policy for frontend display and enforcement."""
        return Response(policy_payload(), status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def save_mcq(self, request, pk=None):
        """Save MCQ answers mid-test without marking it as completed."""
        session = self.get_object()
        if session.status not in {'ongoing', 'flagged'}:
            return Response({'error': 'Cannot save MCQ, session is not active.'}, status=status.HTTP_400_BAD_REQUEST)

        user_answers = request.data.get('answers', {}) 
        if not isinstance(user_answers, dict):
            user_answers = {}

        existing_answers = session.mcq_answers if isinstance(session.mcq_answers, dict) else {}
        merged_answers = {**existing_answers, **user_answers}
        score, total_questions = self._compute_mcq_score(session.question_set, merged_answers)

        session.mcq_answers = merged_answers
        session.score = score
        session.save(update_fields=['mcq_answers', 'score'])

        answered_count = sum(1 for _k, v in merged_answers.items() if v not in {None, ''})
        
        return Response({
            'message': 'MCQ progress saved successfully',
            'score': score,
            'total': total_questions,
            'answered': answered_count,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def submit_test(self, request, pk=None):
        session = self.get_object()
        if session.status == 'completed':
            return Response({'error': 'Test already submitted'}, status=status.HTTP_400_BAD_REQUEST)

        user_answers = request.data.get('answers', {}) 
        if not isinstance(user_answers, dict):
            user_answers = {}

        existing_answers = session.mcq_answers if isinstance(session.mcq_answers, dict) else {}
        merged_answers = {**existing_answers, **user_answers}
        score, total_questions = self._compute_mcq_score(session.question_set, merged_answers)

        session.mcq_answers = merged_answers
        session.score = score
        # Only mark as completed if it wasn't already flagged
        if session.status != 'flagged':
            session.status = 'completed'
        session.end_time = timezone.now()
        session.save()
        
        proctoring_ai = compute_proctoring_risk_summary(session)
        return Response(
            {
                'status': 'Test submitted',
                'score': score,
                'total': total_questions,
                'proctoring_ai': proctoring_ai,
            },
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def submit_video(self, request, pk=None):
        """Direct multipart video upload — saves to S3 via default_storage."""
        session = self.get_object()
        video_file = request.FILES.get('video')
        question_id = request.data.get('question_id')

        if not video_file or not question_id:
            return Response({'error': 'Video file and question_id are required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            file_ext = video_file.name.split('.')[-1] if '.' in video_file.name else 'webm'
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
            import traceback
            traceback.print_exc()
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def latest_result(self, request):
        past_sessions = UserSession.objects.filter(application=request.application).exclude(status='ongoing')
        failed_attempts = 0
        is_disqualified = False
        
        for s in past_sessions:
            if s.status == 'flagged':
                is_disqualified = True
                break
            if s.status == 'completed' and s.score < 30:
                failed_attempts += 1
        
        if failed_attempts >= 2:
            is_disqualified = True

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
                'score': session.score if session.status != 'flagged' else None,
                'total': len(session.question_set),
                'passed': session.score >= 30 and session.status != 'flagged',
                'status': session.status,
                'session_id': session.id,
                'video_score': video_score if session.status != 'flagged' else None,
                'video_total_possible': video_total_possible,
                'video_evaluation_complete': video_evaluation_complete,
                'hide_marks': session.status == 'flagged',
                'proctoring_ai': compute_proctoring_risk_summary(session),
            })
            return Response(response_data, status=status.HTTP_200_OK)
        
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def log_violation(self, request, pk=None):
        session = self.get_object()
        serializer = ViolationSerializer(data=request.data)
        if serializer.is_valid():
            violation_type = serializer.validated_data.get('violation_type', 'unknown')
            serializer.save(session=session)
            applied = self._apply_violation(session, violation_type)

            if applied.get('ignored'):
                return Response(
                    proctoring_response(
                        STATUS_OK,
                        applied['violation_count'],
                        violation=False,
                        reason=applied['reason'],
                        context={
                            'violation_type': applied['violation_type'],
                            'violation_type_count': applied['violation_type_count'],
                            'violation_counters': applied['violation_counters'],
                        },
                    ),
                    status=status.HTTP_200_OK
                )

            if applied['terminated']:
                return Response(
                    proctoring_response(
                        STATUS_TERMINATED,
                        applied['violation_count'],
                        violation=True,
                        reason=applied['reason'],
                        context={
                            'violation_type': applied['violation_type'],
                            'violation_type_count': applied['violation_type_count'],
                            'violation_counters': applied['violation_counters'],
                        },
                    ),
                    status=status.HTTP_200_OK
                )
            return Response(
                proctoring_response(
                    STATUS_WARNING,
                    applied['violation_count'],
                    violation=True,
                    reason=f"Violation logged: {applied['violation_type']}",
                    context={
                        'violation_type': applied['violation_type'],
                        'violation_type_count': applied['violation_type_count'],
                        'violation_counters': applied['violation_counters'],
                    },
                ),
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def process_proctoring_snapshot(self, request, pk=None):
        session = self.get_object()
        if session.status != 'ongoing':
            return Response({'error': 'Session not active'}, status=status.HTTP_400_BAD_REQUEST)

        image_file = request.FILES.get('image')
        if not image_file:
            return Response({'error': 'Image required'}, status=status.HTTP_400_BAD_REQUEST)
        
        def parse_optional_float(value):
            if value is None:
                return None
            value = str(value).strip()
            if value == '':
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def parse_label_detection_results(value):
            if value is None or value == '':
                return []
            if isinstance(value, (list, dict)):
                return value
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return []

        def parse_optional_bool(value):
            if value is None:
                return None
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized == '':
                return None
            if normalized in {'true', '1', 'yes', 'on'}:
                return True
            if normalized in {'false', '0', 'no', 'off'}:
                return False
            return None

        def parse_optional_str(value, max_len=50):
            if value is None:
                return None
            value = str(value).strip()
            if value == '':
                return None
            return value[:max_len]

        snapshot_id = request.data.get('snapshot_id')
        if snapshot_id is not None:
            snapshot_id = str(snapshot_id).strip()[:64] or None

        # Optional client-side metadata (backward-compatible for old clients)
        pose_yaw = parse_optional_float(request.data.get('pose_yaw'))
        pose_pitch = parse_optional_float(request.data.get('pose_pitch'))
        pose_roll = parse_optional_float(request.data.get('pose_roll'))
        gaze_violation_input = parse_optional_bool(request.data.get('gaze_violation'))
        audio_detected = parse_bool(request.data.get('audio_detected'), default=False)
        mouth_state = request.data.get('mouth_state')
        if mouth_state is not None:
            mouth_state = str(mouth_state).strip()[:20] or None
        label_detection_results = parse_label_detection_results(request.data.get('label_detection_results'))
        client_detector_status = parse_optional_str(request.data.get('detector_status'))
        client_webcam_status = parse_optional_str(request.data.get('webcam_status'))
        client_mic_status = parse_optional_str(request.data.get('mic_status'))

        snapshot_context = {
            'snapshot_id': snapshot_id,
            'audio_detected': audio_detected,
            'gaze_violation': gaze_violation_input if gaze_violation_input is not None else False,
            'pose_yaw': pose_yaw,
            'pose_pitch': pose_pitch,
            'pose_roll': pose_roll,
            'mouth_state': mouth_state,
            'label_detection_results': label_detection_results,
            'fullscreen_state': request.data.get('fullscreen_state') or 'unknown',
            'client_timestamp': request.data.get('client_timestamp'),
            'client_detector_status': client_detector_status,
            'client_webcam_status': client_webcam_status,
            'client_mic_status': client_mic_status,
        }

        # Terminate if limit reached
        if session.violation_count >= MAX_SESSION_VIOLATIONS:
             session.status = 'flagged'
             session.save()
             return Response(proctoring_response(STATUS_TERMINATED, session.violation_count, violation=True), status=status.HTTP_200_OK)

        try:
            from ..models import ProctoringSnapshot

            # Idempotency: if this snapshot_id was already processed for this session,
            # return deterministic response without creating duplicate violations.
            if snapshot_id:
                existing_snapshot = ProctoringSnapshot.objects.filter(
                    session=session,
                    snapshot_id=snapshot_id
                ).first()
                if existing_snapshot:
                    duplicate_context = {
                        **snapshot_context,
                        'duplicate': True,
                        'existing_snapshot_id': existing_snapshot.id,
                        'face_count': existing_snapshot.face_count,
                        'match_score': existing_snapshot.match_score,
                        'pose_yaw': existing_snapshot.pose_yaw,
                        'pose_pitch': existing_snapshot.pose_pitch,
                        'pose_roll': existing_snapshot.pose_roll,
                        'mouth_state': existing_snapshot.mouth_state,
                        'audio_detected': existing_snapshot.audio_detected,
                        'gaze_violation': existing_snapshot.gaze_violation,
                        'label_detection_results': existing_snapshot.label_detection_results,
                        'rule_outcomes': existing_snapshot.rule_outcomes,
                        'detector_mode': 'duplicate_cached',
                    }
                    duplicate_status = STATUS_WARNING if existing_snapshot.is_violation else STATUS_OK
                    duplicate_reason = existing_snapshot.violation_reason if existing_snapshot.is_violation else None
                    if session.status == 'flagged' or session.violation_count >= MAX_SESSION_VIOLATIONS:
                        duplicate_status = STATUS_TERMINATED
                    return Response(
                        proctoring_response(
                            duplicate_status,
                            session.violation_count,
                            violation=existing_snapshot.is_violation,
                            reason=duplicate_reason,
                            context=duplicate_context,
                        ),
                        status=status.HTTP_200_OK
                    )

            # 1. Save Snapshot to S3
            from django.core.files.storage import default_storage
            from django.core.files.base import ContentFile
            
            image_content = image_file.read()
            
            file_path = f"proctoring/{session.application.id}/{session.id}/{uuid.uuid4()}.jpg"
            saved_path = default_storage.save(file_path, ContentFile(image_content))

            # 2. Rekognition Analysis
            from ..utils.rekognition_client import get_rekognition_client
            rekognition = get_rekognition_client()

            det_response = rekognition.detect_faces(Image={'Bytes': image_content})
            face_details = det_response.get('FaceDetails', [])
            face_count = len(face_details)

            # Browser-agnostic telemetry fallback from Rekognition.
            server_fallback_applied = False
            primary_face = face_details[0] if face_count > 0 else None
            if primary_face:
                pose = primary_face.get('Pose', {})
                derived_yaw = pose.get('Yaw')
                derived_pitch = pose.get('Pitch')
                derived_roll = pose.get('Roll')
                print(f"--- PROCTORING DEBUG ---")
                print(f"  Face {session.application.email} - Pose:")
                print(f"  Yaw (left/right): {derived_yaw}")
                print(f"  Pitch (up/down): {derived_pitch}")
                print(f"  Roll (tilt): {derived_roll}")
                print(f"------------------------")
                if pose_yaw is None and derived_yaw is not None:
                    pose_yaw = float(derived_yaw)
                    server_fallback_applied = True
                if pose_pitch is None and derived_pitch is not None:
                    pose_pitch = float(derived_pitch)
                    server_fallback_applied = True
                if pose_roll is None and derived_roll is not None:
                    pose_roll = float(derived_roll)
                    server_fallback_applied = True

                if mouth_state is None:
                    mouth_info = primary_face.get('MouthOpen', {})
                    mouth_val = mouth_info.get('Value')
                    if isinstance(mouth_val, bool):
                        mouth_state = 'open' if mouth_val else 'closed'
                        server_fallback_applied = True

            derived_gaze_violation = False
            if pose_yaw is not None and abs(pose_yaw) > 20:
                derived_gaze_violation = True
            if pose_pitch is not None and abs(pose_pitch) > 15:
                derived_gaze_violation = True
            gaze_violation = gaze_violation_input if gaze_violation_input is not None else derived_gaze_violation
            if gaze_violation_input is None:
                server_fallback_applied = True

            if not label_detection_results:
                try:
                    labels_response = rekognition.detect_labels(
                        Image={'Bytes': image_content},
                        MaxLabels=10,
                        MinConfidence=80
                    )
                    label_detection_results = [
                        {
                            'name': label.get('Name'),
                            'confidence': round(label.get('Confidence', 0.0), 2),
                        }
                        for label in labels_response.get('Labels', [])
                    ]
                    server_fallback_applied = True
                except Exception:
                    label_detection_results = []

            snapshot_context.update({
                'gaze_violation': gaze_violation,
                'pose_yaw': pose_yaw,
                'pose_pitch': pose_pitch,
                'pose_roll': pose_roll,
                'mouth_state': mouth_state,
                'label_detection_results': label_detection_results,
                'server_fallback_applied': server_fallback_applied,
                'detector_mode': 'server_fallback' if server_fallback_applied else 'client',
            })

            is_violation = False
            violation_reason = None
            match_score = 0.0
            structured_reasons = []
            rule_outcomes = {}

            # Rule 1: Multiple Faces
            if face_count > 1:
                is_violation = True
                violation_reason = f"Multiple faces detected: {face_count}"
                structured_reasons.append({
                    'rule': 'face_count',
                    'severity': 'high',
                    'message': violation_reason,
                    'enforce_violation': True,
                })
            
            # Rule 2: Face Match (if exactly 1 face)
            elif face_count == 1:
                from ..models import FaceVerification
                try:
                    verification = FaceVerification.objects.get(application=session.application)
                    ref_image_path = verification.live_image_path
                    
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
                            structured_reasons.append({
                                'rule': 'face_match',
                                'severity': 'high',
                                'message': violation_reason,
                                'enforce_violation': True,
                            })
                except FaceVerification.DoesNotExist:
                     pass

            elif face_count == 0:
                 is_violation = True
                 violation_reason = "No face detected"
                 structured_reasons.append({
                    'rule': 'face_presence',
                    'severity': 'high',
                    'message': violation_reason,
                    'enforce_violation': True,
                })

            # Rule 3: Head pose check (sustained-window enforcement)
            head_pose_triggered = False
            if pose_yaw is not None and abs(pose_yaw) > HEAD_POSE_YAW_THRESHOLD:
                head_pose_triggered = True
            if pose_pitch is not None and abs(pose_pitch) > HEAD_POSE_PITCH_THRESHOLD:
                head_pose_triggered = True
            if pose_roll is not None and abs(pose_roll) > HEAD_POSE_ROLL_THRESHOLD:
                head_pose_triggered = True

            # Reset sustained-window counting after a pose violation is raised, so older samples don't keep re-triggering.
            last_pose_violation_at = (
                Violation.objects
                .filter(session=session, violation_type='pose')
                .order_by('-timestamp')
                .values_list('timestamp', flat=True)
                .first()
            )
            recent_snapshots_qs = ProctoringSnapshot.objects.filter(session=session)
            if last_pose_violation_at:
                recent_snapshots_qs = recent_snapshots_qs.filter(timestamp__gt=last_pose_violation_at)
            recent_snapshots = recent_snapshots_qs.order_by('-timestamp')[:HEAD_POSE_SUSTAINED_WINDOW - 1]
            historical_hits = 0
            historical_count = 0
            for snap in recent_snapshots:
                if snap.pose_yaw is None and snap.pose_pitch is None:
                    continue
                historical_count += 1
                if (
                    (snap.pose_yaw is not None and abs(snap.pose_yaw) > HEAD_POSE_YAW_THRESHOLD)
                    or (snap.pose_pitch is not None and abs(snap.pose_pitch) > HEAD_POSE_PITCH_THRESHOLD)
                    or (snap.pose_roll is not None and abs(snap.pose_roll) > HEAD_POSE_ROLL_THRESHOLD)
                ):
                    historical_hits += 1

            sustained_hits = historical_hits + (1 if head_pose_triggered else 0)
            sustained_window_count = historical_count + (1 if (pose_yaw is not None or pose_pitch is not None) else 0)
            sustained_head_pose_triggered = (
                sustained_hits >= HEAD_POSE_SUSTAINED_MIN_HITS
                and sustained_window_count >= HEAD_POSE_SUSTAINED_MIN_HITS
            )

            if head_pose_triggered:
                structured_reasons.append({
                    'rule': 'head_pose',
                    'severity': 'medium',
                    'message': f"Suspicious head pose detected (yaw={pose_yaw}, pitch={pose_pitch}, sustained_hits={sustained_hits})",
                    'enforce_violation': sustained_head_pose_triggered,
                })
            rule_outcomes['head_pose'] = {
                'triggered': head_pose_triggered,
                'sustained_triggered': sustained_head_pose_triggered,
                'yaw': pose_yaw,
                'pitch': pose_pitch,
                'roll': pose_roll,
                'thresholds': {
                    'yaw_abs_gt': HEAD_POSE_YAW_THRESHOLD,
                    'pitch_abs_gt': HEAD_POSE_PITCH_THRESHOLD,
                    'roll_abs_gt': HEAD_POSE_ROLL_THRESHOLD,
                },
                'window': {
                    'size': HEAD_POSE_SUSTAINED_WINDOW,
                    'min_hits': HEAD_POSE_SUSTAINED_MIN_HITS,
                    'hits': sustained_hits,
                    'samples': sustained_window_count,
                },
            }
            if sustained_head_pose_triggered and not is_violation:
                is_violation = True
                violation_reason = "Sustained head pose deviation detected"

            # Rule 4: Gaze signal check (sustained-window)
            # Reset sustained-window counting after a gaze violation is raised, so older samples don't keep re-triggering.
            last_gaze_violation_at = (
                Violation.objects
                .filter(session=session, violation_type='gaze')
                .order_by('-timestamp')
                .values_list('timestamp', flat=True)
                .first()
            )
            recent_gaze_qs = ProctoringSnapshot.objects.filter(session=session)
            if last_gaze_violation_at:
                recent_gaze_qs = recent_gaze_qs.filter(timestamp__gt=last_gaze_violation_at)
            recent_gaze_snapshots = recent_gaze_qs.order_by('-timestamp')[:GAZE_SUSTAINED_WINDOW - 1]
            gaze_historical_hits = 0
            gaze_historical_samples = 0
            for snap in recent_gaze_snapshots:
                if snap.gaze_violation is None:
                    continue
                gaze_historical_samples += 1
                if bool(snap.gaze_violation):
                    gaze_historical_hits += 1
            gaze_sustained_hits = gaze_historical_hits + (1 if bool(gaze_violation) else 0)
            gaze_sustained_samples = gaze_historical_samples + 1
            sustained_gaze_triggered = (
                gaze_sustained_hits >= GAZE_SUSTAINED_MIN_HITS
                and gaze_sustained_samples >= GAZE_SUSTAINED_MIN_HITS
            )

            if gaze_violation:
                structured_reasons.append({
                    'rule': 'gaze_signal',
                    'severity': 'medium',
                    'message': f"Gaze violation signal detected (sustained_hits={gaze_sustained_hits})",
                    'enforce_violation': sustained_gaze_triggered,
                })
            rule_outcomes['gaze_signal'] = {
                'triggered': bool(gaze_violation),
                'value': bool(gaze_violation),
                'sustained_triggered': sustained_gaze_triggered,
                'window': {
                    'size': GAZE_SUSTAINED_WINDOW,
                    'min_hits': GAZE_SUSTAINED_MIN_HITS,
                    'hits': gaze_sustained_hits,
                    'samples': gaze_sustained_samples,
                },
            }
            if sustained_gaze_triggered and not is_violation:
                is_violation = True
                violation_reason = "Sustained gaze deviation detected"

            # Rule 5: Audio + mouth correlation
            audio_mouth_triggered = bool(audio_detected) and (mouth_state == 'closed')
            if audio_mouth_triggered:
                structured_reasons.append({
                    'rule': 'audio_mouth_correlation',
                    'severity': 'low',
                    'message': "Audio detected while mouth appears closed",
                    'enforce_violation': True,
                })
            rule_outcomes['audio_mouth_correlation'] = {
                'triggered': audio_mouth_triggered,
                'audio_detected': bool(audio_detected),
                'mouth_state': mouth_state,
            }
            if audio_mouth_triggered and not is_violation:
                is_violation = True
                violation_reason = "Suspicious voice activity detected"

            # Rule 6: Earphone/headphone label detection
            label_names = [
                str(label.get('name', '')).strip().lower()
                for label in label_detection_results
                if isinstance(label, dict)
            ]
            earphone_keywords = {'headphone', 'headphones', 'earphone', 'earphones', 'airpod', 'earbud', 'earbuds'}
            matched_earphone_labels = sorted({
                name for name in label_names
                if any(keyword in name for keyword in earphone_keywords)
            })
            earphone_triggered = len(matched_earphone_labels) > 0
            if earphone_triggered:
                structured_reasons.append({
                    'rule': 'earphone_label',
                    'severity': 'medium',
                    'message': f"Earphone/headphone-like label detected: {', '.join(matched_earphone_labels)}",
                    'enforce_violation': False,
                })
            rule_outcomes['earphone_label'] = {
                'triggered': earphone_triggered,
                'matched_labels': matched_earphone_labels,
            }

            # Face-related rules captured for consistency.
            rule_outcomes['face_count'] = {
                'triggered': face_count != 1,
                'face_count': face_count,
            }
            rule_outcomes['face_match'] = {
                'triggered': bool(face_count == 1 and match_score == 0.0 and is_violation and (violation_reason or '').startswith('Face mismatch')),
                'match_score': match_score,
                'threshold': 80,
            }
            rule_outcomes['client_capabilities'] = {
                'webcam_status': client_webcam_status or 'unknown',
                'mic_status': client_mic_status or 'unknown',
                'detector_status': client_detector_status or 'unknown',
            }
            rule_outcomes['processing_meta'] = {
                'server_fallback_applied': bool(server_fallback_applied),
                'detector_mode': 'server_fallback' if server_fallback_applied else 'client',
            }

            snapshot_context.update({
                'rule_outcomes': rule_outcomes,
                'reasons': structured_reasons,
            })

            applied = None
            if is_violation:
                violation_type = 'webcam'
                if violation_reason in {"No face detected", "Face mismatch with reference photo"} or str(violation_reason).startswith("Multiple faces detected"):
                    violation_type = 'face'
                elif violation_reason == "Sustained head pose deviation detected":
                    violation_type = 'pose'
                elif violation_reason == "Sustained gaze deviation detected":
                    violation_type = 'gaze'
                elif violation_reason == "Suspicious voice activity detected":
                    violation_type = 'voice'

                Violation.objects.create(session=session, violation_type=violation_type)
                applied = self._apply_violation(session, violation_type)
                snapshot_context.update({
                    'violation_type': applied['violation_type'],
                    'violation_type_count': applied['violation_type_count'],
                    'violation_counters': applied['violation_counters'],
                })
            
            # Save Snapshot Record
            ProctoringSnapshot.objects.create(
                session=session,
                snapshot_id=snapshot_id,
                image_url=saved_path,
                is_violation=is_violation,
                violation_reason=violation_reason,
                face_count=face_count,
                match_score=match_score,
                pose_yaw=pose_yaw,
                pose_pitch=pose_pitch,
                pose_roll=pose_roll,
                mouth_state=mouth_state,
                audio_detected=audio_detected,
                gaze_violation=gaze_violation,
                label_detection_results=label_detection_results,
                rule_outcomes=rule_outcomes,
            )

            if applied and applied['terminated']:
                 response_data = proctoring_response(
                    STATUS_TERMINATED,
                    applied['violation_count'],
                    violation=is_violation,
                    reason=applied['reason'] or violation_reason,
                    context=snapshot_context,
                )
            elif is_violation:
                 response_data = proctoring_response(
                    STATUS_WARNING,
                    session.violation_count,
                    violation=True,
                    reason=violation_reason,
                    context=snapshot_context,
                )
            else:
                response_data = proctoring_response(
                    STATUS_OK,
                    session.violation_count,
                    violation=False,
                    context=snapshot_context,
                )

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"Proctoring Error: {e}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
