from datetime import date, time
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from consultations.models import ConsultationBooking, Topic
from consultants.models import ConsultantServiceProfile, Service, ServiceCategory
from core_auth.models import User
from service_orders.models import OrderItem, ServiceOrder


class AdditionalPaymentEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.consultant = User.objects.create_user(
            username='consultant_additional_payment',
            email='consultant-additional@example.com',
            password='password',
            role=User.CONSULTANT,
        )
        self.consultant_profile = ConsultantServiceProfile.objects.create(
            user=self.consultant,
            qualification='CA',
            experience_years=5,
        )
        self.customer = User.objects.create_user(
            username='client_additional_payment',
            email='client-additional@example.com',
            password='password',
            role=User.CLIENT,
        )
        self.category = ServiceCategory.objects.create(
            name='Returns',
            description='Return filing services',
        )
        self.service = Service.objects.create(
            category=self.category,
            title='ITR Salary Filing',
            price=Decimal('1499.00'),
            tat='3 days',
            documents_required='PAN, Aadhaar',
            is_active=True,
        )
        self.topic = Topic.objects.create(name='Tax Planning')
        self.booking = ConsultationBooking.objects.create(
            consultant=self.consultant,
            client=self.customer,
            topic=self.topic,
            booking_date=date.today(),
            start_time=time(10, 0),
            end_time=time(10, 30),
            status='confirmed',
            payment_status='paid',
            amount=Decimal('999.00'),
        )

    @patch('service_orders.views._push_additional_payment_notification')
    @patch('service_orders.views.razorpay_client.order.create', return_value={'id': 'order_test_123'})
    def test_request_additional_service_creates_pending_order(self, _mock_order_create, _mock_push):
        self.client.force_authenticate(user=self.consultant)

        response = self.client.post(
            reverse('request-additional-service'),
            {
                'booking_id': self.booking.id,
                'service_id': self.service.id,
                'description': 'Additional review requested during the call.',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        order = ServiceOrder.objects.get(id=response.data['order_id'])
        item = order.items.get()

        self.assertTrue(order.is_additional)
        self.assertEqual(order.user, self.customer)
        self.assertEqual(order.initiated_by, self.consultant)
        self.assertEqual(order.from_booking, self.booking)
        self.assertEqual(order.razorpay_order_id, 'order_test_123')
        self.assertEqual(item.service, self.service)
        self.assertEqual(item.selected_consultant, self.consultant_profile)
        self.assertEqual(item.selection_mode, 'manual')

    @patch('service_orders.views._push_additional_payment_notification')
    @patch('service_orders.views.razorpay_client.order.create', return_value={'id': 'order_multi_123'})
    def test_request_additional_service_supports_multiple_items(self, _mock_order_create, _mock_push):
        self.client.force_authenticate(user=self.consultant)

        response = self.client.post(
            reverse('request-additional-service'),
            {
                'booking_id': self.booking.id,
                'items': [
                    {
                        'service_id': self.service.id,
                        'description': 'Salary and arrears need separate review.',
                    },
                    {
                        'custom_title': 'Advance Tax Projection',
                        'custom_price': '2000',
                        'category_slug': 'itr',
                        'description': 'Client requested quarterly advance tax estimation.',
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        order = ServiceOrder.objects.get(id=response.data['order_id'])
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(order.razorpay_order_id, 'order_multi_123')
        self.assertEqual(order.total_amount, Decimal('3499.00'))

    @patch('service_orders.views.razorpay_client.order.create', return_value={'id': 'order_partial_789'})
    def test_update_additional_selection_keeps_selected_items_only(self, _mock_order_create):
        order = ServiceOrder.objects.create(
            user=self.customer,
            total_amount=Decimal('2499.00'),
            original_amount=Decimal('2499.00'),
            discount_amount=Decimal('0.00'),
            status='pending',
            from_booking=self.booking,
            is_additional=True,
            initiated_by=self.consultant,
            razorpay_order_id='order_pending_111',
        )
        item1 = OrderItem.objects.create(
            order=order,
            service=self.service,
            category=self.category.name,
            service_title='ITR Salary Filing',
            variant_name='First line reason',
            price=Decimal('1499.00'),
            quantity=1,
            selected_consultant=self.consultant_profile,
            selection_mode='manual',
        )
        OrderItem.objects.create(
            order=order,
            service=self.service,
            category=self.category.name,
            service_title='Additional Review',
            variant_name='Second line reason',
            price=Decimal('1000.00'),
            quantity=1,
            selected_consultant=self.consultant_profile,
            selection_mode='manual',
        )

        self.client.force_authenticate(user=self.customer)
        response = self.client.post(
            reverse('update-additional-selection'),
            {
                'order_id': order.id,
                'selected_item_ids': [item1.id],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal('1499.00'))
        self.assertEqual(order.razorpay_order_id, 'order_partial_789')
        self.assertEqual(order.items.count(), 1)

    def test_pending_additional_requests_returns_client_orders(self):
        order = ServiceOrder.objects.create(
            user=self.customer,
            total_amount=Decimal('1499.00'),
            original_amount=Decimal('1499.00'),
            discount_amount=Decimal('0.00'),
            status='pending',
            from_booking=self.booking,
            is_additional=True,
            initiated_by=self.consultant,
            razorpay_order_id='order_pending_456',
        )
        OrderItem.objects.create(
            order=order,
            service=self.service,
            category=self.category.name,
            service_title=self.service.title,
            variant_name='Additional review requested during the call.',
            price=Decimal('1499.00'),
            quantity=1,
            selected_consultant=self.consultant_profile,
            selection_mode='manual',
        )

        self.client.force_authenticate(user=self.customer)
        response = self.client.get(reverse('pending-additional'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['order_id'], order.id)
        self.assertEqual(response.data['results'][0]['service_title'], 'ITR Salary Filing')
        self.assertEqual(response.data['results'][0]['consultant_name'], self.consultant.get_full_name() or self.consultant.username)
