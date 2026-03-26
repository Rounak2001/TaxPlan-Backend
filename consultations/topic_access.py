from __future__ import annotations

from django.contrib.auth import get_user_model

from .models import Topic

User = get_user_model()


def resolve_topic(topic_identifier) -> Topic | None:
    if not topic_identifier:
        return None

    if isinstance(topic_identifier, Topic):
        return topic_identifier

    if isinstance(topic_identifier, int) or str(topic_identifier).isdigit():
        return Topic.objects.filter(id=int(topic_identifier)).first()

    topic_lookup = str(topic_identifier).strip()
    if not topic_lookup:
        return None

    from django.db import models

    # 1. Try exact match (case-insensitive)
    topic = Topic.objects.filter(name__iexact=topic_lookup).first()
    if topic:
        return topic
        
    # 2. Try matching by "slugified" name (replace underscores with spaces)
    # This helps if the frontend sends "itr_salary" but the topic is "ITR Salary Filing"
    query_as_name = topic_lookup.replace('_', ' ')
    topic = Topic.objects.filter(name__icontains=query_as_name).first()
    if topic:
        return topic

    # 3. Fallback to generic icontains
    return Topic.objects.filter(name__icontains=topic_lookup).first()


def get_consultants_for_topic(topic: Topic | None):
    consultants = User.objects.filter(role="CONSULTANT")
    if topic is None:
        return consultants.distinct()

    if topic.service_id:
        consultants = consultants.filter(
            consultant_service_profile__service_expertise__service_id=topic.service_id
        )
    elif topic.category_id:
        consultants = consultants.filter(
            consultant_service_profile__service_expertise__service__category_id=topic.category_id
        )
    else:
        consultants = consultants.filter(topics=topic)

    return consultants.distinct()
