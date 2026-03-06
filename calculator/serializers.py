from rest_framework import serializers
from datetime import datetime


class TransactionSerializer(serializers.Serializer):
    """Serializer for a single transaction"""
    # Deductee details
    deductee_name = serializers.CharField(max_length=200)
    deductee_pan = serializers.CharField(max_length=10, required=False, allow_blank=True, allow_null=True)
    no_pan_available = serializers.BooleanField(required=False, default=False)
    
    # Transaction details
    section_code = serializers.CharField(max_length=20)
    amount = serializers.FloatField(min_value=0)
    category = serializers.ChoiceField(choices=[
        ("Company / Firm / Co-operative Society / Local Authority", "Company / Firm / Co-operative Society / Local Authority"),
        ("Individual / HUF", "Individual / HUF")
    ])
    pan_available = serializers.BooleanField()
    deduction_date = serializers.DateField()
    payment_date = serializers.DateField()
    threshold_type = serializers.CharField(max_length=50, required=False, allow_blank=True, allow_null=True)
    annual_threshold_exceeded = serializers.BooleanField(required=False, default=False)
    selected_slab = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    selected_condition = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    threshold_exceeded_before = serializers.BooleanField(required=False, default=False)


class DeductorSerializer(serializers.Serializer):
    """Serializer for deductor details"""
    deductor_name = serializers.CharField(max_length=200)
    tan_number = serializers.CharField(max_length=10)
    entity_name = serializers.CharField(max_length=200)
    
    def validate_tan_number(self, value):
        """
        Validate TAN format:
        - First 3 characters (A-Z): Jurisdiction code
        - 4th character (A-Z): Initial of the deductor
        - Next 5 characters (0-9): Numeric
        - Last character (A-Z): Alphabet
        """
        import re
        value = value.upper()
        if not re.match(r'^[A-Z]{3}[A-Z][0-9]{5}[A-Z]$', value):
            raise serializers.ValidationError(
                "Invalid TAN format. Expected: ABCD12345E (3 letters + 1 letter + 5 digits + 1 letter)"
            )
        return value


class CalculateRequestSerializer(serializers.Serializer):
    """Serializer for TDS calculation request"""
    deductor = DeductorSerializer()
    transactions = TransactionSerializer(many=True)
    
    def validate_transactions(self, value):
        if not value:
            raise serializers.ValidationError("At least one transaction is required")
        return value


class ExcelRequestSerializer(serializers.Serializer):
    """Serializer for Excel generation request"""
    deductor = DeductorSerializer()

class CalculatorSaveSerializer(serializers.ModelSerializer):
    class Meta:
        from .models import CalculatorSave
        model = CalculatorSave
        fields = ['calculator_type', 'data', 'updated_at']
        read_only_fields = ['updated_at']

    def validate_calculator_type(self, value):
        if value not in ['partnership', 'bulk_tds']:
            raise serializers.ValidationError("Saving data is currently only supported for the Partnership and Bulk TDS calculators.")
        return value

    def validate_data(self, value):
        import json
        calculator_type = self.initial_data.get('calculator_type', 'partnership')
        
        # Prevent ridiculously large payloads
        payload_string = json.dumps(value)
        if calculator_type == 'bulk_tds':
            if len(payload_string) > 2048000: # 2MB limit for bulk TDS (large excel files)
                raise serializers.ValidationError("Payload size exceeds maximum allowed limit for Bulk TDS (2MB).")
        else:
            if len(payload_string) > 102400: # 100KB limit for other calculators
                raise serializers.ValidationError("Payload size exceeds maximum allowed limit (100KB).")
            
        # Enforce history limits
        if isinstance(value, dict) and 'history' in value:
            history = value.get('history', [])
            if isinstance(history, list):
                max_history = 5 if calculator_type == 'bulk_tds' else 10
                if len(history) > max_history:
                    value['history'] = history[:max_history]
                
        return value

    def create(self, validated_data):
        from .models import CalculatorSave
        user = self.context['request'].user
        calculator_type = validated_data.get('calculator_type')
        data = validated_data.get('data')
        
        obj, created = CalculatorSave.objects.update_or_create(
            user=user,
            calculator_type=calculator_type,
            defaults={'data': data}
        )
        return obj
