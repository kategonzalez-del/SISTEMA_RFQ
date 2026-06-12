from django.urls import path
from . import views

#from .views import analisis_dibujo, rfq_detail_view
from .views import upload_and_process_rfq
from .views import process_single_file_async
from .views import finalize_analysis_status

app_name = 'RFQ'

urlpatterns = [

    path('rfq/upload/', upload_and_process_rfq, name='upload_rfq'),
    path('rfq/process-file-async/', process_single_file_async, name='process-file-async'),
    path('rfq/finalize-analysis/', views.finalize_analysis_status, name='finalize_analysis'),
    path('api/rfq/check-status/', views.check_analysis_status, name='check_analysis_status'),
]